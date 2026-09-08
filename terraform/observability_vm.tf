# VM self-hospedando Phoenix + LangFuse (observability/docker-compose.yml)
# pros DOIS motivos que Cloud Run não resolve:
#
#   1. Cloud Run só aloca CPU pro container ENQUANTO ele processa uma
#      requisição -- quebra o BatchSpanProcessor do Phoenix (thread de
#      background nunca escalonada) e deixaria qualquer alternativa
#      síncrona exposta à MESMA instabilidade do lado que recebe, se
#      Phoenix/LangFuse também rodassem em Cloud Run (cold
#      start/throttling tornando os exports lentos o bastante pra
#      estourar timeout -- foi exatamente isso que derrubou
#      sdr-bot-api quando o Phoenix ainda rodava em Cloud Run, ver
#      app/agents/observability.py). Uma VM normal não tem esse
#      problema em nenhum dos dois lados.
#   2. Storage persiste de verdade entre restarts (disco da VM),
#      diferente do Cloud Run, onde só dava pra fixar
#      --min-instances=1/--max-instances=1 e mesmo assim perder tudo
#      numa reciclagem de instância.
#
# sdr-bot-api e litellm-proxy continuam em Cloud Run -- só o BACKEND de
# observabilidade migrou. A VM fica numa rede privada, sem IP externo,
# alcançável pelo Cloud Run só via Serverless VPC Access (nunca exposta
# na internet pública -- mesma postura de privacidade já adotada pro
# resto do projeto, e mais importante aqui: os traces contêm o
# CONTEÚDO da conversa do lead).

resource "google_compute_network" "observability_vpc" {
  name                    = "observability-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "observability_subnet" {
  name          = "observability-subnet"
  network       = google_compute_network.observability_vpc.id
  region        = var.gcp_region
  ip_cidr_range = "10.10.0.0/24"
}

# Sem IP externo, a VM não tem NENHUM acesso à internet por padrão --
# nem entrada nem SAÍDA. O Cloud NAT resolve só a saída (a VM consegue
# iniciar conexões pra fora, ex: apt/Docker Hub no boot), sem abrir
# NENHUMA porta de entrada -- continua impossível iniciar uma conexão
# DE FORA pra VM sem passar pelo connector/firewall já definidos acima.
# Descoberto rodando de verdade: o startup-script travou ~6min em
# "Network is unreachable" tentando alcançar deb.debian.org/
# download.docker.com sem isso.
resource "google_compute_router" "observability_router" {
  name    = "observability-router"
  region  = var.gcp_region
  network = google_compute_network.observability_vpc.id
}

resource "google_compute_router_nat" "observability_nat" {
  name                               = "observability-nat"
  router                             = google_compute_router.observability_router.name
  region                             = var.gcp_region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# Serverless VPC Access -- a "ponte" que permite Cloud Run (sdr-bot-api,
# litellm-proxy) alcançar o IP INTERNO da VM. Precisa do próprio range
# /28, separado do range da VM (não pode sobrepor).
resource "google_vpc_access_connector" "cloud_run_connector" {
  name          = "cloud-run-connector"
  region        = var.gcp_region
  network       = google_compute_network.observability_vpc.name
  ip_cidr_range = "10.10.1.0/28"
  # A API do GCP passou a exigir isso explícito (sem default implícito
  # mais) -- min_instances=2 é o mínimo permitido pelo Serverless VPC
  # Access; max_instances=3 mantém o footprint pequeno (é só uma ponte
  # de rede pra um projeto de baixo tráfego, não precisa de mais).
  min_instances = 2
  max_instances = 3
  depends_on    = [google_project_service.apis]
}

# Só o tráfego vindo do connector acima chega na VM -- nem a internet
# pública, nem outros ranges internos do projeto.
resource "google_compute_firewall" "allow_cloud_run_to_observability" {
  name          = "allow-cloud-run-to-observability"
  network       = google_compute_network.observability_vpc.name
  source_ranges = [google_vpc_access_connector.cloud_run_connector.ip_cidr_range]

  allow {
    protocol = "tcp"
    ports    = ["3000", "6006"] # langfuse-web, phoenix
  }
}

# Acesso administrativo (gcloud compute ssh --tunnel-through-iap) sem
# expor a porta 22 na internet -- 35.235.240.0/20 é o range fixo e
# documentado do Identity-Aware Proxy, não um IP nosso. Ainda exige que
# a pessoa/identidade tenha roles/iap.tunnelResourceAccessor concedido
# manualmente (fora de escopo daqui -- é sobre QUEM pode acessar, não
# sobre a infra em si).
resource "google_compute_firewall" "allow_iap_ssh" {
  name          = "allow-iap-ssh-to-observability"
  network       = google_compute_network.observability_vpc.name
  source_ranges = ["35.235.240.0/20"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_service_account" "observability_vm_sa" {
  account_id   = "observability-vm"
  display_name = "Observability VM (Phoenix + LangFuse)"
}

# Bucket só pra guardar observability/docker-compose.yml -- a VM baixa
# esse arquivo no boot (gsutil, autenticado via a própria identidade da
# VM, sem chave nenhuma). Terraform reenvia o arquivo sempre que o
# conteúdo muda, então "atualizar a stack" é: editar o compose,
# terraform apply, reiniciar a VM (ou rodar o startup-script nela de
# novo).
resource "google_storage_bucket" "observability_configs" {
  name                        = "${var.gcp_project_id}-observability-configs"
  location                    = var.gcp_region
  uniform_bucket_level_access = true
  force_destroy               = true
  depends_on                  = [google_project_service.apis]
}

resource "google_storage_bucket_object" "observability_compose_file" {
  name   = "docker-compose.yml"
  bucket = google_storage_bucket.observability_configs.name
  source = "${path.module}/../observability/docker-compose.yml"
}

resource "google_storage_bucket_iam_member" "observability_vm_reads_bucket" {
  bucket = google_storage_bucket.observability_configs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.observability_vm_sa.email}"
}

# Segredo ÚNICO com o conteúdo INTEIRO do .env (todas as ~10 variáveis
# LANGFUSE_* que langfuse-secrets/langfuse-secrets no Makefile gera) --
# deliberadamente diferente do padrão de "um secret por chave" usado no
# resto deste projeto (secrets.tf, telegram.tf): aqui são ~10 valores
# que só fazem sentido juntos, como UM arquivo .env, não como
# configuração individualmente referenciável por um --set-secrets do
# Cloud Run. Populado manualmente como os outros (nunca em .tf/state) --
# ver README pro conteúdo exato esperado.
resource "google_secret_manager_secret" "observability_env" {
  secret_id = "observability-vm-env"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "observability_vm_reads_env" {
  secret_id = google_secret_manager_secret.observability_env.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.observability_vm_sa.email}"
}

# Estes DOIS, em contraste, são secrets individuais de propósito --
# litellm-proxy (Cloud Run) precisa referenciá-los via --set-secrets
# pro callback langfuse_otel (ver litellm_proxy/config.yaml), então
# precisam existir como recursos separados, não escondidos dentro do
# blob observability-vm-env. O VALOR tem que ser o MESMO par colado
# dentro do .env acima (LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY) --
# duplicação inevitável entre um blob de .env e secrets individuais do
# Cloud Run, documentada aqui pra não dessincronizar por acidente.
resource "google_secret_manager_secret" "langfuse_public_key" {
  secret_id = "langfuse-public-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "langfuse_secret_key" {
  secret_id = "langfuse-secret-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# litellm-proxy roda sem --service-account (SA default do Compute) --
# mesma razão de secrets.tf/telegram.tf.
resource "google_secret_manager_secret_iam_member" "compute_default_reads_langfuse_public_key" {
  secret_id = google_secret_manager_secret.langfuse_public_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.compute_default_sa}"
}

resource "google_secret_manager_secret_iam_member" "compute_default_reads_langfuse_secret_key" {
  secret_id = google_secret_manager_secret.langfuse_secret_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.compute_default_sa}"
}

# e2-medium (2 vCPU, 4GB) é o ponto de partida -- mesma filosofia de
# "começar barato, subir com evidência de OOM real" já usada pra
# memória dos serviços Cloud Run neste projeto (ver comentários em
# .github/workflows/ci-cd.yml). ClickHouse é o componente mais pesado
# desta stack; se a VM não aguentar, o sintoma vai ser igual ao que já
# vimos lá: procurar "Out of memory" nos logs e subir pra e2-standard-2.
resource "google_compute_instance" "observability_vm" {
  name         = "observability-vm"
  machine_type = var.observability_vm_machine_type
  zone         = "${var.gcp_region}-a"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 50 # GB -- imagens Docker + volumes do Postgres/ClickHouse/MinIO crescem com o tempo
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.observability_subnet.id
    # SEM access_config{} -- de propósito, é isso que garante que a VM
    # não tem IP externo nenhum.
  }

  service_account {
    email  = google_service_account.observability_vm_sa.email
    scopes = ["cloud-platform"]
  }

  # Roda no primeiro boot E em todo boot subsequente (comportamento
  # padrão do GCE) -- o `docker compose up -d` final é idempotente, faz
  # a VM se auto-curar depois de qualquer restart/manutenção do host
  # sem intervenção manual.
  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -euo pipefail

    mkdir -p /opt/observability
    cd /opt/observability

    if ! command -v docker &> /dev/null; then
      apt-get update
      apt-get install -y ca-certificates curl gnupg
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
      chmod a+r /etc/apt/keyrings/docker.asc
      echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi

    if ! command -v gcloud &> /dev/null; then
      apt-get install -y apt-transport-https gnupg
      echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list
      curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
      apt-get update
      apt-get install -y google-cloud-cli
    fi

    gsutil cp "gs://${google_storage_bucket.observability_configs.name}/docker-compose.yml" /opt/observability/docker-compose.yml
    gcloud secrets versions access latest --secret="${google_secret_manager_secret.observability_env.secret_id}" > /opt/observability/.env

    docker compose up -d
  EOT

  depends_on = [google_project_service.apis]
}

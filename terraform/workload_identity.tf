# Setup de WIF pro GitHub Actions -- reproduz de forma idempotente/versionada
# o que README.md > "Configurando deploy na GCP" documenta como setup
# manual. Migrado do GitLab CI (issuer/claims diferentes: GitHub usa
# "repository" em vez de "project_path", e o issuer é
# token.actions.githubusercontent.com) -- ver git log deste arquivo pra a
# versão anterior, caso o GitLab volte a ser usado.

resource "google_iam_workload_identity_pool" "cicd_pool" {
  workload_identity_pool_id = var.wif_pool_id
  display_name              = "CI/CD"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github_provider" {
  workload_identity_pool_id         = google_iam_workload_identity_pool.cicd_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = var.wif_provider_id
  display_name                       = "GitHub Actions OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deploy_sa" {
  account_id   = var.deploy_sa_account_id
  display_name = "GitHub Actions CI/CD deployer"
}

# Permite que a identidade federada do GitHub Actions (restrita ao repo
# exato via attribute_condition acima) impersone a SA de deploy. Usa o
# nome numérico do pool (google_iam_workload_identity_pool.cicd_pool.name,
# formato projects/<numero>/locations/global/workloadIdentityPools/<id>),
# não a string do pool_id -- caso contrário o binding não casa com nada.
resource "google_service_account_iam_member" "deploy_sa_wif_binding" {
  service_account_id = google_service_account.deploy_sa.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.cicd_pool.name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "deploy_sa_run_admin" {
  project = var.gcp_project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deploy_sa.email}"
}

resource "google_project_iam_member" "deploy_sa_ar_writer" {
  project = var.gcp_project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deploy_sa.email}"
}

# Sem isso, `gcloud run deploy --set-secrets=...` falha: o GCP exige que a
# identidade que faz o deploy tenha acesso de leitura ao secret que está
# sendo referenciado, como salvaguarda contra escalar privilégio via uma
# SA de runtime. O script manual anterior no README nunca concedia isso --
# teria quebrado silenciosamente na primeira execução real dos jobs
# deploy_litellm_proxy/deploy_sdr_bot_api/deploy_telegram_service.
resource "google_project_iam_member" "deploy_sa_secret_accessor" {
  project = var.gcp_project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.deploy_sa.email}"
}

# NÃO é iam.serviceAccountUser a nível de projeto: escopado só às duas SAs
# que o deploy de fato impersona -- a de runtime do sdr-bot-api (definida
# em cloud_sql.tf, via --service-account=) e a default do Compute Engine
# (litellm-proxy/telegram-service rodam nela, sem --service-account=
# explícito -- ver secrets.tf > compute_default_reads_*). Um binding
# project-wide deixaria o deployer agir-como QUALQUER SA atual ou futura
# do projeto, sem nenhum ganho correspondente.
resource "google_service_account_iam_member" "deploy_sa_can_actas_runtime_sa" {
  service_account_id = google_service_account.sdr_bot_api_runtime.name
  role                = "roles/iam.serviceAccountUser"
  member              = "serviceAccount:${google_service_account.deploy_sa.email}"
}

resource "google_service_account_iam_member" "deploy_sa_can_actas_compute_default" {
  service_account_id = "projects/${var.gcp_project_id}/serviceAccounts/${local.compute_default_sa}"
  role                = "roles/iam.serviceAccountUser"
  member              = "serviceAccount:${google_service_account.deploy_sa.email}"
}

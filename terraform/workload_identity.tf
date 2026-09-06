# Setup de WIF pro GitLab CI -- reproduz de forma idempotente/versionada o
# que README.md > "Configurando deploy na GCP" documentava como script
# manual (issuer, attribute-mapping e attribute-condition exatos já
# confirmados lá, não inferidos).

resource "google_iam_workload_identity_pool" "gitlab_pool" {
  workload_identity_pool_id = var.wif_pool_id
  display_name              = "GitLab CI"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "gitlab_provider" {
  workload_identity_pool_id         = google_iam_workload_identity_pool.gitlab_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = var.wif_provider_id
  display_name                       = "GitLab.com OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.project_path"
  }
  attribute_condition = "assertion.project_path == '${var.gitlab_project_path}'"

  oidc {
    issuer_uri = "https://gitlab.com"
  }
}

resource "google_service_account" "deploy_sa" {
  account_id   = var.deploy_sa_account_id
  display_name = "GitLab CI/CD deployer"
}

# Permite que a identidade federada do GitLab (restrita ao projeto exato
# via attribute_condition acima) impersone a SA de deploy. Usa o nome
# numérico do pool (google_iam_workload_identity_pool.gitlab_pool.name,
# formato projects/<numero>/locations/global/workloadIdentityPools/<id>),
# não a string do pool_id -- caso contrário o binding não casa com nada.
resource "google_service_account_iam_member" "deploy_sa_wif_binding" {
  service_account_id = google_service_account.deploy_sa.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.gitlab_pool.name}/attribute.repository/${var.gitlab_project_path}"
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

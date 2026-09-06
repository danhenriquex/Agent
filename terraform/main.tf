terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

locals {
  # APIs necessárias para todo o setup: Cloud Run + Artifact Registry pros
  # três serviços, Secret Manager pros 5 secrets, IAM + IAM Credentials pro
  # WIF (impersonação de curta duração), Cloud SQL Admin pra instância
  # Postgres, Resource Manager + Service Usage como dependências transitivas
  # de habilitar as demais, e Compute porque litellm-proxy e
  # telegram-service (sem --service-account no gcloud run deploy) rodam
  # como a Compute Engine default service account -- essa identidade só é
  # garantida existir depois que a API de Compute foi habilitada ao menos
  # uma vez no projeto.
  apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sqladmin.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "compute.googleapis.com",
  ]

  compute_default_sa = "${var.gcp_project_number}-compute@developer.gserviceaccount.com"
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)
  project  = var.gcp_project_id
  service  = each.value

  # Nunca desabilitar APIs do projeto num `terraform destroy` -- isso afeta
  # qualquer outra coisa que dependa delas, não só os recursos deste stack.
  disable_on_destroy = false
}

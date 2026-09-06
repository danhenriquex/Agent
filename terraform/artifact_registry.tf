resource "google_artifact_registry_repository" "sdr_bot_repo" {
  repository_id = var.ar_repository
  location       = var.ar_region
  format         = "DOCKER"
  description    = "Imagens Docker de sdr-bot-api, litellm-proxy e telegram-service."

  depends_on = [google_project_service.apis]
}

# litellm_proxy/Dockerfile
#
# O docker-compose local (uso em dev) monta config.yaml como volume.
# O Cloud Run não suporta volumes montados a partir do host, então essa
# imagem embute o config.yaml diretamente — qualquer mudança de rota-
# mento de modelo exige rebuild + redeploy desta imagem, não só editar
# um arquivo no servidor. Isso é aceitável aqui porque o roteamento de
# modelo é configuração versionada em código, não segredo de runtime.

FROM ghcr.io/berriai/litellm:main-latest

COPY config.yaml /app/config.yaml

ENV PORT=4000
EXPOSE 4000

CMD ["--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]

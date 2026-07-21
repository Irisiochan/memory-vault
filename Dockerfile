FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMORY_VAULT_PATH=/vault

WORKDIR /opt/memory-vault

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY _meta ./_meta
COPY template ./template
COPY docker-entrypoint.sh ./docker-entrypoint.sh

RUN chmod +x ./docker-entrypoint.sh

EXPOSE 8900
VOLUME ["/vault"]
ENTRYPOINT ["./docker-entrypoint.sh"]

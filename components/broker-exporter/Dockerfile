# Imagen del exporter de carga del broker (sidecar de mosquitto).
#
# Build para linux/amd64 (arquitectura de las VMs Multipass del clúster):
#   docker build --platform linux/amd64 -t hyrox/broker-exporter:0.1.0 components/broker-exporter
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 appuser
USER appuser

ENTRYPOINT ["hyrox-broker-exporter"]

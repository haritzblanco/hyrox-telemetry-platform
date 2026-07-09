# Imagen del microservicio de procesado (MQTT -> InfluxDB).
#
# Build (dentro de la VM k3s, ver infra/manifests/processor/README.md):
#   docker build -t hyrox/processor:0.1.0 components/processor
#
# Se construye para linux/amd64 (arquitectura de las VMs Multipass del clúster).
FROM python:3.12-slim AS base

# No escribir .pyc y volcar logs sin buffer (para ver la salida en `kubectl logs`).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Primero los metadatos del paquete: aprovecha la caché de capas si el código
# cambia pero las dependencias no.
COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

# Usuario sin privilegios (infraestructura inmutable, mínimo de permisos).
RUN useradd --create-home --uid 10001 appuser
USER appuser

# El entrypoint es el script de consola declarado en pyproject.toml.
# Los argumentos de conexión se inyectan desde el Deployment.
ENTRYPOINT ["hyrox-processor"]

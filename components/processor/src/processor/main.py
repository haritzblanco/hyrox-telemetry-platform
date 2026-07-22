"""Punto de entrada del procesador."""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import uuid
from datetime import datetime, timezone

from processor.consumer import MqttConsumer
from processor.metrics import Metrics
from processor.writer import InfluxWriter


logger = logging.getLogger(__name__)


def _transport_latency_ms(reading: dict) -> float | None:
    """Latencia de transporte en ms (emisión en el simulador a consumo aquí),
    o None si la lectura no trae timestamp válido. Cruza relojes de máquinas
    distintas: exige que estén sincronizadas por NTP."""
    raw = reading.get("timestamp")
    if not raw:
        return None
    try:
        emitted = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if emitted.tzinfo is None:
        emitted = emitted.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - emitted).total_seconds() * 1000.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Procesador: consume lecturas de MQTT y las persiste en InfluxDB",
    )
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--broker-username", default=None,
                        help="Usuario MQTT. El broker exige autenticación en el "
                             "listener externo; sin él la conexión se rechaza.")
    parser.add_argument("--broker-password", default=None, help="Contraseña MQTT.")
    parser.add_argument("--broker-ca", default=None,
                        help="Ruta al certificado de la CA para validar el broker "
                             "por TLS. Si se indica, la conexión va cifrada.")
    parser.add_argument("--topic", default="hyrox/+/biometrics")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument(
        "--client-id", default=None,
        help="ID de cliente MQTT. Por defecto se genera uno único por instancia "
             "(hostname + sufijo aleatorio); dos clientes con el mismo id se "
             "expulsan mutuamente del broker.",
    )
    parser.add_argument(
        "--share-group", default=None,
        help="Grupo de suscripción compartida MQTT ($share). Si se indica, varias "
             "réplicas del procesador reparten la carga sin duplicar escrituras.",
    )
    parser.add_argument("--influx-url", default="http://localhost:8086")
    parser.add_argument("--influx-org", default="hyrox")
    parser.add_argument("--influx-bucket", default="telemetry")
    parser.add_argument("--influx-token", required=True)
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--metrics-interval", type=float, default=0.0,
        help="Segundos entre volcados de métricas (throughput/latencia) por stdout "
             "en JSON, para la evaluación experimental. 0 (def) = desactivado.",
    )
    parser.add_argument(
        "--metrics-tag", default="",
        help="Etiqueta libre que se incluye en cada línea de métricas (p.ej. el "
             "identificador de la corrida: réplicas y carga).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # client_id único por instancia (en k8s el hostname es el pod): dos
    # clientes con el mismo id se expulsan mutuamente del broker.
    client_id = args.client_id or f"hyrox-processor-{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    logger.info("client_id MQTT: %s", client_id)

    count = 0
    metrics = (
        Metrics(interval_s=args.metrics_interval, tag=args.metrics_tag, client_id=client_id)
        if args.metrics_interval > 0 else None
    )

    with InfluxWriter(
        url=args.influx_url, token=args.influx_token,
        org=args.influx_org, bucket=args.influx_bucket,
        metrics=metrics,
    ) as writer:

        def handle(reading: dict) -> None:
            nonlocal count
            if metrics is not None:
                latency = _transport_latency_ms(reading)
                if latency is not None:
                    metrics.record_consume(latency)
            writer.write(reading)
            count += 1
            if count % 50 == 0:
                logger.info("Lecturas persistidas: %d", count)

        consumer = MqttConsumer(
            host=args.broker_host, port=args.broker_port,
            topic=args.topic, on_reading=handle, qos=args.qos,
            client_id=client_id, share_group=args.share_group,
            username=args.broker_username, password=args.broker_password,
            ca_certs=args.broker_ca,
        )
        # SIGTERM sigue el camino de Ctrl-C para vaciar los lotes pendientes al salir.
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

        logger.info("Procesador iniciado. Esperando mensajes en %s", args.topic)
        if metrics is not None:
            metrics.start()
        with consumer:
            try:
                consumer.loop_forever()
            except KeyboardInterrupt:
                logger.info("Procesador detenido. Total persistido: %d", count)
            finally:
                if metrics is not None:
                    metrics.stop()
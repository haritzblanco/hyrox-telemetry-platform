"""Escritor de lecturas en InfluxDB."""

from __future__ import annotations

import logging
from typing import Self

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions

from processor.metrics import Metrics, PersistenceTracker


logger = logging.getLogger(__name__)


def _count_points(data) -> int:
    """Número de puntos (líneas de line-protocol) en el payload de un lote."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "ignore")
    return sum(1 for line in data.split("\n") if line.strip())


class InfluxWriter:
    """Escribe lecturas biométricas en un bucket de InfluxDB.

    Cada lectura es un punto del measurement biometrics (athlete_id, phase y
    phase_type como tags; las métricas como fields). La escritura es por lotes
    en segundo plano: write() solo encola y vuelve, para que el callback MQTT
    no se bloquee en HTTP (la escritura síncrona saturaba el consumidor a
    ~600 msg/s y mosquitto descartaba lecturas).
    """

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        metrics: Metrics | None = None,
    ) -> None:
        self.org = org
        self.bucket = bucket
        self.metrics = metrics
        # Solo se rastrea la persistencia si hay métricas activas.
        self._tracker = PersistenceTracker() if metrics is not None else None
        self._client = InfluxDBClient(url=url, token=token, org=org)
        self._write_api = self._client.write_api(
            write_options=WriteOptions(
                batch_size=500,
                flush_interval=500,       # ms: vacía el lote como muy tarde cada 0,5 s
                jitter_interval=0,
                retry_interval=2000,
                max_retries=5,
                max_retry_delay=15000,
                exponential_base=2,
            ),
            success_callback=self._on_success,
            error_callback=self._on_error,
            retry_callback=self._on_retry,
        )

    def _on_success(self, conf, data) -> None:
        if self._tracker is None or self.metrics is None:
            return
        n = _count_points(data)
        latencies = self._tracker.on_ack(n)
        if latencies:
            self.metrics.record_persist(latencies)

    def _on_error(self, conf, data, exception) -> None:
        n = _count_points(data)
        logger.error("Fallo escribiendo lote de %d puntos: %s", n, exception)
        if self._tracker is not None and self.metrics is not None:
            self._tracker.on_drop(n)
            self.metrics.record_errors(n)

    def _on_retry(self, conf, data, exception) -> None:
        logger.warning("Reintentando lote de %d puntos: %s", _count_points(data), exception)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # close() vacía los lotes pendientes antes de cerrar
        try:
            self._write_api.close()
        finally:
            self._client.close()

    def write(self, reading: dict) -> None:
        """Convierte una lectura (dict) en un punto y lo escribe."""
        point = (
            Point("biometrics")
            .tag("athlete_id", reading["athlete_id"])
            .tag("session_id", reading.get("session_id", "unknown"))
            .tag("phase", reading.get("phase", "unknown"))
            .tag("phase_type", reading.get("phase_type", "unknown"))
            .field("heart_rate", int(reading["heart_rate"]))
            .field("cadence", int(reading["cadence"]))
            .field("power", int(reading["power"]))
            .field("speed", float(reading["speed"]))
            .field("distance", float(reading["distance"]))
            .field("elapsed_seconds", int(reading["elapsed_seconds"]))
            .time(reading["timestamp"])
        )
        if self._tracker is not None:
            self._tracker.on_enqueue()
        self._write_api.write(bucket=self.bucket, org=self.org, record=point)
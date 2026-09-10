"""Escritor de lecturas en InfluxDB, con el lote gestionado aquí."""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Self

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

from processor.metrics import Metrics


logger = logging.getLogger(__name__)

BATCH_SIZE = 500
FLUSH_INTERVAL_S = 0.5
QUEUE_SIZE = 50_000
MAX_RETRIES = 5
RETRY_INTERVAL_S = 2.0
MAX_RETRY_DELAY_S = 15.0


def _tag(value: str) -> str:
    """Escapa un valor de tag para line protocol (coma, espacio, igual)."""
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def _to_line(reading: dict) -> str:
    """Serializa una lectura a una línea de line protocol.

    Construcción directa en vez del objeto Point del cliente: hace lo mismo
    en ~3 µs frente a ~29 µs (medido), y este código corre en el hilo de
    callbacks de MQTT, donde cada microsegundo limita el caudal por réplica.
    """
    ts = datetime.fromisoformat(reading["timestamp"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ns = int(ts.timestamp() * 1_000_000_000)
    return (
        f'biometrics,athlete_id={_tag(reading["athlete_id"])}'
        f',session_id={_tag(reading.get("session_id", "unknown"))}'
        f',phase={_tag(reading.get("phase", "unknown"))}'
        f',phase_type={_tag(reading.get("phase_type", "unknown"))}'
        f' heart_rate={int(reading["heart_rate"])}i'
        f',cadence={int(reading["cadence"])}i'
        f',power={int(reading["power"])}i'
        f',speed={float(reading["speed"])}'
        f',distance={float(reading["distance"])}'
        f',elapsed_seconds={int(reading["elapsed_seconds"])}i'
        f' {ns}'
    )


class InfluxWriter:
    """Escribe lecturas biométricas en un bucket de InfluxDB.

    Cada lectura es un punto del measurement biometrics (athlete_id, phase y
    phase_type como tags; las métricas como fields). write() solo serializa y
    encola, para que el callback MQTT no se bloquee en HTTP (la escritura
    síncrona en línea saturaba el consumidor a ~600 msg/s y mosquitto
    descartaba lecturas). Un hilo propio agrupa la cola en lotes y los escribe.

    El lote se gestiona aquí y no en el cliente de InfluxDB porque su modo de
    escritura por lotes descarta puntos en silencio cuando el productor corre en
    otro hilo: el operador de ventanas sustituye la ventana en curso desde el
    hilo del temporizador de vaciado sin sincronizarse con el del productor, y
    los puntos que caen en medio se pierden sin excepción ni callback de error
    (hasta un 9 por ciento con la CPU disputada; ver
    experiments/results/20260910_101652_causa_raiz/validation.md). Con la cola
    propia cada punto viaja con su marca de encolado y el lote se escribe en
    modo síncrono, así que todo punto acaba confirmado, reintentado o contado
    como error.
    """

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        metrics: Metrics | None = None,
        batch_size: int = BATCH_SIZE,
        flush_interval_s: float = FLUSH_INTERVAL_S,
        queue_size: int = QUEUE_SIZE,
    ) -> None:
        self.org = org
        self.bucket = bucket
        self.metrics = metrics
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._client = InfluxDBClient(url=url, token=token, org=org)
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)

        # (marca de encolado, línea). La marca viaja con el punto: la latencia
        # de persistencia sale de restar en el momento de confirmar el lote, sin
        # emparejamientos que puedan desalinearse.
        self._queue: queue.Queue[tuple[float, str]] = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._rejected = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="influx-writer", daemon=True)
        self._thread.start()

    def write(self, reading: dict) -> None:
        """Serializa la lectura y la encola para el hilo de escritura."""
        item = (time.monotonic(), _to_line(reading))
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # Cola llena: InfluxDB no sigue el ritmo de entrada. Se descarta la
            # lectura, pero contada y registrada, nunca en silencio.
            self._rejected += 1
            if self._rejected == 1 or self._rejected % 1000 == 0:
                logger.error(
                    "Cola de escritura llena (%d puntos): %d lecturas descartadas",
                    self._queue.maxsize, self._rejected,
                )
            if self.metrics is not None:
                self.metrics.record_errors(1)

    def pending(self) -> int:
        """Puntos encolados o en vuelo, todavía sin confirmar.

        En régimen sube y baja con cada lote y vuelve a rozar el cero. Un suelo
        estable por encima de cero es una cola de escritura real: InfluxDB va
        más lento que la entrada. Ese retraso se suma a la latencia de
        persistencia medida, que es lo correcto, porque el punto efectivamente
        tarda eso en quedar guardado.
        """
        with self._lock:
            return self._queue.qsize() + self._in_flight

    def _run(self) -> None:
        """Hilo de escritura: agrupa la cola en lotes y los envía."""
        while True:
            batch = self._collect()
            if batch:
                self._send(batch)
            elif self._stop.is_set():
                return

    def _collect(self) -> list[tuple[float, str]]:
        """Espera al primer punto y sigue acumulando hasta llenar el lote.

        El lote se cierra por tamaño o al agotarse el intervalo de vaciado, que
        acota la espera del primer punto de un lote corto. Al cerrar solo se
        vacía lo que ya haya, para no retrasar la parada.
        """
        batch: list[tuple[float, str]] = []
        if self._stop.is_set():
            # Vaciado final: agrupa lo que ya haya sin esperar a nada, pero en
            # lotes llenos, no punto a punto.
            while len(batch) < self._batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
        else:
            try:
                batch.append(self._queue.get(timeout=self._flush_interval_s))
            except queue.Empty:
                return batch
            deadline = time.monotonic() + self._flush_interval_s
            while len(batch) < self._batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(self._queue.get(timeout=remaining))
                except queue.Empty:
                    break
        with self._lock:
            self._in_flight = len(batch)
        return batch

    def _send(self, batch: list[tuple[float, str]]) -> None:
        """Escribe el lote, reintentando con espera creciente, y mide."""
        body = "\n".join(line for _, line in batch)
        # Al cerrar no se insiste: un reintento largo alargaría la parada del
        # pod más allá del periodo de gracia y acabaría en SIGKILL.
        attempts = 1 if self._stop.is_set() else MAX_RETRIES
        for attempt in range(1, attempts + 1):
            try:
                self._write_api.write(bucket=self.bucket, org=self.org, record=body)
            except Exception as exc:
                if attempt == attempts:
                    logger.error(
                        "Lote de %d puntos descartado tras %d intento(s): %s",
                        len(batch), attempt, exc,
                    )
                    self._done(batch, ok=False)
                    return
                delay = min(RETRY_INTERVAL_S * 2 ** (attempt - 1), MAX_RETRY_DELAY_S)
                logger.warning(
                    "Reintentando lote de %d puntos en %.0f s: %s", len(batch), delay, exc,
                )
                self._stop.wait(delay)
            else:
                self._done(batch, ok=True)
                return

    def _done(self, batch: list[tuple[float, str]], ok: bool) -> None:
        with self._lock:
            self._in_flight = 0
        if self.metrics is None:
            return
        if ok:
            now = time.monotonic()
            self.metrics.record_persist([(now - t) * 1000.0 for t, _ in batch])
        else:
            self.metrics.record_errors(len(batch))
        self.metrics.record_pending(self.pending())

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # El hilo vacía lo que quede en la cola antes de terminar.
        self._stop.set()
        try:
            self._thread.join(timeout=30.0)
            if self._thread.is_alive():
                logger.error("El hilo de escritura no terminó a tiempo; se pierden los pendientes")
        finally:
            self._client.close()

"""Métricas para la evaluación experimental (--metrics-interval > 0).

Un hilo emite por stdout una línea JSON por ventana con throughput,
percentiles de latencia y errores, que el orquestador recoge por kubectl
logs. Con intervalo 0 (el defecto) no arranca nada.

Dos latencias con relojes distintos: la de transporte (emisión a consumo)
cruza máquinas y exige NTP; la de persistencia (encolado a confirmación de
InfluxDB) se mide con time.monotonic() y no depende de relojes externos.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Percentil `q` (0..1) por interpolación lineal sobre una lista ordenada."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _summary(values: list[float]) -> dict | None:
    """Resumen estadístico (n, media, p50/p95/p99, máx) de una lista de latencias."""
    if not values:
        return None
    s = sorted(values)
    return {
        "n": len(s),
        "mean": round(sum(s) / len(s), 2),
        "p50": round(_percentile(s, 0.50), 2),
        "p95": round(_percentile(s, 0.95), 2),
        "p99": round(_percentile(s, 0.99), 2),
        "max": round(s[-1], 2),
    }


class Metrics:
    """Acumula latencias y throughput y los emite por ventana en JSON.

    Thread-safe: los record_* llegan desde los callbacks de MQTT e InfluxDB,
    en hilos distintos del que vuelca las ventanas.
    """

    def __init__(
        self,
        interval_s: float,
        tag: str = "",
        client_id: str = "",
        emit=None,
    ) -> None:
        self.interval_s = interval_s
        self.tag = tag
        self.client_id = client_id
        self._emit = emit or _stdout_emit
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.total_consumed = 0
        self.total_acked = 0
        self.total_errors = 0
        self._reset_window()

    def _reset_window(self) -> None:
        self._consume_lat: list[float] = []
        self._persist_lat: list[float] = []
        self._win_consumed = 0
        self._win_acked = 0
        self._win_errors = 0
        self._win_start = time.monotonic()

    def record_consume(self, latency_ms: float) -> None:
        with self._lock:
            self._consume_lat.append(latency_ms)
            self._win_consumed += 1
            self.total_consumed += 1

    def record_persist(self, latencies_ms: list[float]) -> None:
        with self._lock:
            self._persist_lat.extend(latencies_ms)
            self._win_acked += len(latencies_ms)
            self.total_acked += len(latencies_ms)

    def record_errors(self, n: int) -> None:
        with self._lock:
            self._win_errors += n
            self.total_errors += n

    def start(self) -> None:
        if self.interval_s <= 0:
            return
        self._thread = threading.Thread(target=self._run, name="metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=self.interval_s + 1.0)
        self._flush(final=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._flush()

    def _flush(self, final: bool = False) -> None:
        with self._lock:
            dt = max(1e-6, time.monotonic() - self._win_start)
            record = {
                "kind": "metrics",
                "tag": self.tag,
                "client_id": self.client_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "window_s": round(dt, 3),
                "consumed": self._win_consumed,
                "acked": self._win_acked,
                "errors": self._win_errors,
                "thr_consumed_s": round(self._win_consumed / dt, 1),
                "thr_acked_s": round(self._win_acked / dt, 1),
                "lat_transport_ms": _summary(self._consume_lat),
                "lat_persist_ms": _summary(self._persist_lat),
                "total_consumed": self.total_consumed,
                "total_acked": self.total_acked,
                "total_errors": self.total_errors,
            }
            if final:
                record["final"] = True
            self._reset_window()
        self._emit(json.dumps(record))


def _stdout_emit(line: str) -> None:
    # Con flush, para que kubectl logs la vea al momento.
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


class MetricsFanout:
    """Reparte cada medida entre varios destinos.

    Las ventanas JSON y el endpoint de Prometheus consumen los mismos hechos,
    así que quien mide llama una sola vez y no necesita saber cuántos escuchan.
    """

    def __init__(self, sinks: list) -> None:
        self._sinks = sinks

    def record_consume(self, latency_ms: float) -> None:
        for sink in self._sinks:
            sink.record_consume(latency_ms)

    def record_persist(self, latencies_ms: list[float]) -> None:
        for sink in self._sinks:
            sink.record_persist(latencies_ms)

    def record_errors(self, n: int) -> None:
        for sink in self._sinks:
            sink.record_errors(n)


class PersistenceTracker:
    """Empareja cada punto encolado con la confirmación de su lote.

    Los puntos se encolan en orden y los lotes se confirman en ese orden, así
    que basta una cola FIFO de marcas de tiempo: al confirmarse un lote de n
    puntos, las n más antiguas dan la latencia de persistencia.
    """

    def __init__(self) -> None:
        self._pending: deque[float] = deque()
        self._lock = threading.Lock()

    def on_enqueue(self) -> None:
        with self._lock:
            self._pending.append(time.monotonic())

    def on_ack(self, n: int) -> list[float]:
        """Latencias (ms) de los n puntos más antiguos confirmados."""
        now = time.monotonic()
        out: list[float] = []
        with self._lock:
            for _ in range(min(n, len(self._pending))):
                out.append((now - self._pending.popleft()) * 1000.0)
        return out

    def on_drop(self, n: int) -> None:
        """Descarta n marcas (lote fallido) para no desalinear la cola."""
        with self._lock:
            for _ in range(min(n, len(self._pending))):
                self._pending.popleft()

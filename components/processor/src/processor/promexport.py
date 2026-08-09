"""Exposición de las métricas del procesador en formato Prometheus.

El módulo metrics.py vuelca ventanas en JSON por stdout, que es lo que recoge
el arnés de la evaluación experimental. Esto es lo complementario: un endpoint
HTTP que Prometheus raspa, con los mismos hechos (latencia de transporte y de
persistencia, lecturas consumidas y persistidas, errores de escritura)
acumulados desde el arranque, para que los cuadros de mando muestren en vivo lo
que el arnés mide fuera de línea.

Ambas vías se alimentan de los mismos puntos de medida, así que no hay dos
instrumentaciones que puedan discrepar. La diferencia está en el destinatario:
el JSON sirve a una corrida acotada, este endpoint al seguimiento continuo.

Sin dependencias externas, igual que el exporter del broker: el formato de
texto de Prometheus es lo bastante simple como para no traer una biblioteca.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Los límites cubren desde la decena de milisegundos hasta la decena de
# segundos. El de 2 s no es casual: es el umbral del requisito de latencia
# (RNF-2), de modo que el cuadro de mando puede dibujar la proporción de
# lecturas que lo respeta sin interpolar entre límites.
LATENCY_BUCKETS_S = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0)


class Histogram:
    """Histograma acumulativo con los límites fijados al construirlo."""

    def __init__(self, buckets: tuple[float, ...] = LATENCY_BUCKETS_S) -> None:
        self.buckets = buckets
        self._counts = [0] * len(buckets)
        self._inf = 0
        self._sum = 0.0
        self._lock = threading.Lock()

    def observe(self, value_s: float) -> None:
        with self._lock:
            self._sum += value_s
            self._inf += 1
            for i, limit in enumerate(self.buckets):
                if value_s <= limit:
                    self._counts[i] += 1

    def snapshot(self) -> tuple[list[int], int, float]:
        with self._lock:
            return list(self._counts), self._inf, self._sum

    def render(self, name: str, help_text: str) -> str:
        counts, total, total_sum = self.snapshot()
        lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
        for limit, count in zip(self.buckets, counts):
            lines.append(f'{name}_bucket{{le="{limit}"}} {count}')
        lines.append(f'{name}_bucket{{le="+Inf"}} {total}')
        lines.append(f"{name}_sum {total_sum:.6f}")
        lines.append(f"{name}_count {total}")
        return "\n".join(lines) + "\n"


class Counter:
    """Contador monótono."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, n: int = 1) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def render(self, name: str, help_text: str) -> str:
        return (
            f"# HELP {name} {help_text}\n"
            f"# TYPE {name} counter\n"
            f"{name} {self.value}\n"
        )


class PrometheusExporter:
    """Acumula las métricas del procesador y las sirve por HTTP.

    Ofrece la misma interfaz de registro que Metrics (record_consume,
    record_persist y record_errors), de modo que ambos destinos se pueden
    alimentar a la vez sin que quien mide sepa cuántos hay escuchando.
    """

    def __init__(self, replica: str = "") -> None:
        self.replica = replica
        self.transport = Histogram()
        self.persist = Histogram()
        self.consumed = Counter()
        self.persisted = Counter()
        self.errors = Counter()
        self._server: ThreadingHTTPServer | None = None

    def record_consume(self, latency_ms: float) -> None:
        self.consumed.inc()
        self.transport.observe(latency_ms / 1000.0)

    def record_persist(self, latencies_ms: list[float]) -> None:
        self.persisted.inc(len(latencies_ms))
        for value in latencies_ms:
            self.persist.observe(value / 1000.0)

    def record_errors(self, n: int) -> None:
        self.errors.inc(n)

    def render(self) -> str:
        return "".join([
            self.transport.render(
                "hyrox_processor_transport_latency_seconds",
                "Latencia desde la emision de la lectura hasta su consumo.",
            ),
            self.persist.render(
                "hyrox_processor_persist_latency_seconds",
                "Latencia desde el encolado hasta la confirmacion de InfluxDB.",
            ),
            self.consumed.render(
                "hyrox_processor_consumed_total",
                "Lecturas consumidas del broker desde el arranque.",
            ),
            self.persisted.render(
                "hyrox_processor_persisted_total",
                "Lecturas confirmadas por InfluxDB desde el arranque.",
            ),
            self.errors.render(
                "hyrox_processor_write_errors_total",
                "Puntos descartados por fallo de escritura.",
            ),
        ])

    def serve(self, port: int) -> None:
        """Arranca el servidor HTTP en un hilo de fondo."""
        exporter = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/metrics", "/healthz"):
                    self.send_error(404)
                    return
                body = (exporter.render() if self.path == "/metrics" else "ok\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                # Prometheus raspa cada pocos segundos: un log por peticion solo
                # ensucia los del procesado.
                pass

        self._server = ThreadingHTTPServer(("", port), _Handler)
        threading.Thread(
            target=self._server.serve_forever, name="prometheus", daemon=True
        ).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

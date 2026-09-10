"""Expone por HTTP el caudal de publicaciones del broker.

Este proceso se suscribe al contador acumulado
$SYS/broker/publish/messages/received y sirve como JSON su derivada, en
mensajes por segundo. Es la señal con la que KEDA escala el procesador: mide la
carga ofrecida a la entrada de la plataforma, no el consumo aguas abajo, así que
reacciona antes de que el procesador se sature.

Se deriva el contador en vez de leer la media móvil de un minuto que mosquitto
publica en $SYS/broker/load/publish/received/1min, que era lo que se hacía antes.
Esa media llega tarde por construcción: en la corrida de arranque en frío del 10
de septiembre marcaba 135 msg/s a los 23 segundos cuando ya se ofrecían 800, de
modo que el disparador de caudal de KEDA no llegaba a activarse nunca y el
escalado lo acababa llevando el de CPU, un escalón por ciclo. El contador se
publica cada sys_interval (5 s en el chart), así que la derivada reacciona en
ese plazo. Ver results/20260910_131609_peak_target150/validation.md.
"""

import argparse
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

COUNT_TOPIC = "$SYS/broker/publish/messages/received"

log = logging.getLogger("broker-exporter")


class RateMeter:
    """Deriva el contador acumulado de mensajes recibidos por el broker.

    Guarda la última lectura y devuelve mensajes por segundo entre dos
    lecturas consecutivas. La primera lectura no da caudal, porque no hay con
    qué compararla, y un contador que retrocede significa que el broker se ha
    reiniciado: en ambos casos devuelve None y el llamante deja el valor
    anterior en pie hasta la siguiente lectura.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count: int | None = None
        self._ts: float = 0.0

    def update(self, count: int, now: float) -> float | None:
        with self._lock:
            anterior, antes = self._count, self._ts
            self._count, self._ts = count, now
        if anterior is None or count < anterior or now <= antes:
            return None
        return (count - anterior) / (now - antes)


class LoadGauge:
    """Último valor recibido, con caducidad.

    Si el broker deja de publicar (caída o desconexión), un valor viejo
    mantendría al autoescalador arriba sin motivo: pasado stale_after_s
    sin actualizaciones se devuelve 0.
    """

    def __init__(self, stale_after_s: float = 60.0):
        self._lock = threading.Lock()
        self._value = 0.0
        self._updated: float | None = None
        self._stale_after = stale_after_s

    def set(self, value: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._value = value
            self._updated = now

    def get(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._updated is None or now - self._updated > self._stale_after:
                return 0.0
            return self._value


def render_metrics(value: float) -> str:
    """Formatea el caudal como una métrica de Prometheus (formato de texto)."""
    return (
        "# HELP hyrox_broker_messages_per_second Mensajes PUBLISH recibidos por "
        "el broker por segundo, derivados de su contador acumulado.\n"
        "# TYPE hyrox_broker_messages_per_second gauge\n"
        f"hyrox_broker_messages_per_second {value:.3f}\n"
    )


class _Handler(BaseHTTPRequestHandler):
    gauge: LoadGauge

    def do_GET(self):
        if self.path == "/metrics":
            body = render_metrics(self.gauge.get()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path not in ("/", "/load", "/healthz"):
            self.send_error(404)
            return
        body = json.dumps({"messages_per_second": round(self.gauge.get(), 3)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # KEDA consulta cada pocos segundos; el log por petición solo hace ruido.
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--http-port", type=int, default=9090)
    parser.add_argument("--stale-after", type=float, default=30.0,
                        help="segundos sin datos del broker tras los que se reporta 0. "
                             "Tiene que dar margen a sys_interval del broker, que es "
                             "cada cuánto se publica el contador")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    gauge = LoadGauge(args.stale_after)
    meter = RateMeter()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="broker-exporter")

    def on_connect(cli, userdata, flags, reason_code, properties):
        # La suscripción va aquí para que se renueve en cada reconexión.
        cli.subscribe(COUNT_TOPIC)
        log.info("conectado al broker, suscrito a %s", COUNT_TOPIC)

    def on_message(cli, userdata, msg):
        try:
            caudal = meter.update(int(msg.payload), time.monotonic())
        except ValueError:
            log.warning("payload no numérico en %s: %r", msg.topic, msg.payload)
            return
        if caudal is not None:
            gauge.set(caudal)

    client.on_connect = on_connect
    client.on_message = on_message
    # connect_async mas el hilo del loop reintentan solos: en el arranque del
    # pod este sidecar puede adelantarse al broker.
    client.connect_async(args.broker_host, args.broker_port)
    client.loop_start()

    _Handler.gauge = gauge
    server = ThreadingHTTPServer(("", args.http_port), _Handler)
    log.info("sirviendo la carga del broker en el puerto %d", args.http_port)
    server.serve_forever()


if __name__ == "__main__":
    main()

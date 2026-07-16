"""Expone por HTTP el caudal de publicaciones del broker.

Mosquitto publica en $SYS/broker/load/publish/received/1min una media
móvil del número de mensajes PUBLISH recibidos por minuto. Este proceso
se suscribe a ese topic y sirve el último valor como JSON en mensajes
por segundo, la señal con la que KEDA escala el procesador: mide la
carga ofrecida a la entrada de la plataforma, no el consumo aguas
abajo, así que reacciona antes de que el procesador se sature.
"""

import argparse
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

LOAD_TOPIC = "$SYS/broker/load/publish/received/1min"

log = logging.getLogger("broker-exporter")


def per_second(payload: bytes) -> float:
    """Convierte el valor del topic (mensajes por minuto) a mensajes por segundo."""
    return float(payload) / 60.0


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


class _Handler(BaseHTTPRequestHandler):
    gauge: LoadGauge

    def do_GET(self):
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
    parser.add_argument("--stale-after", type=float, default=60.0,
                        help="segundos sin datos del broker tras los que se reporta 0")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    gauge = LoadGauge(args.stale_after)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="broker-exporter")

    def on_connect(cli, userdata, flags, reason_code, properties):
        # La suscripción va aquí para que se renueve en cada reconexión.
        cli.subscribe(LOAD_TOPIC)
        log.info("conectado al broker, suscrito a %s", LOAD_TOPIC)

    def on_message(cli, userdata, msg):
        try:
            gauge.set(per_second(msg.payload))
        except ValueError:
            log.warning("payload no numérico en %s: %r", msg.topic, msg.payload)

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

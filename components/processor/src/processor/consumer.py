"""Consumidor MQTT que reenvía las lecturas al escritor de InfluxDB."""

from __future__ import annotations

import json
import logging
import time
from typing import Callable, Self

import paho.mqtt.client as mqtt


logger = logging.getLogger(__name__)

# Espera máxima acumulada para la primera conexión y tope del retardo entre
# intentos, en segundos.
CONNECT_TIMEOUT_S = 60.0
CONNECT_BACKOFF_MAX_S = 8.0


class MqttConsumer:
    """Se suscribe a un topic MQTT y entrega cada mensaje a un callback."""

    def __init__(
        self,
        host: str,
        port: int,
        topic: str,
        on_reading: Callable[[dict], None],
        client_id: str = "hyrox-processor",
        qos: int = 1,
        share_group: str | None = None,
        username: str | None = None,
        password: str | None = None,
        ca_certs: str | None = None,
        connect_timeout: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self.host = host
        self.port = port
        self.topic = topic
        self.qos = qos
        self.connect_timeout = connect_timeout
        # Con grupo compartido el broker reparte cada mensaje entre los
        # suscriptores en vez de entregarlo a todos: las réplicas escalan sin
        # duplicar escrituras. Cada una necesita un client_id distinto.
        self.share_group = share_group
        self.subscribe_topic = f"$share/{share_group}/{topic}" if share_group else topic
        self._on_reading = on_reading
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        if username is not None:
            self._client.username_pw_set(username, password)
        # Con ca_certs el cliente valida el certificado del broker contra esa CA
        # y cifra el canal; el broker exige TLS en el listener externo 8883.
        if ca_certs is not None:
            self._client.tls_set(ca_certs=ca_certs)
        # Ante una caída con la conexión ya establecida, loop_forever reintenta
        # por su cuenta; el retardo se acota para no castigar al broker cuando
        # es él quien se está reiniciando.
        self._client.reconnect_delay_set(min_delay=1, max_delay=int(CONNECT_BACKOFF_MAX_S))
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def __enter__(self) -> Self:
        logger.info("Conectando al broker MQTT en %s:%d", self.host, self.port)
        self._connect_con_reintentos()
        return self

    def _connect_con_reintentos(self) -> None:
        """Insiste en la primera conexión mientras el broker no responda.

        Al arrancar el clúster el procesador suele estar listo antes que el
        broker, y sin reintento el proceso moría en el primer rechazo y quedaba
        a expensas de que Kubernetes lo reiniciara, con un par de reinicios
        contabilizados en cada arranque. La espera se acota para que un error de
        configuración (un host equivocado, por ejemplo) siga saliendo a la luz
        como un pod que no arranca en lugar de como uno que espera para siempre.
        """
        limite = time.monotonic() + self.connect_timeout
        espera = 1.0
        intento = 0
        while True:
            intento += 1
            try:
                self._client.connect(self.host, self.port)
                return
            except OSError as exc:
                if time.monotonic() + espera >= limite:
                    logger.error(
                        "El broker sigue sin responder tras %d intentos en %.0f s: %s",
                        intento, self.connect_timeout, exc,
                    )
                    raise
                logger.warning(
                    "El broker no responde (%s). Reintento en %.0f s.", exc, espera,
                )
                time.sleep(espera)
                espera = min(espera * 2, CONNECT_BACKOFF_MAX_S)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._client.disconnect()

    def loop_forever(self) -> None:
        self._client.loop_forever()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info("Conectado al broker. Suscribiendo a %s", self.subscribe_topic)
            client.subscribe(self.subscribe_topic, qos=self.qos)
        else:
            logger.error("Error de conexión MQTT: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            reading = json.loads(msg.payload.decode("utf-8"))
            self._on_reading(reading)
        except json.JSONDecodeError:
            logger.warning("Mensaje no es JSON válido en %s", msg.topic)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error procesando mensaje: %s", exc)
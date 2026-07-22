"""Publisher MQTT para enviar lecturas al broker."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Self

import paho.mqtt.client as mqtt

from simulator.athlete import Reading


logger = logging.getLogger(__name__)


class MqttPublisher:
    """Publica lecturas en un broker MQTT.

    El topic sigue el patrón `hyrox/<athlete_id>/biometrics`.

    Distingue entre lecturas encoladas (aceptadas por el cliente) y confirmadas
    (PUBACK del broker con QoS 1): si la máquina que simula va saturada, el
    cliente encola más rápido de lo que transmite y solo el recuento de
    confirmadas dice cuántas lecturas entraron de verdad en la plataforma.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        client_id: str = "hyrox-simulator",
        qos: int = 1,
        username: str | None = None,
        password: str | None = None,
        ca_certs: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.qos = qos
        self.enqueued = 0
        self.acked = 0
        self._ack_event = threading.Event()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        # Cada atleta se autentica con su propia identidad: la ACL del broker
        # solo le deja publicar en hyrox/<usuario>/biometrics.
        if username is not None:
            self._client.username_pw_set(username, password)
        # Con ca_certs el canal va cifrado y se valida el certificado del broker
        # contra esa CA; es lo que exige el listener externo 8883.
        if ca_certs is not None:
            self._client.tls_set(ca_certs=ca_certs)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_publish = self._on_publish

    def __enter__(self) -> Self:
        logger.info("Conectando al broker MQTT en %s:%d", self.host, self.port)
        self._client.connect(self.host, self.port)
        self._client.loop_start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.drain()
        logger.info("Desconectando del broker MQTT")
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, reading: Reading) -> None:
        topic = f"hyrox/{reading.athlete_id}/biometrics"
        payload = json.dumps(reading.to_dict())
        result = self._client.publish(topic, payload, qos=self.qos)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            self.enqueued += 1
        else:
            logger.warning("Fallo publicando en %s: rc=%s", topic, result.rc)

    def drain(self, timeout: float = 30.0) -> bool:
        """Espera a que el broker confirme todo lo encolado (o agota el plazo).

        Devuelve True si no quedó nada pendiente. Sin este drenado, desconectar
        descartaría las lecturas aún en la cola local y se contarían como
        pérdida de la plataforma sin haber llegado al broker.
        """
        deadline = time.monotonic() + timeout
        while self.acked < self.enqueued:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "Drenado incompleto: %d lecturas sin confirmar",
                    self.enqueued - self.acked,
                )
                return False
            self._ack_event.wait(min(remaining, 0.5))
            self._ack_event.clear()
        return True

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        self.acked += 1
        self._ack_event.set()

    @staticmethod
    def _on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info("Conectado al broker MQTT")
        else:
            logger.error("Error de conexión MQTT: %s", reason_code)

    @staticmethod
    def _on_disconnect(client, userdata, flags, reason_code, properties):
        logger.info("Desconectado del broker MQTT (rc=%s)", reason_code)

"""Publisher MQTT para enviar lecturas al broker."""

from __future__ import annotations

import json
import logging
from typing import Self

import paho.mqtt.client as mqtt

from simulator.athlete import Reading


logger = logging.getLogger(__name__)


class MqttPublisher:
    """Publica lecturas en un broker MQTT.

    El topic sigue el patrón `hyrox/<athlete_id>/biometrics`.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        client_id: str = "hyrox-simulator",
        qos: int = 1,
    ) -> None:
        self.host = host
        self.port = port
        self.qos = qos
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def __enter__(self) -> Self:
        logger.info("Conectando al broker MQTT en %s:%d", self.host, self.port)
        self._client.connect(self.host, self.port)
        self._client.loop_start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        logger.info("Desconectando del broker MQTT")
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, reading: Reading) -> None:
        topic = f"hyrox/{reading.athlete_id}/biometrics"
        payload = json.dumps(reading.to_dict())
        result = self._client.publish(topic, payload, qos=self.qos)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("Fallo publicando en %s: rc=%s", topic, result.rc)

    @staticmethod
    def _on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info("Conectado al broker MQTT")
        else:
            logger.error("Error de conexión MQTT: %s", reason_code)

    @staticmethod
    def _on_disconnect(client, userdata, flags, reason_code, properties):
        logger.info("Desconectado del broker MQTT (rc=%s)", reason_code)

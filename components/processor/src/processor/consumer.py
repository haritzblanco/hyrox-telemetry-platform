"""Consumidor MQTT que reenvía las lecturas al escritor de InfluxDB."""

from __future__ import annotations

import json
import logging
from typing import Callable, Self

import paho.mqtt.client as mqtt


logger = logging.getLogger(__name__)


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
    ) -> None:
        self.host = host
        self.port = port
        self.topic = topic
        self.qos = qos
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
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def __enter__(self) -> Self:
        logger.info("Conectando al broker MQTT en %s:%d", self.host, self.port)
        self._client.connect(self.host, self.port)
        return self

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
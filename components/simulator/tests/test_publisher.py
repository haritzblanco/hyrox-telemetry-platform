"""Pruebas del publisher: recuento de confirmadas y drenado antes de salir."""

import threading
from unittest.mock import MagicMock, patch

import paho.mqtt.client as mqtt
import pytest

from simulator.publisher import MqttPublisher


@pytest.fixture
def publisher():
    with patch("simulator.publisher.mqtt.Client") as cliente:
        cliente.return_value.publish.return_value = MagicMock(rc=mqtt.MQTT_ERR_SUCCESS)
        yield MqttPublisher(host="broker", port=1883)


def _lectura():
    r = MagicMock()
    r.athlete_id = "atleta-001"
    r.to_dict.return_value = {"athlete_id": "atleta-001", "heart_rate": 150}
    return r


def test_publicar_cuenta_encoladas(publisher):
    publisher.publish(_lectura())
    publisher.publish(_lectura())
    assert publisher.enqueued == 2
    assert publisher.acked == 0


def test_fallo_de_publicacion_no_cuenta(publisher):
    publisher._client.publish.return_value = MagicMock(rc=mqtt.MQTT_ERR_NO_CONN)
    publisher.publish(_lectura())
    assert publisher.enqueued == 0


def test_puback_cuenta_confirmadas(publisher):
    publisher.publish(_lectura())
    publisher._on_publish(None, None, 1, None, None)
    assert publisher.acked == 1


def test_drain_sin_pendientes_vuelve_al_instante(publisher):
    publisher.publish(_lectura())
    publisher._on_publish(None, None, 1, None, None)
    assert publisher.drain(timeout=0.1) is True


def test_drain_espera_las_confirmaciones(publisher):
    for _ in range(3):
        publisher.publish(_lectura())

    def confirmar():
        for i in range(3):
            publisher._on_publish(None, None, i, None, None)

    threading.Timer(0.05, confirmar).start()
    assert publisher.drain(timeout=2.0) is True
    assert publisher.acked == 3


def test_drain_agota_el_plazo_si_no_llegan(publisher):
    publisher.publish(_lectura())
    assert publisher.drain(timeout=0.15) is False


def test_salir_drena_antes_de_desconectar(publisher):
    orden = []
    publisher.drain = lambda *a, **k: orden.append("drain")
    publisher._client.loop_stop.side_effect = lambda: orden.append("loop_stop")
    publisher._client.disconnect.side_effect = lambda: orden.append("disconnect")
    publisher.__exit__(None, None, None)
    assert orden == ["drain", "loop_stop", "disconnect"]

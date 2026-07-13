"""Pruebas del consumidor MQTT: topics y manejo de mensajes."""

import json
from types import SimpleNamespace


from processor.consumer import MqttConsumer


def _consumer(on_reading=lambda r: None, **kwargs):
    return MqttConsumer(
        host="localhost", port=1883, topic="hyrox/+/biometrics",
        on_reading=on_reading, **kwargs,
    )


def _msg(payload: bytes, topic: str = "hyrox/atleta-001/biometrics"):
    return SimpleNamespace(payload=payload, topic=topic)


def test_sin_grupo_suscribe_al_topic_directo():
    c = _consumer()
    assert c.subscribe_topic == "hyrox/+/biometrics"


def test_con_grupo_usa_suscripcion_compartida():
    c = _consumer(share_group="processors")
    assert c.subscribe_topic == "$share/processors/hyrox/+/biometrics"


def test_mensaje_valido_llega_al_callback(lectura):
    recibidas = []
    c = _consumer(on_reading=recibidas.append)
    c._on_message(None, None, _msg(json.dumps(lectura).encode()))
    assert recibidas == [lectura]


def test_json_invalido_se_descarta_sin_romper():
    recibidas = []
    c = _consumer(on_reading=recibidas.append)
    c._on_message(None, None, _msg(b"esto no es json"))
    assert recibidas == []


def test_excepcion_del_callback_no_propaga(lectura):
    def explota(reading):
        raise RuntimeError("fallo simulado")

    c = _consumer(on_reading=explota)
    # El bucle de red de paho no debe caerse por un mensaje problemático.
    c._on_message(None, None, _msg(json.dumps(lectura).encode()))

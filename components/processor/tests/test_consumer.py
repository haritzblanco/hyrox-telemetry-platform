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


def test_no_esta_disponible_hasta_que_se_concede_la_suscripcion():
    c = _consumer(share_group="processors")
    assert not c.is_connected()
    c._on_subscribe(None, None, 1, [1], None)
    assert c.is_connected()


def test_una_suscripcion_rechazada_no_marca_disponible():
    c = _consumer()
    c._on_subscribe(None, None, 1, [128], None)
    assert not c.is_connected()


def test_la_desconexion_retira_la_disponibilidad():
    c = _consumer()
    c._on_subscribe(None, None, 1, [1], None)
    c._on_disconnect(None, None, None, 7, None)
    assert not c.is_connected()


def test_una_conexion_rechazada_retira_la_disponibilidad():
    c = _consumer()
    c._on_subscribe(None, None, 1, [1], None)
    c._on_connect(None, None, None, 5, None)
    assert not c.is_connected()


def test_reintenta_hasta_que_el_broker_acepta(monkeypatch):
    intentos = []

    def connect(host, port):
        intentos.append((host, port))
        if len(intentos) < 3:
            raise ConnectionRefusedError(61, "Connection refused")

    c = _consumer()
    monkeypatch.setattr(c._client, "connect", connect)
    monkeypatch.setattr("processor.consumer.time.sleep", lambda _: None)

    with c:
        pass

    assert len(intentos) == 3


def test_desiste_si_el_broker_nunca_responde(monkeypatch):
    def connect(host, port):
        raise ConnectionRefusedError(61, "Connection refused")

    # Un plazo corto basta: lo que se comprueba es que la espera está acotada y
    # el error acaba propagando, no cuánto se espera.
    c = _consumer(connect_timeout=2.0)
    monkeypatch.setattr(c._client, "connect", connect)
    monkeypatch.setattr("processor.consumer.time.sleep", lambda _: None)

    try:
        with c:
            pass
    except ConnectionRefusedError:
        return
    raise AssertionError("se esperaba que el error propagase")

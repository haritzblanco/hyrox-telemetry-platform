"""Pruebas de los auxiliares del punto de entrada."""

from datetime import datetime, timedelta, timezone

from processor.main import _transport_latency_ms


def test_timestamp_reciente_da_latencia_positiva():
    ts = (datetime.now(timezone.utc) - timedelta(milliseconds=50)).isoformat()
    lat = _transport_latency_ms({"timestamp": ts})
    assert lat is not None
    assert 0 <= lat < 5000


def test_timestamp_naive_se_asume_utc():
    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    lat = _transport_latency_ms({"timestamp": ts})
    assert lat is not None
    assert abs(lat) < 5000


def test_sin_timestamp_devuelve_none():
    assert _transport_latency_ms({}) is None
    assert _transport_latency_ms({"timestamp": ""}) is None


def test_timestamp_invalido_devuelve_none():
    assert _transport_latency_ms({"timestamp": "ayer por la tarde"}) is None
    assert _transport_latency_ms({"timestamp": 12345}) is None

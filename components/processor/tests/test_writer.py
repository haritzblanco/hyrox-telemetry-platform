"""Pruebas del escritor de InfluxDB con el cliente sustituido por un doble."""

from unittest.mock import MagicMock, patch

import pytest

from processor.metrics import Metrics
from processor.writer import InfluxWriter, _count_points, _to_line


class TestCountPoints:
    def test_cuenta_lineas_de_line_protocol(self):
        data = "biometrics,athlete_id=a hr=150\nbiometrics,athlete_id=b hr=140\n"
        assert _count_points(data) == 2

    def test_acepta_bytes(self):
        assert _count_points(b"linea1\nlinea2") == 2

    def test_ignora_lineas_vacias(self):
        assert _count_points("linea1\n\n  \n") == 1


@pytest.fixture
def writer_con_doble():
    """InfluxWriter con el cliente HTTP sustituido; expone el write_api falso."""
    with patch("processor.writer.InfluxDBClient") as cliente:
        write_api = MagicMock()
        cliente.return_value.write_api.return_value = write_api
        writer = InfluxWriter(
            url="http://influx:8086", token="t", org="hyrox", bucket="telemetry",
        )
        yield writer, write_api


def test_write_construye_la_linea(writer_con_doble, lectura):
    writer, write_api = writer_con_doble
    writer.write(lectura)

    kwargs = write_api.write.call_args.kwargs
    assert kwargs["bucket"] == "telemetry"
    assert kwargs["org"] == "hyrox"
    linea = kwargs["record"]
    assert linea.startswith("biometrics,")
    assert "athlete_id=atleta-001" in linea
    assert "session_id=20260712_100000" in linea
    assert "phase=run_1" in linea
    assert "phase_type=run" in linea
    assert "heart_rate=155i" in linea
    assert "speed=3.42" in linea
    assert "distance=145.3" in linea


def test_campos_ausentes_usan_unknown(writer_con_doble, lectura):
    writer, write_api = writer_con_doble
    del lectura["phase"], lectura["phase_type"], lectura["session_id"]
    writer.write(lectura)
    linea = write_api.write.call_args.kwargs["record"]
    assert "phase=unknown" in linea
    assert "session_id=unknown" in linea


def test_linea_equivale_al_point_del_cliente(lectura):
    # La construcción directa debe producir el mismo line protocol que el
    # objeto Point del cliente oficial (los tags van ordenados distinto y el
    # timestamp difiere en el redondeo sub-µs; se comparan campos y tags).
    from influxdb_client import Point

    point = (
        Point("biometrics")
        .tag("athlete_id", lectura["athlete_id"]).tag("session_id", lectura["session_id"])
        .tag("phase", lectura["phase"]).tag("phase_type", lectura["phase_type"])
        .field("heart_rate", int(lectura["heart_rate"])).field("cadence", int(lectura["cadence"]))
        .field("power", int(lectura["power"])).field("speed", float(lectura["speed"]))
        .field("distance", float(lectura["distance"]))
        .field("elapsed_seconds", int(lectura["elapsed_seconds"]))
        .time(lectura["timestamp"])
    )
    esperado = point.to_line_protocol()
    obtenido = _to_line(lectura)

    def partes(lp):
        cabecera, campos, ts = lp.rsplit(" ", 2)
        return set(cabecera.split(",")), set(campos.split(",")), int(ts)

    tags_e, campos_e, ts_e = partes(esperado)
    tags_o, campos_o, ts_o = partes(obtenido)
    assert tags_o == tags_e
    assert campos_o == campos_e
    assert abs(ts_o - ts_e) < 1000  # mismo instante, redondeo sub-µs


def test_tags_con_caracteres_especiales_se_escapan(lectura):
    lectura["athlete_id"] = "atleta con espacios,y=comas"
    linea = _to_line(lectura)
    assert "athlete_id=atleta\\ con\\ espacios\\,y\\=comas" in linea


def test_timestamp_naive_se_asume_utc(lectura):
    from datetime import datetime, timezone

    aware = datetime.now(timezone.utc)
    lectura["timestamp"] = aware.replace(tzinfo=None).isoformat()
    ns = int(_to_line(lectura).rsplit(" ", 1)[1])
    assert abs(ns - aware.timestamp() * 1e9) < 1e6


def test_sin_metricas_no_hay_tracker(writer_con_doble):
    writer, _ = writer_con_doble
    assert writer._tracker is None


@pytest.fixture
def writer_con_metricas():
    with patch("processor.writer.InfluxDBClient") as cliente:
        cliente.return_value.write_api.return_value = MagicMock()
        emitidas = []
        metrics = Metrics(interval_s=0, emit=emitidas.append)
        writer = InfluxWriter(
            url="http://influx:8086", token="t", org="hyrox", bucket="telemetry",
            metrics=metrics,
        )
        yield writer, metrics


def test_confirmacion_de_lote_registra_latencias(writer_con_metricas, lectura):
    writer, metrics = writer_con_metricas
    writer.write(lectura)
    writer.write(lectura)

    # Simula el callback de éxito de un lote de 2 puntos.
    writer._on_success(None, "linea1\nlinea2")
    assert metrics.total_acked == 2
    assert len(writer._tracker._pending) == 0


def test_lote_fallido_cuenta_errores_y_desalinea(writer_con_metricas, lectura):
    writer, metrics = writer_con_metricas
    writer.write(lectura)
    writer.write(lectura)

    writer._on_error(None, "linea1\nlinea2", RuntimeError("influx caído"))
    assert metrics.total_errors == 2
    assert metrics.total_acked == 0
    assert len(writer._tracker._pending) == 0

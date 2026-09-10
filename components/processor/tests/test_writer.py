"""Pruebas del escritor de InfluxDB con el cliente sustituido por un doble."""

import queue
import time
from unittest.mock import MagicMock, patch

import pytest

from processor.metrics import Metrics
from processor.writer import InfluxWriter, _to_line


def esperar(condicion, timeout=3.0):
    """Espera a que el hilo de escritura haga su trabajo."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.01)
    return False


def lineas_escritas(write_api):
    """Todas las líneas de line protocol que han llegado al cliente."""
    out = []
    for llamada in write_api.write.call_args_list:
        out.extend(llamada.kwargs["record"].split("\n"))
    return out


@pytest.fixture
def escritor():
    """Fábrica de InfluxWriter con el cliente HTTP sustituido.

    Devuelve (writer, write_api) y cierra el escritor al terminar la prueba,
    que arranca un hilo propio.
    """
    creados = []

    def fabricar(metrics=None, **kwargs):
        with patch("processor.writer.InfluxDBClient") as cliente:
            write_api = MagicMock()
            cliente.return_value.write_api.return_value = write_api
            writer = InfluxWriter(
                url="http://influx:8086", token="t", org="hyrox", bucket="telemetry",
                metrics=metrics, batch_size=kwargs.pop("batch_size", 2),
                flush_interval_s=kwargs.pop("flush_interval_s", 0.05), **kwargs,
            )
            creados.append(writer)
            return writer, write_api

    yield fabricar
    for w in creados:
        w.__exit__(None, None, None)


class TestSerializacion:
    def test_construye_la_linea(self, escritor, lectura):
        writer, write_api = escritor()
        writer.write(lectura)
        writer.write(lectura)

        assert esperar(lambda: write_api.write.called)
        kwargs = write_api.write.call_args.kwargs
        assert kwargs["bucket"] == "telemetry"
        assert kwargs["org"] == "hyrox"
        linea = kwargs["record"].split("\n")[0]
        assert linea.startswith("biometrics,")
        assert "athlete_id=atleta-001" in linea
        assert "session_id=20260712_100000" in linea
        assert "phase=run_1" in linea
        assert "phase_type=run" in linea
        assert "heart_rate=155i" in linea
        assert "speed=3.42" in linea
        assert "distance=145.3" in linea

    def test_campos_ausentes_usan_unknown(self, lectura):
        del lectura["phase"], lectura["phase_type"], lectura["session_id"]
        linea = _to_line(lectura)
        assert "phase=unknown" in linea
        assert "session_id=unknown" in linea

    def test_linea_equivale_al_point_del_cliente(self, lectura):
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

    def test_tags_con_caracteres_especiales_se_escapan(self, lectura):
        lectura["athlete_id"] = "atleta con espacios,y=comas"
        linea = _to_line(lectura)
        assert "athlete_id=atleta\\ con\\ espacios\\,y\\=comas" in linea

    def test_timestamp_naive_se_asume_utc(self, lectura):
        from datetime import datetime, timezone

        aware = datetime.now(timezone.utc)
        lectura["timestamp"] = aware.replace(tzinfo=None).isoformat()
        ns = int(_to_line(lectura).rsplit(" ", 1)[1])
        assert abs(ns - aware.timestamp() * 1e9) < 1e6


class TestLotePropio:
    def test_el_lote_se_cierra_al_llenarse(self, escritor, lectura):
        writer, write_api = escritor(batch_size=3)
        for _ in range(6):
            writer.write(lectura)

        assert esperar(lambda: len(lineas_escritas(write_api)) == 6)
        assert all(len(c.kwargs["record"].split("\n")) <= 3
                   for c in write_api.write.call_args_list)

    def test_el_lote_incompleto_se_cierra_por_tiempo(self, escritor, lectura):
        writer, write_api = escritor(batch_size=1000, flush_interval_s=0.05)
        writer.write(lectura)

        assert esperar(lambda: write_api.write.called)
        assert lineas_escritas(write_api) == [_to_line(lectura)]

    def test_no_se_pierde_ninguna_lectura(self, escritor, lectura):
        """El defecto que motivó el lote propio: puntos que se esfuman."""
        writer, write_api = escritor(batch_size=64)
        for _ in range(1000):
            writer.write(lectura)

        assert esperar(lambda: len(lineas_escritas(write_api)) == 1000)

    def test_al_cerrar_se_vacia_lo_pendiente(self, lectura):
        with patch("processor.writer.InfluxDBClient") as cliente:
            write_api = MagicMock()
            cliente.return_value.write_api.return_value = write_api
            writer = InfluxWriter(
                url="http://influx:8086", token="t", org="hyrox", bucket="telemetry",
                batch_size=1000, flush_interval_s=5.0,
            )
            for _ in range(10):
                writer.write(lectura)
            # El lote no se ha cerrado todavía: lo fuerza la salida.
            writer.__exit__(None, None, None)

        assert len(lineas_escritas(write_api)) == 10
        assert cliente.return_value.close.called


class TestMedidas:
    def _metrics(self):
        return Metrics(interval_s=0, emit=lambda _: None)

    def test_confirmar_el_lote_mide_cada_punto(self, escritor, lectura):
        metrics = self._metrics()
        writer, write_api = escritor(metrics=metrics, batch_size=4)
        for _ in range(4):
            writer.write(lectura)

        assert esperar(lambda: metrics.total_acked == 4)
        assert metrics.total_errors == 0
        assert len(metrics._persist_lat) == 4
        assert all(v >= 0 for v in metrics._persist_lat)

    def test_lote_fallido_cuenta_errores(self, escritor, lectura, monkeypatch):
        monkeypatch.setattr("processor.writer.MAX_RETRIES", 1)
        metrics = self._metrics()
        writer, write_api = escritor(metrics=metrics, batch_size=2)
        write_api.write.side_effect = RuntimeError("influx caído")
        writer.write(lectura)
        writer.write(lectura)

        assert esperar(lambda: metrics.total_errors == 2)
        assert metrics.total_acked == 0

    def test_reintenta_antes_de_darse_por_vencido(self, escritor, lectura, monkeypatch):
        monkeypatch.setattr("processor.writer.RETRY_INTERVAL_S", 0.01)
        monkeypatch.setattr("processor.writer.MAX_RETRY_DELAY_S", 0.01)
        metrics = self._metrics()
        writer, write_api = escritor(metrics=metrics, batch_size=2)
        write_api.write.side_effect = [RuntimeError("corte"), None]
        writer.write(lectura)
        writer.write(lectura)

        assert esperar(lambda: metrics.total_acked == 2)
        assert metrics.total_errors == 0
        assert write_api.write.call_count == 2

    def test_la_cola_llena_descarta_pero_lo_cuenta(self, escritor, lectura, monkeypatch):
        """Nunca en silencio: es justo lo que hacía mal el cliente."""
        metrics = self._metrics()
        writer, _ = escritor(metrics=metrics, batch_size=1000, flush_interval_s=5.0)
        monkeypatch.setattr(writer._queue, "put_nowait",
                            MagicMock(side_effect=queue.Full))
        writer.write(lectura)

        assert metrics.total_errors == 1
        assert writer._rejected == 1

    def test_pending_cuenta_lo_que_espera_en_la_cola(self, escritor, lectura):
        writer, _ = escritor(batch_size=1000, flush_interval_s=5.0)
        for _ in range(5):
            writer.write(lectura)
        assert esperar(lambda: writer.pending() == 5)

    def test_se_publica_la_cola_al_cerrar_cada_lote(self, escritor, lectura):
        registradas = []

        class SinkFalso:
            def record_consume(self, latency_ms): pass
            def record_persist(self, latencies_ms): pass
            def record_errors(self, n): pass
            def record_pending(self, n): registradas.append(n)

        writer, write_api = escritor(metrics=SinkFalso(), batch_size=2)
        for _ in range(2):
            writer.write(lectura)

        assert esperar(lambda: sum(len(c.kwargs["record"].split("\n"))
                                   for c in write_api.write.call_args_list) == 2)
        assert registradas and all(n == 0 for n in registradas)

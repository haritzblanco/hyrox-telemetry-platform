"""Pruebas de percentiles, ventanas de métricas y rastreo de persistencia."""

import json

import pytest

from processor.metrics import Metrics, PersistenceTracker, _percentile, _summary


class TestPercentile:
    def test_un_solo_valor(self):
        assert _percentile([7.0], 0.99) == 7.0

    def test_mediana_interpola(self):
        assert _percentile([1.0, 2.0, 3.0, 4.0], 0.50) == pytest.approx(2.5)

    def test_extremos(self):
        vals = [1.0, 2.0, 3.0]
        assert _percentile(vals, 0.0) == 1.0
        assert _percentile(vals, 1.0) == 3.0

    def test_p95(self):
        vals = [float(i) for i in range(1, 101)]
        assert _percentile(vals, 0.95) == pytest.approx(95.05)


class TestSummary:
    def test_lista_vacia_devuelve_none(self):
        assert _summary([]) is None

    def test_resumen_basico(self):
        s = _summary([10.0, 20.0, 30.0])
        assert s["n"] == 3
        assert s["mean"] == 20.0
        assert s["p50"] == 20.0
        assert s["max"] == 30.0


class TestMetrics:
    def _metrics(self, emitidas):
        # interval_s=0: el hilo no arranca y _flush se llama a mano.
        return Metrics(interval_s=0, tag="prueba", client_id="proc-1",
                       emit=lambda line: emitidas.append(json.loads(line)))

    def test_ventana_acumula_y_se_resetea(self):
        emitidas = []
        m = self._metrics(emitidas)
        m.record_consume(5.0)
        m.record_consume(15.0)
        m.record_persist([100.0, 200.0, 300.0])
        m.record_errors(2)
        m._flush()

        rec = emitidas[0]
        assert rec["kind"] == "metrics"
        assert rec["tag"] == "prueba"
        assert rec["client_id"] == "proc-1"
        assert rec["consumed"] == 2
        assert rec["acked"] == 3
        assert rec["errors"] == 2
        assert rec["lat_transport_ms"]["p50"] == 10.0
        assert rec["lat_persist_ms"]["n"] == 3

        # La ventana siguiente parte de cero; los totales se conservan.
        m._flush()
        rec2 = emitidas[1]
        assert rec2["consumed"] == 0
        assert rec2["lat_transport_ms"] is None
        assert rec2["total_consumed"] == 2
        assert rec2["total_acked"] == 3
        assert rec2["total_errors"] == 2

    def test_flush_final_se_marca(self):
        emitidas = []
        m = self._metrics(emitidas)
        m._flush(final=True)
        assert emitidas[0]["final"] is True

    def test_sin_intervalo_no_arranca_hilo(self):
        m = self._metrics([])
        m.start()
        assert m._thread is None
        m.stop()  # no debe fallar aunque no haya hilo


class TestPersistenceTracker:
    def test_ack_devuelve_las_mas_antiguas(self):
        t = PersistenceTracker()
        for _ in range(3):
            t.on_enqueue()
        lat = t.on_ack(2)
        assert len(lat) == 2
        assert all(v >= 0 for v in lat)
        assert len(t._pending) == 1

    def test_ack_mayor_que_pendientes_no_falla(self):
        t = PersistenceTracker()
        t.on_enqueue()
        assert len(t.on_ack(5)) == 1
        assert t.on_ack(5) == []

    def test_drop_realinea_la_cola(self):
        t = PersistenceTracker()
        for _ in range(4):
            t.on_enqueue()
        t.on_drop(2)
        assert len(t.on_ack(10)) == 2

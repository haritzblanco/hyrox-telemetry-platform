"""Pruebas del medidor de carga y de la conversión de unidades."""

import pytest

from broker_exporter.main import LoadGauge, RateMeter, render_metrics


class TestRateMeter:
    def test_la_primera_lectura_no_da_caudal(self):
        assert RateMeter().update(1000, now=10.0) is None

    def test_deriva_el_contador(self):
        m = RateMeter()
        m.update(1000, now=10.0)
        assert m.update(5000, now=15.0) == 800.0

    def test_encadena_lecturas(self):
        m = RateMeter()
        m.update(0, now=0.0)
        assert m.update(500, now=5.0) == 100.0
        assert m.update(1500, now=10.0) == 200.0

    def test_un_contador_que_retrocede_es_un_reinicio_del_broker(self):
        m = RateMeter()
        m.update(9000, now=10.0)
        assert m.update(12, now=15.0) is None
        # Tras el reinicio se sigue midiendo desde el contador nuevo.
        assert m.update(1012, now=20.0) == 200.0

    def test_dos_lecturas_del_mismo_instante_no_dan_caudal(self):
        m = RateMeter()
        m.update(1000, now=10.0)
        assert m.update(2000, now=10.0) is None

    def test_payload_no_numerico_lanza(self):
        with pytest.raises(ValueError):
            RateMeter().update(int(b"no-numerico"), now=1.0)


class TestLoadGauge:
    def test_sin_datos_devuelve_cero(self):
        assert LoadGauge().get() == 0.0

    def test_valor_fresco(self):
        g = LoadGauge(stale_after_s=60.0)
        g.set(5.5, now=100.0)
        assert g.get(now=130.0) == 5.5

    def test_valor_caducado_devuelve_cero(self):
        g = LoadGauge(stale_after_s=60.0)
        g.set(5.5, now=100.0)
        assert g.get(now=161.0) == 0.0


class TestRenderMetrics:
    def test_formato_prometheus(self):
        out = render_metrics(12.3456)
        assert "# TYPE hyrox_broker_messages_per_second gauge" in out
        assert out.strip().endswith("hyrox_broker_messages_per_second 12.346")

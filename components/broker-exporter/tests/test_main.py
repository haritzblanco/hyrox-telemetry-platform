"""Pruebas del medidor de carga y de la conversión de unidades."""

import pytest

from broker_exporter.main import LoadGauge, per_second


class TestPerSecond:
    def test_convierte_minutos_a_segundos(self):
        assert per_second(b"600.00") == 10.0

    def test_payload_no_numerico_lanza(self):
        with pytest.raises(ValueError):
            per_second(b"no-numerico")


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

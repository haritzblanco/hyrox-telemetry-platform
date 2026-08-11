"""Pruebas del endpoint de Prometheus: histogramas, contadores y servicio HTTP."""

import urllib.error
import urllib.request

import pytest

from processor.metrics import MetricsFanout
from processor.promexport import Counter, Histogram, PrometheusExporter


class TestHistogram:
    def test_los_buckets_son_acumulativos(self):
        h = Histogram(buckets=(1.0, 2.0, 3.0))
        for v in (0.5, 1.5, 2.5):
            h.observe(v)
        counts, total, suma = h.snapshot()
        assert counts == [1, 2, 3]
        assert total == 3
        assert suma == pytest.approx(4.5)

    def test_un_valor_por_encima_del_ultimo_limite_solo_cuenta_en_inf(self):
        h = Histogram(buckets=(1.0, 2.0))
        h.observe(9.0)
        counts, total, _ = h.snapshot()
        assert counts == [0, 0]
        assert total == 1

    def test_el_valor_igual_al_limite_entra_en_su_bucket(self):
        h = Histogram(buckets=(1.0,))
        h.observe(1.0)
        assert h.snapshot()[0] == [1]

    def test_render_incluye_inf_suma_y_total(self):
        h = Histogram(buckets=(1.0,))
        h.observe(0.5)
        texto = h.render("lat_seconds", "Latencia.")
        assert "# TYPE lat_seconds histogram" in texto
        assert 'lat_seconds_bucket{le="1.0"} 1' in texto
        assert 'lat_seconds_bucket{le="+Inf"} 1' in texto
        assert "lat_seconds_sum 0.500000" in texto
        assert "lat_seconds_count 1" in texto


class TestCounter:
    def test_incrementa(self):
        c = Counter()
        c.inc()
        c.inc(4)
        assert c.value == 5

    def test_render(self):
        c = Counter()
        c.inc(2)
        texto = c.render("cosas_total", "Cosas.")
        assert "# TYPE cosas_total counter" in texto
        assert "cosas_total 2" in texto


class TestExporter:
    def test_consumo_cuenta_y_convierte_a_segundos(self):
        e = PrometheusExporter()
        e.record_consume(1500.0)
        assert e.consumed.value == 1
        assert e.transport.snapshot()[2] == pytest.approx(1.5)

    def test_persistencia_cuenta_todas_las_del_lote(self):
        e = PrometheusExporter()
        e.record_persist([100.0, 200.0, 300.0])
        assert e.persisted.value == 3
        assert e.persist.snapshot()[2] == pytest.approx(0.6)

    def test_errores(self):
        e = PrometheusExporter()
        e.record_errors(3)
        assert "hyrox_processor_write_errors_total 3" in e.render()

    def test_render_expone_las_cinco_metricas(self):
        texto = PrometheusExporter().render()
        for nombre in (
            "hyrox_processor_transport_latency_seconds",
            "hyrox_processor_persist_latency_seconds",
            "hyrox_processor_consumed_total",
            "hyrox_processor_persisted_total",
            "hyrox_processor_write_errors_total",
        ):
            assert f"# TYPE {nombre}" in texto


class TestServidor:
    @pytest.fixture
    def exporter(self):
        e = PrometheusExporter()
        e.serve(0)  # puerto libre elegido por el sistema
        yield e
        e.stop()

    def _url(self, exporter, path):
        return f"http://127.0.0.1:{exporter._server.server_address[1]}{path}"

    def test_metrics_responde_el_texto(self, exporter):
        exporter.record_consume(50.0)
        with urllib.request.urlopen(self._url(exporter, "/metrics"), timeout=5) as r:
            cuerpo = r.read().decode()
            assert r.status == 200
        assert "hyrox_processor_consumed_total 1" in cuerpo

    def test_healthz(self, exporter):
        with urllib.request.urlopen(self._url(exporter, "/healthz"), timeout=5) as r:
            assert r.status == 200

    def test_readyz_sin_comprobacion_responde_503(self, exporter):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(self._url(exporter, "/readyz"), timeout=5)
        assert exc.value.code == 503

    def test_readyz_sigue_a_la_comprobacion(self, exporter):
        listo = False
        exporter.ready_check = lambda: listo

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(self._url(exporter, "/readyz"), timeout=5)
        assert exc.value.code == 503

        listo = True
        with urllib.request.urlopen(self._url(exporter, "/readyz"), timeout=5) as r:
            assert r.status == 200

    def test_otras_rutas_dan_404(self, exporter):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(self._url(exporter, "/otra"), timeout=5)
        assert exc.value.code == 404


class TestFanout:
    def test_reparte_a_todos_los_destinos(self):
        a, b = PrometheusExporter(), PrometheusExporter()
        fan = MetricsFanout([a, b])
        fan.record_consume(10.0)
        fan.record_persist([20.0, 30.0])
        fan.record_errors(1)
        for e in (a, b):
            assert e.consumed.value == 1
            assert e.persisted.value == 2
            assert e.errors.value == 1

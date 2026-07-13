"""Pruebas del modelo del atleta: fases, factores, límites y determinismo."""

import pytest

from simulator.athlete import Athlete


def _atleta(perfil, **kwargs):
    kwargs.setdefault("seed", 42)
    return Athlete("atleta-test", "sesion-test", perfil, **kwargs)


def _sesion_completa(atleta):
    lecturas = []
    while not atleta.finished:
        lecturas.append(atleta.next_reading())
    return lecturas


def test_una_lectura_por_segundo(perfil):
    atleta = _atleta(perfil)
    lecturas = _sesion_completa(atleta)
    assert len(lecturas) == atleta.total_seconds
    assert [r.elapsed_seconds for r in lecturas] == list(range(atleta.total_seconds))


def test_al_terminar_devuelve_none(perfil):
    atleta = _atleta(perfil)
    _sesion_completa(atleta)
    assert atleta.finished
    assert atleta.next_reading() is None


def test_fases_en_orden_y_estacion_sin_cv_dura_lo_calibrado(perfil):
    # run_1 tiene cv=0: con factores neutros dura exactamente lo calibrado.
    atleta = _atleta(perfil)
    lecturas = _sesion_completa(atleta)
    fases = [r.phase for r in lecturas]
    assert fases[:60] == ["run_1"] * 60
    # El resto de fases aparecen una sola vez y en orden.
    orden = list(dict.fromkeys(fases))
    assert orden == ["run_1", "roxzone", "skierg"]


def test_mismo_seed_misma_sesion(perfil):
    a = _sesion_completa(_atleta(perfil, seed=7))
    b = _sesion_completa(_atleta(perfil, seed=7))
    for ra, rb in zip(a, b):
        da, db = ra.to_dict(), rb.to_dict()
        # El timestamp es reloj de pared; el resto debe ser idéntico.
        da.pop("timestamp"), db.pop("timestamp")
        assert da == db


def test_seeds_distintos_sesiones_distintas(perfil):
    a = _sesion_completa(_atleta(perfil, seed=1))
    b = _sesion_completa(_atleta(perfil, seed=2))
    assert any(ra.heart_rate != rb.heart_rate for ra, rb in zip(a, b))


def test_fitness_escala_la_duracion_total(perfil):
    base = _atleta(perfil, fitness=1.0).total_seconds
    lento = _atleta(perfil, fitness=1.5).total_seconds
    rapido = _atleta(perfil, fitness=0.8).total_seconds
    assert lento == pytest.approx(base * 1.5, rel=0.05)
    assert rapido == pytest.approx(base * 0.8, rel=0.05)


def test_strength_factor_solo_afecta_estaciones(perfil):
    # Con el mismo seed el ruido de duración coincide: la diferencia entre
    # ambos atletas es exactamente el 50% extra de la estación (40 s).
    base = _atleta(perfil, seed=3).total_seconds
    fuerte = _atleta(perfil, seed=3, strength_factor=1.5).total_seconds
    assert fuerte - base == pytest.approx(0.5 * 40, abs=2)


def test_run_factor_no_toca_las_estaciones(perfil):
    base = _atleta(perfil, seed=3)
    corredor = _atleta(perfil, seed=3, run_factor=0.8)
    # Solo cambian run (60 s) y roxzone; la estación queda igual.
    assert base._durations[2] == corredor._durations[2]
    assert corredor._durations[0] == pytest.approx(60 * 0.8, abs=1)


def test_senales_dentro_de_limites(perfil):
    lecturas = _sesion_completa(_atleta(perfil, seed=11))
    fases = {p.name: p for p in perfil.phases}
    for r in lecturas:
        fase = fases[r.phase]
        assert 60 <= r.heart_rate <= 200
        assert fase.cadence.min - 0.5 <= r.cadence <= fase.cadence.max + 0.5
        assert fase.power.min - 0.5 <= r.power <= fase.power.max + 0.5
        assert r.speed >= 0


def test_distancia_no_decrece_y_no_avanza_en_estacion(perfil):
    lecturas = _sesion_completa(_atleta(perfil, seed=5))
    anterior = 0.0
    for r in lecturas:
        assert r.distance >= anterior
        if r.phase_type == "station":
            # En estación la velocidad refleja esfuerzo, no desplazamiento.
            assert r.distance == anterior
        anterior = r.distance


def test_distancia_del_run_respeta_el_recinto(perfil):
    # El run mide 300 m en el perfil: la suma de velocidades debe acercarse.
    lecturas = _sesion_completa(_atleta(perfil, seed=9))
    fin_run = max(r.distance for r in lecturas if r.phase == "run_1")
    assert fin_run == pytest.approx(300, rel=0.1)


def test_hr_offset_desplaza_el_pulso(perfil):
    frio = _sesion_completa(_atleta(perfil, seed=4))
    caliente = _sesion_completa(_atleta(perfil, seed=4, hr_offset=15.0))
    media_frio = sum(r.heart_rate for r in frio) / len(frio)
    media_caliente = sum(r.heart_rate for r in caliente) / len(caliente)
    assert media_caliente - media_frio == pytest.approx(15.0, abs=5.0)


def test_to_dict_serializa_para_mqtt(perfil):
    r = _atleta(perfil).next_reading()
    d = r.to_dict()
    assert d["athlete_id"] == "atleta-test"
    assert d["session_id"] == "sesion-test"
    assert d["phase"] == "run_1"
    assert d["phase_type"] == "run"
    assert d["elapsed_seconds"] == 0
    assert isinstance(d["heart_rate"], int)
    assert isinstance(d["cadence"], int)
    assert isinstance(d["power"], int)
    assert d["speed"] == round(r.speed, 2)
    assert d["distance"] == round(r.distance, 1)
    # ISO 8601 con zona horaria: el procesador lo parsea con fromisoformat.
    assert "T" in d["timestamp"] and "+00:00" in d["timestamp"]

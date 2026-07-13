"""Pruebas de la carga y saneado del perfil de calibración."""

import copy
import json

import pytest

from simulator.stations import SessionProfile

from conftest import PERFIL_MINIMO


def _escribir(tmp_path, raw):
    path = tmp_path / "perfil.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_carga_basica(perfil):
    assert perfil.source == "sintetico"
    assert perfil.total_seconds == 110
    assert perfil.drift_bpm_total == 10.0
    assert [p.name for p in perfil.phases] == ["run_1", "roxzone", "skierg"]
    assert [p.type for p in perfil.phases] == ["run", "roxzone", "station"]


def test_carga_perfil_empaquetado():
    # El perfil por defecto (sin ruta) es el calibrado con las carreras reales.
    perfil = SessionProfile.load()
    assert len(perfil.phases) == 30
    assert {p.type for p in perfil.phases} == {"run", "station", "roxzone"}
    assert perfil.total_seconds > 0
    assert perfil.run_distances and "run_1" in perfil.run_distances


def test_metricas_acotadas_a_cuatro_desviaciones(tmp_path):
    # Un outlier del .fit (velocidad absurda) debe quedar acotado a media ± 4σ.
    raw = copy.deepcopy(PERFIL_MINIMO)
    raw["phases"][0]["speed"] = {"mean": 3.0, "std": 1.0, "min": -50.0, "max": 50.0}
    perfil = SessionProfile.load(_escribir(tmp_path, raw))
    speed = perfil.phases[0].speed
    assert speed.min == pytest.approx(3.0 - 4.0)
    assert speed.max == pytest.approx(3.0 + 4.0)


def test_limites_estrechos_se_respetan(tmp_path):
    # Si los min/max observados ya caen dentro de ±4σ no se tocan.
    raw = copy.deepcopy(PERFIL_MINIMO)
    raw["phases"][0]["speed"] = {"mean": 3.0, "std": 1.0, "min": 2.0, "max": 4.5}
    perfil = SessionProfile.load(_escribir(tmp_path, raw))
    speed = perfil.phases[0].speed
    assert speed.min == 2.0
    assert speed.max == 4.5


def test_distancia_roxzone_es_el_resto_del_recorrido(perfil):
    # 350 m totales - 300 m del run = 50 m para roxzone.
    assert perfil.run_distances == {"run_1": 300.0}
    assert perfil.roxzone_distance == pytest.approx(50.0)


def test_sin_course_no_hay_distancias(tmp_path):
    raw = copy.deepcopy(PERFIL_MINIMO)
    del raw["course"]
    perfil = SessionProfile.load(_escribir(tmp_path, raw))
    assert perfil.run_distances is None
    assert perfil.roxzone_distance is None


def test_roxzone_negativa_queda_a_none(tmp_path):
    # Si los runs suman más que el recorrido total, no se inventa una roxzone.
    raw = copy.deepcopy(PERFIL_MINIMO)
    raw["course"]["total_distance_m"] = 200
    perfil = SessionProfile.load(_escribir(tmp_path, raw))
    assert perfil.roxzone_distance is None


def test_duracion_cv_por_defecto_cero(tmp_path):
    raw = copy.deepcopy(PERFIL_MINIMO)
    del raw["phases"][0]["duration_cv"]
    perfil = SessionProfile.load(_escribir(tmp_path, raw))
    assert perfil.phases[0].duration_cv == 0.0

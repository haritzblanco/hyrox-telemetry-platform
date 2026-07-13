"""Fixtures compartidas: un perfil de calibración sintético y reducido.

El perfil real tiene 30 fases y valores calibrados de carreras reales; para
las pruebas basta una sesión mínima (run + roxzone + estación) con números
redondos que hagan las aserciones legibles.
"""

import json

import pytest

from simulator.stations import SessionProfile


PERFIL_MINIMO = {
    "source": "sintetico",
    "total_seconds": 110,
    "cardiac_drift": {"bpm_total": 10.0},
    "course": {
        "total_distance_m": 350,
        "run_distances_m": {"run_1": 300},
    },
    "phases": [
        {
            "name": "run_1", "type": "run", "duration_s": 60, "duration_cv": 0.0,
            "heart_rate": {"mean": 150.0, "std": 10.0, "min": 100, "max": 190},
            "cadence": {"mean": 85.0, "std": 5.0, "min": 60, "max": 100},
            "power": {"mean": 300.0, "std": 50.0, "min": 100, "max": 500},
            "speed": {"mean": 5.0, "std": 1.0, "min": 0.0, "max": 8.0},
        },
        {
            "name": "roxzone", "type": "roxzone", "duration_s": 10, "duration_cv": 0.3,
            "heart_rate": {"mean": 160.0, "std": 8.0, "min": 120, "max": 190},
            "cadence": {"mean": 40.0, "std": 10.0, "min": 0, "max": 80},
            "power": {"mean": 100.0, "std": 40.0, "min": 0, "max": 300},
            "speed": {"mean": 1.5, "std": 0.5, "min": 0.0, "max": 3.0},
        },
        {
            "name": "skierg", "type": "station", "duration_s": 40, "duration_cv": 0.05,
            "heart_rate": {"mean": 165.0, "std": 6.0, "min": 130, "max": 195},
            "cadence": {"mean": 45.0, "std": 5.0, "min": 20, "max": 70},
            "power": {"mean": 250.0, "std": 30.0, "min": 100, "max": 400},
            "speed": {"mean": 1.2, "std": 0.3, "min": 0.0, "max": 2.5},
        },
    ],
}


@pytest.fixture
def perfil_json(tmp_path):
    """Ruta a un archivo JSON con el perfil sintético."""
    path = tmp_path / "perfil.json"
    path.write_text(json.dumps(PERFIL_MINIMO), encoding="utf-8")
    return path


@pytest.fixture
def perfil(perfil_json):
    """Perfil sintético ya cargado."""
    return SessionProfile.load(perfil_json)

"""Fixtures compartidas del procesador."""

from datetime import datetime, timezone

import pytest


@pytest.fixture
def lectura():
    """Una lectura biométrica como la publica el simulador.

    Espeja el esquema de Reading.to_dict() del simulador: si aquel cambia,
    esta fixture debe cambiar con él (es el contrato entre componentes).
    """
    return {
        "athlete_id": "atleta-001",
        "session_id": "20260712_100000",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": "run_1",
        "phase_type": "run",
        "elapsed_seconds": 42,
        "heart_rate": 155,
        "cadence": 86,
        "power": 310,
        "speed": 3.42,
        "distance": 145.3,
    }

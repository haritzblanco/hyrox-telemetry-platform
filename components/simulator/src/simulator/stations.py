"""Carga de perfiles de calibración y secuencia de fases de una sesión Hyrox."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass
class MetricProfile:
    """Perfil estadístico de una métrica dentro de una fase."""
    mean: float
    std: float
    min: float
    max: float


@dataclass
class Phase:
    """Una fase de la prueba (una carrera o una estación)."""
    name: str
    type: str  # "run" | "station" | "roxzone"
    duration_s: int
    heart_rate: MetricProfile
    cadence: MetricProfile
    power: MetricProfile
    speed: MetricProfile
    # Variabilidad de la duración entre las 3 carreras reales; calibra el ruido.
    duration_cv: float = 0.0


@dataclass
class SessionProfile:
    """Perfil completo de una sesión, cargado desde el archivo de calibración."""
    source: str
    total_seconds: int
    drift_bpm_total: float
    phases: list[Phase]
    # Distancia real de cada run en el recinto (m); si falta se usa la calibrada.
    run_distances: dict[str, float] | None = None
    # Metros de roxzone: recorrido total menos runs, repartidos por duración.
    roxzone_distance: float | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SessionProfile":
        """Carga el perfil desde un archivo JSON.

        Si no se indica ruta, usa el archivo de calibración empaquetado
        con el simulador (derivado de una sesión real de Hyrox Barcelona).
        """
        if path is not None:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        else:
            data_pkg = resources.files("simulator.data")
            raw = json.loads(
                (data_pkg / "hyrox_profile.json").read_text(encoding="utf-8")
            )

        def metric(d: dict | None) -> MetricProfile:
            if d is None:
                return MetricProfile(0, 0, 0, 0)
            mean, std = d["mean"], d["std"]
            # Los min/max del .fit traen outliers (un burpee a 9 m/s); se acotan
            # a 4 desviaciones para que el límite sea físicamente creíble.
            lo, hi = d["min"], d["max"]
            if std > 0:
                lo = max(lo, mean - 4 * std)
                hi = min(hi, mean + 4 * std)
            return MetricProfile(mean=mean, std=std, min=lo, max=hi)

        phases = [
            Phase(
                name=p["name"], type=p["type"], duration_s=p["duration_s"],
                heart_rate=metric(p["heart_rate"]),
                cadence=metric(p["cadence"]),
                power=metric(p["power"]),
                speed=metric(p["speed"]),
                duration_cv=p.get("duration_cv", 0.0),
            )
            for p in raw["phases"]
        ]

        course = raw.get("course") or {}
        run_distances = {
            name: float(dist)
            for name, dist in (course.get("run_distances_m") or {}).items()
        }

        # Metros de roxzone: lo que queda del recorrido total tras restar los runs.
        roxzone_distance = None
        if course.get("total_distance_m") and run_distances:
            rox = float(course["total_distance_m"]) - sum(run_distances.values())
            roxzone_distance = rox if rox > 0 else None

        return cls(
            source=raw["source"],
            total_seconds=raw["total_seconds"],
            drift_bpm_total=raw["cardiac_drift"]["bpm_total"],
            phases=phases,
            run_distances=run_distances or None,
            roxzone_distance=roxzone_distance,
        )
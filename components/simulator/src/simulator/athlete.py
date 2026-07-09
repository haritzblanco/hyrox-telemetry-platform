"""Modelo del atleta Hyrox calibrado con perfiles reales por fase."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone

from simulator.stations import SessionProfile, Phase, MetricProfile


@dataclass
class Reading:
    """Una medida instantánea de un atleta durante la sesión."""
    athlete_id: str
    session_id: str
    timestamp: datetime
    phase: str
    phase_type: str
    elapsed_seconds: int
    heart_rate: int
    cadence: int
    power: int
    speed: float
    distance: float

    def to_dict(self) -> dict:
        return {
            "athlete_id": self.athlete_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "phase": self.phase,
            "phase_type": self.phase_type,
            "elapsed_seconds": self.elapsed_seconds,
            "heart_rate": self.heart_rate,
            "cadence": self.cadence,
            "power": self.power,
            "speed": round(self.speed, 2),
            "distance": round(self.distance, 1),
        }


class Athlete:
    """Atleta que recorre una sesión Hyrox completa.

    Cada señal parte del valor anterior, suma ruido y recibe un empujón hacia
    el valor típico de la fase (AR(1) con reversión a la media), calibrado con
    3 carreras reales. Un atleta se distingue de otro por fitness (ritmo
    global), run_factor/strength_factor (sesgo carrera/fuerza que no altera el
    tiempo total), hr_offset (pulso base) y drift (subida del pulso por fatiga).
    """

    # Ajustados a mano para parecerse a las señales de los .fit reales.
    STEP_STD = 0.3     # ruido por paso (fracción de la desviación de la fase)
    REGRESSION = 0.15  # empujón hacia el valor típico de la fase
    HR_TRACK = 0.1     # velocidad a la que el pulso persigue su objetivo
    HR_NOISE = 0.25    # ruido del pulso (fracción de la desviación de la fase)

    def __init__(
        self,
        athlete_id: str,
        session_id: str,
        profile: SessionProfile,
        seed: int | None = None,
        fitness: float = 1.0,
        run_factor: float = 1.0,
        strength_factor: float = 1.0,
        hr_offset: float = 0.0,
        drift: float | None = None,
    ) -> None:
        self.athlete_id = athlete_id
        self.session_id = session_id
        self.profile = profile
        self.fitness = fitness
        self.run_factor = run_factor
        self.strength_factor = strength_factor
        self.hr_offset = hr_offset
        self._rng = random.Random(seed)
        self._drift_total = profile.drift_bpm_total if drift is None else drift

        self._elapsed = 0
        self._distance = 0.0
        self._phase_idx = 0
        self._phase_elapsed = 0

        # Duración de cada fase: la calibrada, escalada por el ritmo del atleta
        # y con ruido según la variabilidad real de esa fase (las roxzone, sin
        # ruido: son cortas y demasiado variables).
        self._durations: list[int] = []
        for p in profile.phases:
            axis = fitness * (strength_factor if p.type == "station" else run_factor)
            noise = 1.0
            if p.duration_cv and p.type != "roxzone":
                noise = self._rng.gauss(1.0, p.duration_cv)
                noise = max(1 - 2 * p.duration_cv, min(1 + 2 * p.duration_cv, noise))
            self._durations.append(max(1, int(round(p.duration_s * axis * noise))))
        self._total = sum(self._durations)

        # Las medias calibradas ya traen la fatiga de la carrera real: se resta
        # esa deriva y al simular se recompone con la del atleta.
        self._hr_base: list[float] = []
        t = 0.0
        for p in profile.phases:
            mid = (t + p.duration_s / 2) / profile.total_seconds
            self._hr_base.append(p.heart_rate.mean - profile.drift_bpm_total * mid)
            t += p.duration_s

        self._hr = float(self._hr_base[0] + hr_offset)

        # La distancia es geometría, no estadística: cada run mide lo que mide
        # el recinto y el atleta rápido lo cubre a más velocidad. La roxzone se
        # lleva el resto del recorrido total; las estaciones no suman metros.
        run_distances = profile.run_distances or {}
        rox_total_dist = profile.roxzone_distance or 0.0
        rox_total_base = sum(p.duration_s for p in profile.phases if p.type == "roxzone") or 1
        self._move_speed: list[float | None] = []  # objetivo de velocidad en runs/roxzone
        for i, p in enumerate(profile.phases):
            if p.type == "run":
                run_distance = run_distances.get(p.name) or (p.duration_s * p.speed.mean)
                self._move_speed.append(run_distance / self._durations[i])
            elif p.type == "roxzone":
                rox_distance = rox_total_dist * (p.duration_s / rox_total_base)
                self._move_speed.append(rox_distance / self._durations[i])
            else:  # station
                self._move_speed.append(None)

    @property
    def finished(self) -> bool:
        return self._phase_idx >= len(self.profile.phases)

    @property
    def total_seconds(self) -> int:
        return self._total

    def _walk(self, current: float, prof: MetricProfile, target: float | None = None) -> float:
        """Un paso de la señal: ruido más empujón hacia el objetivo (por
        defecto la media de la fase), acotado a los límites del perfil."""
        mean = prof.mean if target is None else target
        step = self._rng.gauss(0, prof.std * self.STEP_STD)
        regression = (mean - current) * self.REGRESSION
        value = current + step + regression
        return max(prof.min, min(prof.max, value))

    def next_reading(self) -> Reading | None:
        """Genera la siguiente lectura, o None si la sesión ha terminado."""
        if self.finished:
            return None

        phase: Phase = self.profile.phases[self._phase_idx]

        # El pulso persigue su objetivo con suavizado exponencial y ruido.
        progress = self._elapsed / self._total
        hr_target = self._hr_base[self._phase_idx] + self._drift_total * progress + self.hr_offset
        self._hr += (hr_target - self._hr) * self.HR_TRACK \
            + self._rng.gauss(0, phase.heart_rate.std * self.HR_NOISE)
        self._hr = max(60, min(200, self._hr))

        cadence = self._walk(phase.cadence.mean, phase.cadence)
        power = self._walk(phase.power.mean, phase.power)

        move_target = self._move_speed[self._phase_idx]
        if move_target is not None:
            speed = self._walk(move_target, phase.speed, target=move_target)
            self._distance += speed  # 1 lectura por segundo: la velocidad son metros
        else:
            # En estación la velocidad refleja esfuerzo, no desplazamiento.
            speed = self._walk(phase.speed.mean, phase.speed)

        reading = Reading(
            athlete_id=self.athlete_id,
            session_id=self.session_id,
            timestamp=datetime.now(timezone.utc),
            phase=phase.name,
            phase_type=phase.type,
            elapsed_seconds=self._elapsed,
            heart_rate=round(self._hr),
            cadence=round(cadence),
            power=round(power),
            speed=speed,
            distance=self._distance,
        )

        self._elapsed += 1
        self._phase_elapsed += 1
        if self._phase_elapsed >= self._durations[self._phase_idx]:
            self._phase_idx += 1
            self._phase_elapsed = 0

        return reading

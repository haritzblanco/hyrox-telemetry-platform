"""Punto de entrada del simulador."""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone

from simulator.athlete import Athlete
from simulator.publisher import MqttPublisher
from simulator.stations import SessionProfile


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulador de un atleta Hyrox calibrado, publicando en MQTT",
    )
    parser.add_argument("--athlete-id", default="atleta-001")
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Segundos reales entre publicaciones (default: %(default)s)")
    parser.add_argument("--speedup", type=float, default=1.0,
                        help="Factor de aceleración temporal: 60 = 1 min de carrera por segundo")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--fitness", type=float, default=1.0,
                        help="Ritmo global: <1 atleta más en forma, >1 menos")
    parser.add_argument("--run-factor", type=float, default=1.0,
                        help="Multiplicador del ritmo en runs/roxzone (eje carrera)")
    parser.add_argument("--strength-factor", type=float, default=1.0,
                        help="Multiplicador del ritmo en estaciones (eje fuerza)")
    parser.add_argument("--hr-offset", type=float, default=0.0,
                        help="Desplazamiento del pulso basal (sale más caliente/frío), bpm")
    parser.add_argument("--drift", type=float, default=None,
                        help="Deriva cardiaca total de la sesión (bpm). Por defecto la del perfil")
    parser.add_argument("--profile", default=None,
                        help="Ruta a un perfil de calibración alternativo (JSON)")
    parser.add_argument("--loop", action="store_true",
                        help="Reiniciar la sesión al terminar (útil para pruebas de carga)")
    parser.add_argument("--session-id", default=None,
                        help="ID de sesión compartida (útil para sincronizar varios atletas)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    profile = SessionProfile.load(args.profile)
    sleep_time = args.interval / args.speedup

    publisher = MqttPublisher(
        host=args.broker_host, port=args.broker_port,
        client_id=f"hyrox-sim-{args.athlete_id}", qos=args.qos,
    )

    logger.info(
        "Simulador para %s | perfil=%s | %d fases | %ds de sesión | speedup=%.0fx",
        args.athlete_id, profile.source, len(profile.phases),
        profile.total_seconds, args.speedup,
    )

    published = 0
    with publisher:
        try:
            while True:
                session_id = args.session_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                logger.info("Nueva sesión: %s", session_id)
                athlete = Athlete(
                    athlete_id=args.athlete_id, session_id=session_id,
                    profile=profile, seed=args.seed,
                    fitness=args.fitness, run_factor=args.run_factor,
                    strength_factor=args.strength_factor,
                    hr_offset=args.hr_offset, drift=args.drift,
                )
                while not athlete.finished:
                    reading = athlete.next_reading()
                    publisher.publish(reading)
                    published += 1
                    logger.debug("%s", reading.to_dict())
                    time.sleep(sleep_time)
                logger.info("Sesión completada para %s", args.athlete_id)
                if not args.loop:
                    break
        except KeyboardInterrupt:
            logger.info("Simulador detenido por el usuario")

    # Resumen para las pruebas de carga: publicado vs persistido.
    print(json.dumps({
        "kind": "sim",
        "athlete_id": args.athlete_id,
        "session_id": args.session_id or "",
        "published": published,
    }), flush=True)


if __name__ == "__main__":
    main()
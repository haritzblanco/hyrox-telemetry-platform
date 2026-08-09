"""Punto de entrada del simulador."""

from __future__ import annotations

import argparse
import contextlib
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
        description="Simulador de atletas Hyrox calibrados, publicando en MQTT",
    )
    parser.add_argument("--athletes", type=int, default=1,
                        help="Atletas simulados por este proceso, cada uno con su "
                             "propia conexión MQTT (default: %(default)s)")
    parser.add_argument("--athlete-id", default="atleta-001",
                        help="Id del atleta cuando --athletes es 1")
    parser.add_argument("--athlete-prefix", default="atleta",
                        help="Prefijo de los ids con --athletes > 1: <prefijo>-001..N")
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--broker-password", default=None,
                        help="Contraseña de dispositivo compartida por los atletas. "
                             "El usuario es el propio id del atleta.")
    parser.add_argument("--broker-ca", default=None,
                        help="Ruta al certificado de la CA para validar el broker "
                             "por TLS. Si se indica, la conexión va cifrada.")
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


def athlete_specs(args: argparse.Namespace) -> list[tuple[str, int | None]]:
    """Ids y semillas de los atletas de este proceso.

    Con un solo atleta se respeta --athlete-id; con varios se numeran con el
    prefijo. La semilla base se desplaza por atleta: pelotón variado pero
    reproducible.
    """
    if args.athletes <= 1:
        return [(args.athlete_id, args.seed)]
    return [
        (f"{args.athlete_prefix}-{i:03d}", None if args.seed is None else args.seed + i)
        for i in range(1, args.athletes + 1)
    ]


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    profile = SessionProfile.load(args.profile)
    sleep_time = args.interval / args.speedup
    specs = athlete_specs(args)

    logger.info(
        "Simulador de %d atleta(s) | perfil=%s | %d fases | %ds de sesión | speedup=%.0fx",
        len(specs), profile.source, len(profile.phases),
        profile.total_seconds, args.speedup,
    )

    # Una conexión MQTT por atleta, como en un evento real (cada dispositivo
    # mantiene la suya): el broker ve la misma topología de clientes con 1 o
    # con N atletas por proceso.
    publishers = {
        athlete_id: MqttPublisher(
            host=args.broker_host, port=args.broker_port,
            client_id=f"hyrox-sim-{athlete_id}", qos=args.qos,
            username=athlete_id, password=args.broker_password,
            ca_certs=args.broker_ca,
        )
        for athlete_id, _ in specs
    }

    with contextlib.ExitStack() as stack:
        for publisher in publishers.values():
            stack.enter_context(publisher)
        try:
            while True:
                session_id = args.session_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                logger.info("Nueva sesión: %s", session_id)
                pending = [
                    Athlete(
                        athlete_id=athlete_id, session_id=session_id,
                        profile=profile, seed=seed,
                        fitness=args.fitness, run_factor=args.run_factor,
                        strength_factor=args.strength_factor,
                        hr_offset=args.hr_offset, drift=args.drift,
                    )
                    for athlete_id, seed in specs
                ]
                # Pacing por plazos absolutos: el sueño descuenta lo que costó
                # publicar el tick, para que el ritmo no derive con N atletas.
                next_tick = time.monotonic()
                while pending:
                    for athlete in pending:
                        reading = athlete.next_reading()
                        publishers[athlete.athlete_id].publish(reading)
                        logger.debug("%s", reading.to_dict())
                    pending = [a for a in pending if not a.finished]
                    next_tick += sleep_time
                    delay = next_tick - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                logger.info("Sesión completada (%d atletas)", len(specs))
                if not args.loop:
                    break
        except KeyboardInterrupt:
            logger.info("Simulador detenido por el usuario")

    # Resumen para las pruebas de carga, una línea por atleta. La carga
    # ofrecida real es `acked` (confirmado por el broker); `published` solo
    # dice cuánto encoló el cliente y sobreestima si el generador va saturado.
    for athlete_id, _ in specs:
        publisher = publishers[athlete_id]
        print(json.dumps({
            "kind": "sim",
            "athlete_id": athlete_id,
            "session_id": args.session_id or "",
            "published": publisher.enqueued,
            "acked": publisher.acked,
            # Retardo publicación → PUBACK: cuánto del retardo total lo pone el
            # generador antes de que la lectura entre en la plataforma.
            "ack_latency_ms": publisher.ack_latency_stats(),
        }), flush=True)


if __name__ == "__main__":
    main()

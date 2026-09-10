"""Banco de la tubería de escritura del cliente de InfluxDB, sin clúster.

Reproduce en local el descarte silencioso que sufre el procesador: ejerce el
WriteApi real en modo lote con el POST HTTP sustituido por un contador, de modo
que lo único bajo prueba es la tubería reactivex del cliente. Una sonda sobre
Subject.on_next clasifica cada punto perdido según la vía por la que se fue.

    python bench_write_api.py --puntos 12000 --tasa 800 --quemadores 4 --switch 1e-6

Los quemadores son hilos que compiten por el GIL: imitan la falta de CPU de una
réplica estrangulada, que es lo que multiplica el descarte. `--switch` ajusta
sys.setswitchinterval para ensanchar la ventana de carrera y hacerla visible en
corridas cortas.

Diagnóstico completo en results/20260910_*_causa_raiz/validation.md.
"""

import argparse
import sys
import threading
import time

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import WriteApi, WriteOptions
from reactivex.subject import Subject


def _puntos(cuerpo) -> int:
    if isinstance(cuerpo, bytes):
        cuerpo = cuerpo.decode("utf-8", "ignore")
    return sum(1 for linea in cuerpo.split("\n") if linea.strip())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--puntos", type=int, default=12000)
    p.add_argument("--tasa", type=float, default=800, help="msg/s ofrecidos (0 = sin freno)")
    p.add_argument("--quemadores", type=int, default=0, help="hilos que compiten por el GIL")
    p.add_argument("--switch", type=float, default=None, help="sys.setswitchinterval")
    p.add_argument("--batch", type=int, default=500, help="WriteOptions.batch_size")
    p.add_argument("--flush", type=int, default=500, help="WriteOptions.flush_interval (ms)")
    args = p.parse_args()

    if args.switch is not None:
        sys.setswitchinterval(args.switch)

    # Sonda: clasifica los on_next que no llegan a ningún suscriptor. La ventana
    # del operador es un Subject que el temporizador cierra y sustituye sin
    # sincronizar con el productor, así que un punto puede caer en la ventana ya
    # cerrada o en la nueva antes de que el resto de la tubería se suscriba a
    # ella. En ambos casos el Subject lo descarta sin error.
    sonda = {"cerrada": 0, "sin_suscriptor": 0}
    on_next_original = Subject.on_next

    def on_next_con_sonda(self, valor):
        if self.is_stopped:
            sonda["cerrada"] += 1
        elif not self.observers:
            sonda["sin_suscriptor"] += 1
        return on_next_original(self, valor)

    Subject.on_next = on_next_con_sonda

    # El POST se sustituye por un contador: aísla la tubería del cliente de la
    # red y de InfluxDB.
    enviados = [0]
    confirmados = [0]
    cerrojo = threading.Lock()

    def post_contador(self, _async_req, bucket, org, body, precision, **kwargs):
        with cerrojo:
            enviados[0] += _puntos(body)
        return None

    WriteApi._post_write = post_contador

    def al_confirmar(conf, data):
        with cerrojo:
            confirmados[0] += _puntos(data)

    def al_fallar(conf, data, excepcion):
        print(f"error de escritura: {excepcion}")

    cliente = InfluxDBClient(url="http://localhost:9999", token="sin-uso", org="hyrox")
    api = cliente.write_api(
        write_options=WriteOptions(
            batch_size=args.batch,
            flush_interval=args.flush,
            jitter_interval=0,
            retry_interval=2000,
            max_retries=5,
            max_retry_delay=15000,
            exponential_base=2,
        ),
        success_callback=al_confirmar,
        error_callback=al_fallar,
    )

    parar = threading.Event()

    def quemar():
        x = 0
        while not parar.is_set():
            x = (x * 31 + 7) % 1000003

    for _ in range(args.quemadores):
        threading.Thread(target=quemar, daemon=True).start()

    linea = (
        "biometrics,athlete_id=a1,session_id=s,phase=run,phase_type=run "
        "heart_rate=150i,cadence=80i,power=200i,speed=3.0,distance=1.0,"
        "elapsed_seconds=%di %d"
    )
    t0 = time.monotonic()
    for i in range(args.puntos):
        api.write(bucket="hyrox", org="hyrox", record=linea % (i, 1700000000000000000 + i * 1000000))
        if args.tasa:
            espera = t0 + (i + 1) / args.tasa - time.monotonic()
            if espera > 0:
                time.sleep(espera)
    duracion = time.monotonic() - t0

    # Los quemadores paran antes de cerrar: con ellos girando, el drenaje final
    # del cliente se queda sin turnos de GIL y close() no vuelve.
    parar.set()
    time.sleep(0.2)
    api.close()
    cliente.close()

    perdidos = args.puntos - enviados[0]
    print(f"encolados      {args.puntos}")
    print(f"enviados       {enviados[0]}")
    print(f"confirmados    {confirmados[0]}")
    print(f"PERDIDOS       {perdidos}  ({perdidos * 100 / args.puntos:.3f} %)")
    print(f"  a ventana ya cerrada      {sonda['cerrada']}")
    print(f"  a ventana sin suscriptor  {sonda['sin_suscriptor']}")
    print(f"duración {duracion:.1f} s -> {args.puntos / duracion:.0f} msg/s")


if __name__ == "__main__":
    main()

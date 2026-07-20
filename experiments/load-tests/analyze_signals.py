"""Resume una corrida de compare_signals.py: reacción y dimensionado por señal.

Lee samples_<label>.csv y result_<label>.json de un directorio y calcula, para
cada señal (caudal del broker y CPU del procesador), cuándo habría ordenado la
primera réplica extra (tiempo de reacción) y qué dimensionado sostiene en
régimen. La comparación sale de la misma corrida, sin varianza entre
ejecuciones.
"""

import argparse
import csv
import json
import statistics as st
from pathlib import Path


def first_cross(rows, key, level):
    for r in rows:
        if int(r[key]) >= level:
            return float(r["t"])
    return None


def summarize(outdir, label):
    d = Path(outdir)
    rows = list(csv.DictReader(open(d / f"samples_{label}.csv")))
    result = json.loads((d / f"result_{label}.json").read_text())

    # Régimen: último tercio de la corrida.
    tail = rows[len(rows) * 2 // 3:]
    caudal_vals = [float(r["caudal_msg_s"]) for r in tail if r["caudal_msg_s"]]
    cpu_vals = [int(r["cpu_total_m"]) for r in tail]

    print(f"=== {label}  (~{result['nominal_msg_s']} msg/s nominales, "
          f"hold {result['hold_s']}s) ===")
    print(f"carga confirmada (acked): {result['acked']}  "
          f"persistida: {result['persisted']}  perdida: {result['loss_pct']}%")
    print()
    print("Tiempo de reaccion (primera vez que cada senal pide N replicas):")
    for level in (2, 3, 4):
        tc = first_cross(rows, "caudal_cmd", level)
        tp = first_cross(rows, "cpu_cmd", level)
        print(f"  -> {level} replicas:  caudal {fmt(tc):>8}   cpu {fmt(tp):>8}")
    print()
    print("Regimen (mediana del ultimo tercio):")
    print(f"  caudal          : {st.median(caudal_vals):7.1f} msg/s "
          f"-> ordena {med_cmd(tail, 'caudal_cmd')} replicas")
    print(f"  cpu total       : {st.median(cpu_vals):7.0f} m     "
          f"-> ordena {med_cmd(tail, 'cpu_cmd')} replicas")
    print(f"  replicas reales : {med_cmd(tail, 'replicas')}  "
          f"(las decide max(caudal,cpu) del ScaledObject de produccion)")
    print()


def med_cmd(rows, key):
    return int(st.median([int(r[key]) for r in rows]))


def fmt(t):
    return "nunca" if t is None else f"{t:.0f}s"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--labels", nargs="+", required=True)
    args = ap.parse_args()
    for label in args.labels:
        summarize(args.outdir, label)

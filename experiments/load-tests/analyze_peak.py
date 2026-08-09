#!/usr/bin/env python3
"""Analiza la corrida al pico de diseño y la contrasta con los umbrales.

Toma el directorio que deja run_peak.sh y resume las tres magnitudes con
umbral en el capítulo de requisitos:

    RNF-2  latencia emisión → dato consultable, p95 < 2000 ms
    RNF-3  pérdida < 0,5 % de lo confirmado por el broker
    RNF-1  el escalado sigue al caudal (línea temporal de réplicas)

La latencia se mide en dos tramos con relojes distintos (transporte entre
máquinas, persistencia intra-proceso), así que no hay una marca por mensaje
que recorra los dos. La cifra de extremo a extremo se acota por la suma de
ambos p95, que es una cota superior: el p95 de la suma nunca la excede.

El p95 de cada tramo es la MEDIANA de los p95 por ventana, igual que en
notebooks/analyze.py, para que una ventana atípica (un reintento de InfluxDB)
no arrastre el resultado. El peor p95 por ventana se conserva aparte.

Uso:
    python3 analyze_peak.py experiments/results/<stamp>_peak
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

LAT_THRESHOLD_MS = 2000.0
LOSS_THRESHOLD_PCT = 0.5


def _windows(outdir: Path) -> list[dict]:
    """Ventanas de métricas de todas las réplicas, en orden temporal."""
    out: list[dict] = []
    for f in sorted(outdir.glob("proc_*.jsonl")):
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "metrics":
                rec["_pod"] = f.stem.removeprefix("proc_")
                out.append(rec)
    return sorted(out, key=lambda r: r.get("ts", ""))


def _tramo(windows: list[dict], key: str) -> dict:
    """Resumen de un tramo de latencia a partir de sus ventanas."""
    p95s, p99s, means, weights, peak = [], [], [], [], 0.0
    for w in windows:
        s = w.get(key)
        if not s or not s.get("n"):
            continue
        p95s.append(s["p95"])
        p99s.append(s["p99"])
        means.append(s["mean"] * s["n"])
        weights.append(s["n"])
        peak = max(peak, s.get("max", 0.0))
    if not p95s:
        return {"n": 0}
    return {
        "n": sum(weights),
        "mean": round(sum(means) / sum(weights), 1),
        "p95": round(statistics.median(p95s), 1),
        "p99": round(statistics.median(p99s), 1),
        "p95_worst": round(max(p95s), 1),
        "max": round(peak, 1),
    }


def _replica_timeline(outdir: Path) -> list[tuple[str, int, str]]:
    """Cambios en el número de réplicas listas, con el caudal que los motiva."""
    path = outdir / "replicas.csv"
    if not path.exists():
        return []
    changes, last = [], None
    with path.open() as fh:
        for row in csv.DictReader(fh):
            try:
                ready = int(row.get("ready_replicas") or 0)
            except ValueError:
                continue
            if ready != last:
                changes.append((row["ts"], ready, row.get("keda_metric") or "-"))
                last = ready
    return changes


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    outdir = Path(sys.argv[1])
    run = json.loads((outdir / "run.json").read_text())
    windows = _windows(outdir)

    offered, delivered = run["offered"], run["delivered"]
    loss_pct = 100.0 * (offered - delivered) / offered if offered else 0.0

    transport = _tramo(windows, "lat_transport_ms")
    persist = _tramo(windows, "lat_persist_ms")
    e2e_p95 = (transport.get("p95") or 0.0) + (persist.get("p95") or 0.0)

    consumed = sum(w.get("consumed", 0) for w in windows)
    acked = sum(w.get("acked", 0) for w in windows)
    errors = sum(w.get("errors", 0) for w in windows)
    pods = sorted({w["_pod"] for w in windows})
    timeline = _replica_timeline(outdir)

    print(f"=== Corrida al pico de diseño: {outdir.name} ===")
    print(f"Carga nominal: {run['target_rate_msg_s']} msg/s "
          f"({run['athletes']} atletas × speedup {run['speedup']}), escalado {run['scaling']}")
    print(f"Réplicas que emitieron métricas: {len(pods)}")
    print()

    print("── RNF-3. Fiabilidad de la entrega ──")
    print(f"  encolado en el cliente : {run['enqueued']}")
    print(f"  ofrecido (PUBACK)      : {offered}")
    print(f"  persistido (InfluxDB)  : {delivered}")
    print(f"  pérdida                : {offered - delivered}  ({loss_pct:.2f} %)")
    print(f"  consumido/confirmado por el procesador: {consumed}/{acked}  (errores de escritura: {errors})")
    verdict = "CUMPLE" if loss_pct < LOSS_THRESHOLD_PCT else "NO CUMPLE"
    print(f"  umbral < {LOSS_THRESHOLD_PCT} %  →  {verdict}")
    print()

    print("── RNF-2. Latencia ──")
    for nombre, t in (("transporte  (emisión → consumo)", transport),
                      ("persistencia (encolado → confirmación)", persist)):
        if not t.get("n"):
            print(f"  {nombre}: sin muestras")
            continue
        print(f"  {nombre}: n={t['n']}  media={t['mean']} ms  "
              f"p95={t['p95']} ms  p99={t['p99']} ms  (peor ventana p95={t['p95_worst']} ms)")
    print(f"  extremo a extremo (cota superior, suma de p95): {e2e_p95:.1f} ms")
    verdict = "CUMPLE" if e2e_p95 < LAT_THRESHOLD_MS else "NO CUMPLE"
    print(f"  umbral p95 < {LAT_THRESHOLD_MS:.0f} ms  →  {verdict}")
    print()

    print("── RNF-1. Escalado ──")
    if timeline:
        for ts, ready, metric in timeline:
            print(f"  {ts}  réplicas={ready}  caudal KEDA={metric}")
    else:
        print("  sin muestreo de réplicas")

    summary = {
        "run": run,
        "loss_pct": round(loss_pct, 3),
        "lat_transport_ms": transport,
        "lat_persist_ms": persist,
        "lat_e2e_p95_upper_ms": round(e2e_p95, 1),
        "consumed": consumed,
        "acked": acked,
        "write_errors": errors,
        "pods": pods,
        "replica_timeline": [{"ts": t, "replicas": r, "keda_metric": m} for t, r, m in timeline],
        "thresholds": {"lat_p95_ms": LAT_THRESHOLD_MS, "loss_pct": LOSS_THRESHOLD_PCT},
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nResumen en {outdir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

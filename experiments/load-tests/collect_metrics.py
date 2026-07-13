#!/usr/bin/env python3
"""Extrae las líneas de métricas del processor para una ventana temporal.

Lee `kubectl logs` de un pod (o de stdin) y emite solo las líneas JSON de
métricas (`{"kind":"metrics",...}`) cuyo campo `ts` cae dentro de [start, end].
Así cada corrida se queda con las ventanas que le corresponden aunque el pod
lleve emitiendo desde antes.

Uso:
    # Desde kubectl (recoge el pod indicado):
    python collect_metrics.py --pod processor-xxx --namespace hyrox \
        --start 2026-06-16T10:00:00+00:00 --end 2026-06-16T10:01:30+00:00 \
        > proc_processor-xxx.jsonl

    # O por tubería:
    kubectl logs <pod> -n hyrox | python collect_metrics.py --start ... --end ...
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pod", help="Pod del processor. Si se omite, lee de stdin.")
    ap.add_argument("--namespace", default="hyrox")
    ap.add_argument("--start", required=True, help="Inicio de la ventana (ISO 8601).")
    ap.add_argument("--end", required=True, help="Fin de la ventana (ISO 8601).")
    args = ap.parse_args()

    start, end = _parse_iso(args.start), _parse_iso(args.end)

    if args.pod:
        # --since-time acota el volcado en origen; el filtrado fino lo hace el ts.
        proc = subprocess.run(
            ["kubectl", "logs", args.pod, "-n", args.namespace,
             f"--since-time={args.start}"],
            capture_output=True, text=True, check=False,
        )
        lines = proc.stdout.splitlines()
    else:
        lines = sys.stdin.read().splitlines()

    kept = 0
    for line in lines:
        line = line.strip()
        if '"kind": "metrics"' not in line and '"kind":"metrics"' not in line:
            continue
        try:
            rec = json.loads(line)
            ts = _parse_iso(rec["ts"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if start <= ts <= end:
            sys.stdout.write(json.dumps(rec) + "\n")
            kept += 1

    print(f"[collect_metrics] {kept} ventanas en [{args.start}, {args.end}]",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

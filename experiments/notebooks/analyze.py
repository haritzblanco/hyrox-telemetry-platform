#!/usr/bin/env python3
"""Agrega los resultados de una tanda de experimentos y genera las figuras.

Lee un directorio `experiments/results/<timestamp>` con una subcarpeta por
corrida (`R{r}_N{n}`) y produce:

  - `summary.csv`     una fila por corrida (réplicas, carga, throughput,
                      latencias, pérdida, CPU/memoria).
  - `figures/*.png`   latencia vs carga, throughput, pérdida y recursos.

Uso:
    python experiments/notebooks/analyze.py experiments/results/<timestamp>

Requiere pandas y matplotlib. Sobre las latencias: la instrumentación emite un
RESUMEN por ventana (no las muestras crudas), así que aquí la media se pondera
por nº de muestras y el percentil de cola (p95/p99) se toma como el MÁXIMO entre
ventanas (cota conservadora del peor momento de la corrida).
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

try:
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("Faltan dependencias: pip install pandas matplotlib")


def _load_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _agg_latency(windows: list[dict], key: str) -> dict:
    """Agrega los resúmenes por ventana de una corrida.

    `mean` se pondera por nº de muestras. La cola (`p95`/`p99`) se toma como la
    MEDIANA de los percentiles por ventana → la cola REPRESENTATIVA del régimen
    estable, sin que una ventana atípica aislada (p.ej. un reintento puntual de
    InfluxDB) la dispare. `p95_worst` y `max` conservan el peor caso observado.
    """
    n_tot = mean_acc = 0.0
    p95s: list[float] = []
    p99s: list[float] = []
    maxs: list[float] = []
    for w in windows:
        s = w.get(key)
        if not s:
            continue
        n = s.get("n", 0)
        if n <= 0:
            continue
        n_tot += n
        mean_acc += s.get("mean", 0.0) * n
        p95s.append(s.get("p95", 0.0))
        p99s.append(s.get("p99", 0.0))
        maxs.append(s.get("max", 0.0))
    if not n_tot:
        return {"mean": None, "p95": None, "p99": None, "p95_worst": None, "max": None}
    return {
        "mean": round(mean_acc / n_tot, 2),
        "p95": round(statistics.median(p95s), 2),
        "p99": round(statistics.median(p99s), 2),
        "p95_worst": round(max(p95s), 2),
        "max": round(max(maxs), 2),
    }


def aggregate_run(rundir: Path) -> dict | None:
    meta_path = rundir / "run.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())

    # ventanas de métricas de todas las réplicas
    windows: list[dict] = []
    for jf in rundir.glob("proc_*.jsonl"):
        windows.extend(_load_jsonl(jf))

    acked = sum(w.get("acked", 0) for w in windows)
    consumed = sum(w.get("consumed", 0) for w in windows)
    errors = sum(w.get("errors", 0) for w in windows)

    # throughput agregado: total confirmado / span temporal de las ventanas
    ts = sorted(pd.to_datetime(w["ts"]) for w in windows if "ts" in w)
    span_s = (ts[-1] - ts[0]).total_seconds() if len(ts) > 1 else 0.0
    thr_acked = round(acked / span_s, 1) if span_s > 0 else None

    transport = _agg_latency(windows, "lat_transport_ms")
    persist = _agg_latency(windows, "lat_persist_ms")

    offered = meta.get("offered", 0)
    delivered = meta.get("delivered", 0)
    loss_pct = round(100.0 * (offered - delivered) / offered, 2) if offered else None

    # recursos: CPU/memoria totales (suma de réplicas) por instante de muestreo
    cpu_peak = cpu_mean = mem_peak = mem_mean = None
    rcsv = rundir / "resources.csv"
    if rcsv.exists():
        df = pd.read_csv(rcsv)
        if not df.empty:
            tot = df.groupby("ts_unix").agg(cpu_m=("cpu_m", "sum"),
                                            mem_mi=("mem_mi", "sum"))
            cpu_peak, cpu_mean = float(tot.cpu_m.max()), round(float(tot.cpu_m.mean()), 1)
            mem_peak, mem_mean = float(tot.mem_mi.max()), round(float(tot.mem_mi.mean()), 1)

    return {
        "run": meta["run"],
        "replicas": meta["replicas"],
        "athletes": meta["athletes"],
        "target_rate_msg_s": meta["target_rate_msg_s"],
        "offered": offered,
        "delivered": delivered,
        "loss_pct": loss_pct,
        "consumed": consumed,
        "acked": acked,
        "write_errors": errors,
        "thr_acked_s": thr_acked,
        "lat_transport_mean_ms": transport["mean"],
        "lat_transport_p95_ms": transport["p95"],
        "lat_transport_p99_ms": transport["p99"],
        "lat_transport_p95worst_ms": transport["p95_worst"],
        "lat_persist_mean_ms": persist["mean"],
        "lat_persist_p95_ms": persist["p95"],
        "lat_persist_p99_ms": persist["p99"],
        "lat_persist_p95worst_ms": persist["p95_worst"],
        "cpu_peak_m": cpu_peak,
        "cpu_mean_m": cpu_mean,
        "mem_peak_mi": mem_peak,
        "mem_mean_mi": mem_mean,
    }


def _plot_by_replicas(df, x, y, xlabel, ylabel, title, path, ref_diag=False):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for r, g in df.sort_values(x).groupby("replicas"):
        ax.plot(g[x], g[y], marker="o", label=f"{r} réplica(s)")
    if ref_diag:
        lim = [0, df[x].max()]
        ax.plot(lim, lim, "--", color="gray", linewidth=1, label="ideal (sin pérdida)")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit("uso: analyze.py experiments/results/<timestamp>")
    base = Path(sys.argv[1])
    if not base.is_dir():
        sys.exit(f"No existe el directorio: {base}")

    rows = [r for d in sorted(base.iterdir()) if d.is_dir()
            and (r := aggregate_run(d)) is not None]
    if not rows:
        sys.exit("No se encontraron corridas con run.json en ese directorio.")

    df = pd.DataFrame(rows)
    df.to_csv(base / "summary.csv", index=False)
    print(df.to_string(index=False))

    figdir = base / "figures"
    figdir.mkdir(exist_ok=True)

    _plot_by_replicas(df, "target_rate_msg_s", "lat_transport_p95_ms",
                      "Carga ofrecida (msg/s)", "Latencia transporte p95 (ms)",
                      "Latencia de transporte vs carga", figdir / "latency_transport.png")
    _plot_by_replicas(df, "target_rate_msg_s", "lat_persist_p95_ms",
                      "Carga ofrecida (msg/s)", "Latencia persistencia p95 (ms)",
                      "Latencia de persistencia vs carga", figdir / "latency_persist.png")
    _plot_by_replicas(df, "target_rate_msg_s", "thr_acked_s",
                      "Carga ofrecida (msg/s)", "Throughput confirmado (msg/s)",
                      "Throughput alcanzado vs carga", figdir / "throughput.png", ref_diag=True)
    _plot_by_replicas(df, "target_rate_msg_s", "loss_pct",
                      "Carga ofrecida (msg/s)", "Pérdida (%)",
                      "Pérdida vs carga", figdir / "loss.png")
    if df["cpu_peak_m"].notna().any():
        _plot_by_replicas(df, "target_rate_msg_s", "cpu_peak_m",
                          "Carga ofrecida (msg/s)", "CPU pico total (millicores)",
                          "Consumo de CPU vs carga", figdir / "cpu.png")

    print(f"\nResumen → {base/'summary.csv'}\nFiguras → {figdir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

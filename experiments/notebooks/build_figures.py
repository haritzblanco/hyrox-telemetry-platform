#!/usr/bin/env python3
"""Figuras del capítulo de evaluación de la memoria.

A diferencia de `analyze.py`, que dibuja para explorar una campaña recién
corrida, este script produce las figuras DEFINITIVAS que se insertan en el
documento: mismo estilo en todas, sin título embebido (lo lleva el pie de
figura) y a partir de los datasets que se consideran válidos, que no son todos
los que hay en `results/`.

Uso:
    python3 experiments/notebooks/build_figures.py \
        --matrix experiments/results/<matriz> \
        --peak-cold experiments/results/20260809_192126_target300_cold3 \
        --peak-pre experiments/results/20260806_204127_peak

Cada figura se escribe en docs/figuras/cap10/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import _load_jsonl, aggregate_run  # noqa: E402

# Paleta categórica validada para daltonismo (deutan/protan/tritan) sobre fondo
# claro. Cada serie lleva además un marcador propio, de modo que la identidad no
# dependa solo del color: la memoria puede acabar impresa en blanco y negro.
SERIE = {1: "#2a78d6", 2: "#eb6834", 4: "#1baf7a"}
MARCA = {1: "o", 2: "s", 4: "^"}
TINTA = "#1b1b1b"
TINTA_TENUE = "#6b6b6b"
REFERENCIA = "#9a9a9a"

plt.rcParams.update({
    "figure.dpi": 200,
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
    "font.size": 10,
    "axes.labelsize": 10.5,
    "axes.labelcolor": TINTA,
    "axes.edgecolor": TINTA_TENUE,
    "axes.titlesize": 10.5,
    "legend.fontsize": 9.5,
    "legend.frameon": False,
    "xtick.color": TINTA,
    "ytick.color": TINTA,
    "xtick.labelsize": 9.5,
    "ytick.labelsize": 9.5,
    "lines.linewidth": 2,
    "lines.markersize": 6,
})


def _num(x: float, decimales: int = 0) -> str:
    """Formato español: punto para los miles, coma para los decimales."""
    return f"{x:,.{decimales}f}".translate(str.maketrans({",": ".", ".": ","}))


def _caudal_sostenido(rundir: Path) -> float | None:
    """Caudal en régimen, no promediado sobre toda la corrida.

    Dividir el total confirmado entre el intervalo completo de ventanas mezcla
    el régimen estable con el arranque y con la cola de drenaje, y produce una
    cifra por debajo de la carga ofrecida incluso cuando no se ha perdido ni
    una lectura. Aquí se suma el caudal de las réplicas instante a instante y se
    toma la mediana de las ventanas en las que la corrida está realmente en
    carga (la mitad del máximo observado), que es lo que la figura quiere
    comparar con la diagonal ideal.
    """
    filas = []
    for jf in rundir.glob("proc_*.jsonl"):
        for w in _load_jsonl(jf):
            filas.append({"ts": pd.to_datetime(w["ts"]),
                          "thr": w.get("thr_acked_s", 0.0)})
    if not filas:
        return None
    df = pd.DataFrame(filas)
    t0 = df["ts"].min()
    df["cubo"] = ((df["ts"] - t0).dt.total_seconds() // 10).astype(int)
    agregado = df.groupby("cubo")["thr"].sum()
    en_carga = agregado[agregado >= 0.5 * agregado.max()]
    return round(float(en_carga.median()), 1) if not en_carga.empty else None


def _ejes(ax) -> None:
    """Rejilla discreta y marco abierto: el dato manda sobre el andamiaje."""
    ax.grid(True, axis="y", color="#d8d8d8", linewidth=0.6)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)


def _umbral(ax, y: float, etiqueta: str) -> None:
    """Dibuja el umbral solo si cabe sin aplastar los datos.

    Cuando la medida se queda uno o dos órdenes de magnitud por debajo del
    umbral (el caso habitual aquí: 0,14 % de pérdida frente a un umbral del
    0,5 %, o 293 m de CPU frente a un límite de 1000 m), estirar el eje hasta
    la línea deja la serie pegada al suelo y la figura deja de leerse. En ese
    caso el umbral se declara con una nota, que informa igual sin sacrificar
    la resolución vertical.
    """
    tope = max(linea.get_ydata().max() for linea in ax.get_lines()
               if len(linea.get_ydata()))
    if y <= tope * 1.6:
        ax.axhline(y, color=REFERENCIA, linestyle="--", linewidth=1.2)
        ax.set_ylim(top=max(y, tope) * 1.12)
        ax.annotate(etiqueta, xy=(0.995, 0.99), xycoords="axes fraction",
                    ha="right", va="top", fontsize=9, color=TINTA_TENUE)
    else:
        ax.annotate(f"{etiqueta} (fuera de escala)", xy=(0.995, 0.99),
                    xycoords="axes fraction", ha="right", va="top",
                    fontsize=9, color=TINTA_TENUE)


def _series_por_replicas(ax, df: pd.DataFrame, y: str) -> None:
    for r, g in sorted(df.groupby("replicas")):
        g = g.sort_values("target_rate_msg_s")
        ax.plot(g["target_rate_msg_s"], g[y], marker=MARCA.get(r, "o"),
                color=SERIE.get(r, TINTA), label=f"{r} réplica" + ("s" if r > 1 else ""),
                markeredgecolor="white", markeredgewidth=0.8)
    ax.set_xlabel("Carga ofrecida (msg/s)")
    _ejes(ax)


def fig_caudal(df: pd.DataFrame, destino: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    lim = [df["target_rate_msg_s"].min(), df["target_rate_msg_s"].max()]
    ax.plot(lim, lim, linestyle="--", linewidth=1.2, color=REFERENCIA,
            label="ideal (sin pérdida)", zorder=0)
    _series_por_replicas(ax, df, "caudal_sostenido")
    ax.set_ylabel("Caudal confirmado en régimen (msg/s)")
    ax.legend(loc="upper left")
    fig.savefig(destino)
    plt.close(fig)


def fig_latencia(df: pd.DataFrame, destino: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.9), sharex=True)
    for ax, col, titulo in ((axes[0], "lat_transport_p95_ms", "Transporte"),
                            (axes[1], "lat_persist_p95_ms", "Persistencia")):
        _series_por_replicas(ax, df, col)
        ax.set_title(titulo, color=TINTA)
        ax.set_ylim(bottom=0)
    axes[0].set_ylabel("Latencia p95 (ms)")
    axes[1].legend(loc="lower right")
    fig.savefig(destino)
    plt.close(fig)


def fig_perdida(df: pd.DataFrame, destino: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    _series_por_replicas(ax, df, "loss_pct")
    ax.set_ylabel("Pérdida (%)")
    ax.set_ylim(bottom=0)
    # Única figura con decimales en el eje: se fuerza la coma decimal.
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda v, _: _num(v, 2)))
    _umbral(ax, 0.5, "umbral RNF-3: 0,5 %")
    ax.legend(loc="center right")
    fig.savefig(destino)
    plt.close(fig)


def fig_cpu(df: pd.DataFrame, destino: Path) -> None:
    """CPU POR RÉPLICA, no total: es lo que se compara con el límite del pod."""
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    df = df.assign(cpu_por_replica=df["cpu_mean_m"] / df["replicas"])
    _series_por_replicas(ax, df, "cpu_por_replica")
    ax.set_ylabel("CPU media por réplica (millicores)")
    ax.set_ylim(bottom=0)
    _umbral(ax, 1000, "límite del contenedor: 1000 m")
    ax.legend(loc="upper left")
    fig.savefig(destino)
    plt.close(fig)


def _ventanas_por_pod(rundir: Path) -> pd.DataFrame:
    filas = []
    for jf in sorted(rundir.glob("proc_*.jsonl")):
        for w in _load_jsonl(jf):
            tr = w.get("lat_transport_ms") or {}
            pe = w.get("lat_persist_ms") or {}
            filas.append({
                "pod": jf.stem.replace("proc_", ""),
                "ts": pd.to_datetime(w["ts"]),
                "acked": w.get("acked", 0),
                "thr_acked_s": w.get("thr_acked_s", 0.0),
                "transporte_p95": tr.get("p95"),
                "persistencia_p95": pe.get("p95"),
                "n": tr.get("n", 0),
            })
    return pd.DataFrame(filas)


def fig_arranque_frio(rundir: Path, destino: Path) -> None:
    """Cronología del escalado: réplicas, caudal y latencia sobre el mismo eje.

    Tres paneles apilados en lugar de un eje doble: superponer dos magnitudes de
    escalas distintas en una misma caja invita a leer cruces que no significan
    nada.
    """
    meta = json.loads((rundir / "run.json").read_text())
    t0 = pd.to_datetime(meta["start"])
    carga = meta["target_rate_msg_s"]

    vent = _ventanas_por_pod(rundir)
    if vent.empty:
        raise SystemExit(f"sin ventanas de métricas en {rundir}")
    vent["t"] = (vent["ts"] - t0).dt.total_seconds()

    # Las réplicas emiten sus ventanas cada 10 s pero no en el mismo instante;
    # se agrupan en cubos de 10 s para poder sumar caudal entre ellas.
    vent["cubo"] = (vent["t"] // 10 * 10).astype(int)
    caudal = vent.groupby("cubo")["thr_acked_s"].sum()
    # Latencia representativa del instante: mediana entre réplicas vivas, la
    # misma estadística con la que se resume la corrida entera.
    lat = vent.groupby("cubo")[["transporte_p95", "persistencia_p95"]].median()

    rep = pd.read_csv(rundir / "replicas.csv")
    fecha = t0.strftime("%Y-%m-%d")
    rep["ts"] = pd.to_datetime(fecha + " " + rep["ts"].astype(str)).dt.tz_localize(t0.tz)
    rep["t"] = (rep["ts"] - t0).dt.total_seconds()
    rep = rep[rep["t"] >= 0]

    fig, axes = plt.subplots(3, 1, figsize=(6.8, 6.6), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1.3, 1.3]})

    ax = axes[0]
    ax.step(rep["t"], rep["ready_replicas"], where="post", color=SERIE[1])
    ax.fill_between(rep["t"], rep["ready_replicas"], step="post",
                    color=SERIE[1], alpha=0.12)
    ax.set_ylabel("Réplicas listas")
    ax.set_yticks(range(0, int(rep["ready_replicas"].max()) + 2))
    _ejes(ax)

    ax = axes[1]
    ax.axhline(carga, color=REFERENCIA, linestyle="--", linewidth=1.2)
    ax.annotate(f"carga ofrecida: {carga} msg/s", xy=(0.995, carga),
                xycoords=("axes fraction", "data"), ha="right", va="bottom",
                fontsize=9, color=TINTA_TENUE)
    ax.plot(caudal.index, caudal.values, color=SERIE[4])
    ax.set_ylabel("Caudal confirmado\n(msg/s)")
    ax.set_ylim(bottom=0)
    _ejes(ax)

    ax = axes[2]
    ax.plot(lat.index, lat["transporte_p95"], color=SERIE[1], label="transporte")
    ax.plot(lat.index, lat["persistencia_p95"], color=SERIE[2], label="persistencia")
    ax.set_ylabel("Latencia p95 (ms)")
    ax.set_xlabel("Tiempo desde el inicio de la corrida (s)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper right")
    _ejes(ax)

    fig.align_ylabels(axes)
    fig.savefig(destino)
    plt.close(fig)


def fig_inflight(antes: Path, despues: Path, destino: Path) -> None:
    """El mismo pico, antes y después de ampliar la ventana del broker.

    Escala logarítmica: entre las dos barras hay dos órdenes de magnitud y en
    lineal la segunda desaparecería contra el eje.
    """
    def _p95(d: Path) -> tuple[float, float]:
        s = json.loads((d / "summary.json").read_text())
        return s["lat_transport_ms"]["p95"], s["loss_pct"]

    p95_antes, perdida_antes = _p95(antes)
    p95_despues, perdida_despues = _p95(despues)

    fig, ax = plt.subplots(figsize=(6.4, 2.9))
    # barh apila de abajo arriba, así que el orden se invierte para que la
    # configuración anterior quede arriba y se lea antes → después.
    etiquetas = ["ventana de 500 mensajes",
                 "ventana de 20 mensajes\n(valor por defecto)"]
    valores = [p95_despues, p95_antes]
    perdidas = [perdida_despues, perdida_antes]
    colores = [SERIE[1], SERIE[2]]

    barras = ax.barh(etiquetas, valores, color=colores, height=0.55)
    ax.set_xscale("log")
    ax.set_xlabel("Latencia de transporte p95 (ms), escala logarítmica")
    ax.set_xlim(right=max(valores) * 4)
    for barra, valor, perdida in zip(barras, valores, perdidas):
        ax.annotate(f"{_num(valor)} ms · pérdida {_num(perdida, 2)} %",
                    xy=(valor, barra.get_y() + barra.get_height() / 2),
                    xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=9.5, color=TINTA)
    ax.grid(True, axis="x", color="#d8d8d8", linewidth=0.6)
    ax.set_axisbelow(True)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.savefig(destino)
    plt.close(fig)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--matrix", type=Path, required=True,
                   help="campaña de matriz réplicas × carga")
    p.add_argument("--peak-cold", type=Path, required=True,
                   help="corrida al pico con escalado automático desde 1 réplica")
    p.add_argument("--peak-pre", type=Path,
                   help="corrida al pico anterior al arreglo de la ventana en vuelo")
    p.add_argument("--out", type=Path,
                   default=Path(__file__).resolve().parents[2] / "docs/figuras/cap10")
    args = p.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    filas = []
    for d in sorted(args.matrix.iterdir()):
        if not d.is_dir():
            continue
        fila = aggregate_run(d)
        if fila is None:
            continue
        fila["caudal_sostenido"] = _caudal_sostenido(d)
        filas.append(fila)
    if not filas:
        raise SystemExit(f"sin corridas en {args.matrix}")
    df = pd.DataFrame(filas)
    df.to_csv(args.out / "datos-matriz.csv", index=False)

    fig_caudal(df, args.out / "fig-escalado-caudal.png")
    fig_latencia(df, args.out / "fig-escalado-latencia.png")
    fig_perdida(df, args.out / "fig-escalado-perdida.png")
    if df["cpu_mean_m"].notna().any():
        fig_cpu(df, args.out / "fig-escalado-cpu.png")
    fig_arranque_frio(args.peak_cold, args.out / "fig-arranque-frio.png")
    if args.peak_pre:
        fig_inflight(args.peak_pre, args.peak_cold, args.out / "fig-inflight.png")

    for f in sorted(args.out.glob("*.png")):
        print(f"  {f.relative_to(Path.cwd()) if f.is_relative_to(Path.cwd()) else f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

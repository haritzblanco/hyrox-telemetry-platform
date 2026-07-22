"""Compara las dos señales de autoescalado del procesador bajo la misma carga.

No modifica ningún recurso del clúster: lanza una carga sostenida contra el
ScaledObject de producción (sin tocarlo) y registra en cada instante el caudal
que reporta el exporter del broker y la CPU del procesador. Del registro se
deriva, para cada señal por separado, cuántas réplicas ordenaría, de modo que
la comparación de tiempo de reacción y dimensionado sale de una única corrida y
sin varianza entre ejecuciones.

Uso:
  python3 compare_signals.py --athletes 20 --speedup 60 --hold 240 --label r1200
"""

import argparse
import csv
import json
import math
import signal
import subprocess
import time
from pathlib import Path

NS = "hyrox"
SELECTOR = "app.kubernetes.io/name=processor"
HPA = "keda-hpa-processor"
CP_NODE = "k3s-hyrox"
REQUEST_CPU_M = 200      # requests.cpu de una réplica (base del porcentaje de CPU)
TARGET_CPU_UTIL = 0.75   # umbral del disparador de CPU
TARGET_MSG_S = 400       # objetivo del disparador de caudal (msg/s por réplica)
MIN_REP, MAX_REP = 1, 4


def sh(args, timeout=15):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


def exporter_clusterip():
    return sh(["kubectl", "get", "svc", "mosquitto-metrics", "-n", NS,
               "-o", "jsonpath={.spec.clusterIP}"])


def read_caudal(cip, port=9090):
    # El exporter solo tiene ClusterIP: se consulta desde un nodo del clúster.
    raw = sh(["multipass", "exec", CP_NODE, "--",
              "curl", "-s", "-m", "3", f"http://{cip}:{port}/load"])
    try:
        return float(json.loads(raw)["messages_per_second"])
    except (ValueError, KeyError):
        return None


def read_cpu_total_m():
    raw = sh(["kubectl", "top", "pods", "-n", NS, "-l", SELECTOR, "--no-headers"])
    total = 0
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        c = parts[1]
        if c.endswith("m"):
            total += int(c[:-1])
        elif c.isdigit():
            total += int(c) * 1000
    return total


def read_replicas():
    ready = sh(["kubectl", "get", "deploy", "processor", "-n", NS,
                "-o", "jsonpath={.status.readyReplicas}"])
    desired = sh(["kubectl", "get", "hpa", HPA, "-n", NS,
                  "-o", "jsonpath={.status.desiredReplicas}"])
    return (int(ready) if ready.isdigit() else 0,
            int(desired) if desired.isdigit() else 0)


def clamp(n):
    return max(MIN_REP, min(MAX_REP, n))


def caudal_would_command(caudal):
    # metrics-api con metricType AverageValue: replicas = techo(caudal/objetivo).
    return clamp(math.ceil(caudal / TARGET_MSG_S)) if caudal > 0 else MIN_REP


def cpu_would_command(cpu_total_m):
    # HPA de CPU: replicas = techo(CPU_total / (request * umbral)).
    denom = REQUEST_CPU_M * TARGET_CPU_UTIL
    return clamp(math.ceil(cpu_total_m / denom)) if cpu_total_m > 0 else MIN_REP


def influx_count(session):
    token = sh(["kubectl", "get", "secret", "influxdb-auth", "-n", NS,
                "-o", "jsonpath={.data.token}"])
    token = subprocess.run(["base64", "-d"], input=token, capture_output=True,
                           text=True).stdout.strip()
    cip = sh(["kubectl", "get", "svc", "influxdb", "-n", NS,
              "-o", "jsonpath={.spec.clusterIP}"])
    flux = (f'from(bucket:"telemetry") |> range(start:-2d, stop:1d) '
            f'|> filter(fn:(r)=> r._measurement=="biometrics" and '
            f'r.session_id=="{session}" and r._field=="heart_rate") '
            f'|> group() |> count()')
    raw = sh(["multipass", "exec", CP_NODE, "--", "curl", "-s", "-m", "30",
              "--request", "POST", f"http://{cip}:8086/api/v2/query?org=hyrox",
              "--header", f"Authorization: Token {token}",
              "--header", "Accept: application/csv",
              "--header", "Content-Type: application/vnd.flux",
              "--data", flux], timeout=40)
    for row in reversed(raw.splitlines()):
        cell = row.split(",")[-1].strip()
        if cell.isdigit():
            return int(cell)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--athletes", type=int, required=True)
    ap.add_argument("--speedup", type=int, required=True)
    ap.add_argument("--hold", type=int, default=240)
    ap.add_argument("--loop", dest="loop", action="store_true", default=True,
                    help="carga sostenida (se corta con SIGINT al cumplir hold)")
    ap.add_argument("--no-loop", dest="loop", action="store_false",
                    help="una sola sesion por atleta; termina sola e imprime acked")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--label", required=True)
    ap.add_argument("--broker-host", default="192.168.252.2")
    ap.add_argument("--broker-port", type=int, default=31883)
    ap.add_argument("--broker-ca", default="infra/mqtt/ca.crt",
                    help="CA para validar el broker por TLS")
    ap.add_argument("--broker-password", default=None,
                    help="Contraseña de dispositivo; por defecto se lee del "
                         "Secret device-broker del cluster")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--sim",
                    default="components/simulator/.venv/bin/hyrox-sim")
    args = ap.parse_args()

    if args.broker_password is None:
        args.broker_password = subprocess.check_output(
            ["kubectl", "get", "secret", "device-broker", "-n", "hyrox",
             "-o", "jsonpath={.data.password}"]).decode()
        import base64
        args.broker_password = base64.b64decode(args.broker_password).decode()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    session = f"cmp_{args.label}_{time.strftime('%H%M%S')}"
    cip = exporter_clusterip()
    print(f"[{args.label}] sesion={session} exporter={cip} "
          f"carga={args.athletes}x{args.speedup}~{args.athletes*args.speedup} msg/s")

    sim_out = open(outdir / f"sim_{args.label}.jsonl", "w")
    sim = subprocess.Popen(
        [args.sim, "--athletes", str(args.athletes),
         "--athlete-prefix", "atleta", "--session-id", session,
         "--broker-host", args.broker_host, "--broker-port", str(args.broker_port),
         "--broker-password", args.broker_password, "--broker-ca", args.broker_ca,
         "--speedup", str(args.speedup), "--seed", "42",
         "--log-level", "WARNING"] + (["--loop"] if args.loop else []),
        stdout=sim_out, stderr=subprocess.DEVNULL)

    samples = []
    t0 = time.monotonic()
    next_tick = t0
    while time.monotonic() - t0 < args.hold:
        if sim.poll() is not None:
            print(f"[{args.label}] el simulador termino solo a los "
                  f"{time.monotonic() - t0:.0f}s")
            break
        t_rel = round(time.monotonic() - t0, 1)
        caudal = read_caudal(cip)
        cpu = read_cpu_total_m()
        ready, desired = read_replicas()
        row = {
            "t": t_rel,
            "caudal_msg_s": caudal if caudal is not None else "",
            "cpu_total_m": cpu,
            "replicas": ready,
            "desired": desired,
            "caudal_cmd": caudal_would_command(caudal or 0),
            "cpu_cmd": cpu_would_command(cpu),
        }
        samples.append(row)
        print(f"[{args.label}] t={t_rel:6.1f}s caudal={row['caudal_msg_s']!s:>7} "
              f"cpu={cpu:>4}m repl={ready} des={desired} "
              f"| caudal->{row['caudal_cmd']} cpu->{row['cpu_cmd']}")
        next_tick += args.interval
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    if sim.poll() is None:
        sim.send_signal(signal.SIGINT)
    try:
        sim.wait(timeout=60)
    except subprocess.TimeoutExpired:
        sim.terminate()
    sim_out.close()

    acked = enqueued = 0
    for line in (outdir / f"sim_{args.label}.jsonl").read_text().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        rec = json.loads(line)
        if rec.get("kind") == "sim":
            acked += rec.get("acked", 0)
            enqueued += rec.get("published", 0)

    time.sleep(4)  # margen para el ultimo flush a InfluxDB
    persisted = influx_count(session)
    loss = round(100 * (1 - persisted / acked), 2) if acked else None

    with open(outdir / f"samples_{args.label}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(samples[0].keys()))
        w.writeheader()
        w.writerows(samples)

    result = {
        "label": args.label, "session": session,
        "athletes": args.athletes, "speedup": args.speedup,
        "nominal_msg_s": args.athletes * args.speedup, "hold_s": args.hold,
        "acked": acked, "enqueued": enqueued,
        "persisted": persisted, "loss_pct": loss,
    }
    (outdir / f"result_{args.label}.json").write_text(json.dumps(result, indent=2))
    print(f"[{args.label}] acked={acked} persisted={persisted} loss={loss}%")


if __name__ == "__main__":
    main()

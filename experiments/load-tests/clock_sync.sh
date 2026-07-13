#!/usr/bin/env bash
# Verifica/asegura la sincronía de reloj de las VMs k3s. Es lo que hace válida la
# latencia de TRANSPORTE (consumo en la VM − emisión en el Mac): si los relojes
# no coinciden, esa resta no significa nada. La de PERSISTENCIA no se ve afectada
# (se mide dentro del proceso con monotonic()).
#
# Check autoritativo: systemd-timesyncd. Si ambas máquinas están sincronizadas a
# NTP, su offset mutuo es de pocos ms y la latencia de transporte es fiable.
# Como cruce secundario se estima el offset Mac↔VM por round-trip, pero quedándose
# con la muestra de MENOR RTT (la menos contaminada por el coste de `multipass
# exec`, que de lo contrario sesga el offset al alza).
#
# Uso:  bash clock_sync.sh [VM ...]      (default: las 3 VMs del clúster)
set -uo pipefail

VMS=("$@")
[[ ${#VMS[@]} -eq 0 ]] && VMS=(k3s-hyrox hyrox-worker hyrox-worker2)
SAMPLES="${SAMPLES:-7}"

for vm in "${VMS[@]}"; do
    echo "── $vm ────────────────────────────────────"
    # Asegura NTP activo y fuerza una puesta en hora.
    multipass exec "$vm" -- sudo timedatectl set-ntp true 2>/dev/null || true
    multipass exec "$vm" -- sudo systemctl restart systemd-timesyncd 2>/dev/null || true
    sleep 2

    # Check autoritativo de sincronía.
    synced=$(multipass exec "$vm" -- bash -lc 'timedatectl show -p NTPSynchronized --value' 2>/dev/null)
    echo "$vm: NTPSynchronized=${synced:-desconocido}"

    # Estimación cruda del offset: SAMPLES round-trips, nos quedamos con el de
    # menor RTT y corregimos por la mitad de ese RTT (suposición de simetría).
    python3 - "$vm" "$SAMPLES" <<'PY'
import subprocess, sys, time
vm, n = sys.argv[1], int(sys.argv[2])
best = None  # (rtt, offset_ms)
for _ in range(n):
    t0 = time.time()
    try:
        out = subprocess.run(["multipass", "exec", vm, "--", "date", "+%s.%N"],
                             capture_output=True, text=True, timeout=10)
        vm_epoch = float(out.stdout.strip())
    except Exception:
        continue
    t1 = time.time()
    rtt = t1 - t0
    offset_ms = (vm_epoch - (t0 + t1) / 2.0) * 1000.0
    if best is None or rtt < best[0]:
        best = (rtt, offset_ms)
if best is None:
    print(f"{vm}: no se pudo estimar el offset (¿VM arrancada?)")
else:
    rtt_ms, off = best[0] * 1000.0, best[1]
    print(f"{vm}: offset crudo (VM − Mac) ≈ {off:+.1f} ms  [mejor de {n}, RTT {rtt_ms:.0f} ms]")
    if abs(off) > 5000:
        # NTPSynchronized puede seguir en "yes" tras un salto de reloj por
        # suspensión de la VM: un offset de segundos es desincronía real.
        print(f"{vm}: *** DESINCRONIZADO ({off/1000:+.0f} s): no te fíes de "
              f"NTPSynchronized; reinicia systemd-timesyncd y vuelve a medir. ***")
    else:
        print(f"{vm}: nota — el offset crudo incluye overhead de `multipass exec`; "
              f"la sincronía real la confirma NTPSynchronized de arriba.")
PY
done

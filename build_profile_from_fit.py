"""Construye el perfil de calibración del simulador (hyrox_profile.json) a partir
de las 3 carreras reales (.fit + splits) en data/fit-samples/.

Agrupa las 3 carreras (BCN 1:07:20, Madrid 1:10:23, IAN 1:01:44) y saca, por
cada una de las 30 fases, la duración media con su desviación y las biométricas
(HR, cadencia, potencia, velocidad). Para las estaciones añade dos anclas de HR,
hr_work (nivel al límite al entrar del run) y hr_floor (recuperación profunda),
que el modelo de atleta interpola. Calibra además los rangos observados de
deriva cardiaca y de variación de capacidad entre carrera y fuerza.

Requiere fitparse:  /tmp/fitenv/bin/python3 build_profile_from_fit.py
(o instala fitparse en el venv del simulador).
"""
import json
import statistics as st
from fitparse import FitFile

OUT = "components/simulator/src/simulator/data/hyrox_profile.json"

# Eventos (elapsed seg) de las tablas de splits, orden canónico (30 boundaries):
RACES = {
 'BCN':   [231,235,503,505,742,757,972,976,1218,1238,1465,1479,1718,1722,1915,1947,2190,2195,2463,2499,2737,2759,2877,2920,3161,3179,3392,3434,3687,4040],
 'MADRID':[231,237,509,553,803,809,980,1020,1284,1296,1524,1566,1827,1840,2066,2083,2339,2362,2634,2655,2907,2924,3024,3057,3309,3347,3558,3568,3855,4223],
 'IAN':   [209,236,486,502,721,740,918,930,1156,1174,1407,1417,1642,1656,1830,1842,2070,2073,2334,2370,2593,2610,2710,2746,2965,2979,3195,3226,3447,3704],
}
FILES = {'BCN':'HYROX_BCN','MADRID':'HYROX_MADRID','IAN':'HYROX-BCN_IAN'}

# 30 fases: (name, type, a, b)  -> ventana [ev[a], ev[b]) con ev[-1]=0 (salida)
PHASES = [
 ('run_1','run',-1,0),('roxzone','roxzone',0,1),('skierg','station',1,2),('roxzone','roxzone',2,3),
 ('run_2','run',3,4),('roxzone','roxzone',4,5),('sled_push','station',5,6),('roxzone','roxzone',6,7),
 ('run_3','run',7,8),('roxzone','roxzone',8,9),('sled_pull','station',9,10),('roxzone','roxzone',10,11),
 ('run_4','run',11,12),('roxzone','roxzone',12,13),('burpee_bj','station',13,14),('roxzone','roxzone',14,15),
 ('run_5','run',15,16),('roxzone','roxzone',16,17),('row','station',17,18),('roxzone','roxzone',18,19),
 ('run_6','run',19,20),('roxzone','roxzone',20,21),('farmers_carry','station',21,22),('roxzone','roxzone',22,23),
 ('run_7','run',23,24),('roxzone','roxzone',24,25),('sandbag_lunges','station',25,26),('roxzone','roxzone',26,27),
 ('run_8','run',27,28),('wall_balls','station',28,29),
]

def bnd(ev, i): return 0 if i == -1 else ev[i]

def load(fn):
    recs = list(FitFile(f"data/fit-samples/{fn}.fit").get_messages('record'))
    t0 = recs[0].get_value('timestamp')
    rows = []
    for m in recs:
        e = (m.get_value('timestamp') - t0).total_seconds()
        rows.append((e, m.get_value('heart_rate'), m.get_value('cadence'),
                     m.get_value('power'), m.get_value('enhanced_speed')))
    return rows

DATA = {n: load(FILES[n]) for n in RACES}

def seg(rows, ev, a, b):
    lo, hi = bnd(ev, a), bnd(ev, b)
    return [r for r in rows if lo <= r[0] < hi]

def mstats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"mean": 0.0, "std": 0.0, "min": 0, "max": 0}
    return {"mean": round(st.mean(vals), 1),
            "std": round(st.pstdev(vals), 1) if len(vals) > 1 else 0.0,
            "min": round(min(vals), 2) if isinstance(vals[0], float) else min(vals),
            "max": round(max(vals), 2) if isinstance(vals[0], float) else max(vals)}

# Ancla de HR (run_1 pooled) para el escalado de deriva en el atleta
run1 = [r[1] for n in RACES for r in seg(DATA[n], RACES[n], -1, 0) if r[1] is not None]
ANCHOR_HR = round(st.mean(run1), 1)

# Deriva por carrera = HR medio run_8 menos run_1
def run_mean_hr(n, name):
    a, b = [(a, b) for p, t, a, b in PHASES if p == name][0]
    return st.mean([r[1] for r in seg(DATA[n], RACES[n], a, b) if r[1]])
drifts = [run_mean_hr(n, 'run_8') - run_mean_hr(n, 'run_1') for n in RACES]

# Totales runs/estaciones por carrera -> calibrar ejes carrera/fuerza
run_tot, sta_tot = {}, {}
for n in RACES:
    ev = RACES[n]
    run_tot[n] = sum(bnd(ev, b) - bnd(ev, a) for p, t, a, b in PHASES if t == 'run')
    sta_tot[n] = sum(bnd(ev, b) - bnd(ev, a) for p, t, a, b in PHASES if t == 'station')
run_ref = st.mean(run_tot.values()); sta_ref = st.mean(sta_tot.values())

# ---- construir fases ----
phases_out = []
station_dur_cv = {}
for idx, (name, typ, a, b) in enumerate(PHASES):
    durs = [bnd(RACES[n], b) - bnd(RACES[n], a) for n in RACES]
    dur_mean = st.mean(durs)
    dur_std = st.pstdev(durs) if len(durs) > 1 else 0.0
    # biométricas pooled (todas las carreras concatenadas en esta fase)
    pooled = [r for n in RACES for r in seg(DATA[n], RACES[n], a, b)]
    hr = mstats([r[1] for r in pooled]); cad = mstats([r[2] for r in pooled])
    pw = mstats([r[3] for r in pooled]); sp = mstats([r[4] for r in pooled])
    hr["mean_base"] = hr["mean"]
    phase = {
        "name": name, "type": typ,
        "duration_s": int(round(dur_mean)),
        "duration_cv": round(dur_std / dur_mean, 3) if dur_mean else 0.0,
        "heart_rate": hr, "cadence": cad, "power": pw, "speed": sp,
    }
    if typ == "station":
        # hr_work = entrada (primeros 8s, nivel "al límite" al salir del run), media entre carreras
        entries = []
        floors = []
        for n in RACES:
            s = seg(DATA[n], RACES[n], a, b)
            hrv = [r[1] for r in s if r[1] is not None]
            if hrv:
                entries.append(st.mean(hrv[:8]))
                floors.append(min(hrv))
        phase["hr_work"] = round(st.mean(entries), 1) if entries else hr["mean"]
        phase["hr_floor"] = round(min(floors), 1) if floors else hr["min"]
        station_dur_cv[name] = phase["duration_cv"]
    phases_out.append(phase)

profile = {
    "source": "Pool 3 carreras reales HYROX (BCN 1:07:20, Madrid 1:10:23, IAN 1:01:44), TFM",
    "description": "Perfil de calibración construido con build_profile_from_fit.py a partir "
                   "de data/fit-samples/. Biométricas por segundo agrupadas de las 3 carreras; "
                   "duración media por fase; estaciones con anclas hr_work/hr_floor para la "
                   "persona de recuperación; rangos de deriva y de capacidad observados.",
    "total_seconds": int(round(st.mean([RACES[n][-1] for n in RACES]))),
    "anchor_hr": ANCHOR_HR,
    "cardiac_drift": {
        "bpm_total": round(st.mean(drifts), 1),
        "bpm_min": round(min(drifts), 1),
        "bpm_max": round(max(drifts), 1),
        "note": "Deriva = HR medio run_8 menos run_1. Rango observado entre las 3 carreras; "
                "el atleta muestrea su propia deriva en [bpm_min, bpm_max].",
    },
    "ability": {
        "run_ref_s": int(round(run_ref)), "station_ref_s": int(round(sta_ref)),
        "run_cv": round(st.pstdev(run_tot.values()) / run_ref, 3),
        "station_cv": round(st.pstdev(sta_tot.values()) / sta_ref, 3),
        "note": "Tiempos totales de runs y de estaciones por carrera. run_cv/station_cv = "
                "dispersión observada; el simulador la usa para calibrar los ejes "
                "carrera/fuerza. Las estaciones varían menos entre sí que los runs.",
    },
    # geometría del recinto (BCN; estándar HYROX): distancias para derivar velocidad de run
    "course": {
        "source": "Planeta Híbrido, HYROX Barcelona 2026 (FIRA Hall 04)",
        "lap_m": 428, "out_to_in_m": 108, "start_to_in_m": 134, "laps_per_run": 2,
        "total_distance_m": 8700,
        "run_distances_m": {"run_1": 990, "run_2": 964, "run_3": 964, "run_4": 964,
                            "run_5": 964, "run_6": 964, "run_7": 964, "run_8": 964},
        "note": "run+roxzone = total_distance_m. roxzone_total = total menos sum(runs), repartida "
                "entre fases roxzone proporcional a su duración.",
    },
    "phases": phases_out,
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(profile, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"Perfil escrito en {OUT}")
print(f"  total_seconds(medio)={profile['total_seconds']}  anchor_hr={ANCHOR_HR}")
print(f"  deriva: {profile['cardiac_drift']['bpm_total']} bpm (rango {min(drifts):.0f}-{max(drifts):.0f})")
print(f"  runs: ref={profile['ability']['run_ref_s']}s cv={profile['ability']['run_cv']}  "
      f"estaciones: ref={profile['ability']['station_ref_s']}s cv={profile['ability']['station_cv']}")
print("  CV duración por estación (ruido específico):")
for k, v in sorted(station_dur_cv.items(), key=lambda x: -x[1]):
    print(f"    {k:<16} {v}")

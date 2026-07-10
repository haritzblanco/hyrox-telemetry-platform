"""
Genera los 4 dashboards Hyrox y sus ConfigMaps de Kubernetes.
  - hyrox-overview: el existente, solo se corrigen los bugs de run_order
  - hyrox-live: monitorización en tiempo real de la carrera
  - hyrox-analysis: revisión individual post-carrera
  - hyrox-compare: comparativa entre atletas
"""
import copy, json
from pathlib import Path

BASE_DIR = Path("infra/manifests/grafana")
DS = {"type": "influxdb", "uid": "influxdb-hyrox"}

# Flux fragments
RUN_ORDER = (
    'if r.phase == "run_1" then "01" '
    'else if r.phase == "skierg" then "02" '
    'else if r.phase == "run_2" then "03" '
    'else if r.phase == "sled_push" then "04" '
    'else if r.phase == "run_3" then "05" '
    'else if r.phase == "sled_pull" then "06" '
    'else if r.phase == "run_4" then "07" '
    'else if r.phase == "burpee_bj" then "08" '
    'else if r.phase == "run_5" then "09" '
    'else if r.phase == "row" then "10" '
    'else if r.phase == "run_6" then "11" '
    'else if r.phase == "farmers_carry" then "12" '
    'else if r.phase == "run_7" then "13" '
    'else if r.phase == "sandbag_lunges" then "14" '
    'else if r.phase == "run_8" then "15" '
    'else if r.phase == "wall_balls" then "16" '
    'else "99"'
)
PHASE_LABEL = (
    'if r.phase == "run_1" then "Run 1" '
    'else if r.phase == "skierg" then "SkiErg" '
    'else if r.phase == "run_2" then "Run 2" '
    'else if r.phase == "sled_push" then "Sled Push" '
    'else if r.phase == "run_3" then "Run 3" '
    'else if r.phase == "sled_pull" then "Sled Pull" '
    'else if r.phase == "run_4" then "Run 4" '
    'else if r.phase == "burpee_bj" then "Burpee BJ" '
    'else if r.phase == "run_5" then "Run 5" '
    'else if r.phase == "row" then "Row" '
    'else if r.phase == "run_6" then "Run 6" '
    'else if r.phase == "farmers_carry" then "Farmers Carry" '
    'else if r.phase == "run_7" then "Run 7" '
    'else if r.phase == "sandbag_lunges" then "Sandbag Lunges" '
    'else if r.phase == "run_8" then "Run 8" '
    'else if r.phase == "wall_balls" then "Wall Balls" '
    'else r.phase'
)

def flux(field, extra_filters='', body='', extra=''):
    """Base Flux query with session filter."""
    q = (
        'from(bucket: "telemetry")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "biometrics")\n'
        '  |> filter(fn: (r) => r["session_id"] =~ /${session_id}/)\n'
    )
    if extra_filters:
        q += extra_filters
    q += f'  |> filter(fn: (r) => r._field == "{field}")\n'
    q += body
    q += extra
    return q

ATHLETE_F = '  |> filter(fn: (r) => r["athlete_id"] =~ /${athlete_id}/)\n'

MAP_PHASE = (
    '  |> map(fn: (r) => ({\n'
    '      _time: 1970-01-01T00:00:00Z,\n'
    '      run_order: ' + RUN_ORDER + ',\n'
    '      phase_label: ' + PHASE_LABEL + ',\n'
    '      _value: r._value\n'
    '  }))\n'
)
SORT_DROP = '  |> sort(columns: ["run_order"])\n  |> drop(columns: ["run_order"])'

# All canonical queries

Q = {}

# Stats (aggregate, no athlete filter)
Q['atletas'] = flux('heart_rate',
    body='  |> group(columns: ["athlete_id"])\n  |> last()\n  |> group()\n  |> count()')

Q['hr_mean'] = flux('heart_rate',
    body='  |> group()\n  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)')

Q['hr_max'] = flux('heart_rate',
    body='  |> group()\n  |> aggregateWindow(every: 1m, fn: max, createEmpty: false)')

Q['power_mean'] = flux('power',
    body='  |> group()\n  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)')

Q['speed_mean'] = flux('speed',
    body='  |> group()\n  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)\n'
         '  |> map(fn: (r) => ({r with _value: r._value * 3.6}))')

Q['session_time'] = flux('elapsed_seconds',
    body='  |> group()\n  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)\n'
         '  |> map(fn: (r) => ({r with _value: float(v: r._value)}))')

# Stats filtered by athlete (for analysis dashboard)
def with_athlete(q):
    return q.replace(
        '|> filter(fn: (r) => r["session_id"] =~ /${session_id}/)\n',
        '|> filter(fn: (r) => r["session_id"] =~ /${session_id}/)\n' + ATHLETE_F
    )

Q['hr_mean_a']   = with_athlete(Q['hr_mean'])
Q['hr_max_a']    = with_athlete(Q['hr_max'])
Q['power_mean_a']= with_athlete(Q['power_mean'])
Q['speed_mean_a']= with_athlete(Q['speed_mean'])
Q['session_time_a'] = with_athlete(Q['session_time'])
Q['distance_total'] = flux('distance', extra_filters=ATHLETE_F,
    body='  |> group(columns: ["athlete_id"])\n'
         '  |> aggregateWindow(every: v.windowPeriod, fn: last, createEmpty: false)\n'
         '  |> last()\n  |> group()\n  |> mean()')

# Leaderboard
Q['leaderboard'] = (
    'from(bucket: "telemetry")\n'
    '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
    '  |> filter(fn: (r) => r._measurement == "biometrics")\n'
    '  |> filter(fn: (r) => r["session_id"] =~ /${session_id}/)\n'
    '  |> filter(fn: (r) => r._field == "elapsed_seconds" or r._field == "distance" or r._field == "heart_rate")\n'
    '  |> group(columns: ["athlete_id", "_field"])\n'
    '  |> last()\n'
    '  |> map(fn: (r) => ({r with _time: 1970-01-01T00:00:00Z, _value: float(v: r._value)}))\n'
    '  |> group(columns: ["athlete_id", "phase", "phase_type"])\n'
    '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
    '  |> keep(columns: ["athlete_id", "phase", "phase_type", "elapsed_seconds", "distance", "heart_rate"])\n'
    '  |> group()\n'
    '  |> sort(columns: ["elapsed_seconds"], desc: true)'
)

# Timeline
Q['timeline'] = flux('heart_rate', extra_filters=ATHLETE_F,
    body='  |> group(columns: ["athlete_id"])\n'
         '  |> aggregateWindow(every: 30s, fn: last, createEmpty: false)\n'
         '  |> map(fn: (r) => ({_time: r._time, _value: r.phase_type, _field: r.athlete_id}))\n'
         '  |> group(columns: ["_field"])')

# Timeseries
Q['hr_ts'] = flux('heart_rate', extra_filters=ATHLETE_F,
    body='  |> group(columns: ["athlete_id"])\n'
         '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)')

Q['power_ts'] = flux('power', extra_filters=ATHLETE_F,
    body='  |> group(columns: ["athlete_id"])\n'
         '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)')

Q['speed_ts'] = flux('speed', extra_filters=ATHLETE_F,
    body='  |> group(columns: ["athlete_id"])\n'
         '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n'
         '  |> map(fn: (r) => ({r with _value: r._value * 3.6}))')

Q['cadence_ts'] = flux('cadence', extra_filters=ATHLETE_F,
    body='  |> group(columns: ["athlete_id"])\n'
         '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)')

Q['distance_ts'] = flux('distance', extra_filters=ATHLETE_F,
    body='  |> group(columns: ["athlete_id"])\n'
         '  |> aggregateWindow(every: v.windowPeriod, fn: last, createEmpty: false)')

# Phase analysis (ordered, no run_order visible)
Q['duration_by_phase'] = (
    flux('elapsed_seconds',
        body='  |> group(columns: ["phase", "phase_type", "athlete_id"])\n'
             '  |> count()\n'
             '  |> group(columns: ["phase", "phase_type"])\n'
             '  |> mean()\n')
    + MAP_PHASE
    + '  |> rename(columns: {_value: "Duración"})\n'
    '  |> group()\n'
    + SORT_DROP
)

Q['hr_by_phase'] = (
    flux('heart_rate',
        body='  |> group(columns: ["phase", "phase_type"])\n  |> mean()\n')
    + MAP_PHASE
    + '  |> rename(columns: {_value: "HR Medio (bpm)"})\n'
    '  |> group()\n'
    + SORT_DROP
)

# Splits (pivot by phase_type, run vs station)
MAP_PHASE_TYPE = (
    '  |> map(fn: (r) => ({\n'
    '      _time: 1970-01-01T00:00:00Z,\n'
    '      run_order: ' + RUN_ORDER + ',\n'
    '      phase_label: ' + PHASE_LABEL + ',\n'
    '      phase_type: r.phase_type,\n'
    '      _value: r._value\n'
    '  }))\n'
)

Q['splits'] = (
    flux('elapsed_seconds', extra_filters=ATHLETE_F,
        body='  |> group(columns: ["athlete_id", "phase", "phase_type"])\n'
             '  |> count()\n'
             '  |> group(columns: ["phase", "phase_type"])\n'
             '  |> mean()\n')
    + MAP_PHASE_TYPE
    + '  |> pivot(rowKey: ["run_order", "phase_label"], columnKey: ["phase_type"], valueColumn: "_value")\n'
    '  |> group()\n'
    + SORT_DROP
)

# Distribution (pivot by athlete_id)
MAP_ATHLETE = (
    '  |> map(fn: (r) => ({\n'
    '      _time: 1970-01-01T00:00:00Z,\n'
    '      athlete_id: r.athlete_id,\n'
    '      _value: float(v: r._value),\n'
    '      run_order: ' + RUN_ORDER + ',\n'
    '      phase_label: ' + PHASE_LABEL + '\n'
    '  }))\n'
)

Q['distribution'] = (
    flux('elapsed_seconds',
        body='  |> group(columns: ["athlete_id", "phase"])\n  |> count()\n  |> group()\n')
    + MAP_ATHLETE
    + '  |> pivot(rowKey: ["run_order", "phase_label"], columnKey: ["athlete_id"], valueColumn: "_value")\n'
    '  |> group()\n'
    + SORT_DROP
)

# Distribución por estación (histograma): tiempos de todos vs. el atleta seleccionado
STATIONS = [
    ("skierg",         "SkiErg"),
    ("sled_push",      "Sled Push"),
    ("sled_pull",      "Sled Pull"),
    ("burpee_bj",      "Burpee Broad Jump"),
    ("row",            "Row"),
    ("farmers_carry",  "Farmers Carry"),
    ("sandbag_lunges", "Sandbag Lunges"),
    ("wall_balls",     "Wall Balls"),
]

def station_dist(phase, athlete=False):
    """Duración (segundos = nº de puntos) por atleta en una estación.
    athlete=False: todos los atletas (la distribución).
    athlete=True: solo el atleta seleccionado (la línea/marcador)."""
    q = (
        'from(bucket: "telemetry")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "biometrics")\n'
        '  |> filter(fn: (r) => r["session_id"] =~ /${session_id}/)\n'
        f'  |> filter(fn: (r) => r.phase == "{phase}")\n'
    )
    if athlete:
        q += ATHLETE_F
    q += (
        '  |> filter(fn: (r) => r._field == "elapsed_seconds")\n'
        '  |> group(columns: ["athlete_id"])\n'
        '  |> count()\n'
        '  |> group()\n'
        '  |> map(fn: (r) => ({_time: 1970-01-01T00:00:00Z, _value: float(v: r._value)}))\n'
        '  |> keep(columns: ["_time", "_value"])\n'
    )
    name = "Tu tiempo" if athlete else "Distribución"
    q += f'  |> rename(columns: {{_value: "{name}"}})'
    return q

# Stats by athlete
Q['stats_athletes'] = (
    'from(bucket: "telemetry")\n'
    '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
    '  |> filter(fn: (r) => r._measurement == "biometrics")\n'
    '  |> filter(fn: (r) => r["session_id"] =~ /${session_id}/)\n'
    '  |> filter(fn: (r) => r._field == "heart_rate" or r._field == "power" or r._field == "speed")\n'
    '  |> group(columns: ["athlete_id", "_field"])\n'
    '  |> mean()\n'
    '  |> map(fn: (r) => ({r with _time: 1970-01-01T00:00:00Z, _value: float(v: r._value)}))\n'
    '  |> group(columns: ["athlete_id"])\n'
    '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
    '  |> map(fn: (r) => ({r with speed: r.speed * 3.6}))\n'
    '  |> keep(columns: ["athlete_id", "heart_rate", "power", "speed"])\n'
    '  |> group()\n'
    '  |> sort(columns: ["athlete_id"])'
)

# Template variables

def var_session():
    q = 'import "influxdata/influxdb/schema"\nschema.tagValues(bucket: "telemetry", tag: "session_id")'
    return {"allValue": ".*", "current": {"selected": False, "text": "All", "value": "$__all"},
            "datasource": DS, "definition": q, "hide": 0, "includeAll": True,
            "label": "Sesión", "multi": False, "name": "session_id", "options": [],
            "query": q, "refresh": 2, "regex": "", "sort": 2, "type": "query"}

def var_athlete(multi=True):
    q = 'import "influxdata/influxdb/schema"\nschema.tagValues(bucket: "telemetry", tag: "athlete_id")'
    return {"allValue": ".*", "current": {"selected": True, "text": "All", "value": "$__all"},
            "datasource": DS, "definition": q, "hide": 0, "includeAll": True,
            "label": "Atleta", "multi": multi, "name": "athlete_id", "options": [],
            "query": q, "refresh": 2, "regex": "", "sort": 1, "type": "query"}

# Panel helpers

def t(query, refId="A"):
    return {"datasource": DS, "query": query, "refId": refId}

def gp(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}

def row_p(id, y, title):
    return {"collapsed": False, "gridPos": gp(0, y, 24, 1),
            "id": id, "title": title, "type": "row"}

def stat_p(id, x, y, w, h, title, query, unit, steps,
           graph_mode="area", decimals=0, display_name=None):
    defaults = {"color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": steps},
                "unit": unit, "decimals": decimals}
    if display_name:
        defaults["displayName"] = display_name
    return {
        "datasource": DS,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "gridPos": gp(x, y, w, h), "id": id,
        "options": {"colorMode": "background", "graphMode": graph_mode,
                    "justifyMode": "center", "orientation": "auto",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "textMode": "auto"},
        "targets": [t(query)], "title": title, "type": "stat"
    }

HR_STEPS  = [{"color": "green", "value": None}, {"color": "yellow", "value": 150}, {"color": "red", "value": 175}]
BLUE_STEP = [{"color": "blue",  "value": None}]
PURP_STEP = [{"color": "purple","value": None}]
TEAL_STEP = [{"color": "semi-dark-blue", "value": None}]

def ts_p(id, x, y, w, h, title, query, unit, ylabel,
         fill=12, min_=None, max_=None, calcs=None, threshold_steps=None):
    custom = {
        "axisBorderShow": False, "axisCenteredZero": False, "axisColorMode": "text",
        "axisLabel": ylabel, "axisPlacement": "auto", "barAlignment": 0,
        "drawStyle": "line", "fillOpacity": fill, "gradientMode": "opacity",
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
        "insertNulls": False, "lineInterpolation": "smooth", "lineWidth": 2,
        "pointSize": 4, "scaleDistribution": {"type": "linear"},
        "showPoints": "never", "spanNulls": False,
        "stacking": {"group": "A", "mode": "none"},
        "thresholdsStyle": {"mode": "line+area" if threshold_steps else "off"}
    }
    defaults = {
        "color": {"mode": "palette-classic"}, "custom": custom,
        "unit": unit, "min": min_, "max": max_
    }
    if threshold_steps:
        defaults["thresholds"] = {"mode": "absolute", "steps": threshold_steps}
    return {
        "datasource": DS,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "gridPos": gp(x, y, w, h), "id": id,
        "options": {
            "legend": {"calcs": calcs or ["mean", "max", "last"],
                       "displayMode": "table", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"}
        },
        "targets": [t(query)], "title": title, "type": "timeseries"
    }

def bar_p(id, x, y, w, h, title, query, unit, orientation="horizontal",
          xField="phase_label", overrides=None, show_value="never",
          calcs=None, decimals=0, rotation=-30):
    return {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {"fillOpacity": 80, "gradientMode": "none",
                           "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                           "lineWidth": 1, "scaleDistribution": {"type": "linear"},
                           "thresholdsStyle": {"mode": "off"}},
                "unit": unit, "decimals": decimals
            },
            "overrides": overrides or []
        },
        "gridPos": gp(x, y, w, h), "id": id,
        "options": {
            "barRadius": 0.3, "barWidth": 0.75, "fullHighlight": False, "groupWidth": 0.7,
            "legend": {"calcs": calcs or ["mean", "max"], "displayMode": "table",
                       "placement": "right", "showLegend": True},
            "orientation": orientation, "showValue": show_value, "stacking": "none",
            "tooltip": {"mode": "multi", "sort": "desc"},
            "xField": xField, "xTickLabelRotation": 0 if orientation == "horizontal" else rotation
        },
        "targets": [t(query)], "title": title, "type": "barchart"
    }

SPLITS_OVR = [
    {"matcher": {"id": "byName", "options": "run"},
     "properties": [{"id": "displayName", "value": "Carrera"},
                    {"id": "color", "value": {"mode": "fixed", "fixedColor": "#5794F2"}}]},
    {"matcher": {"id": "byName", "options": "station"},
     "properties": [{"id": "displayName", "value": "Estación"},
                    {"id": "color", "value": {"mode": "fixed", "fixedColor": "#FF7A45"}}]}
]

LB_OVR = [
    {"matcher": {"id": "byName", "options": "heart_rate"},
     "properties": [{"id": "displayName", "value": "FC (bpm)"},
                    {"id": "custom.cellOptions", "value": {"mode": "gradient", "type": "color-background"}},
                    {"id": "thresholds", "value": {"mode": "absolute", "steps": HR_STEPS}}]},
    {"matcher": {"id": "byName", "options": "elapsed_seconds"},
     "properties": [{"id": "displayName", "value": "Tiempo"}, {"id": "unit", "value": "clocks"}]},
    {"matcher": {"id": "byName", "options": "distance"},
     "properties": [{"id": "displayName", "value": "Distancia"}, {"id": "unit", "value": "lengthm"}, {"id": "decimals", "value": 0}]},
    {"matcher": {"id": "byName", "options": "athlete_id"}, "properties": [{"id": "displayName", "value": "Atleta"}]},
    {"matcher": {"id": "byName", "options": "phase"}, "properties": [{"id": "displayName", "value": "Fase"}]},
    {"matcher": {"id": "byName", "options": "phase_type"}, "properties": [{"id": "displayName", "value": "Tipo"}]},
]

STATS_OVR = [
    {"matcher": {"id": "byName", "options": "athlete_id"},
     "properties": [{"id": "displayName", "value": "Atleta"}, {"id": "custom.width", "value": 140}]},
    {"matcher": {"id": "byName", "options": "heart_rate"},
     "properties": [{"id": "displayName", "value": "HR Medio (bpm)"}, {"id": "unit", "value": "none"}, {"id": "decimals", "value": 0},
                    {"id": "custom.cellOptions", "value": {"mode": "gradient", "type": "color-background"}},
                    {"id": "thresholds", "value": {"mode": "absolute", "steps": HR_STEPS}}]},
    {"matcher": {"id": "byName", "options": "power"},
     "properties": [{"id": "displayName", "value": "Potencia Media (W)"}, {"id": "unit", "value": "watt"}, {"id": "decimals", "value": 0}]},
    {"matcher": {"id": "byName", "options": "speed"},
     "properties": [{"id": "displayName", "value": "Velocidad Media (km/h)"}, {"id": "unit", "value": "velocitykmh"}, {"id": "decimals", "value": 1}]},
]

def table_p(id, x, y, w, h, title, query, overrides, sort_by=None):
    return {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {"color": {"mode": "thresholds"},
                         "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                         "custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False}},
            "overrides": overrides
        },
        "gridPos": gp(x, y, w, h), "id": id,
        "options": {"cellHeight": "sm",
                    "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                    "showHeader": True, "sortBy": sort_by or []},
        "transformations": [{"id": "organize",
                             "options": {"excludeByName": {"_time": True, "_start": True,
                                                           "_stop": True, "_measurement": True},
                                         "indexByName": {}, "renameByName": {}}}],
        "targets": [t(query)], "title": title, "type": "table"
    }

def state_tl(id, x, y, w, h, title, query):
    return {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "custom": {"fillOpacity": 90, "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                           "insertNulls": False, "lineWidth": 1, "spanNulls": False},
                "mappings": [{"type": "value", "options": {
                    "run":     {"color": "#5794F2", "text": "Carrera",  "index": 0},
                    "station": {"color": "#FF7A45", "text": "Estación", "index": 1}
                }}],
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}
            }, "overrides": []
        },
        "gridPos": gp(x, y, w, h), "id": id,
        "options": {"alignValue": "left",
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "mergeValues": True, "rowHeight": 0.9, "showValue": "auto",
                    "tooltip": {"mode": "single", "sort": "none"}},
        "targets": [t(query)], "title": title, "type": "state-timeline"
    }

def hist_p(id, x, y, w, h, title, dist_q, athlete_q, bucket=None):
    """Histograma por estación: distribución de todos + marcador del atleta."""
    options = {
        "bucketOffset": 0, "combine": False,
        "legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": True},
        "tooltip": {"mode": "multi", "sort": "none"}
    }
    if bucket is not None:
        options["bucketSize"] = bucket
    return {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {"fillOpacity": 70, "gradientMode": "none",
                           "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                           "lineWidth": 1},
                "unit": "clocks",
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}
            },
            "overrides": [
                {"matcher": {"id": "byName", "options": "Distribución"},
                 "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "#8E8E3F"}},
                                {"id": "custom.fillOpacity", "value": 55}]},
                {"matcher": {"id": "byName", "options": "Tu tiempo"},
                 "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "#F2CC0C"}},
                                {"id": "custom.fillOpacity", "value": 100},
                                {"id": "custom.lineWidth", "value": 2}]}
            ]
        },
        "gridPos": gp(x, y, w, h), "id": id,
        "options": options,
        "targets": [t(dist_q, "A"), t(athlete_q, "B")],
        "title": title, "type": "histogram"
    }

# Dashboard builder

LINKS = [
    {"icon": "external link", "targetBlank": False, "title": "En Carrera",        "type": "link", "url": "/d/hyrox-live"},
    {"icon": "external link", "targetBlank": False, "title": "Análisis Individual","type": "link", "url": "/d/hyrox-analysis"},
    {"icon": "external link", "targetBlank": False, "title": "Comparativa",        "type": "link", "url": "/d/hyrox-compare"},
    {"icon": "external link", "targetBlank": False, "title": "Overview",           "type": "link", "url": "/d/hyrox-overview"},
]

def make_dash(title, uid, tags, time_from, time_to, refresh, vars_, panels, links=None):
    return {
        "annotations": {"list": []}, "editable": True, "fiscalYearStartMonth": 0,
        "graphTooltip": 1, "id": None, "links": links or LINKS,
        "panels": panels, "refresh": refresh, "schemaVersion": 41,
        "tags": ["hyrox"] + tags,
        "templating": {"list": vars_},
        "time": {"from": time_from, "to": time_to},
        "timepicker": {}, "timezone": "browser", "title": title, "uid": uid, "version": 1
    }

HR_TS_THRESHOLDS = [
    {"color": "transparent", "value": None},
    {"color": "rgba(255,166,0,0.15)", "value": 150},
    {"color": "rgba(255,0,0,0.15)",   "value": 175}
]

# Dashboard 1: hyrox-live

live_panels = [
    row_p(100, 0, "Estado en Tiempo Real"),
    stat_p(1,  0, 1, 4, 4, "Atletas en carrera", Q['atletas'],    "none",        BLUE_STEP, "none", 0, "Atletas"),
    stat_p(2,  4, 1, 4, 4, "Pulso medio (bpm)",  Q['hr_mean'],    "none",        HR_STEPS,  "area"),
    stat_p(3,  8, 1, 4, 4, "Pulso máximo (bpm)", Q['hr_max'],     "none",        HR_STEPS,  "area"),
    stat_p(4, 12, 1, 4, 4, "Velocidad (km/h)",   Q['speed_mean'], "velocitykmh", [{"color":"green","value":None},{"color":"yellow","value":10},{"color":"red","value":12}], "area", 1),
    stat_p(5, 16, 1, 4, 4, "Potencia media (W)", Q['power_mean'], "watt",        PURP_STEP, "area"),
    stat_p(6, 20, 1, 4, 4, "Tiempo sesión",      Q['session_time'], "dthms",     TEAL_STEP, "area"),

    row_p(101, 5, "Posición en Carrera"),
    table_p(7, 0, 6, 24, 7, "Leaderboard", Q['leaderboard'], LB_OVR,
            sort_by=[{"desc": True, "displayName": "Distancia"}]),

    row_p(102, 13, "Fases Activas"),
    state_tl(18, 0, 14, 24, 8, "Fases por atleta", Q['timeline']),

    row_p(103, 22, "Biométricas en Vivo"),
    ts_p(8,  0, 23, 24, 9, "Ritmo Cardiaco",  Q['hr_ts'],    "none",        "bpm", 12, 50, 210, threshold_steps=HR_TS_THRESHOLDS),
    ts_p(10, 0, 32, 12, 8, "Velocidad (km/h)", Q['speed_ts'], "velocitykmh", "km/h", 12, 0),
    ts_p(9, 12, 32, 12, 8, "Potencia (W)",    Q['power_ts'], "watt",        "W",    12, 0),
]

live_dash = make_dash(
    "En Carrera", "hyrox-live", ["live"],
    "now-15m", "now", "5s",
    [var_session(), var_athlete(multi=True)],
    live_panels
)

# Dashboard 2: hyrox-analysis

analysis_panels = [
    row_p(100, 0, "Resumen de Sesión"),
    stat_p(1,  0, 1, 4, 4, "Tiempo total",   Q['session_time_a'], "dthms",      TEAL_STEP, "none"),
    stat_p(2,  4, 1, 4, 4, "HR media (bpm)", Q['hr_mean_a'],      "none",       HR_STEPS,  "area"),
    stat_p(3,  8, 1, 4, 4, "HR máxima (bpm)",Q['hr_max_a'],       "none",       HR_STEPS,  "area"),
    stat_p(4, 12, 1, 4, 4, "Velocidad media",Q['speed_mean_a'],   "velocitykmh",[{"color":"green","value":None},{"color":"yellow","value":10},{"color":"red","value":12}], "area", 1),
    stat_p(5, 16, 1, 4, 4, "Potencia media", Q['power_mean_a'],   "watt",       PURP_STEP, "area"),
    stat_p(6, 20, 1, 4, 4, "Distancia total",Q['distance_total'], "lengthm",    BLUE_STEP, "none", 0),

    row_p(101, 5, "Splits por Fase"),
    bar_p(16,  0, 6, 16, 18, "Splits por Fase",
          Q['splits'], "clocks", "horizontal", "phase_label",
          overrides=SPLITS_OVR, show_value="always", calcs=["mean", "max"]),
    bar_p(14, 16, 6,  8, 18, "Pulso medio por fase",
          Q['hr_by_phase'], "none", "auto", "phase_label",
          calcs=[], rotation=-30),

    row_p(102, 24, "Biométricas"),
    ts_p(8,  0, 25, 24, 9, "Ritmo Cardiaco",     Q['hr_ts'],      "none",        "bpm",   12, 50, 210, threshold_steps=HR_TS_THRESHOLDS),
    ts_p(9,  0, 34, 12, 8, "Potencia (W)",        Q['power_ts'],   "watt",        "W",     12, 0),
    ts_p(10,12, 34, 12, 8, "Velocidad (km/h)",    Q['speed_ts'],   "velocitykmh", "km/h",  12, 0),
    ts_p(11, 0, 42, 12, 8, "Cadencia (rpm)",      Q['cadence_ts'], "rotrpm",      "rpm",   12, 0),
    ts_p(12,12, 42, 12, 8, "Distancia acumulada", Q['distance_ts'],"lengthm",     "m",     12, 0, None, ["last"]),

    row_p(104, 50, "Distribución por Estación"),
]

# Un histograma por estación: dónde cae el atleta seleccionado vs. todos
for i, (phase, label) in enumerate(STATIONS):
    analysis_panels.append(
        hist_p(30 + i, (i % 2) * 12, 51 + (i // 2) * 8, 12, 8, label,
               station_dist(phase), station_dist(phase, athlete=True))
    )

analysis_dash = make_dash(
    "Análisis Individual", "hyrox-analysis", ["analysis"],
    "now-2h", "now", "",
    [var_session(), var_athlete(multi=False)],
    analysis_panels
)

# Dashboard 3: hyrox-compare

compare_panels = [
    row_p(100, 0, "Distribución de Tiempos por Fase"),
    bar_p(17, 0, 1, 24, 18, "Tiempos por fase, todos los atletas",
          Q['distribution'], "clocks", "horizontal", "phase_label",
          show_value="never", calcs=[]),

    row_p(101, 19, "Análisis por Fase"),
    bar_p(13,  0, 20, 12, 10, "Duración media por fase",
          Q['duration_by_phase'], "clocks", "auto", "phase_label",
          calcs=[], rotation=-30),
    bar_p(14, 12, 20, 12, 10, "Pulso medio por fase",
          Q['hr_by_phase'], "none", "auto", "phase_label",
          calcs=[], rotation=-30),

    row_p(102, 30, "Ranking de Atletas"),
    table_p(15, 0, 31, 24, 10, "Estadísticas medias por atleta",
            Q['stats_athletes'], STATS_OVR,
            sort_by=[{"desc": False, "displayName": "Atleta"}]),
]

compare_dash = make_dash(
    "Comparativa de Atletas", "hyrox-compare", ["compare"],
    "now-2h", "now", "",
    [var_session()],
    compare_panels
)

# Corrige los bugs del hyrox-overview existente (run_order de int a string)

with open(BASE_DIR / "hyrox.json") as f:
    overview = json.load(f)

for panel in overview["panels"]:
    pid = panel.get("id")
    if pid not in (13, 14, 16, 17):
        continue
    for tgt in panel.get("targets", []):
        tgt["query"] = (
            Q['duration_by_phase'] if pid == 13 else
            Q['hr_by_phase']       if pid == 14 else
            Q['splits']            if pid == 16 else
            Q['distribution']
        )
        if pid == 16:
            panel.get("fieldConfig", {})["overrides"] = SPLITS_OVR

# Add nav links to overview too
overview["links"] = LINKS

# Write JSON files

def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"  Escrito: {path}")

write_json(BASE_DIR / "hyrox.json",          overview)
write_json(BASE_DIR / "hyrox-live.json",     live_dash)
write_json(BASE_DIR / "hyrox-analysis.json", analysis_dash)
write_json(BASE_DIR / "hyrox-compare.json",  compare_dash)

# Generate ConfigMaps

def make_configmap(json_path, name):
    content = Path(json_path).read_text()
    indented = "\n".join("    " + ln for ln in content.split("\n"))
    key = Path(json_path).name
    cm = (
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
        f"  name: {name}\n  namespace: hyrox\n"
        '  labels:\n    grafana_dashboard: "1"\ndata:\n'
        f"  {key}: |-\n{indented}\n"
    )
    out = BASE_DIR / f"{name}.yaml"
    out.write_text(cm)
    print(f"  ConfigMap: {out}")

make_configmap(BASE_DIR / "hyrox.json",          "grafana-dashboard-hyrox")
make_configmap(BASE_DIR / "hyrox-live.json",     "grafana-dashboard-hyrox-live")
make_configmap(BASE_DIR / "hyrox-analysis.json", "grafana-dashboard-hyrox-analysis")
make_configmap(BASE_DIR / "hyrox-compare.json",  "grafana-dashboard-hyrox-compare")

print("\nValidando JSON...")
for f in ["hyrox.json", "hyrox-live.json", "hyrox-analysis.json", "hyrox-compare.json"]:
    json.loads((BASE_DIR / f).read_text())
    print(f"  {f}")

print("\nListo.")

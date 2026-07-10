#!/usr/bin/env python3
"""Genera infra/manifests/grafana/hyrox-compare.json (dashboard "Comparativa de
Atletas") y regenera su ConfigMap YAML.

Comparativa cara a cara:
- Variable athlete_id multi-select (eliges 2-4 atletas o "All") -> todos los
  paneles filtran a los seleccionados.
- Paneles: bar gauge tiempo total + tabla de medias por atleta, distancia
  recorrida en el tiempo (dónde se abre la brecha), splits cara a cara
  (spread real, no count), HR y velocidad en el tiempo.
- Fuera los promedios de campo (eran análisis de población, no comparación).

NO usar build_dashboards.py (obsoleto). Esta es la fuente del compare.
"""
import json
import os

DS = {"type": "influxdb", "uid": "influxdb-hyrox"}
ACCENT = "#f5e003"

# --- helpers Flux: orden de carrera y etiqueta legible por fase ---------------
ORDER = (
    'if r.phase == "run_1" then "01" else if r.phase == "skierg" then "02" '
    'else if r.phase == "run_2" then "03" else if r.phase == "sled_push" then "04" '
    'else if r.phase == "run_3" then "05" else if r.phase == "sled_pull" then "06" '
    'else if r.phase == "run_4" then "07" else if r.phase == "burpee_bj" then "08" '
    'else if r.phase == "run_5" then "09" else if r.phase == "row" then "10" '
    'else if r.phase == "run_6" then "11" else if r.phase == "farmers_carry" then "12" '
    'else if r.phase == "run_7" then "13" else if r.phase == "sandbag_lunges" then "14" '
    'else if r.phase == "run_8" then "15" else if r.phase == "wall_balls" then "16" else "99"'
)
LABEL = (
    'if r.phase == "run_1" then "Run 1" else if r.phase == "skierg" then "SkiErg" '
    'else if r.phase == "run_2" then "Run 2" else if r.phase == "sled_push" then "Sled Push" '
    'else if r.phase == "run_3" then "Run 3" else if r.phase == "sled_pull" then "Sled Pull" '
    'else if r.phase == "run_4" then "Run 4" else if r.phase == "burpee_bj" then "Burpee BJ" '
    'else if r.phase == "run_5" then "Run 5" else if r.phase == "row" then "Row" '
    'else if r.phase == "run_6" then "Run 6" else if r.phase == "farmers_carry" then "Farmers Carry" '
    'else if r.phase == "run_7" then "Run 7" else if r.phase == "sandbag_lunges" then "Sandbag Lunges" '
    'else if r.phase == "run_8" then "Run 8" else if r.phase == "wall_balls" then "Wall Balls" else r.phase'
)

# filtros comunes: sesión + atletas seleccionados
SEL = (
    '  |> filter(fn: (r) => r._measurement == "biometrics")\n'
    '  |> filter(fn: (r) => r["session_id"] =~ /${session_id}/)\n'
    '  |> filter(fn: (r) => r["athlete_id"] =~ /${athlete_id:regex}/)\n'
)


def target(q):
    return [{"datasource": DS, "query": q, "refId": "A"}]


def ts_custom():
    return {
        "drawStyle": "line", "lineInterpolation": "smooth", "barAlignment": 0,
        "lineWidth": 2, "fillOpacity": 12, "gradientMode": "opacity",
        "spanNulls": True, "insertNulls": False, "showPoints": "never",
        "pointSize": 5, "stacking": {"mode": "none", "group": "A"},
        "axisPlacement": "auto", "axisColorMode": "text", "axisBorderShow": False,
        "axisCenteredZero": False, "axisLabel": "",
        "scaleDistribution": {"type": "linear"},
        "hideFrom": {"tooltip": False, "viz": False, "legend": False},
        "thresholdsStyle": {"mode": "off"}, "lineStyle": {"fill": "solid"},
    }


def ts_options():
    return {
        "legend": {"showLegend": True, "displayMode": "list",
                   "placement": "bottom", "calcs": []},
        "tooltip": {"mode": "multi", "sort": "desc"},
    }


def bar_custom():
    return {
        "fillOpacity": 80, "gradientMode": "none",
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
        "lineWidth": 1, "scaleDistribution": {"type": "linear"},
        "thresholdsStyle": {"mode": "off"},
    }


def row(rid, title, y):
    return {"collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
            "id": rid, "title": title, "type": "row"}


panels = []

# Fila 1: Resumen
panels.append(row(100, "Resumen · atletas seleccionados", 0))

# Bar gauge: tiempo total por atleta (max elapsed_seconds)
q_total = (
    'from(bucket: "telemetry")\n'
    '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
    + SEL +
    '  |> filter(fn: (r) => r._field == "elapsed_seconds")\n'
    '  |> group(columns: ["athlete_id"])\n'
    '  |> max()\n'
    '  |> group()\n'
    '  |> keep(columns: ["athlete_id", "_value"])\n'
    '  |> sort(columns: ["_value"])'
)
panels.append({
    "datasource": DS,
    "fieldConfig": {"defaults": {
        "color": {"mode": "continuous-YlRd"},
        "unit": "dthms", "decimals": 0,
        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
    }, "overrides": []},
    "gridPos": {"x": 0, "y": 1, "w": 12, "h": 8},
    "id": 1,
    "options": {
        "displayMode": "gradient", "orientation": "horizontal",
        "valueMode": "color", "showUnfilled": True,
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "legend": {"showLegend": False},
    },
    "transformations": [{"id": "rowsToFields", "options": {"mappings": [
        {"fieldName": "athlete_id", "handlerKey": "field.name"},
        {"fieldName": "_value", "handlerKey": "field.value"},
    ]}}],
    "title": "Tiempo total (menor = más rápido)",
    "type": "bargauge",
    "targets": target(q_total),
})

# Tabla: medias por atleta (tiempo, HR, velocidad de runs)
q_table = (
    'base = from(bucket: "telemetry")\n'
    '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
    + SEL + '\n'
    'tiempo = base\n'
    '  |> filter(fn: (r) => r._field == "elapsed_seconds")\n'
    '  |> group(columns: ["athlete_id"]) |> max()\n'
    '  |> map(fn: (r) => ({athlete_id: r.athlete_id, metric: "tiempo", val: float(v: r._value)}))\n\n'
    'hr = base\n'
    '  |> filter(fn: (r) => r._field == "heart_rate")\n'
    '  |> group(columns: ["athlete_id"]) |> mean()\n'
    '  |> map(fn: (r) => ({athlete_id: r.athlete_id, metric: "hr", val: r._value}))\n\n'
    'vel = base\n'
    '  |> filter(fn: (r) => r._field == "speed" and r.phase_type == "run")\n'
    '  |> group(columns: ["athlete_id"]) |> mean()\n'
    '  |> map(fn: (r) => ({athlete_id: r.athlete_id, metric: "vel", val: if r._value > 0.0 then 1000.0 / r._value else 0.0}))\n\n'
    'union(tables: [tiempo, hr, vel])\n'
    '  |> group()\n'
    '  |> pivot(rowKey: ["athlete_id"], columnKey: ["metric"], valueColumn: "val")\n'
    '  |> sort(columns: ["tiempo"])'
)
panels.append({
    "datasource": DS,
    "fieldConfig": {"defaults": {
        "color": {"mode": "thresholds"},
        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
        "custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False},
    }, "overrides": [
        {"matcher": {"id": "byName", "options": "athlete_id"},
         "properties": [{"id": "displayName", "value": "Atleta"}, {"id": "custom.width", "value": 130}]},
        {"matcher": {"id": "byName", "options": "tiempo"},
         "properties": [{"id": "displayName", "value": "Tiempo"}, {"id": "unit", "value": "dthms"}, {"id": "decimals", "value": 0}]},
        {"matcher": {"id": "byName", "options": "hr"},
         "properties": [
             {"id": "displayName", "value": "HR medio"}, {"id": "unit", "value": "none"}, {"id": "decimals", "value": 0},
             {"id": "custom.cellOptions", "value": {"mode": "gradient", "type": "color-background"}},
             {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                 {"color": "green", "value": None}, {"color": "yellow", "value": 150}, {"color": "red", "value": 172}]}},
         ]},
        {"matcher": {"id": "byName", "options": "vel"},
         "properties": [{"id": "displayName", "value": "Ritmo (min/km)"}, {"id": "unit", "value": "dthms"}, {"id": "decimals", "value": 0}]},
    ]},
    "gridPos": {"x": 12, "y": 1, "w": 12, "h": 8},
    "id": 15,
    "options": {"cellHeight": "sm", "showHeader": True,
                "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": ""},
                "sortBy": [{"desc": False, "displayName": "Tiempo"}]},
    "transformations": [{"id": "organize", "options": {
        "excludeByName": {}, "renameByName": {},
        "indexByName": {"athlete_id": 0, "tiempo": 1, "vel": 2, "hr": 3}}}],
    "title": "Medias por atleta",
    "type": "table",
    "targets": target(q_table),
})

# Fila 2: Progresión de carrera
panels.append(row(101, "Progresión · ¿dónde se abre la brecha?", 9))

q_dist = (
    'from(bucket: "telemetry")\n'
    '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
    + SEL +
    '  |> filter(fn: (r) => r._field == "distance")\n'
    '  |> group(columns: ["athlete_id"])\n'
    '  |> aggregateWindow(every: v.windowPeriod, fn: max, createEmpty: false)\n'
    '  |> keep(columns: ["_time", "_value", "athlete_id"])'
)
panels.append({
    "datasource": DS,
    "fieldConfig": {"defaults": {
        "color": {"mode": "palette-classic"},
        "custom": ts_custom(), "unit": "lengthm", "decimals": 0,
    }, "overrides": []},
    "gridPos": {"x": 0, "y": 10, "w": 24, "h": 10},
    "id": 2,
    "options": ts_options(),
    "title": "Distancia recorrida en el tiempo: la línea más alta va por delante",
    "type": "timeseries",
    "targets": target(q_dist),
})

# Fila 3: Splits cara a cara
panels.append(row(102, "Splits cara a cara", 20))

q_splits = (
    'from(bucket: "telemetry")\n'
    '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
    + SEL +
    '  |> filter(fn: (r) => r._field == "elapsed_seconds")\n'
    '  |> filter(fn: (r) => r.phase != "roxzone")\n'
    '  |> group(columns: ["athlete_id", "phase"])\n'
    '  |> spread()\n'
    '  |> group()\n'
    '  |> map(fn: (r) => ({\n'
    '      _time: 1970-01-01T00:00:00Z,\n'
    '      athlete_id: r.athlete_id,\n'
    '      _value: float(v: r._value),\n'
    '      run_order: ' + ORDER + ',\n'
    '      phase_label: ' + LABEL + '\n'
    '  }))\n'
    '  |> pivot(rowKey: ["run_order", "phase_label"], columnKey: ["athlete_id"], valueColumn: "_value")\n'
    '  |> group()\n'
    '  |> sort(columns: ["run_order"])\n'
    '  |> drop(columns: ["run_order"])'
)
panels.append({
    "datasource": DS,
    "fieldConfig": {"defaults": {
        "color": {"mode": "palette-classic"}, "custom": bar_custom(),
        "unit": "dthms", "decimals": 0,
    }, "overrides": []},
    "gridPos": {"x": 0, "y": 21, "w": 24, "h": 16},
    "id": 17,
    "options": {
        "barRadius": 0.3, "barWidth": 0.8, "fullHighlight": False, "groupWidth": 0.7,
        "legend": {"calcs": [], "displayMode": "list", "placement": "right", "showLegend": True},
        "orientation": "horizontal", "showValue": "never", "stacking": "none",
        "tooltip": {"mode": "multi", "sort": "desc"},
        "xField": "phase_label", "xTickLabelRotation": 0,
    },
    "title": "Duración de cada split por atleta (spread real)",
    "type": "barchart",
    "targets": target(q_splits),
})

# Fila 4: Biométricas en el tiempo
panels.append(row(103, "Biométricas en el tiempo", 37))


def ts_metric(pid, x, field, title, unit, decimals, mul=None):
    q = (
        'from(bucket: "telemetry")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        + SEL +
        '  |> filter(fn: (r) => r._field == "' + field + '")\n'
        '  |> group(columns: ["athlete_id"])\n'
        '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n'
    )
    if mul:
        q += '  |> map(fn: (r) => ({r with _value: r._value * ' + mul + '}))\n'
    q += '  |> keep(columns: ["_time", "_value", "athlete_id"])'
    return {
        "datasource": DS,
        "fieldConfig": {"defaults": {
            "color": {"mode": "palette-classic"}, "custom": ts_custom(),
            "unit": unit, "decimals": decimals,
        }, "overrides": []},
        "gridPos": {"x": x, "y": 38, "w": 12, "h": 9},
        "id": pid, "options": ts_options(),
        "title": title, "type": "timeseries", "targets": target(q),
    }


panels.append(ts_metric(3, 0, "heart_rate", "Pulso (bpm) en el tiempo", "none", 0))

# Ritmo (min/km) solo en runs: en estaciones la velocidad ~0 y el ritmo se
# dispararía a infinito. Filtramos phase_type=="run" -> 8 segmentos (un run cada
# uno), pace = 1000 / velocidad(m/s) segundos por km, formateado mm:ss con dthms.
pace_custom = ts_custom()
pace_custom["spanNulls"] = False  # estaciones = hueco entre runs, no conectar
q_pace = (
    'from(bucket: "telemetry")\n'
    '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
    + SEL +
    '  |> filter(fn: (r) => r._field == "speed" and r.phase_type == "run")\n'
    '  |> group(columns: ["athlete_id"])\n'
    '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n'
    '  |> filter(fn: (r) => r._value > 0.0)\n'
    '  |> map(fn: (r) => ({r with _value: 1000.0 / r._value}))\n'
    '  |> keep(columns: ["_time", "_value", "athlete_id"])'
)
panels.append({
    "datasource": DS,
    "fieldConfig": {"defaults": {
        "color": {"mode": "palette-classic"}, "custom": pace_custom,
        "unit": "dthms", "decimals": 0,
    }, "overrides": []},
    "gridPos": {"x": 12, "y": 38, "w": 12, "h": 9},
    "id": 4, "options": ts_options(),
    "title": "Ritmo de carrera (min/km) en el tiempo, solo runs",
    "type": "timeseries", "targets": target(q_pace),
})

# Dashboard
dashboard = {
    "annotations": {"list": []},
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 1,
    "id": None,
    "links": [
        {"icon": "external link", "targetBlank": False, "title": "En Carrera", "type": "link", "url": "/d/hyrox-live"},
        {"icon": "external link", "targetBlank": False, "title": "Análisis Individual", "type": "link", "url": "/d/hyrox-analysis"},
        {"icon": "external link", "targetBlank": False, "title": "Comparativa", "type": "link", "url": "/d/hyrox-compare"},
        {"icon": "external link", "targetBlank": False, "title": "Clasificación", "type": "link", "url": "/d/hyrox-clasificacion"},
    ],
    "panels": panels,
    "refresh": "",
    "schemaVersion": 41,
    "tags": ["hyrox", "compare"],
    "templating": {"list": [
        {
            "allValue": ".*",
            "current": {"selected": False, "text": "All", "value": "$__all"},
            "datasource": DS,
            "definition": 'import "influxdata/influxdb/schema"\nschema.tagValues(bucket: "telemetry", tag: "session_id")',
            "hide": 0, "includeAll": True, "label": "Sesión", "multi": False,
            "name": "session_id", "options": [],
            "query": 'import "influxdata/influxdb/schema"\nschema.tagValues(bucket: "telemetry", tag: "session_id")',
            "refresh": 2, "regex": "", "sort": 2, "type": "query",
        },
        {
            "allValue": ".*",
            "current": {"selected": False, "text": "All", "value": "$__all"},
            "datasource": DS,
            "definition": ('import "influxdata/influxdb/schema"\n'
                           'schema.tagValues(\n'
                           '  bucket: "telemetry",\n'
                           '  tag: "athlete_id",\n'
                           '  predicate: (r) => r.session_id =~ /${session_id}/,\n'
                           '  start: -30d,\n'
                           ')'),
            "hide": 0, "includeAll": True, "label": "Atletas", "multi": True,
            "name": "athlete_id", "options": [],
            "query": ('import "influxdata/influxdb/schema"\n'
                      'schema.tagValues(\n'
                      '  bucket: "telemetry",\n'
                      '  tag: "athlete_id",\n'
                      '  predicate: (r) => r.session_id =~ /${session_id}/,\n'
                      '  start: -30d,\n'
                      ')'),
            "refresh": 2, "regex": "", "sort": 1, "type": "query",
        },
    ]},
    "time": {"from": "now-2h", "to": "now"},
    "timepicker": {},
    "timezone": "browser",
    "title": "Comparativa de Atletas",
    "uid": "hyrox-compare",
    "version": 1,
}

BASE = "infra/manifests/grafana"
json_path = os.path.join(BASE, "hyrox-compare.json")
with open(json_path, "w") as f:
    json.dump(dashboard, f, ensure_ascii=False, indent=2)
    f.write("\n")

# Regenerar ConfigMap YAML (no toca el contenido del JSON)
with open(json_path) as f:
    content = f.read().rstrip("\n")
indented = "\n".join("    " + l for l in content.split("\n"))
yaml = (
    "apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
    "  name: grafana-dashboard-hyrox-compare\n  namespace: hyrox\n  labels:\n"
    '    grafana_dashboard: "1"\ndata:\n  hyrox-compare.json: |-\n' + indented + "\n"
)
with open(os.path.join(BASE, "grafana-dashboard-hyrox-compare.yaml"), "w") as f:
    f.write(yaml)

print("OK: hyrox-compare.json + ConfigMap regenerados")
print(f"paneles: {len([p for p in panels if p['type'] != 'row'])} (+{len([p for p in panels if p['type']=='row'])} filas)")

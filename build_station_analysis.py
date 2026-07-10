"""
Añade al dashboard hyrox-analysis la sección "Análisis por estación · impacto y
recuperación" (4 paneles) y reemplaza el stat de Deriva cardiaca por un "Fade":

  A (id 11) barchart  Ritmo por Run vs tu media (s/km)  -> qué estación te frena al correr
  B (id 12) barchart  Potencia media por estación (W)   -> dónde metes más caña
  C (id 13) Business Text  Salida y recuperación por estación (HR media/salida/recuperación)
  D (id 14) Business Text  Coste de cada estación (tiempo estación + impuesto al run siguiente)
  panel 5  -> "Fade Run 1 a 8 (s/km)" = ritmo run_8 - ritmo run_1

Inserta una fila (id 55) en y=25 y desplaza la fila de distribución (60) + tarjetas
(61-69) +20. build_station_cards.py se actualizó para empezar en y=45 (consistencia).

NO ejecutar build_dashboards.py. Edita hyrox-analysis.json, regenera el ConfigMap, kubectl apply.
"""
import json
from pathlib import Path

JP = Path("infra/manifests/grafana/hyrox-analysis.json")
DS = {"type": "influxdb", "uid": "influxdb-hyrox"}

# ---- helpers Flux ----
ORD = ('if r.phase=="run_1" then 1 else if r.phase=="skierg" then 2 else if r.phase=="run_2" then 3 '
 'else if r.phase=="sled_push" then 4 else if r.phase=="run_3" then 5 else if r.phase=="sled_pull" then 6 '
 'else if r.phase=="run_4" then 7 else if r.phase=="burpee_bj" then 8 else if r.phase=="run_5" then 9 '
 'else if r.phase=="row" then 10 else if r.phase=="run_6" then 11 else if r.phase=="farmers_carry" then 12 '
 'else if r.phase=="run_7" then 13 else if r.phase=="sandbag_lunges" then 14 else if r.phase=="run_8" then 15 '
 'else if r.phase=="wall_balls" then 16 else 99')
STPRE = ('if r.phase=="skierg" then "SkiErg" else if r.phase=="sled_push" then "Sled Push" '
 'else if r.phase=="sled_pull" then "Sled Pull" else if r.phase=="burpee_bj" then "Burpee BJ" '
 'else if r.phase=="row" then "Row" else if r.phase=="farmers_carry" then "Farmers" '
 'else if r.phase=="sandbag_lunges" then "Sandbag" else if r.phase=="wall_balls" then "Wall Balls" else r.phase')
TIPO = ('if r.phase=="run_1" or r.phase=="run_2" or r.phase=="run_3" or r.phase=="run_4" or r.phase=="run_5" '
 'or r.phase=="run_6" or r.phase=="run_7" or r.phase=="run_8" then "run" else "station"')
RUNPRE = ('if r.phase=="run_1" then "Run 1" else if r.phase=="run_2" then "Run 2" else if r.phase=="run_3" then "Run 3" '
 'else if r.phase=="run_4" then "Run 4" else if r.phase=="run_5" then "Run 5" else if r.phase=="run_6" then "Run 6" '
 'else if r.phase=="run_7" then "Run 7" else if r.phase=="run_8" then "Run 8" else r.phase')
RUNORD = ('if r.phase=="run_1" then 1 else if r.phase=="run_2" then 2 else if r.phase=="run_3" then 3 '
 'else if r.phase=="run_4" then 4 else if r.phase=="run_5" then 5 else if r.phase=="run_6" then 6 '
 'else if r.phase=="run_7" then 7 else if r.phase=="run_8" then 8 else 99')

RANGE = "|> range(start: v.timeRangeStart, stop: v.timeRangeStop)"


def bar_custom():
    return {"axisBorderShow": False, "axisCenteredZero": False, "axisColorMode": "text",
            "axisLabel": "", "axisPlacement": "auto", "fillOpacity": 80, "gradientMode": "none",
            "hideFrom": {"legend": False, "tooltip": False, "viz": False}, "lineWidth": 1,
            "scaleDistribution": {"type": "linear"}, "thresholdsStyle": {"mode": "off"}}


def bar_options(value_size=12):
    return {"barRadius": 0.25, "barWidth": 0.8, "fullHighlight": False, "groupWidth": 0.7,
            "legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": False},
            "orientation": "auto", "showValue": "always", "stacking": "none",
            "text": {"valueSize": value_size}, "tooltip": {"mode": "single", "sort": "none"},
            "xField": "fase", "xTickLabelRotation": -35, "xTickLabelSpacing": 0}


def bt_options(after):
    return {"afterRender": after, "content": "", "defaultContent": "Sin datos",
            "editors": ["afterRender"], "everyRow": False, "renderMode": "data", "wrap": True,
            "helpers": "", "styles": "", "contentPartials": [], "externalStyles": [], "externalScripts": []}


# Queries
Q_RITMO_RUN = f'''base = from(bucket: "telemetry") {RANGE}
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "speed" and r.athlete_id == "${{athlete_id}}" and r.phase_type == "run")
  |> group(columns: ["phase"]) |> mean()
  |> map(fn: (r) => ({{phase: r.phase, pace: 1000.0 / r._value}}))
mpArr = (base |> group() |> mean(column: "pace") |> findColumn(fn: (key) => true, column: "pace"))
mp = if length(arr: mpArr) > 0 then mpArr[0] else 0.0
base
  |> map(fn: (r) => ({{fase: {RUNPRE}, orden: {RUNORD},
       lento: if (r.pace - mp) > 0.0 then r.pace - mp else 0.0,
       rapido: if (r.pace - mp) < 0.0 then r.pace - mp else 0.0}}))
  |> group() |> sort(columns: ["orden"]) |> keep(columns: ["fase", "lento", "rapido"])'''

Q_POTENCIA = f'''from(bucket: "telemetry") {RANGE}
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "power" and r.athlete_id == "${{athlete_id}}" and r.phase_type == "station")
  |> group(columns: ["phase"]) |> mean()
  |> map(fn: (r) => ({{_value: r._value, fase: {STPRE}, orden: {ORD}}}))
  |> group() |> sort(columns: ["orden"]) |> keep(columns: ["fase", "_value"])'''

# Serie cruda HR por segundo (con elapsed_seconds + fase) para calcular en el
# afterRender: HR entrada (primer seg de la estación), HR salida (último seg) y
# HRR a 60s (HR de salida menos HR 60s después de salir de la estación).
Q_HR = f'''sidArr = (from(bucket: "telemetry") {RANGE}
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "elapsed_seconds" and r.athlete_id == "${{athlete_id}}")
  |> group() |> last() |> findColumn(fn: (key) => true, column: "session_id"))
sid = if length(arr: sidArr) > 0 then sidArr[0] else "__none__"
from(bucket: "telemetry") {RANGE}
  |> filter(fn: (r) => r._measurement == "biometrics" and r.athlete_id == "${{athlete_id}}" and r.session_id == sid and (r._field == "heart_rate" or r._field == "elapsed_seconds"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["elapsed_seconds", "heart_rate", "phase", "phase_type"])
  |> group() |> sort(columns: ["elapsed_seconds"])'''

Q_DUR = f'''from(bucket: "telemetry") {RANGE}
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "elapsed_seconds" and r.athlete_id == "${{athlete_id}}" and r.phase != "roxzone")
  |> group(columns: ["phase"]) |> spread() |> toFloat()
  |> map(fn: (r) => ({{dur: r._value, orden: {ORD}, fase: {STPRE}, tipo: {TIPO}}}))
  |> filter(fn: (r) => r.orden < 99)
  |> group() |> sort(columns: ["orden"]) |> keep(columns: ["fase", "orden", "tipo", "dur"])'''

Q_FADE = f'''import "array"
base = from(bucket: "telemetry") {RANGE}
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "speed" and r.athlete_id == "${{athlete_id}}" and r.phase_type == "run")
  |> group(columns: ["phase"]) |> mean() |> map(fn: (r) => ({{phase: r.phase, pace: 1000.0 / r._value}}))
p1Arr = (base |> filter(fn: (r) => r.phase == "run_1") |> findColumn(fn: (key) => true, column: "pace"))
p8Arr = (base |> filter(fn: (r) => r.phase == "run_8") |> findColumn(fn: (key) => true, column: "pace"))
p1 = if length(arr: p1Arr) > 0 then p1Arr[0] else 0.0
p8 = if length(arr: p8Arr) > 0 then p8Arr[0] else 0.0
array.from(rows: [{{_value: p8 - p1, _time: now()}}])'''

# afterRender C (recuperación)
AR_RECOVERY = r'''
(function(){
 try{
  var el=context.element, df=context.dataFrame; el.style.overflow='hidden';
  if(!df||!df.fields||!df.length){el.innerHTML='<div style="color:#888;padding:10px;font-family:Inter,sans-serif">Sin datos en el rango</div>';return;}
  var get=function(n){var f=df.fields.find(function(f){return f.name===n});if(!f)return[];var v=f.values;return (v&&v.toArray)?v.toArray():v;};
  var PRE={skierg:'SkiErg',sled_push:'Sled Push',sled_pull:'Sled Pull',burpee_bj:'Burpee BJ',row:'Row',farmers_carry:'Farmers',sandbag_lunges:'Sandbag',wall_balls:'Wall Balls'};
  var ORD={skierg:2,sled_push:4,sled_pull:6,burpee_bj:8,row:10,farmers_carry:12,sandbag_lunges:14,wall_balls:16};
  var es=get('elapsed_seconds').map(Number), hr=get('heart_rate').map(Number), ph=get('phase'), pt=get('phase_type');
  var H={};for(var i=0;i<es.length;i++){H[es[i]]=hr[i];}
  var stg={},order=[];
  for(var i=0;i<es.length;i++){ if(pt[i]!=='station')continue; var f=ph[i];
    if(!stg[f]){stg[f]={min:es[i],max:es[i]};order.push(f);} else {if(es[i]<stg[f].min)stg[f].min=es[i]; if(es[i]>stg[f].max)stg[f].max=es[i];} }
  order=order.filter(function(f){return PRE[f];}).sort(function(a,b){return ORD[a]-ORD[b];});
  var hrAt=function(t){for(var k=0;k<=4;k++){if(H[t+k]!=null)return H[t+k];if(H[t-k]!=null)return H[t-k];}return null;};
  var rows=order.map(function(f){
    var entrada=hrAt(stg[f].min), salida=hrAt(stg[f].max);
    var h60=hrAt(stg[f].max+60); var hrr=(h60==null)?null:(salida-h60);
    return {fase:PRE[f],entrada:entrada,salida:salida,rec:hrr};
  });
  var html='<table style="width:100%;border-collapse:collapse;font-family:Inter,Helvetica,sans-serif;color:#e8e8e8">'+
   '<thead><tr style="color:#9a9a9a;font-size:12px">'+
     '<th style="text-align:left;padding:3px 6px">Estaci&oacute;n</th>'+
     '<th style="text-align:right;padding:3px 6px">HR entrada</th>'+
     '<th style="text-align:right;padding:3px 6px">HR salida</th>'+
     '<th style="text-align:right;padding:3px 6px">&Delta; estaci&oacute;n</th>'+
     '<th style="text-align:right;padding:3px 6px">Recup. 60s</th></tr></thead><tbody>';
  rows.forEach(function(r){
    var dlt=r.salida-r.entrada, dup=(dlt>=0);
    var dt=(dup?'&#9650; +':'&#9660; ')+Math.round(dlt)+' bpm';
    var dc=(dup?'#e5534b':'#3fb950'); // sube en estación = rojo (te disparó), baja = verde
    var neg=(r.rec!=null && r.rec<0);
    var rt=(r.rec==null)?'&mdash;':((neg?'&#9650; +':'&#9660; ')+Math.abs(Math.round(r.rec))+' bpm');
    var rc=(r.rec==null)?'#777':(neg?'#e5534b':(r.rec>=18?'#3fb950':(r.rec>=10?'#d4cc1e':'#e5534b')));
    html+='<tr style="border-top:1px solid #ffffff14;font-size:14px">'+
      '<td style="text-align:left;padding:6px;color:#fff">'+r.fase+'</td>'+
      '<td style="text-align:right;padding:6px">'+Math.round(r.entrada)+'</td>'+
      '<td style="text-align:right;padding:6px">'+Math.round(r.salida)+'</td>'+
      '<td style="text-align:right;padding:6px;color:'+dc+'">'+dt+'</td>'+
      '<td style="text-align:right;padding:6px;color:'+rc+';font-weight:600">'+rt+'</td></tr>';
  });
  html+='</tbody></table>';
  el.innerHTML=html;
 }catch(e){ try{context.element.innerHTML='<pre style="color:#f66;padding:8px;white-space:pre-wrap">'+(e&&e.message)+'</pre>';}catch(_){}}
})();
'''

# afterRender D (coste)
AR_COST = r'''
(function(){
 try{
  var el=context.element, df=context.dataFrame; el.style.overflow='hidden';
  if(!df||!df.fields||!df.length){el.innerHTML='<div style="color:#888;padding:10px;font-family:Inter,sans-serif">Sin datos en el rango</div>';return;}
  var get=function(n){var f=df.fields.find(function(f){return f.name===n});if(!f)return[];var v=f.values;return (v&&v.toArray)?v.toArray():v;};
  var fmt=function(s){s=Math.round(s);var m=Math.floor(s/60),ss=s%60;return m+':'+(ss<10?'0':'')+ss;};
  var fa=get('fase'),or=get('orden').map(Number),ti=get('tipo'),du=get('dur').map(Number);
  var runDur={},runs=[];for(var i=0;i<or.length;i++){if(ti[i]==='run'){runDur[or[i]]=du[i];runs.push(du[i]);}}
  var meanRun=runs.length?runs.reduce(function(a,b){return a+b;},0)/runs.length:0;
  var st=[];for(var i=0;i<or.length;i++){ if(ti[i]!=='station')continue; var nd=runDur[or[i]+1];
    var tax=(nd==null||!isFinite(nd))?0:Math.max(0,nd-meanRun); st.push({fase:fa[i],base:du[i],tax:tax,cost:du[i]+tax}); }
  // orden de carrera (como el resto del dashboard); las barras muestran la magnitud del coste
  var maxC=Math.max.apply(null,st.map(function(s){return s.cost;}))||1;
  var W=el.clientWidth||500, H=(el.clientHeight&&el.clientHeight>120)?el.clientHeight:300;
  var headerH=24,n=st.length,rowH=Math.max(22,(H-headerH-2)/n),barH=Math.min(rowH-8,28);
  var labelW=104, costW=58, pad=8, barArea=Math.max(40, W-labelW-costW-pad);
  var html='<div style="font-family:Inter,Helvetica,sans-serif">'+
    '<div style="position:relative;height:'+headerH+'px">'+
      '<span style="position:absolute;left:2px;top:3px;color:#9a9a9a;font-size:12px">Estaci&oacute;n</span>'+
      '<span style="position:absolute;left:'+labelW+'px;top:3px;color:#8a8a2a;font-size:11px">&#9632; tiempo estaci&oacute;n  </span>'+
      '<span style="position:absolute;left:'+(labelW+128)+'px;top:3px;color:#c0392b;font-size:11px">&#9632; impuesto al run</span>'+
      '<span style="position:absolute;right:6px;top:3px;color:#9a9a9a;font-size:12px">Coste</span>'+
    '</div>';
  st.forEach(function(s){
    var bw=barArea*(s.cost/maxC), baseW=barArea*(s.base/maxC), taxW=Math.max(0,bw-baseW), top=((rowH-barH)/2).toFixed(1);
    html+='<div style="position:relative;height:'+rowH+'px">'+
      '<span style="position:absolute;left:2px;top:0;height:'+rowH+'px;line-height:'+rowH+'px;color:#fff;font-size:13px;white-space:nowrap">'+s.fase+'</span>'+
      '<div style="position:absolute;left:'+labelW+'px;top:'+top+'px;height:'+barH+'px;width:'+baseW.toFixed(1)+'px;background:linear-gradient(90deg,#3a3a08,#cfc41e);border-radius:6px 0 0 6px"></div>'+
      (taxW>1?'<div style="position:absolute;left:'+(labelW+baseW).toFixed(1)+'px;top:'+top+'px;height:'+barH+'px;width:'+taxW.toFixed(1)+'px;background:#c0392b;border-radius:0 6px 6px 0"></div>':'')+
      '<span style="position:absolute;right:6px;top:0;height:'+rowH+'px;line-height:'+rowH+'px;color:#e8e8e8;font-size:13px">'+fmt(s.cost)+'</span>'+
    '</div>';
  });
  html+='</div>';
  el.innerHTML=html;
 }catch(e){ try{context.element.innerHTML='<pre style="color:#f66;padding:8px;white-space:pre-wrap">'+(e&&e.message)+'</pre>';}catch(_){}}
})();
'''

# build
d = json.load(open(JP))
# limpiar paneles previos de esta sección si se reejecuta
d["panels"] = [p for p in d["panels"] if p.get("id") not in (11, 12, 13, 14, 55)]

# desplazar distribución (row 60 + tarjetas 61-69) +20 si aún no se hizo (y<45)
for p in d["panels"]:
    if p.get("id", 0) >= 60 and p["gridPos"]["y"] < 45:
        p["gridPos"]["y"] += 20

P = {p.get("id"): p for p in d["panels"]}

# panel 5: Deriva -> Fade
p5 = P[5]
p5["title"] = "Fade Run 1 a 8 (s/km)"
p5["fieldConfig"]["defaults"]["unit"] = "s"
p5["fieldConfig"]["defaults"]["decimals"] = 0
p5["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": [
    {"color": "#3fb950", "value": None}, {"color": "#d4cc1e", "value": 10}, {"color": "#e5534b", "value": 20}]}
p5["targets"][0]["query"] = Q_FADE

# fila de sección
d["panels"].append({"collapsed": False, "gridPos": {"x": 0, "y": 25, "w": 24, "h": 1},
                    "id": 55, "title": "Análisis por estación · impacto y recuperación",
                    "type": "row", "panels": []})

# A) Ritmo por Run vs media
d["panels"].append({
    "datasource": DS, "id": 11, "type": "barchart", "title": "Ritmo por Run vs tu media (+ = más lento)",
    "gridPos": {"x": 0, "y": 26, "w": 12, "h": 9},
    "fieldConfig": {"defaults": {
        "color": {"mode": "palette-classic"}, "custom": bar_custom(), "unit": "s", "decimals": 0,
        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}},
        "overrides": [
            {"matcher": {"id": "byName", "options": "lento"},
             "properties": [{"id": "color", "value": {"fixedColor": "#e5534b", "mode": "fixed"}}, {"id": "displayName", "value": "Más lento que tu media"}]},
            {"matcher": {"id": "byName", "options": "rapido"},
             "properties": [{"id": "color", "value": {"fixedColor": "#3fb950", "mode": "fixed"}}, {"id": "displayName", "value": "Más rápido que tu media"}]},
        ]},
    "options": {**bar_options(12), "showValue": "never", "stacking": "normal",
                "legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": True}},
    "targets": [{"datasource": DS, "query": Q_RITMO_RUN, "refId": "A"}],
})
# B) Potencia media por estación
d["panels"].append({
    "datasource": DS, "id": 12, "type": "barchart", "title": "Potencia media por estación (W)",
    "gridPos": {"x": 12, "y": 26, "w": 12, "h": 9},
    "fieldConfig": {"defaults": {
        "color": {"fixedColor": "#f5e003", "mode": "fixed"}, "custom": bar_custom(), "unit": "watt", "decimals": 0,
        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}}, "overrides": []},
    "options": bar_options(12), "targets": [{"datasource": DS, "query": Q_POTENCIA, "refId": "A"}],
})
# C) Salida y recuperación
d["panels"].append({
    "datasource": DS, "id": 13, "type": "marcusolsson-dynamictext-panel",
    "title": "Salida y recuperación por estación",
    "gridPos": {"x": 0, "y": 35, "w": 12, "h": 10},
    "options": bt_options(AR_RECOVERY),
    "targets": [{"datasource": DS, "query": Q_HR, "refId": "A", "maxDataPoints": 6000}],
})
# D) Coste de cada estación
d["panels"].append({
    "datasource": DS, "id": 14, "type": "marcusolsson-dynamictext-panel",
    "title": "Coste de cada estación (tiempo + impuesto al run)",
    "gridPos": {"x": 12, "y": 35, "w": 12, "h": 10},
    "options": bt_options(AR_COST), "targets": [{"datasource": DS, "query": Q_DUR, "refId": "A"}],
})

json.dump(d, open(JP, "w"), indent=2, ensure_ascii=False)
print("OK. paneles totales:", len(d["panels"]))
print("distribución row 60 y =", P.get(60, {}).get("gridPos", {}).get("y"))

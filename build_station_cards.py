"""
Genera la fila "Distribución por Estación" del dashboard hyrox-analysis:
8 tarjetas (una por estación) tipo Business Text (marcusolsson-dynamictext-panel),
cada una con curva de densidad de los tiempos de la sesión + línea vertical del
atleta + etiqueta de su tiempo flotante (estilo app Hyrox), dibujadas en SVG vía
el código afterRender del plugin (context.element + context.dataFrame).

No ejecutar build_dashboards.py (obsoleto). Este script edita hyrox-analysis.json
directamente, regenera el ConfigMap y se aplica con kubectl.
"""
import json
from pathlib import Path

JP = Path("infra/manifests/grafana/hyrox-analysis.json")
DS = {"type": "influxdb", "uid": "influxdb-hyrox"}

STATIONS = [
    ("skierg", "SkiErg"), ("sled_push", "Sled Push"), ("sled_pull", "Sled Pull"),
    ("burpee_bj", "Burpee Broad Jump"), ("row", "Row"), ("farmers_carry", "Farmers Carry"),
    ("sandbag_lunges", "Sandbag Lunges"), ("wall_balls", "Wall Balls"),
]

# Query: curva (duracion, dist) de la sesión del atleta + adv (su tiempo, constante).
# agg = "spread" para estaciones/runs (duración = max-min de elapsed); "count" para
# la roxzone (no-contigua: su duración total = nº de lecturas, 1/seg).
def q_bt(phase, agg="spread"):
    return '''import "math"

sidArr = (from(bucket: "telemetry")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "elapsed_seconds" and r.athlete_id == "${{athlete_id}}")
  |> group() |> last()
  |> findColumn(fn: (key) => true, column: "session_id"))
sid = if length(arr: sidArr) > 0 then sidArr[0] else "__none__"

durations = from(bucket: "telemetry")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "elapsed_seconds" and r.phase == "{phase}" and r.session_id == sid)
  |> group(columns: ["athlete_id"]) |> {agg}() |> group() |> toFloat()

mnArr = (durations |> min() |> findColumn(fn: (key) => true, column: "_value"))
mxArr = (durations |> max() |> findColumn(fn: (key) => true, column: "_value"))
mn = if length(arr: mnArr) > 0 then mnArr[0] else 0.0
mx = if length(arr: mxArr) > 0 then mxArr[0] else 0.0
span = if mx - mn <= 0.0 then 10.0 else mx - mn
lo = if mn - span * 0.3 < 0.0 then 0.0 else mn - span * 0.3
w = ((mx + span * 0.3) - lo) / 40.0

advArr = (from(bucket: "telemetry")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "elapsed_seconds" and r.phase == "{phase}" and r.session_id == sid and r.athlete_id == "${{athlete_id}}")
  |> group(columns: ["athlete_id"]) |> {agg}() |> group() |> toFloat()
  |> findColumn(fn: (key) => true, column: "_value"))
adv = if length(arr: advArr) > 0 then advArr[0] else 0.0

// puesto del atleta en la estación = nº de atletas con menor tiempo + 1; tot = total de atletas
posArr = (durations |> filter(fn: (r) => r._value < adv) |> count() |> findColumn(fn: (key) => true, column: "_value"))
pos = if length(arr: posArr) > 0 then posArr[0] else 0
totArr = (durations |> count() |> findColumn(fn: (key) => true, column: "_value"))
tot = if length(arr: totArr) > 0 then totArr[0] else 0

// duraciones crudas (1 por atleta) -> la curva (KDE) se calcula en el afterRender
durations
  |> map(fn: (r) => ({{duracion: r._value, adv: adv, pos: pos + 1, tot: tot}}))'''.format(phase=phase, agg=agg)

# afterRender: réplica de la tarjeta de la app Hyrox (header: nombre + tiempo a la
# derecha, subtítulo "#pos of tot · Top X%"; chart: curva degradada + línea vertical
# + píldora con el tiempo en la punta; eje X solo con los 2 extremos). __LABEL__ se
# sustituye por el nombre de la estación en card_panel.
AFTER_RENDER = r'''
(function(){
  try {
    var el = context.element, df = context.dataFrame, LABEL = "__LABEL__";
    el.style.overflowX = 'hidden'; el.style.maxWidth = '100%';
    var fmt=function(s){s=Math.round(s);var m=Math.floor(s/60),ss=s%60;return m+':'+(ss<10?'0':'')+ss;};
    if (!df || !df.fields || !df.length) { el.innerHTML='<div style="color:#888;padding:10px;font-family:Inter,sans-serif">Sin datos en el rango</div>'; return; }
    var get=function(n){var f=df.fields.find(function(f){return f.name===n});if(!f)return[];var v=f.values;return (v&&v.toArray)?v.toArray():v;};
    var durs=get('duracion').map(Number).filter(function(x){return isFinite(x);}), adv=get('adv')[0];
    var pos=get('pos')[0], tot=get('tot')[0];
    if(!durs.length){ el.innerHTML='<div style="color:#888;padding:10px">Sin datos</div>'; return; }
    var tlabel=fmt(adv);
    var pct=(pos&&tot)?(pos/tot*100):0;
    var pctTxt=pct<10?Math.round(pct):pct.toFixed(1);
    var rankLine=(pos&&tot)?('#'+pos+' of '+tot+' &middot; Top '+pctTxt+'%'):'&nbsp;';
    // --- curva: densidad por KDE gaussiana sobre las duraciones (suave con pocos atletas) ---
    var W=el.clientWidth||1000, SH=166, pb=22, pt=28, pad=4;
    var n=durs.length, mean=durs.reduce(function(a,b){return a+b;},0)/n;
    var sd=Math.sqrt(durs.reduce(function(a,b){return a+(b-mean)*(b-mean);},0)/n)||0;
    var dmin=Math.min.apply(null,durs), dmax=Math.max.apply(null,durs);
    // bandwidth PEQUEÑO -> curva afilada/picuda. Con bw pequeño la KDE cae a ~0 (suelo) en
    // cuanto te alejas de los datos, así que la curva puede recorrer TODO el ancho corriendo
    // por el suelo donde no hay tiempos (como la app) y solo se abulta donde hay densidad.
    var bw=Math.max(0.30*sd*Math.pow(n,-0.2), (dmax-dmin)/70, 2);
    var span=(dmax-dmin)||1;
    // eje amplio a ambos lados: la curva LLEGA hasta los bordes (por el suelo). No se recorta:
    // a la izquierda del más rápido la KDE ya es ~0 (no se abulta), no por recorte sino por bw.
    var xmin=Math.max(0,dmin-span*0.28), xmax=dmax+span*0.28, arng=(xmax-xmin)||1, iw=W-pad*2;
    var N=180, xs=[], ys=[], ymax=0;
    for(var i=0;i<=N;i++){var x=xmin+arng*i/N, s=0;
      for(var j=0;j<n;j++){var u=(x-durs[j])/bw; s+=Math.exp(-0.5*u*u);}
      xs.push(x); ys.push(s); if(s>ymax)ymax=s;}
    ymax=ymax||1;
    var sx=function(x){return pad+(x-xmin)/arng*iw;};
    var sy=function(y){return pt+(1-y/ymax)*(SH-pt-pb);};
    var pts=xs.map(function(x,i){return sx(x).toFixed(1)+','+sy(ys[i]).toFixed(1);});
    var area='M'+sx(xmin).toFixed(1)+','+(SH-pb)+' L'+pts.join(' L')+' L'+sx(xmax).toFixed(1)+','+(SH-pb)+' Z';
    var ax=sx(adv);
    var gid='hxg'+Math.floor(Math.random()*1e9);
    // píldora con el tiempo en la punta de la línea
    var pillW=tlabel.length*8+14, pillH=20;
    var pillX=Math.min(Math.max(ax+3, pad), W-pillW-pad), pillY=pt-6;
    var svg='<svg width="100%" height="'+SH+'" viewBox="0 0 '+W+' '+SH+'" preserveAspectRatio="none" style="display:block;max-width:100%">'+
      '<defs><linearGradient id="'+gid+'" x1="0" x2="0" y1="0" y2="1">'+
        '<stop offset="0" stop-color="#f2e600" stop-opacity="0.6"/>'+
        '<stop offset="0.55" stop-color="#cfc400" stop-opacity="0.22"/>'+
        '<stop offset="1" stop-color="#cfc400" stop-opacity="0"/></linearGradient></defs>'+
      '<path d="'+area+'" fill="url(#'+gid+')"/>'+
      '<polyline points="'+pts.join(' ')+'" fill="none" stroke="#ffe600" stroke-width="2.5" stroke-linejoin="round"/>'+
      '<line x1="'+ax.toFixed(1)+'" y1="'+(SH-pb)+'" x2="'+ax.toFixed(1)+'" y2="'+pt+'" stroke="#ffe600" stroke-width="2"/>'+
      '<text x="'+(pad+2)+'" y="'+(SH-6)+'" fill="#8a8a8a" font-size="13" font-family="Inter,Helvetica,sans-serif" text-anchor="start">'+fmt(xmin)+'</text>'+
      '<text x="'+(W-pad-2)+'" y="'+(SH-6)+'" fill="#8a8a8a" font-size="13" font-family="Inter,Helvetica,sans-serif" text-anchor="end">'+fmt(xmax)+'</text>'+
      '<rect x="'+pillX.toFixed(1)+'" y="'+pillY+'" width="'+pillW+'" height="'+pillH+'" rx="4" fill="#1c1c1c" fill-opacity="0.88"/>'+
      '<text x="'+(pillX+pillW/2).toFixed(1)+'" y="'+(pillY+14)+'" fill="#fff200" font-size="13" font-weight="700" text-anchor="middle" font-family="Inter,Helvetica,sans-serif">'+tlabel+'</text>'+
      '</svg>';
    // --- header HTML + chart ---
    el.innerHTML=
      '<div style="font-family:Inter,Helvetica,sans-serif;padding:2px 6px 0">'+
        '<div style="display:flex;justify-content:space-between;align-items:baseline">'+
          '<span style="font-size:21px;font-weight:800;color:#fff;letter-spacing:-0.3px">'+LABEL+'</span>'+
          '<span style="font-size:21px;font-weight:800;color:#fff200">'+tlabel+'</span>'+
        '</div>'+
        '<div style="font-size:13px;color:#8a8a8a;margin-top:3px">'+rankLine+'</div>'+
      '</div>'+svg;
  } catch(e){ try{context.element.innerHTML='<pre style="color:#f66;padding:8px;white-space:pre-wrap">'+(e&&e.message)+'</pre>';}catch(_){} }
})();
'''

def card_panel(pid, y, label, q):
    return {
        "datasource": DS,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 7}, "id": pid,
        "options": {
            "afterRender": AFTER_RENDER.replace("__LABEL__", label), "content": "",
            "defaultContent": "Sin datos", "editors": ["afterRender"], "everyRow": False,
            "renderMode": "data", "wrap": True, "helpers": "", "styles": "",
            "contentPartials": [], "externalStyles": [], "externalScripts": []
        },
        "targets": [{"datasource": DS, "query": q, "refId": "A"}],
        "title": "", "type": "marcusolsson-dynamictext-panel"
    }

d = json.load(open(JP))
# Limpiar paneles de estación previos (row 60, trends/stats 61-77, PoC 80)
d["panels"] = [p for p in d["panels"] if p.get("id") not in set(range(60, 81))]

# y=45: deja sitio arriba a la sección "Análisis por estación" (build_station_analysis.py)
d["panels"].append({"collapsed": False, "gridPos": {"x": 0, "y": 45, "w": 24, "h": 1},
                    "id": 60, "title": "Distribución por Estación (tu tiempo vs. la sesión)",
                    "type": "row", "panels": []})
for i, (phase, label) in enumerate(STATIONS):
    d["panels"].append(card_panel(61 + i, 46 + i * 7, label, q_bt(phase)))
# Tarjeta Roxzone (duración total de transición por atleta = count de lecturas roxzone)
d["panels"].append(card_panel(69, 46 + len(STATIONS) * 7, "Roxzone", q_bt("roxzone", agg="count")))

json.dump(d, open(JP, "w"), indent=2, ensure_ascii=False)
print("total panels:", len(d["panels"]),
      "| cards:", len([p for p in d["panels"] if p.get("type") == "marcusolsson-dynamictext-panel"]))

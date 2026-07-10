"""
Reconstruye el panel 7 "Splits por fase" del dashboard hyrox-analysis al estilo
de la app Hyrox: cada fase es una barra con degradado (escala global de olivo a
amarillo) y esquinas redondeadas, con el nombre dentro, y dos columnas a la
derecha (Tiempo, Posición). Posición = puesto del atleta en esa fase entre los
atletas de su sesión. Se dibuja con el plugin Business Text (marcusolsson-
dynamictext-panel) vía afterRender, igual que las tarjetas de distribución.

No ejecutar build_dashboards.py. Este script edita hyrox-analysis.json, regenera
el ConfigMap y se aplica con kubectl.
"""
import json
from pathlib import Path

JP = Path("infra/manifests/grafana/hyrox-analysis.json")
DS = {"type": "influxdb", "uid": "influxdb-hyrox"}

ORDEN = ('if r.phase == "run_1" then 1 else if r.phase == "skierg" then 2 '
 'else if r.phase == "run_2" then 3 else if r.phase == "sled_push" then 4 '
 'else if r.phase == "run_3" then 5 else if r.phase == "sled_pull" then 6 '
 'else if r.phase == "run_4" then 7 else if r.phase == "burpee_bj" then 8 '
 'else if r.phase == "run_5" then 9 else if r.phase == "row" then 10 '
 'else if r.phase == "run_6" then 11 else if r.phase == "farmers_carry" then 12 '
 'else if r.phase == "run_7" then 13 else if r.phase == "sandbag_lunges" then 14 '
 'else if r.phase == "run_8" then 15 else if r.phase == "wall_balls" then 16 '
 'else if r.phase == "roxzone" then 17 else 99')
PRETTY = ('if r.phase == "run_1" then "Run 1" else if r.phase == "skierg" then "SkiErg" '
 'else if r.phase == "run_2" then "Run 2" else if r.phase == "sled_push" then "Sled Push" '
 'else if r.phase == "run_3" then "Run 3" else if r.phase == "sled_pull" then "Sled Pull" '
 'else if r.phase == "run_4" then "Run 4" else if r.phase == "burpee_bj" then "Burpee Broad Jump" '
 'else if r.phase == "run_5" then "Run 5" else if r.phase == "row" then "Row" '
 'else if r.phase == "run_6" then "Run 6" else if r.phase == "farmers_carry" then "Farmers Carry" '
 'else if r.phase == "run_7" then "Run 7" else if r.phase == "sandbag_lunges" then "Sandbag Lunges" '
 'else if r.phase == "run_8" then "Run 8" else if r.phase == "wall_balls" then "Wall Balls" '
 'else if r.phase == "roxzone" then "Roxzone" else r.phase')

# Query: 1 fila por (atleta, fase) con dur (duración) + me (1 si es ${athlete_id}).
# La sesión se aísla por el session_id del atleta. No-roxzone = spread(elapsed),
# roxzone = count (no-contigua). El ranking (pos) y la escala de barra se calculan
# en el afterRender (más robusto que un join en Flux).
QUERY = '''sidArr = (from(bucket: "telemetry")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "elapsed_seconds" and r.athlete_id == "${athlete_id}")
  |> group() |> last()
  |> findColumn(fn: (key) => true, column: "session_id"))
sid = if length(arr: sidArr) > 0 then sidArr[0] else "__none__"

nonrox = from(bucket: "telemetry")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "elapsed_seconds" and r.session_id == sid and r.phase != "roxzone")
  |> group(columns: ["athlete_id", "phase"]) |> spread() |> toFloat()
  |> group() |> keep(columns: ["athlete_id", "phase", "_value"])

rox = from(bucket: "telemetry")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "elapsed_seconds" and r.session_id == sid and r.phase == "roxzone")
  |> group(columns: ["athlete_id"]) |> count() |> toFloat()
  |> map(fn: (r) => ({athlete_id: r.athlete_id, phase: "roxzone", _value: r._value}))
  |> group() |> keep(columns: ["athlete_id", "phase", "_value"])

union(tables: [nonrox, rox])
  |> map(fn: (r) => ({
      dur: r._value,
      me: if r.athlete_id == "${athlete_id}" then 1 else 0,
      orden: __ORDEN__,
      fase: __PRETTY__
  }))
  |> filter(fn: (r) => r.orden < 99)
  |> group()
  |> keep(columns: ["fase", "orden", "dur", "me"])
  |> sort(columns: ["orden"])'''.replace("__ORDEN__", ORDEN).replace("__PRETTY__", PRETTY)

# afterRender: lista estilo app Hyrox. Barra con degradado de escala GLOBAL
# (background-size = ancho del área de barras => barras cortas quedan oscuras,
# las largas llegan al amarillo), nombre dentro, columnas Tiempo y Posición.
AFTER_RENDER = r'''
(function(){
  try{
    var el=context.element, df=context.dataFrame;
    el.style.overflow='hidden';
    if(!df||!df.fields||!df.length){el.innerHTML='<div style="color:#888;padding:10px;font-family:Inter,sans-serif">Sin datos en el rango</div>';return;}
    var get=function(n){var f=df.fields.find(function(f){return f.name===n});if(!f)return[];var v=f.values;return (v&&v.toArray)?v.toArray():v;};
    var fmt=function(s){s=Math.round(s);var m=Math.floor(s/60),ss=s%60;return m+':'+(ss<10?'0':'')+ss;};
    var fa=get('fase'), du=get('dur').map(Number), me=get('me').map(Number);
    if(!fa.length){el.innerHTML='<div style="color:#888;padding:10px">Sin datos</div>';return;}
    var order=[],G={};
    for(var i=0;i<fa.length;i++){var f=fa[i];if(!G[f]){G[f]={d:[],my:null};order.push(f);}G[f].d.push(du[i]);if(me[i]===1)G[f].my=du[i];}
    var maxMy=0;order.forEach(function(f){var m=G[f].my||0;if(m>maxMy)maxMy=m;});
    if(maxMy<=0)maxMy=1;
    order.forEach(function(f){var g=G[f];var my=(g.my==null?0:g.my);g.pos=g.d.filter(function(d){return d<my;}).length+1;});
    var W=el.clientWidth||900;
    var H=(el.clientHeight&&el.clientHeight>140)?el.clientHeight:560;
    var headerH=28, n=order.length;
    var rowH=Math.max(24,(H-headerH-2)/n), barH=Math.min(rowH-6,42);
    var posW=54, timeW=62, gapR=14;
    var rightReg=8+posW+gapR+timeW+12, barArea=Math.max(60, W-rightReg);
    var fs=Math.max(12, Math.min(18, Math.round(barH*0.55)));
    var timeR=8+posW+gapR, posR=8;
    var html='<div style="position:relative;height:'+headerH+'px;font-family:Inter,Helvetica,sans-serif">'+
      '<span style="position:absolute;left:2px;top:2px;color:#fff;font-weight:800;font-size:16px">Splits</span>'+
      '<span style="position:absolute;right:'+timeR+'px;top:6px;width:'+timeW+'px;text-align:right;color:#9a9a9a;font-size:13px">Tiempo</span>'+
      '<span style="position:absolute;right:'+posR+'px;top:6px;width:'+posW+'px;text-align:right;color:#9a9a9a;font-size:13px">Posici&oacute;n</span>'+
      '</div>';
    order.forEach(function(f){
      var g=G[f], my=(g.my==null?0:g.my);
      var bw=Math.max(8, barArea*(my/maxMy));
      html+='<div style="position:relative;height:'+rowH+'px;font-family:Inter,Helvetica,sans-serif">'+
        '<div style="position:absolute;left:0;top:'+((rowH-barH)/2).toFixed(1)+'px;height:'+barH+'px;width:'+bw.toFixed(1)+'px;border-radius:9px;background:linear-gradient(90deg,#26260a,#eadf1e);background-size:'+barArea.toFixed(0)+'px 100%;background-repeat:no-repeat"></div>'+
        '<span style="position:absolute;left:14px;top:0;height:'+rowH+'px;line-height:'+rowH+'px;color:#fff;font-size:'+fs+'px;white-space:nowrap">'+f+'</span>'+
        '<span style="position:absolute;right:'+timeR+'px;top:0;height:'+rowH+'px;line-height:'+rowH+'px;width:'+timeW+'px;text-align:right;color:#e8e8e8;font-size:15px">'+fmt(my)+'</span>'+
        '<span style="position:absolute;right:'+posR+'px;top:0;height:'+rowH+'px;line-height:'+rowH+'px;width:'+posW+'px;text-align:right;color:#e8e8e8;font-size:15px">'+g.pos+'</span>'+
        '</div>';
    });
    el.innerHTML=html;
  }catch(e){ try{context.element.innerHTML='<pre style="color:#f66;padding:8px;white-space:pre-wrap">'+(e&&e.message)+'</pre>';}catch(_){}}
})();
'''

panel = {
    "datasource": DS,
    "gridPos": {"x": 0, "y": 5, "w": 12, "h": 20},
    "id": 7,
    "options": {
        "afterRender": AFTER_RENDER, "content": "", "defaultContent": "Sin datos",
        "editors": ["afterRender"], "everyRow": False, "renderMode": "data",
        "wrap": True, "helpers": "", "styles": "", "contentPartials": [],
        "externalStyles": [], "externalScripts": []
    },
    "targets": [{"datasource": DS, "query": QUERY, "refId": "A"}],
    "title": "Splits por fase",
    "type": "marcusolsson-dynamictext-panel",
}

d = json.load(open(JP))
d["panels"] = [panel if p.get("id") == 7 else p for p in d["panels"]]
json.dump(d, open(JP, "w"), indent=2, ensure_ascii=False)
print("panel 7 -> marcusolsson-dynamictext-panel (estilo app Hyrox)")

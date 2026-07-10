"""
Reemplaza el panel 5 "Atletas por fase en este momento" del dashboard hyrox-live
por un mapa de carrera en vivo: las 16 fases en fila (todo el recorrido, también
las vacías) y en cada fase los atletas presentes en ese momento como dots
numerados apilados. Runs en azul, estaciones en rojo, fases vacías atenuadas.
Dibujado con Business Text (marcusolsson-dynamictext-panel).

No ejecutar build_dashboards.py. Edita hyrox-live.json, regenera el ConfigMap, kubectl apply.
"""
import json
from pathlib import Path

JP = Path("infra/manifests/grafana/hyrox-live.json")
DS = {"type": "influxdb", "uid": "influxdb-hyrox"}

# Cada atleta -> su fase actual. Ventana corta (-10s): solo cuentan los que están
# emitiendo ahora mismo. Al terminar la carrera un atleta deja de emitir y desaparece del
# mapa en ~10s (con -120s se quedaba pegado 2 minutos). 1 lectura/seg (o más a
# speedup alto) => 10s basta de sobra para no perder a uno activo por un hueco.
QUERY = '''from(bucket: "telemetry")
  |> range(start: -10s)
  |> filter(fn: (r) => r._measurement == "biometrics" and r._field == "heart_rate")
  |> group(columns: ["athlete_id"])
  |> last()
  |> keep(columns: ["athlete_id", "phase", "phase_type"])
  |> group()'''

AFTER_RENDER = r'''
(function(){
 try{
  var el=context.element, df=context.dataFrame; el.style.overflow='hidden';
  var get=function(n){if(!df||!df.fields)return[];var f=df.fields.find(function(f){return f.name===n});if(!f)return[];var v=f.values;return (v&&v.toArray)?v.toArray():v;};
  var PHASES=[
   ['run_1','Run 1','run'],['skierg','SkiErg','station'],['run_2','Run 2','run'],['sled_push','Sled Push','station'],
   ['run_3','Run 3','run'],['sled_pull','Sled Pull','station'],['run_4','Run 4','run'],['burpee_bj','Burpee','station'],
   ['run_5','Run 5','run'],['row','Row','station'],['run_6','Run 6','run'],['farmers_carry','Farmers','station'],
   ['run_7','Run 7','run'],['sandbag_lunges','Sandbag','station'],['run_8','Run 8','run'],['wall_balls','Wall Balls','station']];
  var RUN='#5f636b', STA='#f5e003';  // runs gris oscuro, estaciones (workouts) amarillo (marca Hyrox)
  var aid=get('athlete_id'), ph=get('phase');
  var byPhase={};
  for(var i=0;i<aid.length;i++){ var n=parseInt(String(aid[i]).replace(/\D/g,''),10); if(isNaN(n))n=0; (byPhase[ph[i]]=byPhase[ph[i]]||[]).push(n); }
  var total=aid.length, maxDots=5;
  var dot=function(txt,bg,fg,solid){return '<div style="width:26px;height:26px;border-radius:50%;background:'+bg+';color:'+fg+';font-weight:700;font-size:12px;line-height:26px;text-align:center;margin:3px auto;'+(solid?'box-shadow:0 1px 3px #0007':'border:1px solid '+fg+'55')+'">'+txt+'</div>';};
  var cols=PHASES.map(function(P){
    var key=P[0],name=P[1],col=(P[2]==='run')?RUN:STA,fg=(P[2]==='run')?'#fff':'#1a1a1a';
    var ath=(byPhase[key]||[]).slice().sort(function(a,b){return a-b;});
    var cnt=ath.length, empty=(cnt===0);
    var dots='';
    var vis=(cnt>maxDots)?ath.slice(0,maxDots-1):ath;
    vis.forEach(function(n){ dots+=dot(n,col,fg,true); });
    if(cnt>maxDots){ dots+=dot('+'+(cnt-(maxDots-1)),'#26272b',col,false); }
    return '<div style="flex:1 1 0;min-width:0;text-align:center">'+
      '<div style="margin:0 3px;height:22px;line-height:22px;border-radius:11px;background:'+col+';color:'+fg+';font-size:10.5px;font-weight:700;'+
        'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 4px;opacity:'+(empty?0.28:1)+'">'+name+'</div>'+
      '<div style="color:'+col+';font-size:13px;font-weight:800;opacity:'+(empty?0.35:1)+';margin:2px 0 1px">'+cnt+'</div>'+
      '<div>'+dots+'</div>'+
    '</div>';
  }).join('');
  var head='<div style="display:flex;justify-content:space-between;align-items:center;font-family:Inter,Helvetica,sans-serif;margin:0 2px 4px">'+
    '<span style="color:#9a9a9a;font-size:11px"><span style="color:'+RUN+'">●</span> runs &nbsp; <span style="color:'+STA+'">●</span> estaciones</span>'+
    '<span style="color:#e8e8e8;font-size:12px">'+(total>0?(''+total+' en carrera'):'sin carrera activa')+'</span></div>';
  el.innerHTML=head+'<div style="display:flex;align-items:flex-start;font-family:Inter,Helvetica,sans-serif">'+cols+'</div>';
 }catch(e){ try{context.element.innerHTML='<pre style="color:#f66;padding:8px;white-space:pre-wrap">'+(e&&e.message)+'</pre>';}catch(_){}}
})();
'''

panel = {
    "datasource": DS,
    "gridPos": {"x": 0, "y": 6, "w": 24, "h": 8},
    "id": 5,
    "options": {
        "afterRender": AFTER_RENDER, "content": "", "defaultContent": "Sin datos",
        "editors": ["afterRender"], "everyRow": False, "renderMode": "data", "wrap": True,
        "helpers": "", "styles": "", "contentPartials": [], "externalStyles": [], "externalScripts": []
    },
    "targets": [{"datasource": DS, "query": QUERY, "refId": "A"}],
    "title": "Atletas por fase en este momento",
    "type": "marcusolsson-dynamictext-panel",
}

d = json.load(open(JP))
# panel 5 pasa de h7 a h8 -> empujar 1 fila los paneles de debajo (y>=13)
for p in d["panels"]:
    if p.get("id") != 5 and p.get("gridPos", {}).get("y", 0) >= 13:
        p["gridPos"]["y"] += 1
d["panels"] = [panel if p.get("id") == 5 else p for p in d["panels"]]
json.dump(d, open(JP, "w"), indent=2, ensure_ascii=False)
print("panel 5 -> mapa de carrera (marcusolsson). paneles:", len(d["panels"]))

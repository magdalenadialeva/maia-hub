/* =====================================================================
   MAIA · Hub v27 (oct-2026)
   - Motor de insights por marca (un solo motor para Dashboard, Análisis,
     Seguimiento, Reporte y Agencia: todos dicen lo mismo).
   - Tags de arquetipo / ángulo por creativo (config/brands.yaml).
   - Etapa de funnel deducida por señales de pauta.
   Se carga DESPUÉS del script principal de index.html: reutiliza sus
   helpers (agg, kpis, manualIn, movesOf, impact...) y pisa las vistas.
   ===================================================================== */

const BRANDS = window.BRANDS || {};
const WD = ['dom','lun','mar','mié','jue','vie','sáb'];
const WDL = ['domingo','lunes','martes','miércoles','jueves','viernes','sábado'];
function wdOf(iso){ const p=iso.split('-').map(Number); return new Date(Date.UTC(p[0],p[1]-1,p[2])).getUTCDay(); }
function fmtDM(iso){ const p=iso.split('-'); return (+p[2])+'/'+(+p[1]); }
function fmtWd(iso){ return WDL[wdOf(iso)]+' '+fmtDM(iso); }
function throughOf(b){ const D=DATA[b]; const own=D.through||(D.dates&&D.dates.length?D.dates[D.dates.length-1]:DMAX);
  return (!isLost(b)&&CLOSED&&CLOSED>own)?CLOSED:own; }  // cuenta pausada: la semana es la de hoy, no la de su último dato
function $m(v){ return '$'+money(v); }
function pctTxt(x){ return Math.round(x*100)+'%'; }
function chg(a,b){ return (b>0&&isFinite(a))?(a-b)/b:null; }
function isActiveBrand(b){ return !isLost(b); }
function brand(b){ return BRANDS[b]||{}; }

/* ---------- Tags: arquetipo / ángulo ---------- */
function normBase(n){
  let s=String(n||'').toLowerCase().replace(/\s+/g,' ').trim();
  let prev; do{ prev=s; s=s.replace(/\s*[-–]\s*(copia|copy)(\s*\d+)?\s*$/,'').replace(/\s+(copia|copy)(\s*\d+)?\s*$/,'').trim(); }while(s!==prev);
  return s;
}
function tagOf(b,ad){
  const C=(brand(b).creativos)||{}; const base=normBase(ad), low=String(ad||'').toLowerCase();
  for(const k in C){ if(k[0]!=='~' && normBase(k)===base) return {...C[k],src:'tag'}; }
  for(const k in C){ if(k[0]==='~' && low.includes(k.slice(1).toLowerCase())) return {...C[k],src:'tag'}; }
  const m=String(ad||'').match(/[áa]ngulo\s+(.+?)(\s+[-–·]\s+|$)/i);
  if(m) return {angulo:m[1].trim(),src:'nombre'};
  return {src:'none'};
}

/* ---------- Qué es "resultado" en una ventana ---------- */
const KIND_FIELD={msg:'msgs',purchase:'purch',lead:'leads',traffic:'lc',other:'lc'};
const KIND_UNIT={msg:['conversación','conversaciones'],purchase:['compra','compras'],lead:['lead','leads'],traffic:['clic','clics'],other:['clic','clics']};
function resultDef(b,from,to,forceCi){
  const D=DATA[b];
  let kind=null, cis=null;
  if(forceCi!=null){ kind=campKind(D,forceCi); cis=new Set([forceCi]); }
  else if(D.camps&&D.camps.length){
    const sp={}; for(const r of D.rows){ const dt=D.dates[r[0]]; if(dt<from||dt>to) continue;
      const ci=campOf(D,r[1]); const k=campKind(D,ci); sp[k]=(sp[k]||0)+r[FI.spend]; }
    const tot=Object.values(sp).reduce((a,c)=>a+c,0);
    const ks=Object.keys(sp).sort((a,c)=>sp[c]-sp[a]);
    if(ks.length){ kind=ks[0];
      if(ks.length>1 && sp[ks[0]]/tot<0.8 && ks.includes('purchase')) kind='purchase';
      // sólo las campañas de ese tipo (no mezclamos conversaciones con compras)
      cis=new Set(); (D.camps||[]).forEach((nm,ci)=>{ if(campKind(D,ci)===kind) cis.add(ci); });
      if(ks.length===1) cis=null; }
  }
  if(!kind){ const o=D.obj; kind=(o==='msg')?'msg':(o==='lead'?'lead':(o==='traffic'?'traffic':'purchase')); }
  if(kind==='other') kind='traffic';
  const field=KIND_FIELD[kind]||'purch';
  return {kind,cis,field,unit:KIND_UNIT[kind]||['resultado','resultados'],sales:kind==='purchase'};
}
function rowOk(D,r,R){ return !R.cis || R.cis.has(campOf(D,r[1])); }
function sumWin(b,from,to,R){
  const D=DATA[b]; const T={}; FIELDS.forEach(k=>T[k]=0); const per={};
  for(const r of D.rows){ const dt=D.dates[r[0]]; if(dt<from||dt>to||!rowOk(D,r,R)) continue;
    for(const k of FIELDS) T[k]+=r[FI[k]];
    const a=r[1]; if(!per[a]){per[a]={}; FIELDS.forEach(k=>per[a][k]=0);} for(const k of FIELDS) per[a][k]+=r[FI[k]]; }
  return {T,per};
}
function firstLast(b,R){ // primer / último día con gasto de cada anuncio (todo el historial)
  const D=DATA[b]; const m={};
  for(const r of D.rows){ if(!(r[FI.spend]>0)||!rowOk(D,r,R)) continue; const dt=D.dates[r[0]], a=r[1];
    if(!m[a]) m[a]={first:dt,last:dt}; else { if(dt<m[a].first)m[a].first=dt; if(dt>m[a].last)m[a].last=dt; } }
  return m;
}
function stats(v,R){
  const res=v[R.field]||0, spend=v.spend||0, impr=v.impr||0, lc=v.lc||0;
  return {spend,impr,lc,res,cpr:res?spend/res:null,ctr:impr?100*lc/impr:0,cpc:lc?spend/lc:null,
    cr:lc?res/lc:0,hook:impr?100*(v.v3s||0)/impr:0,hold:v.v3s?100*(v.thru||0)/v.v3s:0,isVid:(v.v3s||0)>0.02*impr,
    roas:spend?(v.pval||0)/spend:0,pval:v.pval||0,atc:v.atc||0,ic:v.ic||0,lpv:v.lpv||0,purch:v.purch||0,cpm:impr?1000*spend/impr:0};
}
function resTxt(n,R){ return money(n)+' '+(Math.round(n)===1?R.unit[0]:R.unit[1]); }
function cprLbl(R){ return 'costo por '+R.unit[0]; }

/* =====================================================================
   MOTOR DE INSIGHTS
   analyze(b,{from,to}) -> período explícito + hallazgos ordenados.
   Cada hallazgo: {sev:'urgente'|'atencion'|'bien'|'info', key, t, d, a, ev, rep}
   ===================================================================== */
const SEVORD={urgente:0,atencion:1,bien:2,info:3};
function analyze(b,opt){
  opt=opt||{};
  const D=DATA[b]; const B=brand(b);
  const to=opt.to||throughOf(b);
  const from=opt.from||addDays(to,-6);
  const len=daysBetween(from,to)+1;
  const pTo=addDays(from,-1), pFrom=addDays(pTo,-(len-1));
  let R=resultDef(b,from,to,opt.ci);
  const cur=sumWin(b,from,to,R), prev=sumWin(b,pFrom,pTo,R);
  const T=stats(cur.T,R), TP=stats(prev.T,R);
  const FL=firstLast(b,R);
  const ads=Object.keys(cur.per).map(a=>({i:+a,ad:D.ads[a],...stats(cur.per[a],R),first:(FL[a]||{}).first,last:(FL[a]||{}).last}))
    .filter(x=>x.spend>0).sort((x,y)=>y.spend-x.spend);
  ads.forEach(x=>{x.share=T.spend?x.spend/T.spend:0; x.tag=tagOf(b,x.ad);});
  const per={from,to,len,pFrom,pTo,label:`${fmtWd(from)} al ${fmtWd(to)}`,short:`${fmtDM(from)}–${fmtDM(to)}`,prevShort:`${fmtDM(pFrom)}–${fmtDM(pTo)}`};
  const F=[]; const push=f=>F.push(f);
  const PW=len===7?'la semana anterior':`los ${len} días anteriores`, CW=len===7?'la semana':'el período';
  const U=R.unit, cprAcc=T.res?T.spend/T.res:null;
  const out={b,D,B,R,per,T,TP,ads,F,cprAcc};

  /* 0 · Sin inversión */
  if(T.spend<=0){
    let lastSp=null; for(const r of D.rows){ if(r[FI.spend]>0){const dt=D.dates[r[0]]; if(!lastSp||dt>lastSp) lastSp=dt;} }
    const planned=/pausa/i.test(B.foco||'');
    push({sev:planned?'info':'urgente',key:'nospend',
      t:lastSp?`Sin inversión desde el ${fmtWd(lastSp)}`:'La cuenta todavía no tuvo inversión',
      d:planned?'Está en pausa a propósito (ver foco del mes).':`En la semana del ${per.short} no se gastó nada.`,
      a:planned?'Retomar cuando estén los creativos para validar el mensaje.':'Confirmar con la marca si la pausa es a propósito; si no, revisar medio de pago y estado de las campañas.',
      rep:{hacemos:planned?'La pauta sigue en pausa mientras preparamos los creativos para validar el mensaje.':null}});
    out.headline=F[0].t; out.sem=planned?'idle':'bad'; return out;
  }

  /* 1 · Tendencia semana contra semana (lo primero que pregunta el cliente) */
  const tr={};
  if(R.sales){
    if(T.purch>=2&&TP.purch>=2){ tr.main='roas'; tr.cur=T.roas; tr.prev=TP.roas; tr.c=chg(T.roas,TP.roas); }
  } else if(T.res>=5&&TP.res>=5){ tr.main='cpr'; tr.cur=T.spend/T.res; tr.prev=TP.spend/TP.res; tr.c=chg(tr.cur,tr.prev); }
  out.tr=tr;
  if(tr.c!=null){
    const better = tr.main==='roas' ? tr.c>0.15 : tr.c<-0.15;
    const worse  = tr.main==='roas' ? tr.c<-0.2 : tr.c>0.2;
    const why=[]; const ctrC=chg(T.ctr,TP.ctr), cpmC=chg(T.cpm,TP.cpm), crC=chg(T.cr,TP.cr);
    if(cpmC!=null&&Math.abs(cpmC)>=0.15) why.push(`llegar a la gente salió ${cpmC>0?'más caro':'más barato'} (CPM ${cpmC>0?'+':''}${pctTxt(cpmC)})`);
    if(ctrC!=null&&Math.abs(ctrC)>=0.15) why.push(`${ctrC>0?'más':'menos'} gente hizo clic (CTR ${dec(TP.ctr)}% → ${dec(T.ctr)}%)`);
    if(crC!=null&&Math.abs(crC)>=0.15) why.push(`de los que hicieron clic, ${crC>0?'más':'menos'} ${R.kind==='msg'?'escribieron':(R.sales?'compraron':'dejaron sus datos')} (${dec(TP.cr*100,1)}% → ${dec(T.cr*100,1)}%)`);
    const mtxt = tr.main==='roas' ? `ROAS ${dec(tr.prev)}x → ${dec(tr.cur)}x` : `${cprLbl(R)}: ${$m(tr.prev)} → ${$m(tr.cur)}`;
    const vol=`${resTxt(TP.res,R)} → ${resTxt(T.res,R)}`;
    if(better) push({sev:'bien',key:'trend',t:tr.main==='roas'?`El ROAS mejoró ${pctTxt(Math.abs(tr.c))} contra ${PW}`:`Cada ${U[0]} salió ${pctTxt(Math.abs(tr.c))} más barata que ${PW}`,
      d:`${mtxt} · ${vol}.${why.length?' Por qué: '+why.join('; ')+'.':''}`,a:'Mantener lo que está funcionando; no tocar presupuesto ni estructura esta semana.',
      ev:`${per.short} vs ${per.prevShort}`,rep:{aprendimos:`${len===7?'Esta semana':'En el período'} ${tr.main==='roas'?'cada peso invertido volvió mejor':'cada '+U[0]+' costó menos'} que la anterior (${mtxt}).`}});
    else if(worse){
      let act='Revisar los creativos: tener lista la próxima tanda.';
      if(ctrC!=null&&ctrC<=-0.15) act='Los anuncios se están cansando: subir creativos nuevos esta semana (no bajar presupuesto: eso no lo arregla).';
      else if(cpmC!=null&&cpmC>=0.15&&(ctrC==null||ctrC>-0.15)) act='Subió el costo de la subasta, no es la pauta: aguantar 3–4 días sin tocar y volver a mirar.';
      else if(crC!=null&&crC<=-0.15) act=R.kind==='msg'?'Los clics no se convierten en mensajes: revisar el cierre del anuncio ("escribime…") y el mensaje de bienvenida de WhatsApp.':(R.sales?'Llegan pero compran menos: revisar web, stock y precio del producto que se pauta.':'Llegan pero no dejan datos: revisar el formulario y la promesa.');
      push({sev:'atencion',key:'trend',t:tr.main==='roas'?`El ROAS bajó ${pctTxt(Math.abs(tr.c))} contra ${PW}`:`Cada ${U[0]} salió ${pctTxt(Math.abs(tr.c))} más cara que ${PW}`,
        d:`${mtxt} · ${vol}.${why.length?' Por qué: '+why.join('; ')+'.':''}`,a:act,ev:`${per.short} vs ${per.prevShort}`,
        rep:{aprendimos:`${len===7?'Esta semana':'En el período'} ${tr.main==='roas'?'el retorno bajó':'cada '+U[0]+' salió más cara'} (${mtxt})${why.length?': '+why[0]:''}.`,hacemos:act}});
    }
  } else if(T.res<5 && !R.sales){
    push({sev:'info',key:'thin',t:`Todavía pocos resultados para comparar semanas (${resTxt(T.res,R)})`,d:'Con menos de 5 resultados por semana el costo por resultado se mueve por azar.',a:'Juzgar por señales de arriba (CTR, hook) hasta juntar volumen.',ev:`Semana ${per.short}`});
  }

  /* 2 · Concentración en un solo anuncio */
  const top=ads[0];
  const famOf=a=>normBase(a);
  if(top && ads.length>=1 && top.share>=0.6){
    const famShare=ads.filter(x=>famOf(x.ad)===famOf(top.ad)).reduce((s,x)=>s+x.share,0);
    const angs=new Set(ads.filter(x=>x.share>=0.05).map(x=>x.tag.angulo||'?'));
    const oneAngle=angs.size===1&&!angs.has('?');
    const good=cprAcc&&top.cpr&&top.cpr<=cprAcc*1.05;
    push({sev:top.share>=0.75?'urgente':'atencion',key:'conc',
      t:`El ${pctTxt(top.share)} de la pauta depende de un solo creativo`,
      d:`${esc(top.ad)} se llevó ${$m(top.spend)} de ${$m(T.spend)} en ${CW}${good&&top.res?` y es el que mejor rinde (${$m(top.cpr)} por ${U[0]})`:''}.${famShare>top.share+0.05?` Con sus iteraciones suma el ${pctTxt(famShare)}.`:''}${oneAngle?` Toda la pauta habla desde un solo ángulo: «${esc([...angs][0])}».`:''} Si se cansa, se cae la cuenta entera.`,
      a:`Iterar el ganador y sumar nuevos urgente: 2–3 iteraciones de ${esc(normBase(top.ad)===top.ad.toLowerCase()?top.ad:top.ad.replace(/\s*[-–]\s*(copia|copy).*$/i,''))} (mismo concepto, otro objeto u otro arranque) + 2 conceptos nuevos${top.tag.angulo?` fuera de «${esc(top.tag.angulo)}»`:''}.`,
      ev:`Semana ${per.short} · ${ads.length} anuncios con gasto`,
      rep:{aprendimos:null,hacemos:`Sumar creativos nuevos: hoy ${pctTxt(top.share)} de la inversión depende de un solo anuncio.`,necesitamos:'Material nuevo esta semana: 2–3 versiones del anuncio ganador y 2 ideas nuevas.'}});
  }

  /* 3 · Iteraciones del ganador: ¿rinden igual o mejor que el original? */
  if(top && top.res>0){
    const base=famOf(top.ad);
    const its=ads.filter(x=>x!==top && famOf(x.ad)===base && x.first);
    const evals=its.map(it=>{
      const since=it.first>addDays(from,-21)?it.first:addDays(from,-21);
      const w=sumWin(b,since,to,R);
      const a=stats(w.per[it.i]||{},R), o=stats(w.per[top.i]||{},R);
      return {it,since,a,o};
    }).filter(e=>e.a.spend>0);
    const enough=e=>e.a.res>=5 || (e.o.cpr && e.a.spend>=2*e.o.cpr);
    const solid=evals.filter(enough).sort((x,y)=>(x.a.cpr||1e12)-(y.a.cpr||1e12));
    if(solid.length){
      const e=solid[0]; const ratio=(e.a.cpr&&e.o.cpr)?e.a.cpr/e.o.cpr:null;
      if(ratio!=null && ratio<=1.1){
        push({sev:'bien',key:'iter',t:`Desde el ${fmtWd(e.since)}, la iteración «${esc(e.it.ad)}» rinde igual o mejor que el original`,
          d:`${$m(e.a.cpr)} vs ${$m(e.o.cpr)} por ${U[0]} (${resTxt(e.a.res,R)} vs ${resTxt(e.o.res,R)} en los mismos días).${solid.length>1?` Hay ${solid.length} iteraciones con datos.`:''}`,
          a:`Seguir por ese camino: hacer 2–3 iteraciones más de ${esc(e.it.ad.replace(/\s*[-–]\s*(copia|copy).*$/i,''))} y pasarles presupuesto de a poco.`,
          ev:`${fmtDM(e.since)}–${fmtDM(to)} · mismo período para los dos`,
          rep:{aprendimos:`La nueva versión del anuncio ganador funciona igual o mejor que el original (${$m(e.a.cpr)} vs ${$m(e.o.cpr)} por ${U[0]}): el concepto se puede seguir estirando.`,hacemos:'Más versiones del anuncio ganador, con otro objeto u otro arranque.'}});
      } else if(ratio!=null && ratio>=1.3){
        push({sev:'info',key:'iter',t:`La iteración «${esc(e.it.ad)}» todavía no le gana al original`,
          d:`${$m(e.a.cpr)} vs ${$m(e.o.cpr)} por ${U[0]} desde el ${fmtWd(e.since)}.`,a:'Probar un cambio más grande (otro objeto, otra escena) en vez de copias del mismo video.',ev:`${fmtDM(e.since)}–${fmtDM(to)}`});
      }
    } else if(evals.length){
      const e=evals.sort((x,y)=>y.it.first.localeCompare(x.it.first))[0];
      push({sev:'info',key:'iter',t:`Iteración nueva en aprendizaje: «${esc(e.it.ad)}»`,d:`Arrancó el ${fmtWd(e.it.first)}; todavía no tiene volumen para compararla (${resTxt(e.a.res,R)}).`,a:addDays(e.it.first,3)<=NOW?'Ya tiene 3 días: si no juntó resultados con el mismo presupuesto, probar otro cambio.':`No tocar hasta el ${fmtWd(addDays(e.it.first,3))}.`,ev:''});
    }
  }

  /* 4 · Cansancio del anuncio principal (últimos 3 días vs 7 previos) */
  if(top){
    const a3=sumWin(b,addDays(to,-2),to,R).per[top.i], a7=sumWin(b,addDays(to,-9),addDays(to,-3),R).per[top.i];
    if(a3&&a7){ const s3=stats(a3,R), s7=stats(a7,R);
      if(s3.impr>=1500&&s7.impr>=1500&&s7.ctr>0&&s3.ctr<=s7.ctr*0.75){
        push({sev:'atencion',key:'fatiga',t:`${esc(top.ad)} empieza a cansarse`,
          d:`CTR ${dec(s7.ctr)}% → ${dec(s3.ctr)}% en los últimos 3 días${(s3.cpr&&s7.cpr)?`, ${cprLbl(R)} ${$m(s7.cpr)} → ${$m(s3.cpr)}`:''}.`,
          a:'Tener la próxima tanda lista y no subirle presupuesto a este anuncio.',ev:`${fmtDM(addDays(to,-2))}–${fmtDM(to)} vs ${fmtDM(addDays(to,-9))}–${fmtDM(addDays(to,-3))}`,
          rep:{hacemos:'Preparar el recambio del anuncio principal, que empieza a cansarse.'}});
      } } }

  /* 5 · Plata sin resultado */
  const floor=Math.max(cprAcc?1.5*cprAcc:0, 0.05*T.spend);
  const waste=ads.filter(x=>x.res===0 && x.spend>=floor && x.impr>=1000);
  if(waste.length){
    const tot=waste.reduce((s,x)=>s+x.spend,0);
    push({sev:'atencion',key:'waste',t:`${waste.length===1?'Un anuncio gastó':waste.length+' anuncios gastaron'} ${$m(tot)} sin ${U[1]}`,
      d:waste.slice(0,4).map(x=>`${esc(x.ad)} (${$m(x.spend)}${x.isVid?`, hook ${dec(x.hook,0)}%`:''}, CTR ${dec(x.ctr)}%)`).join(' · ')+'.',
      a:`Pausar ${waste.length===1?'ese anuncio':'esos anuncios'} y pasar la plata al que funciona.`,ev:`Semana ${per.short}`,
      rep:{hacemos:`Pausar ${waste.length===1?'un anuncio que no trajo':'anuncios que no trajeron'} ${U[1]}.`}});
  }

  /* 6 · Anuncios nuevos en aprendizaje */
  const fresh=ads.filter(x=>x.first && x.first>=addDays(to,-2) && !(x.first<from));
  if(fresh.length && !F.some(f=>f.key==='iter'&&/aprendizaje/.test(f.t))){
    const lastFirst=fresh.map(x=>x.first).sort().slice(-1)[0];
    push({sev:'info',key:'fresh',t:`${fresh.length===1?'Hay un anuncio nuevo':'Hay '+fresh.length+' anuncios nuevos'} en aprendizaje`,
      d:fresh.map(x=>esc(x.ad)).join(' · ')+'.',a:addDays(lastFirst,3)<=NOW?`Ya pasaron 3 días desde el ${fmtWd(lastFirst)}: decidir hoy cuáles siguen (mirá el ranking de creativos).`:`No tocar hasta el ${fmtWd(addDays(lastFirst,3))}; ahí se decide con datos.`,ev:`Arrancaron entre el ${fmtDM(fresh.map(x=>x.first).sort()[0])} y el ${fmtDM(lastFirst)}`});
  }

  /* 7 · Embudo web (sólo campañas de venta en la web) */
  if(R.sales){
    let W=cur, wl=`Semana ${per.short}`;
    if(W.T.lpv<150){ W=sumWin(b,addDays(to,-29),to,R); wl=`Últimos 30 días (${fmtDM(addDays(to,-29))}–${fmtDM(to)})`; }
    const t=W.T;
    if(t.lpv>=150){
      const steps=[['Visita → Carrito',t.atc,t.lpv,0.10,'llegan a la web pero no agregan al carrito: revisar página de producto, precio, talles/stock y que lo que muestra el anuncio sea lo que ven al entrar.'],
        ['Carrito → Inicio de pago',t.ic,t.atc,0.45,'agregan pero no van a pagar: costo de envío, cuotas y medios de pago visibles antes del checkout.'],
        ['Inicio de pago → Compra',t.purch,t.ic,0.45,'empiezan a pagar y no terminan: probar el checkout de punta a punta, envío, cuotas y que el pixel registre bien la compra.']];
      let worst=null; steps.forEach(s=>{ if(s[2]>=10){ const r=s[1]/s[2]; const q=r/s[3]; if(q<0.6&&(!worst||q<worst.q)) worst={s,r,q}; } });
      if(worst) push({sev:'atencion',key:'funnel',t:`La venta se traba en «${worst.s[0]}»`,
        d:`${dec(worst.r*100,1)}% pasa al paso siguiente (sano: ~${Math.round(worst.s[3]*100)}%). ${money(worst.s[2])} → ${money(worst.s[1])}.`,
        a:'No es la pauta: '+worst.s[4],ev:wl,rep:{necesitamos:'Revisar juntos la web en el paso donde se pierde la venta ('+worst.s[0].toLowerCase()+').'}});
    }
  }

  /* 8 · Ventas reportadas por la marca (WhatsApp, local) */
  if(D.manual&&D.manual.length){
    const M30=manualIn(D,addDays(to,-29),to,null), Mw=manualIn(D,from,to,null);
    const sp30=sumWin(b,addDays(to,-29),to,{cis:null}).T.spend;
    const lastTo=D.manual.map(m=>m.to||m.reported).sort().slice(-1)[0];
    if(M30.any) push({sev:'info',key:'manual',t:`Ventas que nos pasó la marca: ${money(M30.sales)} en los últimos 30 días`,
      d:`${$m(M30.amount)} vendidos con ${$m(sp30)} invertidos → por cada $1 volvieron $${dec(sp30?M30.amount/sp30:0)}.`,
      a:'Usar este número (no el de Meta) para decidir si escalar.',ev:`Último reporte: ${M30.last?fmtDM(M30.last):'—'}`});
    if(!Mw.any && lastTo<from) push({sev:'atencion',key:'manualmiss',t:'Falta el reporte de ventas de esta semana',
      d:`El último dato de ventas que nos pasó la marca llega hasta el ${fmtDM(lastTo)}. Sin eso no sabemos cuánto cuesta cada venta real.`,
      a:'Pedirle a la marca las ventas de la semana (cantidad y monto).',ev:'',rep:{necesitamos:'Las ventas de la semana que entraron por WhatsApp (cantidad y monto).'}});
  }

  /* 9 · Ángulos validados / refutados (30 días, más volumen) */
  out.angles=angleBoard(b,addDays(to,-29),to,R);
  out.angles.ang.filter(g=>g.st==='validado').slice(0,2).forEach(g=>push({sev:'bien',key:'angv',t:`Ángulo validado: «${esc(g.name)}»`,
    d:`${$m(g.cpr)} por ${U[0]} contra ${$m(g.rest)} del resto de los anuncios · ${resTxt(g.res,R)} · ${pctTxt(g.share)} de la inversión.`,
    a:'Producir más piezas desde este ángulo, con otros objetos y arranques.',ev:out.angles.lbl,
    rep:{aprendimos:`Lo que más funciona es el ángulo «${g.name}»: ${$m(g.cpr)} por ${U[0]}, contra ${$m(g.rest)} del resto.`}}));
  out.angles.ang.filter(g=>g.st==='refutado').slice(0,2).forEach(g=>push({sev:'info',key:'angr',t:`Ángulo que no funcionó: «${esc(g.name)}»`,
    d:g.res?`${$m(g.cpr)} por ${U[0]} (resto de la cuenta: ${$m(g.rest)}) con ${$m(g.spend)} invertidos.`:`${$m(g.spend)} invertidos sin ${U[1]}.`,
    a:'No volver a pautarlo así; si se insiste, cambiar el enfoque, no el color.',ev:out.angles.lbl,
    rep:{aprendimos:`El ángulo «${g.name}» no funcionó (${g.res?$m(g.cpr)+' por '+U[0]:'sin '+U[1]}).`}}));

  F.sort((x,y)=>SEVORD[x.sev]-SEVORD[y.sev]);
  out.headline=(F.find(f=>f.sev!=='info')||F[0]||{t:'Semana estable, sin novedades'}).t;
  // Semáforo de la semana = cómo vino el resultado (no los riesgos)
  out.sem = !T.res ? (R.kind==='msg'||R.sales?'bad':'idle')
          : (tr.c==null ? 'idle' : ((tr.main==='roas'?-tr.c:tr.c) > 0.35 ? 'bad' : ((tr.main==='roas'?-tr.c:tr.c) > 0.12 ? 'warn' : 'good')));
  return out;
}

/* ---------- Ángulos y arquetipos: validado / refutado / en prueba ---------- */
function angleBoard(b,from,to,R){
  const D=DATA[b]; const w=sumWin(b,from,to,R); const T=stats(w.T,R);
  const acc=T.res?T.spend/T.res:null;
  const mk=key=>{ const g={};
    for(const a in w.per){ const s=stats(w.per[a],R); if(!(s.spend>0)) continue; const tg=tagOf(b,D.ads[a]); const nm=tg[key]||null; if(!nm) continue;
      const x=g[nm]=g[nm]||{name:nm,spend:0,res:0,n:0,impr:0,lc:0}; x.spend+=s.spend; x.res+=s.res; x.n++; x.impr+=s.impr; x.lc+=s.lc; }
    return Object.values(g).map(x=>{ x.cpr=x.res?x.spend/x.res:null; x.share=T.spend?x.spend/T.spend:0;
      const minRes=R.sales?3:10;
      // se compara contra EL RESTO de la cuenta (si un ángulo es el 85% de la pauta, compararlo con el promedio no dice nada)
      const rRes=T.res-x.res, rSp=T.spend-x.spend; x.rest=(rRes>=(R.sales?2:5))?rSp/rRes:acc;
      const ref=x.rest||acc;
      if(ref && x.res>=minRes && x.cpr<=ref*0.8) x.st='validado';
      else if(ref && ((x.res===0 && x.spend>=2*(acc||ref)) || (x.res>=Math.ceil(minRes/2) && x.cpr>=ref*1.5))) x.st='refutado';
      else if(x.spend<(acc||0)) x.st='sin datos'; else x.st='en prueba';
      return x; }).sort((p,q)=>q.spend-p.spend); };
  const untagged=Object.keys(w.per).filter(a=>w.per[a].spend>0 && tagOf(b,D.ads[a]).src==='none').reduce((s,a)=>s+w.per[a].spend,0);
  return {ang:mk('angulo'),arq:mk('arquetipo'),acc,untaggedShare:T.spend?untagged/T.spend:0,lbl:`Últimos 30 días (${fmtDM(from)}–${fmtDM(to)})`};
}

/* ---------- Etapa de funnel deducida + ranking mejor → peor ---------- */
function rankCreatives(b,from,to,forceCi){
  const D=DATA[b]; const R=resultDef(b,from,to,forceCi);
  const w=sumWin(b,from,to,R); const T=stats(w.T,R);
  const stMap=adStatusMap(b);
  // métrica de "cierre": compras si hay volumen; si no, inicios de pago (señal intermedia)
  const closeF = R.sales ? (T.purch>=10?'purch':'ic') : R.field;
  const closeLbl = R.sales ? (closeF==='purch'?'compras':'inicios de pago') : R.unit[1];
  const rows=Object.keys(w.per).map(a=>{ const s=stats(w.per[a],R); s.i=+a; s.ad=D.ads[a]; s.close=w.per[a][closeF]||0; s.tag=tagOf(b,s.ad);
    const st=stMap[s.ad]||{}; s.state=st.state||'pausado'; s.stateEst=st.est!==false; return s; }).filter(s=>s.spend>0);
  const sig=s=>s.impr>=1500 && s.lc>=15;
  const base=rows.filter(sig);
  const med=arr=>{ const v=arr.filter(x=>isFinite(x)).sort((p,q)=>p-q); if(!v.length) return 0; const m=Math.floor(v.length/2); return v.length%2?v[m]:(v[m-1]+v[m])/2; };
  // referencia = la cuenta (ponderada), más estable que la mediana con pocos anuncios
  const refCtr = T.impr?100*T.lc/T.impr:med(base.map(s=>s.ctr));
  const refClose = T.lc? (w.T[closeF]||0)/T.lc : 0;
  rows.forEach(s=>{
    const closeRate=s.lc?s.close/s.lc:0;
    s.closeRate=closeRate; s.cprClose=s.close?s.spend/s.close:null;
    if(!sig(s)){ s.stage='Sin señal'; s.stageWhy=`pocos datos (${money(s.impr)} impresiones, ${money(s.lc)} clics)`; return; }
    const I=refCtr?s.ctr/refCtr:1, C=refClose?closeRate/refClose:(s.close?1:0);
    s.I=I; s.C=C;
    const why=`CTR ${dec(s.ctr)}% (cuenta ${dec(refCtr)}%) · ${dec(closeRate*100,1)}% de los clics → ${closeLbl} (cuenta ${dec(refClose*100,1)}%)`;
    if(I>=1.1 && C>=1.1) s.stage='TOFU+BOFU';
    else if(I>=1.0 && C<0.85) s.stage='TOFU';
    else if(C>=1.15 && I<1.0) s.stage='BOFU';
    else if(I<0.75 && C<0.75) s.stage='No rinde';
    else s.stage='MOFU';
    // venta web: mucho carrito pero poco cierre = MOFU (empuja, no cierra)
    if(R.sales && s.stage!=='No rinde' && s.lpv>=50){ const atcR=s.atc/s.lpv, tAtc=w.T.lpv?w.T.atc/w.T.lpv:0; if(tAtc && atcR>=tAtc*1.2 && C<1) s.stage='MOFU'; }
    s.stageWhy=why;
  });
  // ranking: 1) con resultados suficientes por costo (o ROAS); 2) con señal, por señales; 3) sin señal, por gasto
  const minRes=R.sales?2:5;
  const tier=s=> (s.res>=minRes)?0:(sig(s)?1:2);
  const key=s=>{ const t=tier(s);
    if(t===0) return R.sales? -s.roas : s.cpr;
    if(t===1) return s.cprClose!=null ? s.cprClose : 1e12 - s.ctr;
    return -s.spend; };
  rows.sort((p,q)=> tier(p)-tier(q) || key(p)-key(q));
  rows.forEach((s,i)=>{ s.rank=i+1; s.tier=tier(s); s.share=T.spend?s.spend/T.spend:0;
    const acc=T.res?T.spend/T.res:null;
    if(s.tier===2) s.verd='poca';
    else if(s.tier===0){ const q=R.sales?(s.roas/(T.roas||1)):(acc/s.cpr); s.verd=q>=1.15?'escalar':(q>=0.8?'mantener':'iterar'); }
    else s.verd = s.stage==='No rinde'?'pausar':(s.res===0&&acc&&s.spend>=1.5*acc?'pausar':'probando');
  });
  return {R,T,rows,closeLbl,refCtr,refClose};
}

/* =====================================================================
   UI compartida
   ===================================================================== */
const SEVLBL={urgente:['Urgente','bad'],atencion:['Atención','warn'],bien:['Funciona','good'],info:['Para saber','']};
const SEMLBL={good:['Viene bien','good'],warn:['Mirar','warn'],bad:['Problema','bad'],idle:['Sin datos para comparar','']};
function thumbOf(D,nm){ const TB=D.thumbs||{}; if(TB[nm]) return TB[nm]; const n=String(nm).toLowerCase().replace(/\s+/g,' ').trim();
  for(const k in TB){ const kk=String(k).toLowerCase().replace(/\s+/g,' ').trim(); if(kk===n||kk.startsWith(n)||n.startsWith(kk)) return TB[k]; } return null; }
function lsGet(k){ try{return localStorage.getItem(k)||'';}catch(e){return '';} }
function lsSet(k,v){ try{ v?localStorage.setItem(k,v):localStorage.removeItem(k);}catch(e){} }
function v3Picker(hostId,onPick,def){
  const box=document.getElementById(hostId); const id='sel-'+hostId.replace('pick-','');
  const act=CLIENTS.filter(isActiveBrand), lost=CLIENTS.filter(isLost);
  box.innerHTML=`<select class="dd" id="${id}">
    <optgroup label="Activas">${act.map(c=>`<option value="${c}" ${c===def?'selected':''}>${esc(DATA[c].name)}</option>`).join('')}</optgroup>
    ${lost.length?`<optgroup label="Cuentas perdidas">${lost.map(c=>`<option value="${c}" ${c===def?'selected':''}>${esc(DATA[c].name)}</option>`).join('')}</optgroup>`:''}
  </select>`;
  const sel=box.querySelector('select'); sel.addEventListener('change',()=>onPick(sel.value)); onPick(def);
}
function goTo(tab,b){
  goTab(tab);
  const map={analisis:['sel-ana',renderAnalisis],reporte:['sel-rep',renderReporte],dashboard:['sel-dash',renderDash]};
  const m=map[tab]; if(m&&b){ const s=document.getElementById(m[0]); if(s) s.value=b; m[1](b); }
}
function shortAct(f){
  const S={conc:'Iterar el ganador y sumar 2 conceptos nuevos',funnel:'Revisar la web: '+(f.t.match(/«(.+)»/)||[,''])[1],waste:'Pausar lo que gasta sin resultado',
    fatiga:'Preparar recambio del anuncio principal',manualmiss:'Pedir las ventas de la semana',iter:'Más iteraciones del ganador',nospend:f.sev==='info'?'En pausa a propósito':'Confirmar por qué no gasta'};
  if(S[f.key]) return S[f.key];
  const a=String(f.a).replace(/<[^>]+>/g,''); const c=a.split(/[:.;]/)[0]; return c.length<80?c:c.slice(0,77)+'…'; }
function findingHTML(f,i,keyBase,opts){
  opts=opts||{}; const k=keyBase+'_'+f.key; const done=lsGet(k+'_ok')==='1'; const sl=SEVLBL[f.sev];
  return `<div class="fd ${f.sev} ${done?'done':''}">
    <div class="ix">${i+1}</div>
    <div class="what"><span class="tag ${sl[1]}">${sl[0]}</span><div class="t">${f.t}</div><div class="d">${f.d}</div>${f.ev?`<div class="ev">📅 ${f.ev}</div>`:''}</div>
    <div class="todo"><div class="lab">Qué hacer</div><div class="a">${f.a}</div>
      ${opts.noCheck?'':`<label class="ck"><input type="checkbox" data-ok="${k}_ok" ${done?'checked':''}> Hecho</label>
      <textarea class="note" data-note="${k}_nota" placeholder="Nota (qué se hizo, qué dijo el cliente)">${esc(lsGet(k+'_nota'))}</textarea>`}
    </div></div>`;
}
function bindFindings(root,rerender){
  root.querySelectorAll('[data-ok]').forEach(cb=>cb.addEventListener('change',()=>{lsSet(cb.dataset.ok,cb.checked?'1':''); rerender();}));
  root.querySelectorAll('[data-note]').forEach(t=>t.addEventListener('input',()=>lsSet(t.dataset.note,t.value.trim())));
}
function kpiBox(k,v,s){ return `<div class="lcard kpi"><div class="k">${k}</div><div class="v">${v}</div>${s?`<div class="s">${s}</div>`:''}</div>`; }
function dlt(cur,prev,betterUp){ const c=chg(cur,prev); if(c==null) return '<span class="muted">sin semana previa</span>';
  if(Math.abs(c)<0.05) return '<span class="muted">= que la semana anterior</span>';
  const good=betterUp?c>0:c<0; return `<span class="tag ${good?'good':'warn'}">${c>0?'↑':'↓'} ${pctTxt(Math.abs(c))}</span> <span class="muted">vs semana anterior</span>`; }
function weekKpis(x){
  const {T,TP,R}=x; const out=[kpiBox('Inversión',$m(T.spend),dlt(T.spend,TP.spend,true))];
  out.push(kpiBox(R.unit[1][0].toUpperCase()+R.unit[1].slice(1),money(T.res),dlt(T.res,TP.res,true)));
  if(R.sales){ out.push(kpiBox('ROAS',dec(T.roas)+'x',T.purch>=2&&TP.purch>=2?dlt(T.roas,TP.roas,true):'<span class="muted">pocas compras para comparar</span>'));
    out.push(kpiBox('Costo por compra',T.purch?$m(T.spend/T.purch):'—',T.purch&&TP.purch?dlt(T.spend/T.purch,TP.spend/TP.purch,false):'')); }
  else { out.push(kpiBox('Costo por '+R.unit[0],T.res?$m(T.spend/T.res):'—',T.res&&TP.res?dlt(T.spend/T.res,TP.spend/TP.res,false):''));
    out.push(kpiBox('Clic en el anuncio (CTR)',dec(T.ctr)+'%',dlt(T.ctr,TP.ctr,true))); }
  return out.join('');
}
function rdLabel(R,b){ const D=DATA[b];
  const camps=R.cis?[...R.cis].map(ci=>D.camps[ci]):(D.camps||[]);
  const k={msg:'conversaciones de WhatsApp',purchase:'compras en la web',lead:'leads (formulario)',traffic:'clics'}[R.kind]||R.unit[1];
  return `Medimos <b>${k}</b>${camps.length===1?` · campaña ${esc(camps[0])}`:''}.`; }

/* =====================================================================
   DASHBOARD · señales por creativo (pisa sigHTML) + insights arriba
   ===================================================================== */
const STAGECLS={'TOFU':'tag','MOFU':'tag','BOFU':'tag dk','TOFU+BOFU':'tag y','Sin señal':'tag ghost','No rinde':'tag bad'};
const STAGEHELP={'TOFU':'Trae interesados (más clic que la cuenta) pero cierra poco: sirve arriba del embudo.','MOFU':'Intermedio: atrae y cierra parecido al promedio, o lleva al carrito pero no cierra.',
  'BOFU':'Trae menos interesados pero cierra más y más barato: es de cierre.','TOFU+BOFU':'Atrae y cierra por encima de la cuenta: ganador completo.','Sin señal':'Todavía no tiene impresiones/clics suficientes para decir qué rol cumple.','No rinde':'Ni atrae ni cierra mejor que el promedio.'};
const VERDLBL={escalar:['Escalar','good'],mantener:['Mantener',''],iterar:['Iterar','warn'],pausar:['Pausar','bad'],probando:['Probando','ghost'],poca:['Sin datos','ghost']};
function boardHTML(list,R,acc,title){
  if(!list.length) return `<div class="lcard"><div class="eyebrow">${title}</div><div class="muted" style="font-size:13px">Sin creativos taggeados en este período.</div></div>`;
  const ST={validado:'good','refutado':'bad','en prueba':'','sin datos':'ghost'};
  return `<div class="lcard scrollx"><div class="eyebrow">${title}</div><table class="cl"><tr><th>${title.includes('rquetipo')?'Arquetipo':'Ángulo'}</th><th class="r">Creativos</th><th class="r">Inversión</th><th class="r">${R.unit[1]}</th><th class="r">Costo c/u</th><th class="r">vs el resto</th><th>Estado</th></tr>
    ${list.map(g=>`<tr><td><b>${esc(g.name)}</b></td><td class="r">${g.n}</td><td class="r">${pctTxt(g.share)}</td><td class="r">${money(g.res)}</td>
      <td class="r">${g.cpr?$m(g.cpr):'—'}</td><td class="r">${(g.cpr&&g.rest)?`<span class="${g.cpr<=g.rest?'':'muted'}">${g.cpr<=g.rest?'−':'+'}${pctTxt(Math.abs(g.cpr/g.rest-1))}</span>`:'—'}</td><td><span class="tag ${ST[g.st]}">${g.st}</span></td></tr>`).join('')}</table></div>`;
}
function sigHTML(x){
  const b=x.b, D=DATA[b]; const ci=ctxCamp(b);
  const K=rankCreatives(b,RANGE.from,RANGE.to,ci); const R=K.R;
  const AB=angleBoard(b,RANGE.from,RANGE.to,R);
  const acc=K.T.res?K.T.spend/K.T.res:null;
  const rows=K.rows.map(s=>{
    const th=thumbOf(D,s.ad);
    const thumb=th?`<img class="th" src="${th}" loading="lazy" alt="" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'th',textContent:'${s.isVid?'🎬':'🖼️'}'}))">`:`<span class="th">${s.isVid?'🎬':'🖼️'}</span>`;
    const stB=AD_STATE[s.state]||AD_STATE.pausado; const vl=VERDLBL[s.verd]||VERDLBL.poca;
    const tg=s.tag; const arq=tg.arquetipo?esc(tg.arquetipo):'<span class="tag ghost">sin taggear</span>';
    const ang=tg.angulo?esc(tg.angulo)+(tg.src==='nombre'?' <span class="muted" title="Tomado del nombre del anuncio">·n</span>':''):'<span class="tag ghost">sin taggear</span>';
    return `<tr class="${s.rank===1&&s.tier===0?'top1':''} ${s.tier===2?'nosig':''}">
      <td class="rk">${s.rank}</td>
      <td><div class="adc">${thumb}<div style="min-width:0"><div class="adn" title="${esc(s.ad)}">${esc(s.ad)}</div><div class="adm">${stB[0]}${s.stateEst?'*':''} · ${s.isVid?'video':'imagen'}</div></div></div></td>
      <td>${arq}</td><td>${ang}</td>
      <td><span class="${STAGECLS[s.stage]}" title="${esc(STAGEHELP[s.stage]+' '+(s.stageWhy||''))}">${s.stage}</span></td>
      <td class="r">${$m(s.spend)}<div class="bar"><i style="width:${Math.round(s.share*100)}%"></i></div></td>
      <td class="r">${money(s.res)}</td>
      <td class="r">${s.cpr?$m(s.cpr):'—'}</td>
      <td class="r">${dec(s.ctr)}%</td>
      <td class="r">${s.isVid?dec(s.hook,0)+'%':'—'}</td>
      ${R.sales?`<td class="r">${s.purch?dec(s.roas)+'x':'—'}</td>`:''}
      <td><span class="tag ${vl[1]}">${vl[0]}</span></td></tr>`; }).join('');
  return `<div class="v3">
    <div class="grid" style="margin:4px 0 14px">${boardHTML(AB.ang,R,AB.acc,'Ángulos · ¿cuál se valida?')}${boardHTML(AB.arq,R,AB.acc,'Arquetipos · ¿a quién le funciona?')}</div>
    ${AB.untaggedShare>0.15?`<div class="muted" style="font-size:12.5px;margin:-6px 0 12px">⚠ ${pctTxt(AB.untaggedShare)} de la inversión del período está en creativos sin taggear: se cargan en <b>config/brands.yaml</b> (o pedíselo a Claude con la estrategia del cliente).</div>`:''}
    <div class="scrollx"><table class="ctab">
      <tr><th>#</th><th>Anuncio</th><th>Arquetipo</th><th>Ángulo</th><th title="Deducida por las señales: atrae (CTR) vs cierra (${esc(K.closeLbl)} por clic), contra el promedio de la cuenta.">Etapa</th><th class="r">Inversión</th><th class="r">${R.unit[1]}</th><th class="r">Costo c/u</th><th class="r">CTR</th><th class="r">Hook</th>${R.sales?'<th class="r">ROAS</th>':''}<th>Qué hacer</th></tr>
      ${rows}</table></div>
    <div class="muted" style="font-size:12px;margin-top:8px;line-height:1.6">
      <b>Orden:</b> de mejor a peor. Primero los que tienen resultados suficientes (≥${R.sales?2:5} ${R.unit[1]}) por ${R.sales?'ROAS':'costo por '+R.unit[0]}; después los que tienen señal pero pocos resultados (por costo por ${esc(K.closeLbl.replace(/s$/,''))}); al final los que no tienen datos.
      <b>Etapa:</b> se compara cada creativo contra el promedio de la cuenta en el período — <b>TOFU</b> trae más interesados (CTR más alto) pero cierra menos · <b>BOFU</b> trae menos pero cierra más y más barato · <b>MOFU</b> intermedio · <b>TOFU+BOFU</b> hace las dos cosas · <b>Sin señal</b> menos de 1.500 impresiones o 15 clics. En venta web con pocas compras, "cierra" se mide con inicios de pago. Pasá el mouse por la etapa para ver los números.
      Estado con * = estimado por actividad. Hook = % que mira los primeros 3 segundos (sólo video).</div>
  </div>`;
}
const _renderDashOrig=renderDash;
renderDash=function(id){
  const D0=DATA[id]; const o0=D0.obj, ph0=D0.optphases;
  if(DASHCAMP[id]==null){ // si en el período sólo corre WhatsApp (p.ej. Pulpa), el dashboard mide conversaciones, no compras web
    const R0=resultDef(id,RANGE.from,RANGE.to); const ob=KIND2OBJ[R0.kind];
    if(!R0.cis && ob && ob!==o0 && !(o0==='ic'&&ob==='purchase')){ D0.obj=ob; D0.optphases=[]; } }
  try{ _renderDashOrig(id); } finally { D0.obj=o0; D0.optphases=ph0; }
  const body=document.getElementById('body-dash'); if(!body) return;
  body.querySelectorAll('.sec-h').forEach(h=>{ if(/Señales por creativo/.test(h.textContent)){ h.textContent='Creativos · de mejor a peor'; const s=h.nextElementSibling; if(s&&s.classList.contains('sec-s')) s.textContent=`${periodPhrase()}. Con arquetipo, ángulo y la etapa del embudo que cumple cada uno según sus señales.`; } });
  const ci=(DASHCAMP[id]!=null)?DASHCAMP[id]:undefined;
  let x; try{ x=analyze(id,{from:RANGE.from,to:RANGE.to,ci}); }catch(e){ console.error(e); return; }
  const top=x.F.filter(f=>f.sev!=='info').slice(0,3);
  const box=document.createElement('div'); box.className='v3'; box.style.margin='14px 0 6px';
  box.innerHTML=`<div class="darkband"><div class="eyebrow">Lo que dicen los números · ${x.per.short} vs ${x.per.prevShort}</div>
    ${top.length?top.map(f=>`<div style="margin:8px 0 0"><span class="sem ${SEVLBL[f.sev][1]}"></span><b>${f.t}.</b> <span style="color:var(--muteD)">👉 ${f.a}</span></div>`).join(''):'<div style="margin-top:6px">Sin novedades fuertes en este período.</div>'}
    <div style="margin-top:12px"><a href="#" data-goana style="color:var(--yellow);text-decoration:none">Ver el análisis completo →</a></div></div>`;
  const head=body.querySelector('.dhead'); if(head) head.after(box); else body.prepend(box);
  box.querySelector('[data-goana]').addEventListener('click',e=>{e.preventDefault();goTo('analisis',id);});
};

/* =====================================================================
   ANÁLISIS DE PAUTA
   ===================================================================== */
let ANA_P='w0';
function anaWindow(b){ const to=throughOf(b);
  if(ANA_P==='w1'){ const t=addDays(to,-7); return {from:addDays(t,-6),to:t}; }
  if(ANA_P==='m'){ return {from:addDays(to,-29),to}; }
  return {from:addDays(to,-6),to}; }
function renderAnalisis(b){
  const el=document.getElementById('body-analisis'); const D=DATA[b]; if(!D) return;
  const w=anaWindow(b); const x=analyze(b,w); const B=x.B;
  const keyBase='maia27_'+b+'_'+x.per.to;
  const since=startIso(D)||D.dates[0];
  const S=sumWin(b,since,throughOf(b),x.R), ST=stats(S.T,x.R);
  el.innerHTML=`<div class="v3">
    <div class="barra noprint" style="margin-top:6px"><span class="lab">Período</span>
      <div class="picker">${[['w0','Última semana'],['w1','Semana anterior'],['m','Últimos 30 días']].map(([k,l])=>`<button class="pill" data-p="${k}" aria-pressed="${ANA_P===k}">${l}</button>`).join('')}</div></div>
    <div class="eyebrow" style="margin-top:18px">Análisis de pauta · ${esc(D.name)}${isLost(b)?' · cuenta perdida':''}</div>
    <h2>${x.headline}</h2>
    <p class="sub">📅 <b>${x.per.label}</b> (${x.per.len} días), comparado con ${x.per.prevShort}. Datos de Meta hasta el ${fmtWd(throughOf(b))}. ${rdLabel(x.R,b)}</p>
    ${(B.contexto||B.foco)?`<div class="lcard" style="margin-bottom:14px">${B.contexto?`<div class="eyebrow">La marca</div><div style="font-size:14px;margin-bottom:${B.foco?'10px':'0'}">${esc(B.contexto)}</div>`:''}${B.foco?`<div class="eyebrow">Foco del mes</div><div style="font-size:14px">${esc(B.foco)}</div>`:''}</div>`
      :`<div class="lcard muted" style="margin-bottom:14px;font-size:13px">Falta el contexto de esta marca en <b>config/brands.yaml</b> (qué vende, foco del mes). Con eso el análisis y el reporte quedan personalizados.</div>`}
    <div class="grid g4" style="margin-bottom:18px">${weekKpis(x)}</div>
    <h3>Qué está pasando → qué hacer</h3>
    <p class="sub">Ordenado por prioridad. Cada punto dice de qué días sale. "Hecho" y las notas se guardan en este navegador.</p>
    ${x.F.length?x.F.map((f,i)=>findingHTML(f,i,keyBase)).join(''):'<div class="lcard">Sin novedades en este período.</div>'}
    <details><summary>Desde el inicio con MAIA (${fmtDM(since)} → hoy)</summary>
      <div class="grid g4" style="margin:6px 0 12px">${kpiBox('Inversión',$m(ST.spend),'')}${kpiBox(x.R.unit[1],money(ST.res),'')}${kpiBox(x.R.sales?'ROAS':'Costo por '+x.R.unit[0],x.R.sales?dec(ST.roas)+'x':(ST.res?$m(ST.spend/ST.res):'—'),'')}${kpiBox('Anuncios',String(Object.keys(S.per).length),'')}</div>
      <div class="grid">${boardHTML(angleBoard(b,since,throughOf(b),x.R).ang,x.R,ST.res?ST.spend/ST.res:null,'Ángulos · desde el inicio')}${boardHTML(angleBoard(b,since,throughOf(b),x.R).arq,x.R,ST.res?ST.spend/ST.res:null,'Arquetipos · desde el inicio')}</div>
    </details>
    <div class="noprint" style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap"><button class="btn" data-rep>Armar el reporte de esta semana</button><button class="btn ghost" data-dash>Ver creativos en el Dashboard</button></div>
  </div>`;
  el.querySelectorAll('[data-p]').forEach(bt=>bt.addEventListener('click',()=>{ANA_P=bt.dataset.p; renderAnalisis(b);}));
  el.querySelector('[data-rep]').addEventListener('click',()=>goTo('reporte',b));
  el.querySelector('[data-dash]').addEventListener('click',()=>goTo('dashboard',b));
  bindFindings(el,()=>renderAnalisis(b));
}

/* =====================================================================
   REPORTE AL CLIENTE (mismo diagnóstico que el Análisis de la última semana)
   ===================================================================== */
function plain(s){ return String(s||'').replace(/\s*\((CPM|CTR)[^)]*\)/g,'').replace(/<[^>]+>/g,''); }
function uniq(a){ const s=new Set(); return a.filter(v=>v&&!s.has(v)&&s.add(v)); }
function reportModel(b){
  const to=throughOf(b); const x=analyze(b,{from:addDays(to,-6),to}); const D=x.D, B=x.B, R=x.R, T=x.T, TP=x.TP;
  const sem=x.sem;
  const semWhy={good:'La semana vino bien.',warn:'La semana vino un poco más floja.',bad:'La semana vino floja.',idle:'Todavía no hay una semana anterior comparable.'}[sem];
  let num=[];
  num.push(['Invertimos',$m(T.spend),TP.spend?`la semana anterior: ${$m(TP.spend)}`:'']);
  num.push([R.unit[1][0].toUpperCase()+R.unit[1].slice(1),money(T.res),TP.spend?`la semana anterior: ${money(TP.res)}`:'']);
  if(R.sales) num.push(['Por cada $1 invertido volvieron','$'+dec(T.roas),TP.spend?`la semana anterior: $${dec(TP.roas)}`:'']);
  else if(T.res) num.push([`Cada ${R.unit[0]} costó`,$m(T.spend/T.res),TP.res?`la semana anterior: ${$m(TP.spend/TP.res)}`:'']);
  const Mw=manualIn(D,x.per.from,x.per.to,null);
  if(Mw.any) num.push(['Ventas que nos pasaste',money(Mw.sales),`${$m(Mw.amount)} · por cada $1 volvieron $${dec(T.spend?Mw.amount/T.spend:0)}`]);
  const base=[];
  if(x.tr&&x.tr.c!=null&&!x.F.some(f=>f.key==='trend')){
    base.push(x.tr.main==='roas'?`El retorno se mantuvo parecido a la semana anterior (ROAS ${dec(x.tr.prev)}x → ${dec(x.tr.cur)}x).`
      :`Cada ${R.unit[0]} costó ${$m(x.tr.cur)}, ${x.tr.c<0?pctTxt(-x.tr.c)+' menos':pctTxt(x.tr.c)+' más'} que la semana anterior, ${T.res>=TP.res?'con '+pctTxt(chg(T.res,TP.res)||0)+' más':'con menos'} ${R.unit[1]}.`); }
  const apr=uniq([...base,...x.F.map(f=>f.rep&&f.rep.aprendimos)].map(plain)).slice(0,3);
  if(!apr.length){ const f=x.F.find(f=>f.sev!=='info'); if(f) apr.push(plain(f.t)+'.'); }
  const hac=uniq(x.F.filter(f=>f.sev!=='bien'||f.key==='iter').map(f=>(f.rep&&f.rep.hacemos)||null).map(plain)).slice(0,3);
  if(!hac.length) hac.push('Mantener lo que funciona y seguir midiendo.');
  const nec=uniq([...x.F.map(f=>f.rep&&f.rep.necesitamos),...(B.pedidos||[])].map(plain)).slice(0,2);
  return {x,sem,semWhy,num,apr,hac,nec};
}
function repTxt(M,D){ // versión para copiar a WhatsApp
  return `*Reporte semanal · ${D.name}*\n${M.x.per.label}\n\n${({good:'🟢',warn:'🟡',bad:'🔴',idle:'⚪'})[M.sem]} ${M.semWhy}\n\n`+
    M.num.map(n=>`• ${n[0]}: *${n[1]}*`).join('\n')+`\n\n*Qué aprendimos*\n`+M.apr.map((t,i)=>`${i+1}. ${t}`).join('\n')+
    `\n\n*Qué hacemos esta semana*\n`+M.hac.map((t,i)=>`${i+1}. ${t}`).join('\n')+(M.nec.length?`\n\n*Qué necesitamos de vos*\n`+M.nec.map((t,i)=>`${i+1}. ${t}`).join('\n'):'');
}
function renderReporte(b){
  const el=document.getElementById('body-reporte'); const D=DATA[b]; if(!D) return;
  const M=reportModel(b); const x=M.x; const k='maia27_rep_'+b+'_'+x.per.to;
  const ed=(id,html)=>{ const v=lsGet(k+'_'+id); return v||html; };
  const li=a=>a.map(t=>`<li>${esc(t)}</li>`).join('');
  const sl=SEMLBL[M.sem];
  el.innerHTML=`<div class="v3">
    <div class="noprint" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0 14px">
      <button class="btn" data-print>Descargar PDF</button><button class="btn ghost" data-copy>Copiar para WhatsApp</button><button class="btn ghost" data-reset>Volver al texto automático</button>
      <span class="muted" style="font-size:12.5px">Sale del mismo diagnóstico que el Análisis de la última semana. Todo el texto se puede editar (click y escribir); se guarda en este navegador.</span></div>
    <div class="rep">
      <div class="rh"><div class="eyebrow">Reporte semanal · ${esc(D.name)}</div><h2>Semana del <span class="hl">${fmtDM(x.per.from)} al ${fmtDM(x.per.to)}</span></h2><p class="sub">${x.per.label} · comparada con la semana anterior · maia.science</p></div>
      <div class="rb">
        <div class="rsec" style="margin-top:0"><div class="eyebrow">Cómo vino la semana</div>
          <div style="font-size:18px;font-weight:700"><span class="sem ${sl[1]}"></span><span contenteditable data-ed="sem">${ed('sem',esc(M.semWhy))}</span></div></div>
        <div class="rsec"><div class="eyebrow">Los números</div>
          <div class="grid ${M.num.length>3?'g4':'g3'}">${M.num.map(n=>`<div class="lcard kpi"><div class="k">${n[0]}</div><div class="v">${n[1]}</div><div class="s">${n[2]}</div></div>`).join('')}</div></div>
        <div class="rsec"><div class="eyebrow">Qué aprendimos</div><ol contenteditable data-ed="apr">${ed('apr',li(M.apr))}</ol></div>
        <div class="rsec"><div class="eyebrow">Qué hacemos esta semana</div><ol contenteditable data-ed="hac">${ed('hac',li(M.hac))}</ol></div>
        <div class="rsec"><div class="eyebrow">Qué necesitamos de vos</div><ol contenteditable data-ed="nec">${ed('nec',M.nec.length?li(M.nec):'<li>Nada esta semana.</li>')}</ol></div>
        <div class="rsec muted" style="font-size:12px">Datos de Meta Ads hasta el ${fmtWd(throughOf(b))}. ${x.R.kind==='msg'?'El resultado son las conversaciones que se abrieron en WhatsApp; las ventas las sabés vos.':''}</div>
      </div></div></div>`;
  el.querySelectorAll('[data-ed]').forEach(n=>n.addEventListener('input',()=>lsSet(k+'_'+n.dataset.ed,n.innerHTML)));
  el.querySelector('[data-print]').addEventListener('click',()=>window.print());
  el.querySelector('[data-reset]').addEventListener('click',()=>{['sem','apr','hac','nec'].forEach(i=>lsSet(k+'_'+i,'')); renderReporte(b);});
  el.querySelector('[data-copy]').addEventListener('click',()=>{ const t=repTxt(M,D); (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>toast('Copiado: pegalo en WhatsApp')).catch(()=>{ prompt('Copiá el texto:',t); }); });
}

/* =====================================================================
   SEGUIMIENTO · una fila por marca: cómo viene y qué hacer hoy
   ===================================================================== */
function segRow(b){
  const D=DATA[b]; let x; try{ x=analyze(b); }catch(e){ console.error(b,e); return null; }
  const T=x.T, TP=x.TP, R=x.R;
  const mv=movesOf(b)[0]; const wait=mv && daysBetween(mv.date,NOW)<REVIEW_DAYS;
  const firstAct=x.F.find(f=>f.sev==='urgente')||x.F.find(f=>f.sev==='atencion')||x.F.find(f=>f.sev==='bien')||x.F[0];
  let line;
  if(!T.spend) line=`<b>${x.F[0].t}.</b>`;
  else { const c=R.sales?(T.purch?`ROAS ${dec(T.roas)}x`:'sin compras'):(T.res?`${$m(T.spend/T.res)} c/u`:'sin resultados');
    const cmp=x.tr&&x.tr.c!=null?` · ${x.tr.main==='roas'?(x.tr.c>=0?'mejor':'peor'):(x.tr.c<=0?'más barato':'más caro')} que la semana anterior (${x.tr.c>0?'+':'−'}${pctTxt(Math.abs(x.tr.c))})`:'';
    line=`<b>${resTxt(T.res,R)}</b> con ${$m(T.spend)} · ${c}${cmp}.`; }
  return {b,D,x,line,firstAct,wait,mv,rank:x.F.some(f=>f.sev==='urgente')?0:(x.F.some(f=>f.sev==='atencion')?1:2)};
}
function renderSeg(){
  const el=document.getElementById('body-seg');
  const xs=CLIENTS.filter(b=>isActiveBrand(b)&&!SEG_SKIP.includes(b)).map(segRow).filter(Boolean).sort((p,q)=>p.rank-q.rank);
  const urg=[]; xs.forEach(r=>r.x.F.filter(f=>f.sev==='urgente').forEach(f=>urg.push({r,f})));
  el.innerHTML=`<div class="v3">
    <div class="eyebrow" style="margin-top:18px">Seguimiento · semana que terminó el ${CLOSED?fmtWd(CLOSED):'—'}</div>
    <h2>${urg.length?`Hoy hay <span class="hl">${urg.length} ${urg.length===1?'cosa urgente':'cosas urgentes'}</span>`:'Hoy no hay nada <span class="hl">urgente</span>'}</h2>
    <p class="sub">Una fila por marca: cómo vino la última semana contra la anterior, y lo primero que hay que hacer. Regla: después de un cambio, 3 días sin tocar.</p>
    ${urg.length?`<div class="darkband" style="margin-bottom:16px">${urg.map(u=>`<div style="margin:4px 0"><span class="sem bad"></span><b>${esc(u.r.D.name)}:</b> ${u.f.t}. <span style="color:var(--muteD)">👉 ${u.f.a}</span></div>`).join('')}</div>`:''}
    <div class="seg">${xs.map(r=>{ const sl=SEMLBL[r.x.sem];
      const nx=r.wait?`⏳ No tocar hasta el ${fmtWd(addDays(r.mv.date,REVIEW_DAYS))}<small>Hubo cambios el ${fmtWd(r.mv.date)}</small>`
        :(r.firstAct?`👉 ${shortAct(r.firstAct)}`:'Mantener; sin novedades.');
      return `<div class="segrow"><div><div class="nm"><span class="sem ${sl[1]}" title="${sl[0]}"></span>${esc(r.D.name)}</div><div class="ob">${{msg:'WhatsApp',purchase:'Ventas web',lead:'Leads',traffic:'Tráfico'}[r.x.R.kind]||''} · ${r.x.per.short}</div></div>
        <div class="msg">${r.line}${r.firstAct&&r.firstAct.sev!=='bien'?`<div class="muted" style="font-size:13px;margin-top:2px">${r.firstAct.t}.</div>`:(r.firstAct?`<div style="font-size:13px;margin-top:2px;color:var(--good)">${r.firstAct.t}.</div>`:'')}</div>
        <div class="nx">${nx}<div style="margin-top:6px"><a href="#" data-ana="${r.b}" style="font-size:12.5px">Ver análisis →</a></div></div></div>`; }).join('')}</div>
  </div>`;
  el.querySelectorAll('[data-ana]').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();goTo('analisis',a.dataset.ana);}));
}

/* =====================================================================
   AGENCIA · cómo venimos como negocio y qué hacer esta semana.
   Color sólo donde hay una decisión; los números van neutros.
   ===================================================================== */
function renderAgencia(){
  const el=document.getElementById('body-agencia'); const X=agencyCalc();
  if(!X.cl.length){ el.innerHTML='<div class="v3"><p class="sub">Falta config/agency.yaml</p></div>'; return; }
  const M=v=>(v<0?'−':'')+'$'+(Math.abs(v)>=1e6?dec(Math.abs(v)/1e6,1)+'M':money(Math.round(Math.abs(v)/1000))+'k');
  const act=X.cl.filter(c=>c.active), paying=act.filter(c=>c.status==='pagando'), free=act.filter(c=>c.status==='gratis'), toConf=act.filter(c=>c.status==='a_confirmar');
  const convR=X.decided.length?X.converted.length/X.decided.length:null;
  // Acciones de la semana (lo único con color)
  const A=[];
  toConf.forEach(c=>A.push({o:0,cls:'warn',when:'Esta semana',t:`Cerrar ${c.name}`,d:`Está a confirmar${c.pending?` (${M(c.pending.amount)} de ${c.pending.concept||'pauta'})`:''}. Llevar resultados + precio + fecha de decisión por escrito.`}));
  free.forEach(c=>{ if(!c.freeEnd) return; const d=daysBetween(NOW,c.freeEnd);
    if(d<0) A.push({o:0,cls:'bad',when:'Vencido',t:`${c.name}: el mes gratis terminó el ${fmtDM(c.freeEnd)}`,d:'Definir ya: propuesta paga con precio y fecha, o cerrar. Cada semana extra gratis es costo nuestro.'});
    else if(d<=10) A.push({o:1,cls:'warn',when:`En ${d} día${d===1?'':'s'}`,t:`${c.name} decide el ${fmtWd(c.freeEnd)}`,d:'Reunión de resultados antes de esa fecha, con la propuesta de continuidad.'}); });
  act.forEach(c=>{ if(!DATA[c.slug]) return; let x; try{x=analyze(c.slug);}catch(e){return;}
    const u=x.F.find(f=>f.sev==='urgente'); if(u) A.push({o:2,cls:'',when:'Pauta',t:`${c.name}: ${u.t.charAt(0).toLowerCase()+u.t.slice(1)}`,d:u.a}); });
  A.sort((p,q)=>p.o-q.o);
  // Tabla de clientes
  const ST={pagando:['Pagando','good'],gratis:['Mes gratis',''],a_confirmar:['A confirmar','warn'],perdido:['Perdido','ghost']};
  const rowsC=[...act,...X.lost].map(c=>{ let pz='—';
    if(DATA[c.slug]&&c.active){ try{ const x=analyze(c.slug); const sl=SEMLBL[x.sem]; pz=`<span class="sem ${sl[1]}"></span>${x.T.spend?resTxt(x.T.res,x.R)+' en la semana':'sin inversión'}`; }catch(e){} }
    const s=ST[c.status]||['—',''];
    return `<tr><td><b>${esc(c.name)}</b><br><span class="muted" style="font-size:12px">${c.source==='convocatoria'?'convocatoria':'directo'} · desde ${fmtDM(c.start)}${c.end?' · hasta '+fmtDM(c.end):''}</span></td>
      <td><span class="tag ${s[1]}">${s[0]}</span></td><td class="r">${c.rev?M(c.rev):'—'}</td><td>${(c.svc||[]).join(' + ')||'—'}</td><td>${pz}</td></tr>`; }).join('');
  const verdict=`Entra <span class="hl">${M(X.mrr)}</span> este mes con ${paying.length} cliente${paying.length===1?'':'s'} pagando${X.pend?` (+${M(X.pend)} si se confirma lo pendiente)`:''}.`;
  el.innerHTML=`<div class="v3">
    <div class="eyebrow" style="margin-top:18px">Agencia · al ${fmtWd(NOW)}</div>
    <h2>${verdict}</h2>
    <p class="sub">${convR!=null?`De los que terminaron el mes gratis, pasaron a pagar ${X.converted.length} de ${X.decided.length}. `:''}La prioridad es que los que llegan paguen y se queden: las acciones de abajo son las que mueven ese número.</p>
    <div class="grid g4" style="margin-bottom:20px">
      ${kpiBox('Clientes activos',String(act.length),`${paying.length} pagando · ${free.length} gratis · ${toConf.length} a confirmar`)}
      ${kpiBox('Entra este mes',M(X.mrr),`nos quedan ${M(X.mrrM)} después de diseñadoras`)}
      ${kpiBox('Gratis → pago',convR!=null?`${X.converted.length} de ${X.decided.length}`:'—',`meta: ${Math.round((X.T.conversion_free_to_paid||.5)*100)}%`)}
      ${kpiBox('Resultado acumulado',M(X.net),`facturado ${M(X.rev)} − seteos, captación y diseñadoras`)}
    </div>
    <h3>Qué hacer esta semana</h3>
    <div class="acts" style="margin-top:8px">${A.length?A.map(a=>`<div class="act"><div class="when">${a.cls?`<span class="sem ${a.cls}"></span>`:''}${a.when}</div><div><div class="at">${a.t}</div><div class="ad">${a.d}</div></div></div>`).join(''):'<div class="lcard">Nada pendiente esta semana.</div>'}</div>
    <h3>Clientes</h3>
    <div class="lcard scrollx" style="margin-top:8px;padding:6px 10px"><table class="cl"><tr><th>Cliente</th><th>Estado</th><th class="r">Nos pagó</th><th>Servicios</th><th>Pauta · última semana</th></tr>${rowsC}</table></div>
    <h3>Captación</h3>
    <p class="sub">Campaña de la convocatoria (mes gratis) desde ${X.acqFrom?fmtDM(X.acqFrom):'—'}.</p>
    <div class="funnel">
      <div class="st"><div class="v">${money(X.acqLeads)}</div><div class="k">formularios · ${M(X.acqSpend)} invertidos${X.acqLeads?` (${$m(X.acqSpend/X.acqLeads)} c/u)`:''}</div></div><div class="ar">→</div>
      <div class="st"><div class="v">${X.conv.length}</div><div class="k">arrancaron el mes gratis</div></div><div class="ar">→</div>
      <div class="st"><div class="v">${X.conv.filter(c=>c.paid).length}</div><div class="k">pagaron algo</div></div><div class="ar">→</div>
      <div class="st"><div class="v">${X.conv.filter(c=>c.status==='pagando').length}</div><div class="k">siguen pagando</div></div>
    </div>
    <details><summary>Ver la plata y los replanteos</summary>
      <div class="lcard" style="margin-top:6px"><table class="cl">
        <tr><td>Facturado (todo)</td><td class="r">${M(X.rev)}</td></tr>
        <tr><td>Diseñadoras / costos directos</td><td class="r">−${M(X.dcost).replace('$','$')}</td></tr>
        <tr><td>Seteos de campaña (${M(X.setup)} por cliente)</td><td class="r">−${M(X.setups)}</td></tr>
        <tr><td>Pauta de captación</td><td class="r">−${M(X.acqSpend)}</td></tr>
        <tr><td><b>Resultado</b></td><td class="r"><b>${M(X.net)}</b></td></tr></table></div>
      ${agencyDetail()}
    </details>
  </div>`;
}

/* =====================================================================
   INIT (reemplaza el init del script principal)
   ===================================================================== */
(function init(){
  const firstPaying=(()=>{ const ag=(window.AGENCY||{}).clients||[]; const p=ag.find(c=>c.status==='pagando'&&DATA[c.slug]); return p?p.slug:(CLIENTS.find(isActiveBrand)||CLIENTS[0]); })();
  buildRangeBar("range-gen");
  buildRangeBar("range-dash");
  v3Picker("pick-dash", renderDash, firstPaying);
  v3Picker("pick-ana", renderAnalisis, firstPaying);
  v3Picker("pick-rep", renderReporte, firstPaying);
  buildPicker("pick-prop", renderProp, "piera");
  buildPicker("pick-est", renderEst, "vresia");
  renderGeneral();
  syncRangeBars();
  renderSeg();
  renderAgencia();
  window.COMMENTS=window.COMMENTS||{};
  document.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>{
    document.querySelectorAll(".tab").forEach(x=>x.setAttribute("aria-selected","false"));
    document.querySelectorAll(".panel").forEach(x=>x.classList.remove("on"));
    t.setAttribute("aria-selected","true"); document.getElementById("p-"+t.dataset.tab).classList.add("on");
    document.querySelectorAll("#p-"+t.dataset.tab+" iframe").forEach(fit);
  }));
  const h=document.querySelector('.brandtxt h1'); if(h) h.innerHTML='Centro de <span class="hl">Operaciones</span>';
})();

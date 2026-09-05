/**
 * Digital Twin - Philips LED Lamp Panel Physics Engine
 * NB2 Panel: 40 x Philips 15W LED, P_total=430W, PF=0.6 (measured)
 * Physics: Arrhenius AF(T)=exp(Ea/kB*(1/T_ref-1/T_j)), IES LM-80, RC thermal
 * Calibrated: Philips LEDScene D 15W 2700K + NB2 sensor data
 */
'use strict';
const PHY = Object.freeze({
    N_LAMPS:40, P_RATED:15.0, P_MEASURED:10.75, PF:0.60, V_SUPPLY:220.0,
    LUMENS_RATED:1500, EFFICACY_RATED:100, COLOR_TEMP:2700,
    R_TH_NOMINAL:5.5, C_TH:18.0, T_AMBIENT:25.0,
    E_A:0.70, K_B:8.617e-5, T_REF:358.15,
    L70_RATED:15000, K_LM:2.378e-5, L70_THRESHOLD:0.70,
    WEIBULL_SHAPE:2.8, FAILURE_HEALTH:0.05, TICK_MS:16,
});
const K_B_INV_EREF = PHY.E_A / PHY.K_B;
const simEngine = {
    isRunning:false, timeScale:1.0, t_sim_hours:0,
    lastTick:0, lastGaugeUpdate:0, intervalId:null,
    scenario:null, _cycleMode:false, _cycleOnTime:1800,
    _cycleOffTime:300, _cycleTimer:0, _cycleIsOn:true,
    faults:{}, lamps:[],
};
function makeLamp(id) {
    var v = PHY.R_TH_NOMINAL*(1+(Math.random()-0.5)*0.10);
    return {id:id,on:false,failed:false,health:1.0,lumensNorm:1.0,
        Tj:PHY.T_AMBIENT,hoursRun:0,R_th:v,_loadFactor:1.0,failMode:'none'};
}
function initLamps() {
    simEngine.lamps=[];
    for(var i=0;i<PHY.N_LAMPS;i++) simEngine.lamps.push(makeLamp(i));
}
function arrhenius_AF(Tj) { return Math.exp(K_B_INV_EREF*(1/PHY.T_REF-1/(Tj+273.15))); }
function lumenFraction(h,Tj) { return Math.exp(-PHY.K_LM*arrhenius_AF(Tj)*h); }
var BASE_DEGRAD_RATE = 1.0/(PHY.L70_RATED*3600);
function thermalTick(lamp,dt,P,Tamb,Rth) {
    if(!lamp.on||lamp.failed){lamp.Tj=Tamb+(lamp.Tj-Tamb)*Math.exp(-dt/(PHY.C_TH*PHY.R_TH_NOMINAL));return;}
    var Tss=Tamb+Rth*P, tau=PHY.C_TH*Rth;
    lamp.Tj=Tss+(lamp.Tj-Tss)*Math.exp(-dt/tau);
}
function degradationTick(lamp,dt) {
    if(!lamp.on||lamp.failed)return;
    var af=arrhenius_AF(lamp.Tj);
    lamp.health=Math.max(0,lamp.health-BASE_DEGRAD_RATE*af*dt);
    lamp.hoursRun+=dt/3600;
    lamp.lumensNorm=Math.max(0,lumenFraction(lamp.hoursRun,lamp.Tj));
    if(lamp.health<=PHY.FAILURE_HEALTH&&!lamp.failed){killLamp(lamp,'lumen_depreciation');return;}
    if(dt>=1&&lamp.health<0.50){
        var b=PHY.WEIBULL_SHAPE,eta=PHY.L70_RATED*3600/af,t=lamp.hoursRun*3600;
        if(t>0&&eta>0){var ht=(b/eta)*Math.pow(t/eta,b-1);if(Math.random()<Math.min(0.005,ht*dt))killLamp(lamp,'thermal_runaway');}
    }
}
function killLamp(lamp,mode) {
    lamp.failed=true;lamp.on=false;lamp.health=0;lamp.failMode=mode;lamp.Tj=PHY.T_AMBIENT;
    var fc=simEngine.lamps.filter(function(l){return l.failed;}).length;
    simLog('bad','Lamp #'+(lamp.id+1)+' FAILED ('+mode.replace(/_/g,' ')+').  Total: '+fc+'/40');
    var el=document.getElementById('lamp-bulb-'+lamp.id);
    if(el){el.classList.add('lamp-pop');setTimeout(function(){el.classList.remove('lamp-pop');updateLampDOM(lamp);},550);}
}var SCENARIOS = [
    {id:'s1',name:'Baseline Aging 10,000h Nominal',risk:'LOW',riskClass:'normal',hours:10000,
     desc:'Continuous nominal. Tj ~84C. Stochastic failures after ~8,000h. Real NB2 panel lifespan at P=430W, PF=0.6.',
     params:[{k:'Duration',v:'10,000 h'},{k:'Tj',v:'~84 C'},{k:'AF',v:'~1.0x'}],
     applyFn:function(e){e.faults={};turnAllLampsOn();}},
    {id:'s2',name:'Thermal Runaway — Desert Summer',risk:'HIGH',riskClass:'fault',hours:5000,
     desc:'Ambient +45C (Saudi summer) + clogged heatsink. Tj ~115C. AF ~8x. Failures within ~1,900h equivalent.',
     params:[{k:'T_ambient',v:'+20C (45C)'},{k:'R_th',v:'+0.8 C/W'},{k:'AF',v:'~8x'}],
     applyFn:function(e){e.faults={ambientHigh:true};e.lamps.forEach(function(l){if(!l.failed)l.R_th=PHY.R_TH_NOMINAL+0.8;});turnAllLampsOn();}},
    {id:'s3',name:'Dust Accumulation — Progressive R_th Drift',risk:'MEDIUM',riskClass:'warning',hours:8000,
     desc:'Dust on heatsinks: R_th +0.04 C/W per 1000h. Tj creeps up, Arrhenius aging accelerates non-linearly.',
     params:[{k:'R_th drift',v:'+0.04 C/W/1000h'},{k:'Duration',v:'8,000 h'},{k:'Final Tj',v:'~106 C'}],
     applyFn:function(e){e.faults={dustAccum:true};turnAllLampsOn();}},
    {id:'s4',name:'Row Failure Cascade',risk:'HIGH',riskClass:'fault',hours:4000,
     desc:'Row 1 (8 lamps) fails at t=0. Remaining 32 lamps absorb +25% load. Rows 2-5 degrade asymmetrically.',
     params:[{k:'Row 1',v:'FAILED (8 lamps)'},{k:'Overload',v:'+25%'},{k:'AF boost',v:'~1.4x'}],
     applyFn:function(e){
        e.faults={row1Killed:true};turnAllLampsOn();
        for(var i=0;i<8;i++){var l=e.lamps[i];l.failed=true;l.on=false;l.health=0;l.failMode='initial_failure';l.Tj=PHY.T_AMBIENT;}
        simLog('warn','Row 1 pre-failed. Load redistributed to rows 2-5 (+25%).');}},
    {id:'s5',name:'Asymmetric Overload',risk:'CRITICAL',riskClass:'fault',hours:3000,
     desc:'Lamps 1-10 at 150% load (subcircuit A fault). Lamps 11-40 at 70%. Hot-spot cluster AF ~4x on lamps 1-10.',
     params:[{k:'Lamps 1-10',v:'150% load (16.1W)'},{k:'Lamps 11-40',v:'70% load'},{k:'AF hot-spot',v:'~4x'}],
     applyFn:function(e){
        e.faults={};turnAllLampsOn();
        for(var i=0;i<10;i++)e.lamps[i]._loadFactor=1.50;
        for(var i=10;i<40;i++)e.lamps[i]._loadFactor=0.70;
        simLog('warn','Asymmetric load: lamps 1-10 at 150%, 11-40 at 70%.');}},
    {id:'s6',name:'Cold-Start Thermal Cycling',risk:'MEDIUM',riskClass:'warning',hours:6000,
     desc:'Cyclic ON/OFF: 30min ON, 5min OFF. Each cold-start causes solder fatigue (-0.03% health). Simulates motion-sensor offices.',
     params:[{k:'ON cycle',v:'30 min'},{k:'OFF cycle',v:'5 min'},{k:'Fatigue',v:'-0.03%/restart'}],
     applyFn:function(e){
        e.faults={};e._cycleMode=true;e._cycleOnTime=1800;e._cycleOffTime=300;
        e._cycleTimer=0;e._cycleIsOn=true;turnAllLampsOn();
        simLog('info','Thermal cycling: 30min ON / 5min OFF. Cold-start fatigue active.');}},
    {id:'s7',name:'End-of-Life Stress Test',risk:'EXTREME',riskClass:'extreme',hours:2000,
     desc:'All 40 lamps at 60% health (post-L70) + overvoltage +15%. Rapid cascade failures within 300-500h. Demonstrates replacement urgency.',
     params:[{k:'Initial health',v:'60% (post-L70)'},{k:'Overvoltage',v:'+15%'},{k:'Exp. failures',v:'<500h'}],
     applyFn:function(e){
        e.faults={overvoltage:true};
        e.lamps.forEach(function(l){if(!l.failed){l.health=0.60+(Math.random()-0.5)*0.12;l.hoursRun=8000+Math.random()*500;l.lumensNorm=lumenFraction(l.hoursRun,85);l.R_th=PHY.R_TH_NOMINAL+0.4;}});
        turnAllLampsOn();
        simLog('warn','End-of-Life: 40 lamps pre-aged ~60% health + overvoltage. Expect rapid cascade failures.');}}
];var simUI={
    btnRun:document.getElementById('sim-run-btn'),btnPause:document.getElementById('sim-pause-btn'),
    btnReset:document.getElementById('sim-reset-btn'),slider:document.getElementById('tsb-slider'),
    displaySpeed:document.getElementById('tsb-display'),presets:document.querySelectorAll('.tsb-preset'),
    logBody:document.getElementById('sim-log'),btnClearLog:document.getElementById('sim-log-clear-btn'),
    lgHealth:document.getElementById('lg-health'),lgHealthBar:document.getElementById('lg-health-bar'),
    lgTj:document.getElementById('lg-tj'),lgTjBar:document.getElementById('lg-tj-bar'),
    lgEfficacy:document.getElementById('lg-efficacy'),lgEfficacyBar:document.getElementById('lg-efficacy-bar'),
    lgSimtime:document.getElementById('lg-simtime'),lgSimtimeSub:document.getElementById('lg-simtime-sub'),
    lgSimtimeBar:document.getElementById('lg-simtime-bar'),lgFailed:document.getElementById('lg-failed'),
    lgFailedSub:document.getElementById('lg-failed-sub'),lgFailedBar:document.getElementById('lg-failed-bar'),
    lgLumens:document.getElementById('lg-lumens'),lgLumensBar:document.getElementById('lg-lumens-bar'),
    dsHealth:document.getElementById('ds-health'),dsLumens:document.getElementById('ds-lumens'),
    dsFailed:document.getElementById('ds-failed'),dsL70:document.getElementById('ds-l70'),
    dsReplace:document.getElementById('ds-replace'),dsAF:document.getElementById('ds-af'),
    stateBadge:document.getElementById('sim2-state-badge'),
    timeBadge:document.getElementById('sim-time-badge'),
    jumpResult:document.getElementById('time-jump-result'),
};
var _logCount=0;
function simLog(type,msg){
    if(!simUI.logBody)return;
    _logCount++;
    if(_logCount>300){var es=simUI.logBody.querySelectorAll('.log-entry');for(var i=0;i<100&&i<es.length;i++)es[i].remove();_logCount-=100;}
    var h=simEngine.t_sim_hours,days=Math.floor(h/24),hrs=Math.floor(h%24),mins=Math.floor((h*60)%60);
    var ts=String(days).padStart(4,'0')+'d '+String(hrs).padStart(2,'0')+'h '+String(mins).padStart(2,'0')+'m';
    var div=document.createElement('div');div.className='log-entry '+type;
    div.innerHTML='<span class="log-ts">'+ts+'</span><span class="log-msg">'+msg+'</span>';
    simUI.logBody.appendChild(div);simUI.logBody.scrollTop=simUI.logBody.scrollHeight;
}
var chartLumens,chartTemp,chartHealth,_lastChartHour=0;
var CHART_INTERVAL_HOURS=0.5;
function initCharts(){
    var cL=document.getElementById('chart-lumens'),cT=document.getElementById('chart-temp-sim'),cH=document.getElementById('chart-health');
    if(!cL||!cT||!cH)return;
    Chart.defaults.color='#64748B';Chart.defaults.font.family="'Inter',sans-serif";
    var xSc={type:'linear',display:true,title:{display:true,text:'Simulated Hours',font:{size:9}},
        ticks:{font:{size:9},maxTicksLimit:6,callback:function(v){return v>=1000?(v/1000).toFixed(1)+'kh':v+'h';}}};
    var yB={beginAtZero:false,grid:{color:'rgba(15,23,42,0.05)'},ticks:{font:{size:9}}};
    var bO={responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{display:false}},
        elements:{point:{radius:0},line:{borderWidth:1.8,tension:0.15}}};
    chartLumens=new Chart(cL,{type:'line',data:{datasets:[
        {label:'Avg Lm%',data:[],borderColor:'#d97706',backgroundColor:'rgba(217,119,6,0.10)',fill:true},
        {label:'Min Lm%',data:[],borderColor:'#ef4444',backgroundColor:'transparent',borderDash:[3,3]}
    ]},options:Object.assign({},bO,{scales:{x:xSc,y:Object.assign({},yB,{min:0,max:105,title:{display:true,text:'Lumen %',font:{size:9}}})}})});
    chartTemp=new Chart(cT,{type:'line',data:{datasets:[
        {label:'Avg Tj',data:[],borderColor:'#f97316',backgroundColor:'rgba(249,115,22,0.10)',fill:true},
        {label:'Max Tj',data:[],borderColor:'#dc2626',backgroundColor:'transparent',borderDash:[3,3]}
    ]},options:Object.assign({},bO,{scales:{x:xSc,y:Object.assign({},yB,{min:20,max:130,title:{display:true,text:'degC',font:{size:9}}})}})});
    chartHealth=new Chart(cH,{type:'line',data:{datasets:[
        {label:'Avg H%',data:[],borderColor:'#22c55e',backgroundColor:'rgba(34,197,94,0.10)',fill:true},
        {label:'Min H%',data:[],borderColor:'#ef4444',backgroundColor:'transparent',borderDash:[3,3]}
    ]},options:Object.assign({},bO,{scales:{x:xSc,y:Object.assign({},yB,{min:0,max:105,title:{display:true,text:'Health %',font:{size:9}}})}})});
}
function pushChartPoint(t){
    var on=simEngine.lamps.filter(function(l){return !l.failed&&l.on;});
    var all=simEngine.lamps;
    var aLm=on.length>0?on.reduce(function(a,l){return a+l.lumensNorm;},0)/on.length*100:0;
    var mLm=on.length>0?Math.min.apply(null,on.map(function(l){return l.lumensNorm*100;})):0;
    var aTj=on.length>0?on.reduce(function(a,l){return a+l.Tj;},0)/on.length:PHY.T_AMBIENT;
    var xTj=on.length>0?Math.max.apply(null,on.map(function(l){return l.Tj;})):PHY.T_AMBIENT;
    var aH=all.reduce(function(a,l){return a+l.health;},0)/all.length*100;
    var mH=Math.min.apply(null,all.map(function(l){return l.health*100;}));
    function pp(ch,v0,v1){if(!ch)return;
        ch.data.datasets[0].data.push({x:t,y:v0});ch.data.datasets[1].data.push({x:t,y:v1});
        if(ch.data.datasets[0].data.length>800){ch.data.datasets[0].data.shift();ch.data.datasets[1].data.shift();}
        ch.update('none');}
    pp(chartLumens,aLm,mLm);pp(chartTemp,aTj,xTj);pp(chartHealth,aH,mH);
    if(simUI.timeBadge)simUI.timeBadge.textContent='t = '+Math.floor(t).toLocaleString()+' h';
}function buildLampGrid(){
    var g=document.getElementById('lamp-grid');if(!g)return;g.innerHTML='';
    simEngine.lamps.forEach(function(lamp){
        var c=document.createElement('div');c.className='lamp-cell';c.id='lamp-cell-'+lamp.id;
        c.innerHTML='<div class="lamp-bulb lamp-off" id="lamp-bulb-'+lamp.id+'"></div>'+
            '<div class="lamp-id">L'+String(lamp.id+1).padStart(2,'0')+'</div>'+
            '<div class="lamp-health-bar"><div class="lamp-health-fill" id="lamp-hbar-'+lamp.id+'" style="width:100%;background:#22c55e;"></div></div>';
        c.addEventListener('mouseenter',function(e){showLampTooltip(lamp,e);});
        c.addEventListener('mousemove',function(e){moveLampTooltip(e);});
        c.addEventListener('mouseleave',hideLampTooltip);
        g.appendChild(c);
    });
}
function getLampClass(l){
    if(l.failed)return 'lamp-failed';if(!l.on)return 'lamp-off';
    if(l.health>0.80)return 'lamp-on';if(l.health>0.50)return 'lamp-degraded';return 'lamp-critical';
}
function updateLampDOM(l){
    var b=document.getElementById('lamp-bulb-'+l.id),h=document.getElementById('lamp-hbar-'+l.id);
    if(!b||!h)return;b.className='lamp-bulb '+getLampClass(l);
    var p=l.health*100;h.style.width=p.toFixed(1)+'%';
    h.style.background=p>80?'#22c55e':p>50?'#fbbf24':p>20?'#f97316':'#ef4444';
}
function updateAllLampsDOM(){simEngine.lamps.forEach(updateLampDOM);}
var _tt=null;
function ensureTooltip(){if(!_tt){_tt=document.createElement('div');_tt.id='lamp-tooltip';document.body.appendChild(_tt);}return _tt;}
function showLampTooltip(lamp,e){
    var tt=ensureTooltip();
    var h=lamp.health*100,lm=lamp.lumensNorm*100;
    var af=lamp.on?arrhenius_AF(lamp.Tj).toFixed(2):'--';
    var st=lamp.failed?'FAILED':(lamp.on?'ON':'OFF');
    var hc=h>80?'ok':h>50?'warn':'bad',lc=lm>80?'ok':lm>60?'warn':'bad';
    var tc=lamp.Tj<80?'ok':lamp.Tj<100?'warn':'bad',sc=lamp.failed?'dead':(lamp.on?'ok':'');
    tt.innerHTML='<div class="lamp-tt-header">Lamp #'+(lamp.id+1)+' — Row '+(Math.floor(lamp.id/8)+1)+', Col '+(lamp.id%8+1)+'</div>'+
        '<div class="lamp-tt-row"><span class="lamp-tt-label">State</span><span class="lamp-tt-val '+sc+'">'+st+'</span></div>'+
        '<div class="lamp-tt-row"><span class="lamp-tt-label">Health</span><span class="lamp-tt-val '+hc+'">'+h.toFixed(1)+'%</span></div>'+
        '<div class="lamp-tt-row"><span class="lamp-tt-label">Lumen Output</span><span class="lamp-tt-val '+lc+'">'+lm.toFixed(1)+'%</span></div>'+
        '<div class="lamp-tt-row"><span class="lamp-tt-label">Junction Temp</span><span class="lamp-tt-val '+tc+'">'+lamp.Tj.toFixed(1)+' C</span></div>'+
        '<div class="lamp-tt-row"><span class="lamp-tt-label">Hours Run</span><span class="lamp-tt-val">'+Math.floor(lamp.hoursRun)+' h</span></div>'+
        '<div class="lamp-tt-row"><span class="lamp-tt-label">Arrhenius AF</span><span class="lamp-tt-val">'+af+'x</span></div>'+
        '<div class="lamp-tt-row"><span class="lamp-tt-label">R_th</span><span class="lamp-tt-val">'+lamp.R_th.toFixed(2)+' C/W</span></div>'+
        (lamp.failed?'<div class="lamp-tt-row"><span class="lamp-tt-label">Fail Mode</span><span class="lamp-tt-val dead">'+lamp.failMode+'</span></div>':'');
    tt.classList.add('visible');moveLampTooltip(e);
}
function moveLampTooltip(e){var tt=ensureTooltip();tt.style.left=(e.clientX+16)+'px';tt.style.top=(e.clientY-10)+'px';}
function hideLampTooltip(){var tt=ensureTooltip();tt.classList.remove('visible');}
function updateGauges(){
    var lamps=simEngine.lamps;
    var on=lamps.filter(function(l){return l.on&&!l.failed;});
    var fc=lamps.filter(function(l){return l.failed;}).length;
    var aH=lamps.reduce(function(a,l){return a+l.health;},0)/lamps.length*100;
    var aTj=on.length>0?on.reduce(function(a,l){return a+l.Tj;},0)/on.length:PHY.T_AMBIENT;
    var tKlm=on.reduce(function(a,l){return a+l.lumensNorm*PHY.LUMENS_RATED;},0)/1000;
    var tPwr=on.length*PHY.P_MEASURED;
    var eff=tPwr>0?(tKlm*1000)/tPwr:0;
    var t=simEngine.t_sim_hours;
    function s(id,v){var e=document.getElementById(id);if(e)e.textContent=v;}
    function st(id,p,v){var e=document.getElementById(id);if(e)e.style[p]=v;}
    s('lg-health',aH.toFixed(1));st('lg-health-bar','width',aH.toFixed(1)+'%');
    st('lg-health-bar','background',aH>80?'#22c55e':aH>50?'#fbbf24':'#ef4444');
    s('lg-tj',aTj.toFixed(1));var tjp=Math.min(100,((aTj-20)/110)*100);st('lg-tj-bar','width',tjp.toFixed(1)+'%');
    st('lg-tj-bar','background',aTj<70?'#22c55e':aTj<90?'#fbbf24':'#ef4444');
    s('lg-efficacy',eff.toFixed(0));st('lg-efficacy-bar','width',Math.min(100,eff).toFixed(1)+'%');
    var dy=Math.floor(t/24),hr=Math.floor(t%24);
    s('lg-simtime',Math.floor(t).toLocaleString());
    var ssub=document.getElementById('lg-simtime-sub');if(ssub)ssub.textContent=dy+'d '+hr+'h elapsed';
    var sc2=simEngine.scenario;st('lg-simtime-bar','width',Math.min(100,t/(sc2?sc2.hours:10000)*100).toFixed(1)+'%');
    s('lg-failed',fc);
    var fsub=document.getElementById('lg-failed-sub');if(fsub)fsub.textContent=fc===0?'All operational':(fc/40*100).toFixed(0)+'% failed';
    st('lg-failed-bar','width',(fc/40*100).toFixed(1)+'%');
    s('lg-lumens',tKlm.toFixed(1));st('lg-lumens-bar','width',Math.min(100,tKlm/60*100).toFixed(1)+'%');
    var aLm=on.length>0?on.reduce(function(a,l){return a+l.lumensNorm;},0)/on.length*100:0;
    var aAF=on.length>0?on.reduce(function(a,l){return a+arrhenius_AF(l.Tj);},0)/on.length:1.0;
    var hToL70=aH>70?((aH-70)/100)/(BASE_DEGRAD_RATE*aAF*3600):0;
    function sd(el,html,cls){if(!el)return;el.classList.remove('ok','warn','bad');if(cls)el.classList.add(cls);el.innerHTML=html;}
    sd(simUI.dsHealth,aH.toFixed(1)+'<span class="deg-unit">%</span>',aH>80?'ok':aH>50?'warn':'bad');
    sd(simUI.dsLumens,aLm.toFixed(1)+'<span class="deg-unit">%</span>',aLm>80?'ok':aLm>70?'warn':'bad');
    sd(simUI.dsFailed,fc+'<span class="deg-unit">/ 40</span>',fc===0?'ok':fc<8?'warn':'bad');
    if(simUI.dsL70)simUI.dsL70.innerHTML=Math.max(0,Math.floor(hToL70)).toLocaleString()+'<span class="deg-unit">h</span>';
    if(simUI.dsReplace){if(hToL70>0){var nd=new Date();nd.setHours(nd.getHours()+hToL70);simUI.dsReplace.textContent=nd.toLocaleDateString('en-SA',{year:'numeric',month:'short',day:'numeric'});}else simUI.dsReplace.textContent='NOW';}
    if(simUI.dsAF)simUI.dsAF.innerHTML=aAF.toFixed(2)+'<span class="deg-unit">x</span>';
}function physicsTick(dt){
    var e=simEngine,sc=e.scenario;
    var Tamb=PHY.T_AMBIENT+(e.faults.ambientHigh?20.0:0.0);
    if(e._cycleMode){
        e._cycleTimer+=dt;var per=e._cycleIsOn?e._cycleOnTime:e._cycleOffTime;
        if(e._cycleTimer>=per){e._cycleTimer=0;e._cycleIsOn=!e._cycleIsOn;
            e.lamps.forEach(function(l){if(!l.failed){l.on=e._cycleIsOn;if(e._cycleIsOn)l.health=Math.max(0,l.health-0.0003);}});}
    }
    e.lamps.forEach(function(lamp){
        if(lamp.failed)return;
        if(e.faults.row1Killed&&lamp.id<8){lamp.on=false;lamp.Tj=Tamb+(lamp.Tj-Tamb)*Math.exp(-dt/(PHY.C_TH*PHY.R_TH_NOMINAL));return;}
        var lf=lamp._loadFactor||1.0;
        if(e.faults.overvoltage)lf*=1.3225;
        if(e.faults.row1Killed&&lamp.id>=8)lf*=1.25;
        var Peff=PHY.P_MEASURED*lf;
        var Rth=lamp.R_th;
        if(e.faults.dustAccum)Rth=Math.min(PHY.R_TH_NOMINAL+1.5,lamp.R_th+0.04*(lamp.hoursRun/1000));
        thermalTick(lamp,dt,Peff,Tamb,Rth);
        lamp.Tj=Math.max(Tamb,Math.min(150,lamp.Tj));
        degradationTick(lamp,dt);
    });
    e.t_sim_hours+=dt/3600;
    if(sc&&e.t_sim_hours>=sc.hours){
        simLog('ok','Scenario "'+sc.name+'" completed at '+Math.floor(e.t_sim_hours).toLocaleString()+'h.');
        toggleSim(false);e.scenario=null;
        document.querySelectorAll('.scenario-card').forEach(function(c){c.classList.remove('active');});
    }
}
function analyticalJump(hours){
    if(hours<=0)return;
    var dt=hours*3600;
    simLog('info','Analytical Jump: computing '+hours.toLocaleString()+'h of Arrhenius degradation...');
    var e=simEngine,Tamb=PHY.T_AMBIENT+(e.faults.ambientHigh?20.0:0.0);
    e.lamps.forEach(function(lamp){
        if(lamp.failed)return;
        if(e.faults.row1Killed&&lamp.id<8){lamp.Tj=Tamb;return;}
        var lf=lamp._loadFactor||1.0;
        if(e.faults.overvoltage)lf*=1.3225;
        if(e.faults.row1Killed&&lamp.id>=8)lf*=1.25;
        var Peff=PHY.P_MEASURED*lf;
        var Rth=lamp.R_th;
        if(e.faults.dustAccum)Rth=Math.min(PHY.R_TH_NOMINAL+1.5,lamp.R_th+0.04*((lamp.hoursRun+hours/2)/1000));
        var Tjss=Tamb+Rth*Peff;
        lamp.Tj=lamp.on?Math.min(150,Tjss):Tamb;
        if(lamp.on){
            var af=arrhenius_AF(lamp.Tj);
            lamp.health=Math.max(0,lamp.health-BASE_DEGRAD_RATE*af*dt);
            lamp.hoursRun+=hours;
            lamp.lumensNorm=Math.max(0,lumenFraction(lamp.hoursRun,lamp.Tj));
        }
        if(lamp.health<=PHY.FAILURE_HEALTH&&!lamp.failed){lamp.failed=true;lamp.on=false;lamp.health=0;lamp.failMode='lumen_depreciation';}
    });
    e.t_sim_hours+=hours;_lastChartHour=e.t_sim_hours;
    pushChartPoint(e.t_sim_hours);updateAllLampsDOM();updateGauges();
    var fn=e.lamps.filter(function(l){return l.failed;}).length;
    var ah=(e.lamps.reduce(function(a,l){return a+l.health;},0)/e.lamps.length*100).toFixed(1);
    simLog('ok','Jump complete. Time: '+Math.floor(e.t_sim_hours).toLocaleString()+'h. Failed: '+fn+'/40. Avg health: '+ah+'%.');
    if(simUI.jumpResult){simUI.jumpResult.textContent='Jumped to '+Math.floor(e.t_sim_hours).toLocaleString()+' h — '+fn+' lamps failed — Avg health: '+ah+'%';simUI.jumpResult.className='time-jump-result success';}
}
function engineStep(){
    if(!simEngine.isRunning)return;
    var now=performance.now();
    var dt=Math.min(0.1,(now-simEngine.lastTick)/1000);
    simEngine.lastTick=now;physicsTick(dt*simEngine.timeScale);
    if(now-simEngine.lastGaugeUpdate>=100){simEngine.lastGaugeUpdate=now;updateGauges();updateAllLampsDOM();}
    if(simEngine.t_sim_hours-_lastChartHour>=CHART_INTERVAL_HOURS){_lastChartHour=simEngine.t_sim_hours;pushChartPoint(simEngine.t_sim_hours);}
}
function toggleSim(run){
    if(run&&!simEngine.isRunning){
        simEngine.isRunning=true;simEngine.lastTick=performance.now();simEngine.lastGaugeUpdate=0;
        simEngine.intervalId=setInterval(engineStep,PHY.TICK_MS);
        if(simUI.btnRun)simUI.btnRun.disabled=true;if(simUI.btnPause)simUI.btnPause.disabled=false;
        if(simUI.stateBadge){simUI.stateBadge.textContent='RUNNING';simUI.stateBadge.classList.add('active');}
        simLog('ok','Simulation started at '+simEngine.timeScale+'x speed.');
    }else if(!run&&simEngine.isRunning){
        simEngine.isRunning=false;clearInterval(simEngine.intervalId);
        if(simUI.btnRun)simUI.btnRun.disabled=false;if(simUI.btnPause)simUI.btnPause.disabled=true;
        if(simUI.stateBadge){simUI.stateBadge.textContent='PAUSED';simUI.stateBadge.classList.remove('active');}
        simLog('warn','Simulation paused.');
    }
}
function turnAllLampsOn(){simEngine.lamps.forEach(function(l){if(!l.failed)l.on=true;l._loadFactor=1.0;});}
function resetSim(){
    toggleSim(false);simEngine.t_sim_hours=0;simEngine.scenario=null;
    simEngine._cycleMode=false;simEngine.faults={};_lastChartHour=0;_logCount=0;
    initLamps();buildLampGrid();
    [chartLumens,chartTemp,chartHealth].forEach(function(ch){if(ch){ch.data.datasets.forEach(function(d){d.data=[];});ch.update('none');}});
    updateGauges();
    if(simUI.stateBadge)simUI.stateBadge.textContent='Standby';
    if(simUI.timeBadge)simUI.timeBadge.textContent='t = 0 h';
    if(simUI.jumpResult){simUI.jumpResult.textContent='';simUI.jumpResult.className='time-jump-result';}
    document.querySelectorAll('.scenario-card').forEach(function(c){c.classList.remove('active');});
    document.querySelectorAll('.fault-inject-btn').forEach(function(b){b.classList.remove('active');});
    simLog('info','Simulation reset. All 40 lamps restored to factory state.');
}
function buildScenarioCards(){
    var list=document.getElementById('scenario-list');if(!list)return;list.innerHTML='';
    SCENARIOS.forEach(function(sc){
        var d=document.createElement('div');d.className='scenario-card';d.id='sc-card-'+sc.id;
        var ph=sc.params.map(function(p){return '<span class="sc-param">'+p.k+': '+p.v+'</span>';}).join('');
        d.innerHTML='<div class="sc-header"><span class="sc-name">'+sc.name+'</span><span class="sc-risk '+sc.riskClass+'">'+sc.risk+'</span></div>'+
            '<p class="sc-desc">'+sc.desc+'</p><div class="sc-params">'+ph+'</div>'+
            '<button class="sc-run-btn" data-id="'+sc.id+'">&#9654; Select &amp; Run</button>';
        list.appendChild(d);
    });
    list.querySelectorAll('.sc-run-btn').forEach(function(btn){
        btn.addEventListener('click',function(e){
            var scId=e.target.getAttribute('data-id');
            var sc=SCENARIOS.find(function(s){return s.id===scId;});
            if(!sc)return;
            resetSim();
            document.querySelectorAll('.scenario-card').forEach(function(c){c.classList.remove('active');});
            document.getElementById('sc-card-'+sc.id).classList.add('active');
            simEngine.scenario=sc;sc.applyFn(simEngine);
            updateAllLampsDOM();updateGauges();
            simLog('info','Scenario: "'+sc.name+'" — Duration: '+sc.hours.toLocaleString()+' h');
            toggleSim(true);
        });
    });
}
function initSimEngine(){
    initLamps();initCharts();buildLampGrid();buildScenarioCards();
    if(simUI.btnRun)simUI.btnRun.addEventListener('click',function(){toggleSim(true);});
    if(simUI.btnPause)simUI.btnPause.addEventListener('click',function(){toggleSim(false);});
    if(simUI.btnReset)simUI.btnReset.addEventListener('click',resetSim);
    if(simUI.btnClearLog)simUI.btnClearLog.addEventListener('click',function(){simUI.logBody.innerHTML='';_logCount=0;simLog('info','Log cleared.');});
    if(simUI.slider){
        var scales=[0.1,0.5,1,10,50,100];
        simUI.slider.addEventListener('input',function(e){
            var v=parseInt(e.target.value);simEngine.timeScale=scales[v];
            if(simUI.displaySpeed)simUI.displaySpeed.textContent=scales[v]+'x';
            simUI.presets.forEach(function(p){p.classList.remove('active');});
            var pp=document.getElementById('tsb-p'+v);if(pp)pp.classList.add('active');
        });
        simUI.presets.forEach(function(p){p.addEventListener('click',function(e){var s=e.target.getAttribute('data-step');simUI.slider.value=s;simUI.slider.dispatchEvent(new Event('input'));});});
    }
    var jInp=document.getElementById('time-jump-input');
    var jBtn=document.getElementById('btn-time-jump');
    if(jBtn)jBtn.addEventListener('click',function(){var h=parseInt(jInp?jInp.value:5000);if(!isNaN(h)&&h>0)analyticalJump(h);});
    document.querySelectorAll('.time-jump-preset').forEach(function(p){
        p.addEventListener('click',function(e){var h=parseInt(e.target.getAttribute('data-hours'));if(jInp)jInp.value=h;analyticalJump(h);});
    });
    function bfault(id,onF,offF){var el=document.getElementById(id);if(!el)return;el.addEventListener('click',function(){el.classList.toggle('active');if(el.classList.contains('active'))onF();else offF();});}
    bfault('btn-fault-overvolt',function(){simEngine.faults.overvoltage=true;simLog('bad','Overvoltage +15% active.');},function(){simEngine.faults.overvoltage=false;simLog('info','Overvoltage cleared.');});
    bfault('btn-fault-dust',function(){simEngine.faults.dustAccum=true;simLog('warn','Dust accumulation fault active.');},function(){simEngine.faults.dustAccum=false;simLog('info','Dust fault cleared.');});
    bfault('btn-fault-ambient',function(){simEngine.faults.ambientHigh=true;simLog('warn','Ambient +20C active.');},function(){simEngine.faults.ambientHigh=false;simLog('info','Ambient fault cleared.');});
    var pb=document.getElementById('btn-fault-partial');
    if(pb)pb.addEventListener('click',function(){
        pb.classList.toggle('active');var on=pb.classList.contains('active');
        simEngine.faults.row1Killed=on;
        for(var i=0;i<8;i++){var l=simEngine.lamps[i];if(!l.failed){l.on=!on;if(on)l.Tj=PHY.T_AMBIENT;}}
        updateAllLampsDOM();updateGauges();
        simLog(on?'bad':'info',on?'Row 1 (lamps 1-8) killed.':'Row 1 restored.');
    });
    var fr=document.getElementById('btn-fault-reset');
    if(fr)fr.addEventListener('click',function(){simEngine.faults={};document.querySelectorAll('.fault-inject-btn').forEach(function(b){b.classList.remove('active');});simLog('ok','All faults cleared.');});
    updateGauges();
    simLog('info','Physics engine ready. 40 x Philips 15W LED | P=430W | PF=0.6 | L70=15,000h@85C | Arrhenius Ea=0.70eV');
}
window.addEventListener('DOMContentLoaded',initSimEngine);
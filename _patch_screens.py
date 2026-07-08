#!/usr/bin/env python
"""Patch recruiter-screen and panel-screen HTML in app.py with improved dark-theme designs."""

RECRUITER_HTML = r'''    _html = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><title>Phone Screen &mdash; Resume Intelligence</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#07111b;--card:#0f1b2d;--card2:#0b1724;--line:#1e3a56;--text:#f0f5fb;--muted:#7a97b4;--accent:#6ae3c1;--accent2:#8ab4ff;--warn:#ffc36b;--red:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Aptos,'Segoe UI',system-ui,sans-serif;background:radial-gradient(circle at top left,#0f2540 0%,#07111b 55%,#040c16 100%);color:var(--text);min-height:100vh}
a{color:var(--accent2);text-decoration:none}
.nav{background:rgba(7,17,27,.95);border-bottom:1px solid var(--line);backdrop-filter:blur(10px);position:sticky;top:0;z-index:100}
.nav-inner{max-width:1160px;margin:0 auto;padding:0 24px;display:flex;align-items:center;height:52px;gap:2px}
.nav-logo{font-weight:900;font-size:14px;background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-right:20px;white-space:nowrap}
.nav-link{padding:0 13px;height:52px;display:flex;align-items:center;font-size:13px;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;white-space:nowrap;transition:color .15s}
.nav-link:hover{color:var(--text)}.nav-link.active{color:var(--warn);border-bottom-color:var(--warn)}
.nav-right{margin-left:auto}
.btn-nav{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#04111d;border:none;border-radius:10px;padding:8px 18px;font-size:12px;font-weight:900;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.stage-strip{background:rgba(9,20,34,.8);border-bottom:1px solid var(--line)}
.stage-inner{max-width:1160px;margin:0 auto;padding:10px 24px;display:flex;align-items:center}
.st{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:700;color:var(--muted)}
.st-dot{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;flex-shrink:0;border:2px solid var(--line)}
.st.done .st-dot{background:linear-gradient(135deg,var(--accent),#3dd6a3);color:#04111d;border-color:transparent}
.st.done{color:var(--accent)}
.st.active .st-dot{background:linear-gradient(135deg,var(--warn),#ff9f43);color:#04111d;border-color:transparent;box-shadow:0 0 14px rgba(255,195,107,.4)}
.st.active{color:var(--warn)}
.st-line{flex:1;height:2px;margin:0 8px;border-radius:2px;background:var(--line)}
.st-line.lit{background:linear-gradient(90deg,var(--accent),var(--accent2))}
.wrap{max-width:1160px;margin:0 auto;padding:24px 24px 60px}
.card{background:linear-gradient(160deg,rgba(18,32,52,.98),rgba(9,20,34,.98));border:1px solid var(--line);border-radius:20px;padding:22px 26px;margin-bottom:14px;position:relative;overflow:hidden}
.abar{position:absolute;top:0;left:0;right:0;height:3px;border-radius:20px 20px 0 0}
.kicker{text-transform:uppercase;letter-spacing:.12em;font-size:10px;color:var(--muted);margin-bottom:7px}
.sec-title{font-size:15px;font-weight:800;margin-bottom:3px}
.sec-sub{font-size:12px;color:var(--muted);line-height:1.5}
/* HERO */
.hero-layout{display:grid;grid-template-columns:1fr auto;gap:24px;align-items:start}
@media(max-width:640px){.hero-layout{grid-template-columns:1fr}}
.cand-name{font-size:30px;font-weight:900;letter-spacing:-.02em;background:linear-gradient(90deg,#f0f5fb,var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:4px;line-height:1.1}
.cand-id{font-size:12px;color:var(--muted);margin-bottom:12px}
.pills{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:14px}
.pill{display:inline-flex;align-items:center;padding:5px 12px;border-radius:99px;font-size:12px;font-weight:700;background:#0d1e30;border:1px solid var(--line)}
.pill-g{color:var(--accent);border-color:#1a4a3a}.pill-b{color:var(--accent2);border-color:#1e3a6b}.pill-w{color:var(--warn);border-color:#4a3210}
.narrative{font-size:13px;color:#c0d4e8;line-height:1.65}
.score-aside{display:flex;flex-direction:column;gap:10px;align-items:flex-end}
.score-box{background:#091422;border:1px solid var(--line);border-radius:16px;padding:14px 20px;text-align:center;min-width:108px}
.score-val{font-size:28px;font-weight:900;line-height:1}.sv-a{color:var(--accent)}.sv-w{color:var(--warn)}.sv-m{color:var(--muted)}
.score-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-top:4px}
.adds-box{background:#0c1e10;border:1px solid #1a4a2a;border-radius:12px;padding:8px 14px;text-align:center}
.adds-val{display:block;font-size:20px;font-weight:900;color:var(--accent)}.adds-lbl{font-size:10px;color:var(--muted)}
/* PROBE */
.probe-item{display:flex;align-items:flex-start;gap:12px;background:#180e08;border:1px solid #3a1e08;border-radius:12px;padding:11px 14px;margin-bottom:8px}
.probe-score{background:#2a1208;color:var(--warn);border:1px solid #4a2810;border-radius:99px;padding:3px 10px;font-size:11px;font-weight:700;white-space:nowrap}
/* QUESTIONS */
.q-card{background:#091422;border:1px solid #1a3048;border-radius:14px;padding:14px 18px;margin-bottom:10px;transition:border-color .15s}
.q-card:hover{border-color:#2a4a68}
.q-top{display:flex;gap:10px}
.q-num{width:26px;height:26px;border-radius:50%;background:linear-gradient(135deg,#1e3a56,#2a4a68);color:var(--accent2);font-size:11px;font-weight:800;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px}
.q-body{flex:1}
.q-text{font-size:13px;font-weight:700;line-height:1.55}
.q-sub{font-size:11px;color:var(--muted);margin-top:4px}
.tags{display:flex;gap:5px;flex-wrap:wrap;margin:7px 0 0}
.tag{display:inline-flex;align-items:center;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;text-transform:uppercase}
.tgh{background:#2a0e0e;color:var(--red);border:1px solid #5a1a1a}
.tgm{background:#1a1104;color:var(--warn);border:1px solid #4a3210}
.tgl{background:#0c1e16;color:var(--accent);border:1px solid #1a4a2a}
.tgp{background:#0d1e30;color:var(--muted);border:1px solid var(--line)}
.tgs{background:#0d1830;color:var(--accent2);border:1px solid #1e3a6b}
.ans{width:100%;background:#06101c;border:1px solid #1a3048;border-radius:10px;padding:9px 13px;font-size:12px;font-family:inherit;color:var(--text);resize:vertical;margin-top:10px;transition:border .15s}
.ans:focus{outline:none;border-color:var(--accent2)}
.sres{background:#0b1724;border:1px solid var(--line);border-radius:11px;padding:11px 14px;margin-top:9px;font-size:12px}
.apply-bar{background:#0c1e16;border:1px solid #1a4a2a;border-radius:12px;padding:11px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px}
/* PARAMS */
.param-card{background:#091422;border:1px solid #1a3048;border-left:3px solid var(--warn);border-radius:14px;padding:16px 20px;margin-bottom:10px;transition:border-color .2s,box-shadow .2s}
.param-card:hover{border-color:rgba(255,195,107,.5);box-shadow:0 4px 20px rgba(255,195,107,.07)}
.ph{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px}
.pname{font-size:13px;font-weight:800}
.pval{font-size:24px;font-weight:900;color:var(--warn);line-height:1}
.pmax{font-size:12px;color:var(--muted)}
.phelp{font-size:11px;color:var(--muted);line-height:1.5;margin:5px 0 6px}
.pguide{font-size:11px;color:rgba(255,195,107,.8);background:#1a1104;border-radius:8px;padding:6px 10px;margin-bottom:10px;line-height:1.5}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:4px;border-radius:99px;background:var(--line);outline:none;cursor:pointer}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:linear-gradient(135deg,var(--warn),#ff9f43);border:2px solid #04111d;box-shadow:0 0 10px rgba(255,195,107,.4);cursor:pointer;transition:transform .12s,box-shadow .12s}
input[type=range]::-webkit-slider-thumb:hover{transform:scale(1.3);box-shadow:0 0 16px rgba(255,195,107,.7)}
.fromq{display:inline-flex;align-items:center;gap:4px;background:#0c1e16;color:var(--accent);border:1px solid #1a4a2a;border-radius:99px;padding:2px 8px;font-size:10px;font-weight:700;margin-left:7px;vertical-align:middle}
.total-box{background:#1a1104;border:1px solid #4a3210;border-radius:14px;padding:14px 20px;text-align:center}
.total-val{font-size:36px;font-weight:900;color:var(--warn);line-height:1}
.total-lbl{font-size:11px;color:var(--muted);margin-top:4px}
.notes-ta{width:100%;background:#06101c;border:1px solid var(--line);border-radius:12px;padding:12px 16px;font-size:13px;font-family:inherit;color:var(--text);resize:vertical;min-height:80px;transition:border .15s}
.notes-ta:focus{outline:none;border-color:var(--warn)}
.btn-primary{background:linear-gradient(135deg,var(--warn),#ff9f43);color:#04111d;border:none;border-radius:12px;padding:13px 30px;font-size:14px;font-weight:900;cursor:pointer;display:inline-flex;align-items:center;gap:7px;transition:opacity .15s,transform .1s}
.btn-primary:hover{opacity:.9;transform:translateY(-1px)}
.btn-ghost{background:transparent;color:var(--accent2);border:1px solid #2a4a6a;border-radius:12px;padding:12px 22px;font-size:13px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px;transition:background .15s}
.btn-ghost:hover{background:#0d1e30}
.save-msg{font-size:13px;color:var(--muted)}
.success-panel{display:none;background:linear-gradient(135deg,#0c1e16,#071a12);border:1px solid #1a4a2a;border-radius:16px;padding:22px 26px;margin-top:14px}
.btn-proceed{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#04111d;border:none;border-radius:12px;padding:13px 28px;font-size:14px;font-weight:900;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:7px}
.ref-sum{list-style:none;background:#0b1724;border:1px solid var(--line);border-radius:14px;padding:13px 20px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;font-size:13px;font-weight:700;color:var(--muted)}
.ref-sum:hover{border-color:var(--accent2);color:var(--text)}
details[open] .ref-sum{border-radius:14px 14px 0 0;border-color:var(--accent2);color:var(--text)}
.ref-body{background:#0b1724;border:1px solid var(--line);border-top:none;border-radius:0 0 14px 14px;padding:16px 20px}
.rrow{display:flex;align-items:flex-start;gap:10px;padding:5px 0;border-bottom:1px solid #0e1e2e;font-size:11px}.rrow:last-child{border:none}
.rn{min-width:140px;font-weight:600;padding-top:2px}
.rb{flex:0 0 70px;padding-top:5px}.rbt{height:3px;border-radius:99px;background:#0d1e30;overflow:hidden}.rbf{height:100%;border-radius:99px}
.rs{min-width:44px;font-weight:800}.rj{flex:1;color:var(--muted);font-size:10px;line-height:1.4}
.stag{display:inline-flex;padding:1px 6px;border-radius:99px;font-size:9px;font-weight:700;margin-left:4px;vertical-align:middle}
.sr{background:#1a1104;color:var(--warn);border:1px solid #4a3210}.sp{background:#0d1830;color:var(--accent2);border:1px solid #1e3a6b}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:sp .6s linear infinite;vertical-align:middle}
@keyframes sp{to{transform:rotate(360deg)}}
</style>
</head><body>

<div class="nav"><div class="nav-inner">
  <div class="nav-logo">Resume Intelligence</div>
  <a href="/" class="nav-link">&#8592; Analysis</a>
  <a href="/portal" class="nav-link">Pipeline</a>
  <a href="#" class="nav-link active">&#128222; Phone Screen</a>
  <div class="nav-right"><a href="/panel-screen/""" + r"""{CID_JS}""" + r"""" class="btn-nav">Panel Interview &#8594;</a></div>
</div></div>

<div class="stage-strip"><div class="stage-inner">
  <div class="st done"><div class="st-dot">&#10003;</div><span>Resume Analysis</span></div>
  <div class="st-line lit"></div>
  <div class="st active"><div class="st-dot">2</div><span>Phone Screen</span></div>
  <div class="st-line"></div>
  <div class="st"><div class="st-dot">3</div><span>Panel Interview</span></div>
  <div class="st-line"></div>
  <div class="st"><div class="st-dot">4</div><span>Decision</span></div>
</div></div>

<div class="wrap">

<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--warn),#ff9f43)"></div>
  <div class="hero-layout">
    <div>
      <div class="kicker">Phone Screen &mdash; Stage 2 of 4</div>
      <div class="cand-name" id="cName">Loading&hellip;</div>
      <div class="cand-id" id="cMeta"></div>
      <div class="pills" id="pillRow"></div>
      <div class="narrative" id="narrative"></div>
    </div>
    <div class="score-aside">
      <div class="score-box">
        <div class="score-val sv-a" id="scoreResume">&mdash;</div>
        <div class="score-lbl">Resume Score</div>
      </div>
      <div class="adds-box">
        <span class="adds-val" id="scoreAdds">+?</span>
        <span class="adds-lbl">pts recruiter can add</span>
      </div>
      <div class="score-box" id="recScoreBox" style="display:none">
        <div class="score-val sv-w" id="scoreRec">&mdash;</div>
        <div class="score-lbl">After Phone Screen</div>
      </div>
    </div>
  </div>
</div>

<div class="card" id="probeCard" style="display:none">
  <div class="abar" style="background:linear-gradient(90deg,var(--red),#f87171)"></div>
  <div class="kicker">Probe Focus</div>
  <div class="sec-title">Verify these on the call</div>
  <div class="sec-sub" style="margin-bottom:14px">Resume signals scored below 40% &mdash; ask targeted follow-up questions</div>
  <div id="probeContent"></div>
</div>

<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--warn),#ff9f43)"></div>
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px;flex-wrap:wrap;gap:10px">
    <div>
      <div class="kicker">Step 1 of 2 &mdash; Questions</div>
      <div class="sec-title">Phone Screen Questions</div>
      <div class="sec-sub">Score each answer live, then apply to parameter sliders below.</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <button onclick="generateQs()" id="genBtn" style="background:#0d1e30;color:var(--accent2);border:1px solid var(--line);border-radius:10px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer">&#8635; Regenerate</button>
      <span id="genStatus" style="font-size:12px;color:var(--muted)"></span>
    </div>
  </div>
  <div id="qArea"><div style="text-align:center;padding:32px;color:var(--muted)"><span class="spin"></span> Generating tailored questions&hellip;</div></div>
  <div id="applyBar" class="apply-bar" style="display:none">
    <div style="flex:1"><div style="font-weight:700;font-size:13px;color:var(--accent)">&#10003; Questions scored</div>
      <div style="font-size:12px;color:var(--muted);margin-top:2px" id="applySummary"></div></div>
    <button onclick="applyQScores()" style="background:linear-gradient(135deg,var(--accent),var(--accent2));color:#04111d;border:none;border-radius:10px;padding:8px 18px;font-size:12px;font-weight:800;cursor:pointer">Apply to sliders &#8594;</button>
    <span id="applyMsg" style="font-size:12px;color:var(--muted)"></span>
  </div>
</div>

<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--warn),#ff9f43)"></div>
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap;gap:14px">
    <div>
      <div class="kicker">Step 2 of 2 &mdash; Parameters</div>
      <div class="sec-title">Recruiter Parameter Scores</div>
      <div class="sec-sub">Adjust sliders based on your phone screen. Auto-fills after applying question scores above.</div>
    </div>
    <div class="total-box">
      <div class="total-val" id="liveTotal">0</div>
      <div class="total-lbl" id="totalLbl">/ -- pts added</div>
    </div>
  </div>
  <div id="paramRows"></div>
  <div style="margin-top:22px">
    <div class="kicker" style="margin-bottom:8px">Phone Screen Notes</div>
    <textarea id="recNotes" class="notes-ta" placeholder="Observations from the call &mdash; concerns, highlights, cultural fit, red flags&hellip;"></textarea>
  </div>
  <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:16px">
    <button class="btn-primary" onclick="saveRecruiter()">Save Phone Screen &#8594;</button>
    <span id="saveMsg" class="save-msg"></span>
  </div>
  <div class="success-panel" id="successPanel">
    <div style="font-size:16px;font-weight:800;color:var(--accent);margin-bottom:6px">&#10003; Phone screen saved successfully</div>
    <div style="font-size:13px;color:var(--muted);margin-bottom:18px">Recruiter score: <b style="color:var(--warn);font-size:20px" id="savedScoreVal">&mdash;</b><span style="color:var(--muted)"> / 100</span></div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <a href="/panel-screen/""" + r"""{CID_JS}""" + r"""" class="btn-proceed">Proceed to Panel Interview &#8594;</a>
      <a href="/portal" class="btn-ghost">View in Pipeline</a>
    </div>
  </div>
</div>

<details style="margin-bottom:14px">
  <summary class="ref-sum">&#9656;&nbsp; Resume Scorecard Reference <span style="margin-left:auto;font-size:11px;font-weight:400;color:var(--muted)">Base scores &bull; pending params tagged</span></summary>
  <div class="ref-body"><div id="rsContent"><div style="color:var(--muted);font-size:12px;padding:8px 0">Loading&hellip;</div></div></div>
</details>

</div>

<script>
const CID='""" + r"""{CID_JS}""" + r"""';
const RPARAMS=[
  {key:"mentorship_signal",label:"Mentorship / Code Reviews",max:3,step:1,help:"3 = led in \u22652 roles; 2 = one instance; 1 = implied; 0 = pure IC.",guide:"Ask: Have you mentored juniors or led code reviews? In how many roles?"},
  {key:"international_exposure",label:"International Exposure",max:2,step:1,help:"2 = onsite abroad or global team; 1 = cross-timezone; 0 = local only.",guide:"Ask: Have you worked with international teams or clients?"},
  {key:"stakeholder_management",label:"Stakeholder Management",max:2,step:1,help:"2 = client-facing or C-level; 1 = cross-functional; 0 = none.",guide:"Ask: Who did you work with outside your team? Any exec or client exposure?"},
  {key:"project_explanation",label:"Project Walk-through",max:3,step:1,help:"3 = STAR with measurable impact; 2 = clear, no impact; 1 = vague; 0 = cannot explain.",guide:"Ask: Walk me through your most complex project end to end."},
  {key:"linkedin_activity",label:"LinkedIn Activity",max:1,step:1,help:"1 = active profile matching resume; 0 = absent or stale.",guide:"Check LinkedIn before the call \u2014 verify titles and dates match."},
  {key:"extra_curriculars",label:"Extra-curriculars",max:1.25,step:0.25,help:"1.25 = clear evidence; 0.5 = some signal; 0 = none.",guide:"Ask: Do you volunteer, play sport, or participate in community events?"},
];
const PMAX=RPARAMS.reduce((s,p)=>s+p.max,0);
let _analysis=null,_qs=[],_scored={};

function buildParams(){
  document.getElementById('totalLbl').textContent='/ '+PMAX.toFixed(2).replace(/\.?0+$/,'')+' pts added';
  document.getElementById('paramRows').innerHTML=RPARAMS.map(p=>{
    const steps=Math.round(p.max/p.step);
    return '<div class="param-card" id="pc_'+p.key+'">'
      +'<div class="ph"><div><span class="pname">'+p.label+'</span><span id="fq_'+p.key+'" class="fromq" style="display:none">&#10003; from Qs</span></div>'
      +'<div style="display:flex;align-items:baseline;gap:3px"><span class="pval" id="rv_'+p.key+'">0</span><span class="pmax"> / '+p.max+'</span></div></div>'
      +'<div class="phelp">'+p.help+'</div>'
      +'<div class="pguide">&#9654; '+p.guide+'</div>'
      +'<input type="range" id="sl_'+p.key+'" min="0" max="'+steps+'" step="1" value="0" oninput="syncS(\''+p.key+'\','+p.step+')">'
      +'</div>';
  }).join('');
}
function syncS(key,step){
  const el=document.getElementById('sl_'+key),val=parseFloat(el.value)*step;
  document.getElementById('rv_'+key).textContent=val%1===0?val:val.toFixed(2);
  const t=RPARAMS.reduce((s,p)=>s+parseFloat(document.getElementById('sl_'+p.key)?.value||0)*p.step,0);
  document.getElementById('liveTotal').textContent=t%1===0?t:t.toFixed(2);
}
function setS(key,val){
  const p=RPARAMS.find(p=>p.key===key);if(!p)return;
  const el=document.getElementById('sl_'+key);if(!el)return;
  el.value=Math.round(val/p.step);syncS(key,p.step);
  const fq=document.getElementById('fq_'+key);if(fq)fq.style.display='';
}
function getV(key){const p=RPARAMS.find(p=>p.key===key);return p?parseFloat(document.getElementById('sl_'+p.key)?.value||0)*p.step:0;}

function pLbl(k){return({mentorship_signal:'Mentorship',international_exposure:'Intl Exposure',stakeholder_management:'Stakeholder',project_explanation:'Project Walk-through',linkedin_activity:'LinkedIn',extra_curriculars:'Extra-curr',skill_list_years:'Skill List',skill_depth:'Skill Depth',skill_recency:'Recency',certifications:'Certs',coding_community:'Community',career_progression:'Progression',stability:'Stability',company_tier:'Company Tier',communication_skills:'Communication',domain_skills:'Domain',problem_solving:'Problem Solving'}[k]||k.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()));}

async function generateQs(){
  if(!_analysis)return;
  document.getElementById('qArea').innerHTML='<div style="text-align:center;padding:32px;color:var(--muted)"><span class="spin"></span> Generating tailored questions&hellip;</div>';
  try{
    const r=await fetch('/generateInterviewQuestions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({analysis:_analysis})});
    const d=await r.json();
    _qs=(d.questions||[]).filter(q=>q.stage!=='panel');
    renderQs();document.getElementById('genStatus').textContent=_qs.length+' questions';
  }catch(e){document.getElementById('qArea').innerHTML='<div style="color:var(--red);padding:12px">Error: '+e+'</div>';}
}
function renderQs(){
  if(!_qs.length){document.getElementById('qArea').innerHTML='<div style="color:var(--muted);padding:14px">No questions generated.</div>';return;}
  document.getElementById('qArea').innerHTML=_qs.map((q,i)=>{
    const tc={'high':'tgh','medium':'tgm','low':'tgl'}[q.priority||'low'];
    return '<div class="q-card"><div class="q-top"><span class="q-num">'+(i+1)+'</span>'
      +'<div class="q-body">'
      +'<div class="tags"><span class="tag '+tc+'">'+(q.priority||'low')+'</span>'
      +(q.rubric_param?'<span class="tag tgp">'+pLbl(q.rubric_param)+(q.max_pts?' &middot; '+q.max_pts+' pts':'')+'</span>':'')
      +(q.skill?'<span class="tag tgs">'+q.skill+'</span>':'')+'</div>'
      +'<div class="q-text" style="margin-top:8px">'+q.question+'</div>'
      +(q.what_it_tests?'<div class="q-sub"><b>Tests:</b> '+q.what_it_tests+'</div>':'')
      +'<textarea class="ans" id="ans_'+i+'" rows="2" placeholder="Paste candidate answer\u2026"></textarea>'
      +'<div style="display:flex;align-items:center;gap:8px;margin-top:8px">'
      +'<button onclick="scoreQ('+i+')" id="sb_'+i+'" style="background:#0d1e30;color:var(--accent2);border:1px solid var(--line);border-radius:8px;padding:5px 13px;font-size:11px;font-weight:700;cursor:pointer">&#10003; Score</button>'
      +'<span id="sfb_'+i+'" style="font-size:11px;color:var(--muted)"></span>'
      +'<span id="qsc_'+i+'" style="font-size:16px;font-weight:900"></span>'
      +'</div><div id="sr_'+i+'"></div>'
      +'</div></div></div>';
  }).join('');
}
async function scoreQ(i){
  const q=_qs[i],ans=document.getElementById('ans_'+i).value.trim();
  if(!ans){document.getElementById('sfb_'+i).textContent='Enter answer first.';return;}
  const btn=document.getElementById('sb_'+i);btn.textContent='\u23f3';btn.disabled=true;
  try{
    const r=await fetch('/scoreQuestionAnswer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q.question,theme:q.theme||q.rubric_param||'',answer_transcript:ans,skill:q.skill||'',candidate_context:'',candidate_id:CID})});
    const sc=await r.json();_scored[i]=sc;
    const sv=sc.score_0_to_10||0,col=sv>=7?'var(--accent)':sv>=5?'var(--warn)':'var(--red)';
    document.getElementById('qsc_'+i).innerHTML='<span style="color:'+col+'">'+sv+'/10</span>';
    document.getElementById('sr_'+i).innerHTML='<div class="sres"><b style="color:'+col+'">'+sv+'/10</b>'+(sc.rubric_param?' <span style="color:var(--muted);font-size:10px">&rarr; '+pLbl(sc.rubric_param)+'</span>':'')+'<div style="margin-top:6px;color:var(--muted)"><b style="color:var(--text)">Strong:</b> '+(sc.what_was_strong||'\u2014')+'</div><div style="margin-top:3px;color:var(--muted)"><b style="color:var(--text)">Missing:</b> '+(sc.what_was_missing||'\u2014')+'</div><div style="margin-top:3px;color:var(--muted)"><b style="color:var(--text)">Probe:</b> '+(sc.follow_up_probe||'\u2014')+'</div></div>';
    document.getElementById('sfb_'+i).textContent='Scored.';
    const n=Object.keys(_scored).length;
    document.getElementById('applyBar').style.display='';
    document.getElementById('applySummary').textContent=n+' of '+_qs.length+' scored';
  }catch(e){document.getElementById('sfb_'+i).textContent='Error: '+e;}
  btn.textContent='Re-score';btn.disabled=false;
}
async function applyQScores(){
  const scored=Object.values(_scored);if(!scored.length)return;
  document.getElementById('applyMsg').textContent='Applying\u2026';
  try{
    const r=await fetch('/applyCallScores',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,question_scores:scored,stage:'recruiter',recruiter_notes:''})});
    const d=await r.json();
    for(const[k,v]of Object.entries(d.rubric_param_overrides||{}))setS(k,v);
    document.getElementById('applyMsg').textContent='\u2713 Sliders updated from '+scored.length+' answers';
  }catch(e){document.getElementById('applyMsg').textContent='Error: '+e;}
}
async function saveRecruiter(){
  const ov={};for(const p of RPARAMS){const v=getV(p.key);if(v>0)ov[p.key]=v;}
  if(!Object.keys(ov).length){document.getElementById('saveMsg').textContent='Move at least one slider.';return;}
  document.getElementById('saveMsg').textContent='Saving\u2026';
  try{
    const r=await fetch('/updateStageScore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage:'recruiter',stage_overrides:ov,recruiter_notes:document.getElementById('recNotes').value})});
    const d=await r.json();
    if(!r.ok){document.getElementById('saveMsg').textContent='Error: '+(d.detail||r.statusText);return;}
    const s100=d.stage_score_100??d.new_total;
    document.getElementById('saveMsg').textContent='';
    document.getElementById('scoreRec').textContent=s100;
    document.getElementById('recScoreBox').style.display='';
    document.getElementById('savedScoreVal').textContent=s100;
    document.getElementById('successPanel').style.display='';
    try{await fetch('/api/pipeline/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage:'Telephonic'})});}catch(e){}
  }catch(e){document.getElementById('saveMsg').textContent='Error: '+e;}
}
function flatEdu(e){if(!e)return{};const o={};for(const[k,v]of Object.entries(e)){if(k!=='bonus')o[k]=v;}for(const[k,v]of Object.entries(e.bonus||{}))o[k]=v;return o;}
function renderSC(rs){
  if(!rs){document.getElementById('rsContent').innerHTML='<div style="color:var(--muted);font-size:12px">No scorecard.</div>';return;}
  const bd=rs.breakdown||{},ss=rs.stage_scores||{};
  const rp=new Set(ss.recruiter_pending_params||[]),pp=new Set(ss.panel_pending_params||[]);
  const secs=[{l:'Experience',d:bd.experience||{},c:'#6ae3c1'},{l:'Skills',d:bd.skills||{},c:'#8ab4ff'},{l:'Education',d:flatEdu(bd.education||{}),c:'#ffc36b'}];
  let h='';
  for(const s of secs){
    h+='<div style="margin-bottom:14px"><div style="font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:'+s.c+';margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid var(--line)">'+s.l+'</div>';
    for(const[k,v]of Object.entries(s.d)){
      if(!v||typeof v!=='object'||!('score' in v)||v.type==='info')continue;
      const pct=v.max>0?Math.round(v.score/v.max*100):0,fc=pct>=75?'#34d399':pct>=45?'#ffc36b':'#f87171';
      const sb=rp.has(k)?'<span class="stag sr">rec</span>':pp.has(k)?'<span class="stag sp">panel</span>':'';
      h+='<div class="rrow"><div class="rn">'+pLbl(k)+sb+'</div><div class="rb"><div class="rbt"><div class="rbf" style="width:'+pct+'%;background:'+fc+'"></div></div></div><div class="rs" style="color:'+fc+'">'+v.score+'/'+v.max+'</div><div class="rj">'+(v.llm_justification||v.reason||'')+'</div></div>';
    }
    h+='</div>';
  }
  document.getElementById('rsContent').innerHTML=h;
}
function renderProbe(rs){
  const bd=rs?.breakdown||{},ss=rs?.stage_scores||{};
  const rp=new Set(ss.recruiter_pending_params||[]),pp=new Set(ss.panel_pending_params||[]);
  const probes=[];
  for(const[sn,sd]of Object.entries(bd)){
    const flat=sn==='education'?flatEdu(sd):(sd||{});
    for(const[k,v]of Object.entries(flat)){
      if(!v||typeof v!=='object'||!('score' in v)||v.type==='info')continue;
      if(rp.has(k)||pp.has(k))continue;
      if(v.max>=2&&v.score/v.max<0.4)probes.push({key:k,score:v.score,max:v.max,just:v.llm_justification||''});
    }
  }
  if(!probes.length){document.getElementById('probeCard').style.display='none';return;}
  document.getElementById('probeCard').style.display='';
  document.getElementById('probeContent').innerHTML=probes.map(p=>'<div class="probe-item"><div style="flex:1"><b style="font-size:13px">'+pLbl(p.key)+'</b>'+(p.just?'<div style="font-size:11px;color:var(--muted);margin-top:2px">'+p.just+'</div>':'')+'</div><span class="probe-score">'+p.score+' / '+p.max+'</span></div>').join('');
}
async function loadAll(){
  try{
    const r=await fetch('/candidateScore/'+encodeURIComponent(CID));
    if(r.ok){
      const d=await r.json();
      document.getElementById('cName').textContent=d.candidate_name||CID;
      document.getElementById('cMeta').textContent=CID;
      const stages=d.stages||[],ss=(stages[stages.length-1]||{}).stage_scores||{};
      if(ss.resume_score_100)document.getElementById('scoreResume').textContent=ss.resume_score_100;
      if(ss.recruiter_can_add)document.getElementById('scoreAdds').textContent='+'+ss.recruiter_can_add;
      if(ss.recruiter_score_100!=null){document.getElementById('scoreRec').textContent=ss.recruiter_score_100;document.getElementById('recScoreBox').style.display='';}
    }
  }catch(e){}
  try{
    const r=await fetch('/api/candidateAnalysis/'+encodeURIComponent(CID));
    if(r.ok){
      _analysis=await r.json();
      const ov=_analysis.candidate_overview||{},rf=(_analysis.semantic_analysis?.role_family_scores||[])[0],dna=_analysis.dna_classification?.primary||'';
      const pills=[];
      if(rf)pills.push('<span class="pill pill-g">'+rf.role_family.replace(/_/g,' ')+'</span>');
      if(dna)pills.push('<span class="pill pill-w">'+dna+'</span>');
      const exp=(_analysis.experience?.items||[]).length;
      if(exp)pills.push('<span class="pill pill-b">'+exp+' roles</span>');
      document.getElementById('pillRow').innerHTML=pills.join('');
      document.getElementById('narrative').textContent=_analysis.recruiter_narrative||ov.profile_summary||'';
      const rs=_analysis.rubric_scorecard||_analysis.rubric_score||null;
      if(rs){
        renderProbe(rs);renderSC(rs);
        const ss=rs.stage_scores||{};
        if(ss.resume_score_100)document.getElementById('scoreResume').textContent=ss.resume_score_100;
        if(ss.recruiter_can_add)document.getElementById('scoreAdds').textContent='+'+ss.recruiter_can_add;
      }
      generateQs();
    }
  }catch(e){}
  buildParams();
  try{await fetch('/api/pipeline/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID})});}catch(e){}
}
loadAll();
</script>
</body></html>"""
    return _html'''

PANEL_HTML = r'''    _html = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><title>Panel Interview &mdash; Resume Intelligence</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#07111b;--card:#0f1b2d;--card2:#0b1724;--line:#1e3a56;--text:#f0f5fb;--muted:#7a97b4;--accent:#6ae3c1;--accent2:#8ab4ff;--warn:#ffc36b;--red:#f87171;--purple:#c084fc}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Aptos,'Segoe UI',system-ui,sans-serif;background:radial-gradient(circle at top right,#0a1e42 0%,#07111b 55%,#040c16 100%);color:var(--text);min-height:100vh}
a{color:var(--accent2);text-decoration:none}
.nav{background:rgba(7,17,27,.95);border-bottom:1px solid var(--line);backdrop-filter:blur(10px);position:sticky;top:0;z-index:100}
.nav-inner{max-width:1160px;margin:0 auto;padding:0 24px;display:flex;align-items:center;height:52px;gap:2px}
.nav-logo{font-weight:900;font-size:14px;background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-right:20px;white-space:nowrap}
.nav-link{padding:0 13px;height:52px;display:flex;align-items:center;font-size:13px;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;white-space:nowrap;transition:color .15s}
.nav-link:hover{color:var(--text)}.nav-link.active{color:var(--accent2);border-bottom-color:var(--accent2)}
.nav-right{margin-left:auto}
.btn-nav{background:#0d1e30;color:var(--muted);border:1px solid var(--line);border-radius:10px;padding:8px 16px;font-size:12px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px;transition:border-color .15s}
.btn-nav:hover{border-color:var(--accent2);color:var(--accent2)}
.stage-strip{background:rgba(9,20,34,.8);border-bottom:1px solid var(--line)}
.stage-inner{max-width:1160px;margin:0 auto;padding:10px 24px;display:flex;align-items:center}
.st{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:700;color:var(--muted)}
.st-dot{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;flex-shrink:0;border:2px solid var(--line)}
.st.done .st-dot{background:linear-gradient(135deg,var(--accent),#3dd6a3);color:#04111d;border-color:transparent}
.st.done{color:var(--accent)}
.st.active .st-dot{background:linear-gradient(135deg,var(--accent2),#a78bfa);color:#04111d;border-color:transparent;box-shadow:0 0 14px rgba(138,180,255,.4)}
.st.active{color:var(--accent2)}
.st-line{flex:1;height:2px;margin:0 8px;border-radius:2px;background:var(--line)}
.st-line.lit{background:linear-gradient(90deg,var(--accent),var(--accent2))}
.wrap{max-width:1160px;margin:0 auto;padding:24px 24px 60px}
.card{background:linear-gradient(160deg,rgba(18,32,52,.98),rgba(9,20,34,.98));border:1px solid var(--line);border-radius:20px;padding:22px 26px;margin-bottom:14px;position:relative;overflow:hidden}
.abar{position:absolute;top:0;left:0;right:0;height:3px;border-radius:20px 20px 0 0}
.kicker{text-transform:uppercase;letter-spacing:.12em;font-size:10px;color:var(--muted);margin-bottom:7px}
.sec-title{font-size:15px;font-weight:800;margin-bottom:3px}
.sec-sub{font-size:12px;color:var(--muted);line-height:1.5}
.hero-layout{display:grid;grid-template-columns:1fr auto;gap:24px;align-items:start}
@media(max-width:640px){.hero-layout{grid-template-columns:1fr}}
.cand-name{font-size:30px;font-weight:900;letter-spacing:-.02em;background:linear-gradient(90deg,#f0f5fb,var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:4px;line-height:1.1}
.cand-id{font-size:12px;color:var(--muted);margin-bottom:12px}
.pills{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:14px}
.pill{display:inline-flex;align-items:center;padding:5px 12px;border-radius:99px;font-size:12px;font-weight:700;background:#0d1e30;border:1px solid var(--line)}
.pill-g{color:var(--accent);border-color:#1a4a3a}.pill-b{color:var(--accent2);border-color:#1e3a6b}.pill-w{color:var(--warn);border-color:#4a3210}
.score-aside{display:flex;flex-direction:column;gap:8px;align-items:flex-end}
.score-box{background:#091422;border:1px solid var(--line);border-radius:14px;padding:12px 18px;text-align:center;min-width:100px}
.sv{font-size:24px;font-weight:900;line-height:1}.sv-a{color:var(--accent)}.sv-w{color:var(--warn)}.sv-b{color:var(--accent2)}.sv-m{color:var(--muted)}
.s-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-top:3px}
.adds-box{background:#0d1830;border:1px solid #1e3a6b;border-radius:12px;padding:8px 14px;text-align:center}
.adds-v{display:block;font-size:18px;font-weight:900;color:var(--accent2)}.adds-l{font-size:10px;color:var(--muted)}
.rec-notes-box{background:#1a1104;border:1px solid #4a3210;border-radius:14px;padding:14px 18px;margin-bottom:12px}
.skills-probe{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid #0e1e2e;font-size:12px}.skills-probe:last-child{border:none}
.q-card{background:#091422;border:1px solid #1a3048;border-radius:14px;padding:14px 18px;margin-bottom:10px;transition:border-color .15s}
.q-card:hover{border-color:#2a5088}
.q-top{display:flex;gap:10px}
.q-num{width:26px;height:26px;border-radius:50%;background:linear-gradient(135deg,#1e3a6b,#2a4a88);color:var(--accent2);font-size:11px;font-weight:800;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px}
.q-body{flex:1}
.q-text{font-size:13px;font-weight:700;line-height:1.55}
.q-sub{font-size:11px;color:var(--muted);margin-top:4px}
.tags{display:flex;gap:5px;flex-wrap:wrap;margin:7px 0 0}
.tag{display:inline-flex;align-items:center;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;text-transform:uppercase}
.tgh{background:#2a0e0e;color:var(--red);border:1px solid #5a1a1a}
.tgm{background:#0d1830;color:var(--accent2);border:1px solid #1e3a6b}
.tgl{background:#0c1e16;color:var(--accent);border:1px solid #1a4a2a}
.tgp{background:#0d1e30;color:var(--muted);border:1px solid var(--line)}
.tgs{background:#1a0e2a;color:var(--purple);border:1px solid #3a1a5a}
.ans{width:100%;background:#06101c;border:1px solid #1a3048;border-radius:10px;padding:9px 13px;font-size:12px;font-family:inherit;color:var(--text);resize:vertical;margin-top:10px;transition:border .15s}
.ans:focus{outline:none;border-color:var(--accent2)}
.sres{background:#0b1724;border:1px solid var(--line);border-radius:11px;padding:11px 14px;margin-top:9px;font-size:12px}
.apply-bar{background:#0d1830;border:1px solid #1e3a6b;border-radius:12px;padding:11px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px}
.param-card{background:#091422;border:1px solid #1a3048;border-left:3px solid var(--accent2);border-radius:14px;padding:16px 20px;margin-bottom:10px;transition:border-color .2s,box-shadow .2s}
.param-card:hover{border-color:rgba(138,180,255,.5);box-shadow:0 4px 20px rgba(138,180,255,.07)}
.ph{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px}
.pname{font-size:13px;font-weight:800}
.pval{font-size:24px;font-weight:900;color:var(--accent2);line-height:1}.pmax{font-size:12px;color:var(--muted)}
.phelp{font-size:11px;color:var(--muted);line-height:1.5;margin:5px 0 6px}
.pguide{font-size:11px;color:rgba(138,180,255,.8);background:#0d1830;border-radius:8px;padding:6px 10px;margin-bottom:10px;line-height:1.5}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:4px;border-radius:99px;background:var(--line);outline:none;cursor:pointer}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:linear-gradient(135deg,var(--accent2),#a78bfa);border:2px solid #04111d;box-shadow:0 0 10px rgba(138,180,255,.4);cursor:pointer;transition:transform .12s}
input[type=range]::-webkit-slider-thumb:hover{transform:scale(1.3);box-shadow:0 0 16px rgba(138,180,255,.7)}
.fromq{display:inline-flex;align-items:center;gap:4px;background:#0c1e16;color:var(--accent);border:1px solid #1a4a2a;border-radius:99px;padding:2px 8px;font-size:10px;font-weight:700;margin-left:7px;vertical-align:middle}
.total-box{background:#0d1830;border:1px solid #1e3a6b;border-radius:14px;padding:14px 20px;text-align:center}
.total-val{font-size:36px;font-weight:900;color:var(--accent2);line-height:1}
.total-lbl{font-size:11px;color:var(--muted);margin-top:4px}
.notes-ta{width:100%;background:#06101c;border:1px solid var(--line);border-radius:12px;padding:12px 16px;font-size:13px;font-family:inherit;color:var(--text);resize:vertical;min-height:80px;transition:border .15s}
.notes-ta:focus{outline:none;border-color:var(--accent2)}
.btn-save{background:linear-gradient(135deg,var(--accent2),#a78bfa);color:#04111d;border:none;border-radius:12px;padding:13px 30px;font-size:14px;font-weight:900;cursor:pointer;display:inline-flex;align-items:center;gap:7px;transition:opacity .15s,transform .1s}
.btn-save:hover{opacity:.9;transform:translateY(-1px)}
.btn-success{background:linear-gradient(135deg,var(--accent),#3dd6a3);color:#04111d;border:none;border-radius:12px;padding:12px 24px;font-size:13px;font-weight:900;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.btn-ghost{background:transparent;color:var(--muted);border:1px solid var(--line);border-radius:12px;padding:11px 20px;font-size:13px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px;transition:border-color .15s,color .15s}
.btn-ghost:hover{border-color:var(--accent2);color:var(--accent2)}
.save-msg{font-size:13px;color:var(--muted)}
.success-panel{display:none;background:linear-gradient(135deg,#0c1e16,#071a12);border:1px solid #1a4a2a;border-radius:16px;padding:22px 26px;margin-top:14px}
.final-grid{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
.ftile{background:#091422;border:1px solid var(--line);border-radius:14px;padding:14px 20px;text-align:center;min-width:100px}
.fv{font-size:28px;font-weight:900;line-height:1}.fl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-top:4px}
.ref-sum{list-style:none;background:#0b1724;border:1px solid var(--line);border-radius:14px;padding:13px 20px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;font-size:13px;font-weight:700;color:var(--muted)}
.ref-sum:hover{border-color:var(--accent2);color:var(--text)}
details[open] .ref-sum{border-radius:14px 14px 0 0;border-color:var(--accent2);color:var(--text)}
.ref-body{background:#0b1724;border:1px solid var(--line);border-top:none;border-radius:0 0 14px 14px;padding:16px 20px}
.rrow{display:flex;align-items:flex-start;gap:10px;padding:5px 0;border-bottom:1px solid #0e1e2e;font-size:11px}.rrow:last-child{border:none}
.rn{min-width:140px;font-weight:600;padding-top:2px}
.rb{flex:0 0 70px;padding-top:5px}.rbt{height:3px;border-radius:99px;background:#0d1e30;overflow:hidden}.rbf{height:100%;border-radius:99px}
.rs{min-width:44px;font-weight:800}.rj{flex:1;color:var(--muted);font-size:10px;line-height:1.4}
.stag{display:inline-flex;padding:1px 6px;border-radius:99px;font-size:9px;font-weight:700;margin-left:4px;vertical-align:middle}
.sr{background:#1a1104;color:var(--warn);border:1px solid #4a3210}.sp{background:#0d1830;color:var(--accent2);border:1px solid #1e3a6b}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--accent2);border-radius:50%;animation:sp .6s linear infinite;vertical-align:middle}
@keyframes sp{to{transform:rotate(360deg)}}
</style>
</head><body>

<div class="nav"><div class="nav-inner">
  <div class="nav-logo">Resume Intelligence</div>
  <a href="/" class="nav-link">&#8592; Analysis</a>
  <a href="/recruiter-screen/""" + r"""{CID_JS}""" + r"""" class="nav-link">&#8592; Phone Screen</a>
  <a href="#" class="nav-link active">&#128203; Panel Interview</a>
  <div class="nav-right"><a href="/portal" class="btn-nav">Pipeline &#8594;</a></div>
</div></div>

<div class="stage-strip"><div class="stage-inner">
  <div class="st done"><div class="st-dot">&#10003;</div><span>Resume Analysis</span></div>
  <div class="st-line lit"></div>
  <div class="st done"><div class="st-dot">&#10003;</div><span>Phone Screen</span></div>
  <div class="st-line lit"></div>
  <div class="st active"><div class="st-dot">3</div><span>Panel Interview</span></div>
  <div class="st-line"></div>
  <div class="st"><div class="st-dot">4</div><span>Decision</span></div>
</div></div>

<div class="wrap">

<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--accent2),#a78bfa)"></div>
  <div class="hero-layout">
    <div>
      <div class="kicker">Panel Interview &mdash; Stage 3 of 4</div>
      <div class="cand-name" id="cName">Loading&hellip;</div>
      <div class="cand-id" id="cMeta"></div>
      <div class="pills" id="pillRow"></div>
      <div id="recNotesWrap" style="display:none" class="rec-notes-box">
        <div style="font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:var(--warn);margin-bottom:6px">&#9888; Phone Screen Notes (from recruiter)</div>
        <div style="font-size:13px;color:#c0d4e8;line-height:1.6" id="recNotesText"></div>
      </div>
      <div style="font-size:13px;color:#c0d4e8;line-height:1.65" id="narrative"></div>
    </div>
    <div class="score-aside">
      <div class="score-box">
        <div class="sv sv-a" id="scoreResume">&mdash;</div><div class="s-lbl">Resume</div>
      </div>
      <div class="score-box">
        <div class="sv sv-w" id="scoreRec">pending</div><div class="s-lbl">Phone Screen</div>
      </div>
      <div class="adds-box">
        <span class="adds-v" id="panelAdds">+?</span>
        <span class="adds-l">pts panel can add</span>
      </div>
      <div class="score-box" id="panScoreBox" style="display:none">
        <div class="sv sv-b" id="scorePan">&mdash;</div><div class="s-lbl">Final Score</div>
      </div>
    </div>
  </div>
</div>

<div class="card" id="skillsCard" style="display:none">
  <div class="abar" style="background:linear-gradient(90deg,var(--accent2),#a78bfa)"></div>
  <div class="kicker">Skills to Probe</div>
  <div class="sec-title">Verify depth during the interview</div>
  <div class="sec-sub" style="margin-bottom:14px">These skills show weak or limited evidence on the resume</div>
  <div id="skillsProbe"></div>
</div>

<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--accent2),#a78bfa)"></div>
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px;flex-wrap:wrap;gap:10px">
    <div>
      <div class="kicker">Step 1 of 2 &mdash; Questions</div>
      <div class="sec-title">Technical Interview Questions</div>
      <div class="sec-sub">Deep technical questions targeting domain depth, problem-solving, and project ownership.</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <button onclick="generateQs()" style="background:#0d1e30;color:var(--accent2);border:1px solid var(--line);border-radius:10px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer">&#8635; Regenerate</button>
      <span id="genStatus" style="font-size:12px;color:var(--muted)"></span>
    </div>
  </div>
  <div id="qArea"><div style="text-align:center;padding:32px;color:var(--muted)"><span class="spin"></span> Generating technical questions&hellip;</div></div>
  <div id="applyBar" class="apply-bar" style="display:none">
    <div style="flex:1"><div style="font-weight:700;font-size:13px;color:var(--accent)">&#10003; Questions scored</div>
      <div style="font-size:12px;color:var(--muted);margin-top:2px" id="applySummary"></div></div>
    <button onclick="applyQScores()" style="background:linear-gradient(135deg,var(--accent2),#a78bfa);color:#04111d;border:none;border-radius:10px;padding:8px 18px;font-size:12px;font-weight:800;cursor:pointer">Apply to sliders &#8594;</button>
    <span id="applyMsg" style="font-size:12px;color:var(--muted)"></span>
  </div>
</div>

<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--accent2),#a78bfa)"></div>
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap;gap:14px">
    <div>
      <div class="kicker">Step 2 of 2 &mdash; Parameters</div>
      <div class="sec-title">Panel Parameter Scores</div>
      <div class="sec-sub">Rate each dimension after the interview. These scores are added on top of resume + phone screen scores.</div>
    </div>
    <div class="total-box">
      <div class="total-val" id="liveTotal">0</div>
      <div class="total-lbl">/ 16 pts added</div>
    </div>
  </div>
  <div id="paramRows"></div>
  <div style="margin-top:22px">
    <div class="kicker" style="margin-bottom:8px">Panel Notes</div>
    <textarea id="panNotes" class="notes-ta" placeholder="Technical depth, ownership, communication quality, concerns, recommendation&hellip;"></textarea>
  </div>
  <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:16px">
    <button class="btn-save" onclick="savePanel()">Save Panel Score &#8594;</button>
    <span id="saveMsg" class="save-msg"></span>
  </div>
  <div class="success-panel" id="successPanel">
    <div style="font-size:16px;font-weight:800;color:var(--accent);margin-bottom:6px">&#10003; Panel score saved</div>
    <div style="font-size:13px;color:var(--muted);margin-bottom:20px">Final score: <b style="color:var(--accent2);font-size:22px" id="savedScoreVal">&mdash;</b><span style="color:var(--muted)"> / 100</span></div>
    <div class="final-grid" id="finalGrid"></div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px">
      <button onclick="decide('Hired')" class="btn-success">&#10003; Mark as Hired</button>
      <button onclick="decide('Rejected')" style="background:linear-gradient(135deg,var(--red),#f87171);color:#04111d;border:none;border-radius:12px;padding:12px 24px;font-size:13px;font-weight:900;cursor:pointer;display:inline-flex;align-items:center;gap:6px">&#10005; Mark as Rejected</button>
      <a href="/portal" class="btn-ghost">View in Pipeline &rarr;</a>
      <a href="/recruiter/""" + r"""{CID_JS}""" + r"""" class="btn-ghost">Full Portal &#8599;</a>
    </div>
    <div id="decideMsg" style="font-size:14px;font-weight:700"></div>
  </div>
</div>

<details style="margin-bottom:14px">
  <summary class="ref-sum">&#9656;&nbsp; Resume Scorecard Reference <span style="margin-left:auto;font-size:11px;font-weight:400;color:var(--muted)">Base scores &bull; panel params tagged</span></summary>
  <div class="ref-body"><div id="rsContent"><div style="color:var(--muted);font-size:12px;padding:8px 0">Loading&hellip;</div></div></div>
</details>

</div>

<script>
const CID='""" + r"""{CID_JS}""" + r"""';
const PPARAMS=[
  {key:"communication_skills",label:"Communication Skills",max:5,step:0.5,help:"5 = exceptionally clear and structured; 4 = good; 3 = average; 2 = struggles; 1 = poor.",guide:"Assess verbal clarity, answer structure, and ability to explain technical concepts."},
  {key:"domain_skills",label:"Domain / Technical Depth",max:5,step:0.5,help:"5 = expert; 4 = strong; 3 = competent; 2 = foundational; 1 = weak.",guide:"Probe depth in primary domain: ML architecture, system design, data modelling, etc."},
  {key:"problem_solving",label:"Problem Solving",max:3,step:0.5,help:"3 = creative + systematic; 2 = methodical; 1 = ad hoc; 0 = stuck.",guide:"Give a real scenario and observe approach before asking for code."},
  {key:"project_explanation",label:"Project Deep-Dive",max:3,step:1,help:"3 = structured narrative with clear ownership and measurable impact; 2 = good; 1 = surface; 0 = cannot explain.",guide:"Ask: Walk me through the hardest technical decision you made on this project."},
];
let _analysis=null,_qs=[],_scored={};

function buildParams(){
  document.getElementById('paramRows').innerHTML=PPARAMS.map(p=>{
    const steps=Math.round(p.max/p.step);
    return '<div class="param-card" id="pc_'+p.key+'">'
      +'<div class="ph"><div><span class="pname">'+p.label+'</span><span id="fq_'+p.key+'" class="fromq" style="display:none">&#10003; from Qs</span></div>'
      +'<div style="display:flex;align-items:baseline;gap:3px"><span class="pval" id="rv_'+p.key+'">0</span><span class="pmax"> / '+p.max+'</span></div></div>'
      +'<div class="phelp">'+p.help+'</div>'
      +'<div class="pguide">&#9654; '+p.guide+'</div>'
      +'<input type="range" id="sl_'+p.key+'" min="0" max="'+steps+'" step="1" value="0" oninput="syncS(\''+p.key+'\','+p.step+')">'
      +'</div>';
  }).join('');
}
function syncS(key,step){
  const el=document.getElementById('sl_'+key),val=parseFloat(el.value)*step;
  document.getElementById('rv_'+key).textContent=val%1===0?val:val.toFixed(1);
  const t=PPARAMS.reduce((s,p)=>s+parseFloat(document.getElementById('sl_'+p.key)?.value||0)*p.step,0);
  document.getElementById('liveTotal').textContent=t%1===0?t:t.toFixed(1);
}
function setS(key,val){
  const p=PPARAMS.find(p=>p.key===key);if(!p)return;
  const el=document.getElementById('sl_'+key);if(!el)return;
  el.value=Math.round(val/p.step);syncS(key,p.step);
  const fq=document.getElementById('fq_'+key);if(fq)fq.style.display='';
}
function getV(key){const p=PPARAMS.find(p=>p.key===key);return p?parseFloat(document.getElementById('sl_'+p.key)?.value||0)*p.step:0;}

function pLbl(k){return({communication_skills:'Communication',domain_skills:'Domain Depth',problem_solving:'Problem Solving',project_explanation:'Project Deep-Dive',skill_depth:'Skill Depth',skill_recency:'Recency',mentorship_signal:'Mentorship',international_exposure:'Intl Exposure',stakeholder_management:'Stakeholder',career_progression:'Progression',stability:'Stability',company_tier:'Company Tier',certifications:'Certs'}[k]||k.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()));}

async function generateQs(){
  if(!_analysis)return;
  document.getElementById('qArea').innerHTML='<div style="text-align:center;padding:32px;color:var(--muted)"><span class="spin"></span> Generating technical questions&hellip;</div>';
  try{
    const r=await fetch('/generateInterviewQuestions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({analysis:_analysis})});
    const d=await r.json();
    const all=d.questions||[];
    _qs=all.filter(q=>q.stage==='panel');
    if(!_qs.length)_qs=all.filter(q=>['domain','technical','system','problem','project'].some(t=>(q.theme||'').toLowerCase().includes(t)||(q.rubric_param||'').toLowerCase().includes(t)));
    if(!_qs.length)_qs=all.slice(Math.max(0,all.length-6));
    renderQs();document.getElementById('genStatus').textContent=_qs.length+' questions';
  }catch(e){document.getElementById('qArea').innerHTML='<div style="color:var(--red);padding:12px">Error: '+e+'</div>';}
}
function renderQs(){
  if(!_qs.length){document.getElementById('qArea').innerHTML='<div style="color:var(--muted);padding:14px">No panel questions generated.</div>';return;}
  document.getElementById('qArea').innerHTML=_qs.map((q,i)=>{
    const tc={'high':'tgh','medium':'tgm','low':'tgl'}[q.priority||'medium'];
    return '<div class="q-card"><div class="q-top"><span class="q-num">'+(i+1)+'</span>'
      +'<div class="q-body">'
      +'<div class="tags"><span class="tag '+tc+'">'+(q.priority||'medium')+'</span>'
      +(q.rubric_param?'<span class="tag tgp">'+pLbl(q.rubric_param)+(q.max_pts?' &middot; '+q.max_pts+' pts':'')+'</span>':'')
      +(q.skill?'<span class="tag tgs">'+q.skill+'</span>':'')+'</div>'
      +'<div class="q-text" style="margin-top:8px">'+q.question+'</div>'
      +(q.what_it_tests?'<div class="q-sub"><b>Tests:</b> '+q.what_it_tests+'</div>':'')
      +'<textarea class="ans" id="ans_'+i+'" rows="3" placeholder="Type or paste the candidate\u2019s response\u2026"></textarea>'
      +'<div style="display:flex;align-items:center;gap:8px;margin-top:8px">'
      +'<button onclick="scoreQ('+i+')" id="sb_'+i+'" style="background:#0d1e30;color:var(--accent2);border:1px solid var(--line);border-radius:8px;padding:5px 13px;font-size:11px;font-weight:700;cursor:pointer">&#10003; Score</button>'
      +'<span id="sfb_'+i+'" style="font-size:11px;color:var(--muted)"></span>'
      +'<span id="qsc_'+i+'" style="font-size:16px;font-weight:900"></span>'
      +'</div><div id="sr_'+i+'"></div>'
      +'</div></div></div>';
  }).join('');
}
async function scoreQ(i){
  const q=_qs[i],ans=document.getElementById('ans_'+i).value.trim();
  if(!ans){document.getElementById('sfb_'+i).textContent='Enter answer first.';return;}
  const btn=document.getElementById('sb_'+i);btn.textContent='\u23f3';btn.disabled=true;
  try{
    const r=await fetch('/scoreQuestionAnswer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q.question,theme:q.theme||q.rubric_param||'',answer_transcript:ans,skill:q.skill||'',candidate_context:'',candidate_id:CID})});
    const sc=await r.json();_scored[i]=sc;
    const sv=sc.score_0_to_10||0,col=sv>=7?'var(--accent)':sv>=5?'var(--warn)':'var(--red)';
    document.getElementById('qsc_'+i).innerHTML='<span style="color:'+col+'">'+sv+'/10</span>';
    document.getElementById('sr_'+i).innerHTML='<div class="sres"><b style="color:'+col+'">'+sv+'/10</b>'+(sc.rubric_param?' <span style="color:var(--muted);font-size:10px">&rarr; '+pLbl(sc.rubric_param)+'</span>':'')+'<div style="margin-top:6px;color:var(--muted)"><b style="color:var(--text)">Strong:</b> '+(sc.what_was_strong||'\u2014')+'</div><div style="margin-top:3px;color:var(--muted)"><b style="color:var(--text)">Missing:</b> '+(sc.what_was_missing||'\u2014')+'</div><div style="margin-top:3px;color:var(--muted)"><b style="color:var(--text)">Probe:</b> '+(sc.follow_up_probe||'\u2014')+'</div></div>';
    document.getElementById('sfb_'+i).textContent='Scored.';
    const n=Object.keys(_scored).length;
    document.getElementById('applyBar').style.display='';
    document.getElementById('applySummary').textContent=n+' of '+_qs.length+' scored';
  }catch(e){document.getElementById('sfb_'+i).textContent='Error: '+e;}
  btn.textContent='Re-score';btn.disabled=false;
}
async function applyQScores(){
  const scored=Object.values(_scored);if(!scored.length)return;
  document.getElementById('applyMsg').textContent='Applying\u2026';
  try{
    const r=await fetch('/applyCallScores',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,question_scores:scored,stage:'panel',recruiter_notes:''})});
    const d=await r.json();
    for(const[k,v]of Object.entries(d.rubric_param_overrides||{}))setS(k,v);
    document.getElementById('applyMsg').textContent='\u2713 Sliders updated from '+scored.length+' answers';
  }catch(e){document.getElementById('applyMsg').textContent='Error: '+e;}
}
async function savePanel(){
  const ov={};for(const p of PPARAMS){const v=getV(p.key);if(v>0)ov[p.key]=v;}
  if(!Object.keys(ov).length){document.getElementById('saveMsg').textContent='Move at least one slider.';return;}
  document.getElementById('saveMsg').textContent='Saving\u2026';
  try{
    const r=await fetch('/updateStageScore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage:'panel',stage_overrides:ov,recruiter_notes:document.getElementById('panNotes').value})});
    const d=await r.json();
    if(!r.ok){document.getElementById('saveMsg').textContent='Error: '+(d.detail||r.statusText);return;}
    const s100=d.stage_score_100??d.new_total;
    document.getElementById('saveMsg').textContent='';
    document.getElementById('scorePan').textContent=s100;
    document.getElementById('panScoreBox').style.display='';
    document.getElementById('savedScoreVal').textContent=s100;
    document.getElementById('successPanel').style.display='';
    const ss=d.stage_scores||{};
    document.getElementById('finalGrid').innerHTML=
      '<div class="ftile"><div class="fv" style="color:var(--accent)">'+(ss.resume_score_100??'\u2014')+'</div><div class="fl">Resume</div></div>'+
      '<div class="ftile"><div class="fv" style="color:var(--warn)">'+(ss.recruiter_score_100??'pending')+'</div><div class="fl">Phone Screen</div></div>'+
      '<div class="ftile"><div class="fv" style="color:var(--accent2)">'+(ss.panel_score_100??s100)+'</div><div class="fl">Panel / Final</div></div>';
    try{await fetch('/api/pipeline/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage:'Panel'})});}catch(e){}
  }catch(e){document.getElementById('saveMsg').textContent='Error: '+e;}
}
async function decide(stage){
  try{
    await fetch('/api/pipeline/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage})});
    const msg=document.getElementById('decideMsg');
    msg.textContent='\u2713 Candidate marked as '+stage;
    msg.style.color=stage==='Hired'?'var(--accent)':'var(--red)';
  }catch(e){}
}
function flatEdu(e){if(!e)return{};const o={};for(const[k,v]of Object.entries(e)){if(k!=='bonus')o[k]=v;}for(const[k,v]of Object.entries(e.bonus||{}))o[k]=v;return o;}
function renderSC(rs){
  if(!rs){document.getElementById('rsContent').innerHTML='<div style="color:var(--muted);font-size:12px">No scorecard.</div>';return;}
  const bd=rs.breakdown||{},ss=rs.stage_scores||{};
  const pp=new Set(ss.panel_pending_params||[]);
  const secs=[{l:'Experience',d:bd.experience||{},c:'#6ae3c1'},{l:'Skills',d:bd.skills||{},c:'#8ab4ff'},{l:'Education',d:flatEdu(bd.education||{}),c:'#ffc36b'}];
  let h='';
  for(const s of secs){
    h+='<div style="margin-bottom:14px"><div style="font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:'+s.c+';margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid var(--line)">'+s.l+'</div>';
    for(const[k,v]of Object.entries(s.d)){
      if(!v||typeof v!=='object'||!('score' in v)||v.type==='info')continue;
      const pct=v.max>0?Math.round(v.score/v.max*100):0,fc=pct>=75?'#34d399':pct>=45?'#ffc36b':'#f87171';
      const sb=pp.has(k)?'<span class="stag sp">panel</span>':'';
      h+='<div class="rrow"><div class="rn">'+pLbl(k)+sb+'</div><div class="rb"><div class="rbt"><div class="rbf" style="width:'+pct+'%;background:'+fc+'"></div></div></div><div class="rs" style="color:'+fc+'">'+v.score+'/'+v.max+'</div><div class="rj">'+(v.llm_justification||v.reason||'')+'</div></div>';
    }
    h+='</div>';
  }
  document.getElementById('rsContent').innerHTML=h;
}
async function loadAll(){
  try{
    const r=await fetch('/candidateScore/'+encodeURIComponent(CID));
    if(r.ok){
      const d=await r.json();
      document.getElementById('cName').textContent=d.candidate_name||CID;
      document.getElementById('cMeta').textContent=CID;
      const stages=d.stages||[],ss=(stages[stages.length-1]||{}).stage_scores||{};
      if(ss.resume_score_100)document.getElementById('scoreResume').textContent=ss.resume_score_100;
      if(ss.recruiter_score_100!=null)document.getElementById('scoreRec').textContent=ss.recruiter_score_100;
      if(ss.panel_can_add)document.getElementById('panelAdds').textContent='+'+ss.panel_can_add;
      if(ss.panel_score_100!=null){document.getElementById('scorePan').textContent=ss.panel_score_100;document.getElementById('panScoreBox').style.display='';}
      const recStage=stages.find(s=>s.stage==='recruiter');
      if(recStage?.recruiter_notes){document.getElementById('recNotesText').textContent=recStage.recruiter_notes;document.getElementById('recNotesWrap').style.display='';}
    }
  }catch(e){}
  try{
    const r=await fetch('/api/candidateAnalysis/'+encodeURIComponent(CID));
    if(r.ok){
      _analysis=await r.json();
      const ov=_analysis.candidate_overview||{},rf=(_analysis.semantic_analysis?.role_family_scores||[])[0],dna=_analysis.dna_classification?.primary||'';
      const pills=[];
      if(rf)pills.push('<span class="pill pill-g">'+rf.role_family.replace(/_/g,' ')+'</span>');
      if(dna)pills.push('<span class="pill pill-w">'+dna+'</span>');
      const exp=(_analysis.experience?.items||[]).length;if(exp)pills.push('<span class="pill pill-b">'+exp+' roles</span>');
      document.getElementById('pillRow').innerHTML=pills.join('');
      document.getElementById('narrative').textContent=_analysis.recruiter_narrative||ov.profile_summary||'';
      const ev=_analysis.skills?.skill_evidence_map||{};
      const weak=Object.entries(ev).filter(([k,v])=>(v?.level==='WEAK'||v?.level==='MENTION')&&(v?.years||0)<=1).slice(0,8);
      if(weak.length){
        document.getElementById('skillsCard').style.display='';
        document.getElementById('skillsProbe').innerHTML=weak.map(([k,v])=>'<div class="skills-probe"><span style="font-weight:700;min-width:150px">'+k+'</span><span style="background:#1a1104;color:var(--warn);border:1px solid #4a3210;border-radius:99px;padding:2px 9px;font-size:11px;font-weight:600">'+v.level+'</span><span style="color:var(--muted);font-size:11px;margin-left:6px">'+(v.years?v.years+' yr':'seen once')+'</span></div>').join('');
      }
      const rs=_analysis.rubric_scorecard||_analysis.rubric_score||null;
      if(rs){
        renderSC(rs);
        const ss=rs.stage_scores||{};
        if(ss.resume_score_100)document.getElementById('scoreResume').textContent=ss.resume_score_100;
        if(ss.panel_can_add)document.getElementById('panelAdds').textContent='+'+ss.panel_can_add;
      }
      generateQs();
    }
  }catch(e){}
  buildParams();
  try{await fetch('/api/pipeline/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID})});}catch(e){}
}
loadAll();
</script>
</body></html>"""
    return _html'''

with open('E:/Dev/resume_intelligence/app.py', encoding='utf-8') as f:
    content = f.read()

# Replace {CID_JS} placeholders - these will be handled at runtime via Python string concat
# We need to inject them as Python string concatenations
RECRUITER_FINAL = RECRUITER_HTML.replace(r'{CID_JS}', '" + cid_js + "')
PANEL_FINAL = PANEL_HTML.replace(r'{CID_JS}', '" + cid_js + "')

start = content.find('    _html = """<!DOCTYPE html>\n<html lang="en"><head>\n<meta charset="utf-8"><title>Recruiter Screen</title>')
end_marker = '</script>\n</body></html>"""\n    return _html\n\n\n# ---------------------------------------------------------------------------\n# Panel Screen'
end = content.find(end_marker)
end_inner = end + content[end:].find('</script>\n</body></html>"""\n    return _html')
end_final = end_inner + len('</script>\n</body></html>"""\n    return _html')

new_content = content[:start] + RECRUITER_FINAL + '\n\n\n# ---------------------------------------------------------------------------\n# Panel Screen — dedicated panel interview scoring page  (dark theme)\n# ---------------------------------------------------------------------------\n\n@app.get("/panel-screen/{candidate_id}", response_class=HTMLResponse)\ndef panel_screen(candidate_id: str):  # noqa: C901\n    cid_js = candidate_id.replace("\'", "\\\\\'")  \n    ' + PANEL_FINAL + content[end_final:]

with open('E:/Dev/resume_intelligence/app.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Done. Lines:", new_content.count('\n'))

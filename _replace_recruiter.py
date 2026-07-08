#!/usr/bin/env python3
"""Replace recruiter screen with simplified design: analysis + questions + score update."""

NEW_HTML = """\
<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><title>Phone Screen &mdash; Resume Intelligence</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#07111b;--card:#0f1b2d;--line:#1e3a56;--text:#f0f5fb;--muted:#7a97b4;--accent:#6ae3c1;--accent2:#8ab4ff;--warn:#ffc36b;--red:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Aptos,'Segoe UI',system-ui,sans-serif;background:radial-gradient(circle at top left,#0f2540 0%,#07111b 55%,#040c16 100%);color:var(--text);min-height:100vh}
a{color:var(--accent2);text-decoration:none}
.nav{background:rgba(7,17,27,.95);border-bottom:1px solid var(--line);backdrop-filter:blur(10px);position:sticky;top:0;z-index:100}
.nav-inner{max-width:1100px;margin:0 auto;padding:0 24px;display:flex;align-items:center;height:52px;gap:4px}
.nav-logo{font-weight:900;font-size:14px;background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-right:16px;white-space:nowrap}
.nav-link{padding:0 12px;height:52px;display:flex;align-items:center;font-size:13px;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;white-space:nowrap}
.nav-link:hover{color:var(--text)}.nav-link.active{color:var(--warn);border-bottom-color:var(--warn)}
.nav-right{margin-left:auto}
.btn-nav{background:linear-gradient(135deg,var(--accent2),#a78bfa);color:#04111d;border:none;border-radius:10px;padding:7px 16px;font-size:12px;font-weight:900;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.wrap{max-width:1100px;margin:0 auto;padding:24px 24px 80px}
.card{background:linear-gradient(160deg,rgba(18,32,52,.98),rgba(9,20,34,.98));border:1px solid var(--line);border-radius:20px;padding:22px 26px;margin-bottom:16px;position:relative;overflow:hidden}
.abar{position:absolute;top:0;left:0;right:0;height:3px;border-radius:20px 20px 0 0}
.kicker{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin-bottom:6px}
/* HERO */
.hero-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;flex-wrap:wrap}
.cname{font-size:32px;font-weight:900;letter-spacing:-.02em;background:linear-gradient(90deg,#f0f5fb,var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;line-height:1.1;margin-bottom:6px}
.score-badge{background:#091422;border:1px solid var(--line);border-radius:16px;padding:14px 22px;text-align:center;min-width:110px;flex-shrink:0}
.score-val{font-size:36px;font-weight:900;color:var(--accent);line-height:1}
.score-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-top:4px}
.pills{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 12px}
.pill{display:inline-flex;align-items:center;padding:4px 11px;border-radius:99px;font-size:11px;font-weight:700;background:#0d1e30;border:1px solid var(--line)}
.pill-g{color:var(--accent);border-color:#1a4a3a}.pill-b{color:var(--accent2);border-color:#1e3a6b}.pill-w{color:var(--warn);border-color:#4a3210}
.narrative{font-size:13px;color:#c0d4e8;line-height:1.65;margin-top:4px}
/* SCORE TILES */
.score-tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}
@media(max-width:540px){.score-tiles{grid-template-columns:1fr}}
.stile{background:#091422;border:1px solid var(--line);border-radius:14px;padding:14px 18px}
.stile-val{font-size:26px;font-weight:900;line-height:1;color:var(--accent)}
.stile-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-top:4px}
.stile-sub{font-size:11px;color:var(--warn);margin-top:4px}
/* QUESTIONS */
.q-card{background:#091422;border:1px solid var(--line);border-radius:14px;padding:16px 20px;margin-bottom:12px}
.q-card.scored{border-color:var(--accent)}
.q-num{width:24px;height:24px;border-radius:50%;background:linear-gradient(135deg,var(--warn),#ff9f43);color:#04111d;font-size:11px;font-weight:800;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;margin-right:10px}
.q-text{font-size:13px;font-weight:700;line-height:1.55;margin-bottom:12px}
.q-tag{display:inline-flex;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;text-transform:uppercase;margin-right:5px;margin-bottom:8px}
.th{background:#2a0e0e;color:var(--red);border:1px solid #5a1a1a}
.tm{background:#1a1104;color:var(--warn);border:1px solid #4a3210}
.tl{background:#0c1e16;color:var(--accent);border:1px solid #1a4a2a}
.ans{width:100%;background:#06101c;border:1px solid #1a3048;border-radius:10px;padding:10px 13px;font-size:13px;font-family:inherit;color:var(--text);resize:vertical;min-height:72px;transition:border .15s;line-height:1.5}
.ans:focus{outline:none;border-color:var(--warn)}
.score-result{background:#0b1724;border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-top:10px;font-size:12px;display:none}
/* BUTTONS */
.btn-score{background:linear-gradient(135deg,var(--warn),#ff9f43);color:#04111d;border:none;border-radius:12px;padding:13px 32px;font-size:14px;font-weight:900;cursor:pointer;display:inline-flex;align-items:center;gap:7px;transition:opacity .15s,transform .1s}
.btn-score:hover{opacity:.9;transform:translateY(-1px)}
.btn-score:disabled{opacity:.5;cursor:not-allowed;transform:none}
.btn-ghost{background:transparent;color:var(--accent2);border:1px solid #2a4a6a;border-radius:12px;padding:12px 22px;font-size:13px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.btn-ghost:hover{background:#0d1e30}
/* UPDATED SCORE */
.updated-panel{display:none;background:linear-gradient(135deg,#0c1e16,#071a12);border:1px solid #1a4a2a;border-radius:16px;padding:24px 28px;margin-top:16px}
.new-score-val{font-size:56px;font-weight:900;color:var(--accent);line-height:1}
.spin{display:inline-block;width:16px;height:16px;border:2px solid var(--line);border-top-color:var(--warn);border-radius:50%;animation:sp .6s linear infinite;vertical-align:middle}
@keyframes sp{to{transform:rotate(360deg)}}
.status-msg{font-size:13px;color:var(--muted);min-height:20px}
</style>
</head><body>

<div class="nav"><div class="nav-inner">
  <div class="nav-logo">Resume Intelligence</div>
  <a href="/" class="nav-link">&#8592; Analysis</a>
  <a href="#" class="nav-link active">&#128222; Phone Screen</a>
  <div class="nav-right"><a href="/panel-screen/__CID__" class="btn-nav">Panel Interview &#8594;</a></div>
</div></div>

<div class="wrap">

<!-- HERO CARD -->
<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--warn),#ff9f43)"></div>
  <div class="kicker">Phone Screen &mdash; Stage 2 of 4</div>
  <div class="hero-row">
    <div style="flex:1;min-width:0">
      <div class="cname" id="cName">Loading&hellip;</div>
      <div style="font-size:12px;color:var(--muted);margin-bottom:8px" id="cMeta"></div>
      <div class="pills" id="pillRow"></div>
      <div class="narrative" id="narrative"></div>
    </div>
    <div class="score-badge">
      <div class="score-val" id="scoreLive">&mdash;</div>
      <div class="score-lbl" id="scoreLbl">Resume Score</div>
    </div>
  </div>
  <div class="score-tiles">
    <div class="stile">
      <div class="stile-val" id="tExp">&mdash;</div>
      <div class="stile-lbl">Experience <span style="color:var(--muted)">/ 40</span></div>
      <div class="stile-sub" id="tExpPend"></div>
    </div>
    <div class="stile">
      <div class="stile-val" id="tSkills">&mdash;</div>
      <div class="stile-lbl">Skills <span style="color:var(--muted)">/ 45</span></div>
      <div class="stile-sub" id="tSkillsPend"></div>
    </div>
    <div class="stile">
      <div class="stile-val" id="tEdu">&mdash;</div>
      <div class="stile-lbl">Education <span style="color:var(--muted)">/ 15</span></div>
      <div class="stile-sub" id="tEduPend"></div>
    </div>
  </div>
</div>

<!-- QUESTIONS CARD -->
<div class="card">
  <div class="abar" style="background:linear-gradient(90deg,var(--warn),#ff9f43)"></div>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;flex-wrap:wrap;gap:10px">
    <div>
      <div class="kicker">Step 1 &mdash; Phone Screen Questions</div>
      <div style="font-size:15px;font-weight:800">Answer each question below, then score</div>
      <div style="font-size:12px;color:var(--muted);margin-top:3px">Questions are tailored to this candidate&#39;s resume. Type the candidate&#39;s answers as they respond.</div>
    </div>
    <button onclick="loadQs()" style="background:#0d1e30;color:var(--warn);border:1px solid #4a3210;border-radius:10px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer">&#8635; Regenerate</button>
  </div>
  <div id="qArea"><div style="text-align:center;padding:40px 0;color:var(--muted)"><span class="spin"></span>&nbsp; Generating questions&hellip;</div></div>
  <div style="margin-top:20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
    <button class="btn-score" id="scoreBtn" onclick="scoreAll()" disabled>Score Answers &amp; Update Score &#8594;</button>
    <span class="status-msg" id="statusMsg"></span>
  </div>
  <!-- Updated score panel -->
  <div class="updated-panel" id="updatedPanel">
    <div style="font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:var(--accent);margin-bottom:4px">&#10003; Score Updated</div>
    <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px">
      <div class="new-score-val" id="newScoreVal">&mdash;</div>
      <div style="font-size:16px;color:var(--muted)">/ 100 &mdash; after phone screen</div>
    </div>
    <div style="font-size:13px;color:#c0d4e8;margin-bottom:20px" id="scoreDelta"></div>
    <a href="/panel-screen/__CID__" class="btn-score" style="text-decoration:none;background:linear-gradient(135deg,var(--accent2),#a78bfa)">Proceed to Panel Interview &#8594;</a>
  </div>
</div>

</div><!-- /wrap -->

<script>
const CID='__CID__';
let _analysis=null, _qs=[], _scored={};

function esc(x){return x==null?'':String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function pLabel(k){return({mentorship_signal:'Mentorship',international_exposure:'International Exposure',stakeholder_management:'Stakeholder Mgmt',project_explanation:'Project Walk-through',linkedin_activity:'LinkedIn',extra_curriculars:'Extra-curriculars',skill_depth:'Skill Depth',skill_recency:'Recency',certifications:'Certifications',coding_community:'Community',career_progression:'Progression',stability:'Stability',company_tier:'Company Tier',communication_skills:'Communication',domain_skills:'Domain',problem_solving:'Problem Solving'}[k]||k.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase()));}

async function loadAll(){
  /* 1. load score */
  try{
    const r=await fetch('/candidateScore/'+encodeURIComponent(CID));
    if(r.ok){
      const d=await r.json();
      document.getElementById('cName').textContent=d.candidate_name||CID;
      document.getElementById('cMeta').textContent=CID;
      const ss=(((d.stages||[])[0])||{}).stage_scores||{};
      if(ss.resume_score_100!=null){document.getElementById('scoreLive').textContent=ss.resume_score_100;document.getElementById('scoreLbl').textContent='Resume Score';}
    }
  }catch(e){}
  /* 2. load analysis */
  try{
    const r=await fetch('/api/candidateAnalysis/'+encodeURIComponent(CID));
    if(!r.ok)return;
    _analysis=await r.json();
    /* hero pills */
    const rf=((_analysis.semantic_analysis?.role_family_scores||[])[0]||{}).role_family||'';
    const dna=_analysis.dna_classification?.primary||'';
    const exp=(_analysis.experience?.items||[]).length;
    const pills=[];
    if(rf)pills.push('<span class="pill pill-g">'+rf.replace(/_/g,' ')+'</span>');
    if(dna)pills.push('<span class="pill pill-w">'+dna+'</span>');
    if(exp)pills.push('<span class="pill pill-b">'+exp+' roles</span>');
    document.getElementById('pillRow').innerHTML=pills.join('');
    document.getElementById('narrative').textContent=_analysis.recruiter_narrative||_analysis.candidate_overview?.profile_summary||'';
    /* score tiles */
    const rs=_analysis.rubric_scorecard||{};
    const bd=rs.breakdown||{};
    const ss2=rs.stage_scores||{};
    if(bd.experience?.total!=null){document.getElementById('tExp').textContent=bd.experience.total;}
    if(bd.skills?.total!=null){document.getElementById('tSkills').textContent=bd.skills.total;}
    if(bd.education?.total!=null){document.getElementById('tEdu').textContent=bd.education.total;}
    if(ss2.resume_score_100!=null){document.getElementById('scoreLive').textContent=ss2.resume_score_100;document.getElementById('scoreLbl').textContent='Resume Score';}
    if(ss2.recruiter_can_add)document.getElementById('tExpPend').textContent='+'+ss2.recruiter_can_add+' recruiter can add';
    if(ss2.skills_recruiter_pending_pts)document.getElementById('tSkillsPend').textContent='+'+ss2.skills_recruiter_pending_pts+' recruiter';
    if(ss2.edu_recruiter_pending_pts)document.getElementById('tEduPend').textContent='+'+ss2.edu_recruiter_pending_pts+' recruiter';
    /* load questions */
    loadQs();
  }catch(e){document.getElementById('qArea').innerHTML='<div style="color:var(--red);padding:14px">Could not load analysis for this candidate. Make sure the resume has been analysed first.</div>';}
}

async function loadQs(){
  if(!_analysis){document.getElementById('qArea').innerHTML='<div style="color:var(--muted);padding:14px">Analysis not loaded yet.</div>';return;}
  document.getElementById('qArea').innerHTML='<div style="text-align:center;padding:40px 0;color:var(--muted)"><span class="spin"></span>&nbsp; Generating questions&hellip;</div>';
  document.getElementById('scoreBtn').disabled=true;
  _qs=[];_scored={};
  try{
    const r=await fetch('/generateInterviewQuestions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({analysis:_analysis})});
    const d=await r.json();
    const all=d.questions||[];
    /* prefer recruiter-stage questions; fall back to all */
    _qs=all.filter(q=>q.stage==='recruiter'||q.stage==='phone'||q.stage==='phone_screen');
    if(!_qs.length)_qs=all.slice(0,8);
    renderQs();
  }catch(e){document.getElementById('qArea').innerHTML='<div style="color:var(--red);padding:14px">Error generating questions: '+esc(String(e))+'</div>';}
}

function renderQs(){
  if(!_qs.length){document.getElementById('qArea').innerHTML='<div style="color:var(--muted);padding:14px">No questions generated.</div>';return;}
  document.getElementById('qArea').innerHTML=_qs.map((q,i)=>{
    const tc=q.priority==='high'?'th':q.priority==='low'?'tl':'tm';
    return '<div class="q-card" id="qc_'+i+'">'
      +'<div style="display:flex;align-items:flex-start;gap:0;margin-bottom:4px">'
      +'<span class="q-num">'+(i+1)+'</span>'
      +'<div style="flex:1">'
      +'<span class="q-tag '+tc+'">'+(q.priority||'medium')+'</span>'
      +(q.rubric_param?'<span class="q-tag" style="background:#0d1e30;color:var(--muted);border:1px solid var(--line)">'+pLabel(q.rubric_param)+'</span>':'')
      +(q.skill?'<span class="q-tag" style="background:#0d1830;color:var(--accent2);border:1px solid #1e3a6b">'+esc(q.skill)+'</span>':'')
      +'</div></div>'
      +'<div class="q-text">'+esc(q.question)+'</div>'
      +(q.what_it_tests?'<div style="font-size:11px;color:var(--muted);margin-bottom:10px"><b style="color:var(--text)">Tests:</b> '+esc(q.what_it_tests)+'</div>':'')
      +'<textarea class="ans" id="ans_'+i+'" rows="3" placeholder="Type the candidate\'s response here\u2026" oninput="checkReady()"></textarea>'
      +'<div class="score-result" id="sr_'+i+'"></div>'
      +'</div>';
  }).join('');
  document.getElementById('scoreBtn').disabled=false;
}

function checkReady(){
  const any=_qs.some((_,i)=>(document.getElementById('ans_'+i)?.value||'').trim());
  document.getElementById('scoreBtn').disabled=!any;
}

async function scoreAll(){
  const btn=document.getElementById('scoreBtn');
  btn.disabled=true;btn.innerHTML='<span class="spin"></span>&nbsp; Scoring&hellip;';
  document.getElementById('statusMsg').textContent='Scoring answers\u2026';
  _scored={};
  /* score each answered question */
  const promises=_qs.map(async(q,i)=>{
    const ans=(document.getElementById('ans_'+i)?.value||'').trim();
    if(!ans)return;
    try{
      const r=await fetch('/scoreQuestionAnswer',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({question:q.question,theme:q.theme||q.rubric_param||'',answer_transcript:ans,skill:q.skill||'',candidate_context:'',candidate_id:CID})});
      const sc=await r.json();
      _scored[i]=sc;
      const sv=sc.score_0_to_10||0;
      const col=sv>=7?'var(--accent)':sv>=5?'var(--warn)':'var(--red)';
      const box=document.getElementById('sr_'+i);
      box.style.display='';
      box.innerHTML='<b style="color:'+col+'">'+sv+'/10</b>'
        +(sc.rubric_param?'<span style="color:var(--muted);font-size:10px"> &rarr; '+pLabel(sc.rubric_param)+'</span>':'')
        +(sc.what_was_strong?'<div style="margin-top:5px;color:var(--muted)"><b style="color:var(--text)">Strong:</b> '+esc(sc.what_was_strong)+'</div>':'')
        +(sc.what_was_missing?'<div style="margin-top:3px;color:var(--muted)"><b style="color:var(--text)">Gap:</b> '+esc(sc.what_was_missing)+'</div>':'');
      document.getElementById('qc_'+i).classList.add('scored');
    }catch(e){}
  });
  await Promise.all(promises);
  document.getElementById('statusMsg').textContent='Applying scores to rubric\u2026';
  /* apply scores to get rubric param overrides */
  const scoredArr=Object.values(_scored);
  let overrides={};
  if(scoredArr.length){
    try{
      const r=await fetch('/applyCallScores',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({candidate_id:CID,question_scores:scoredArr,stage:'recruiter',recruiter_notes:''})});
      const d=await r.json();
      overrides=d.rubric_param_overrides||{};
    }catch(e){}
  }
  /* save stage score */
  document.getElementById('statusMsg').textContent='Saving updated score\u2026';
  try{
    const r=await fetch('/updateStageScore',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({candidate_id:CID,stage:'recruiter',stage_overrides:overrides,recruiter_notes:''})});
    const d=await r.json();
    if(r.ok){
      const s100=d.stage_score_100??d.new_total;
      const prev=parseFloat(document.getElementById('scoreLive').textContent)||0;
      document.getElementById('scoreLive').textContent=s100;
      document.getElementById('scoreLbl').textContent='After Phone Screen';
      document.getElementById('newScoreVal').textContent=s100;
      const diff=Math.round((s100-prev)*10)/10;
      document.getElementById('scoreDelta').textContent='Resume score was '+prev+' \u2192 now '+s100+'/100 ('+(diff>=0?'+':'')+diff+' pts from recruiter screen)';
      document.getElementById('updatedPanel').style.display='';
      document.getElementById('statusMsg').textContent='';
      try{await fetch('/api/pipeline/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage:'Telephonic'})});}catch(e){}
    }else{
      document.getElementById('statusMsg').textContent='Save error: '+(d.detail||r.statusText);
    }
  }catch(e){document.getElementById('statusMsg').textContent='Error: '+e;}
  btn.innerHTML='Score Answers &amp; Update Score &#8594;';
  btn.disabled=false;
}

loadAll();
</script>
</body></html>"""

import sys, re

with open('E:/Dev/resume_intelligence/app.py', encoding='utf-8') as f:
    src = f.read()

# Find the recruiter screen function bounds
start_marker = '# ---------------------------------------------------------------------------\n# Recruiter Screen'
end_marker   = '# ---------------------------------------------------------------------------\n# Panel Screen'

si = src.find(start_marker)
ei = src.find(end_marker)
if si == -1 or ei == -1:
    print("ERROR: Could not find markers"); sys.exit(1)

# Build replacement
new_func = (
    '# ---------------------------------------------------------------------------\n'
    '# Recruiter Screen — phone screen (simplified: analysis + questions + score)\n'
    '# ---------------------------------------------------------------------------\n\n'
    '@app.get("/recruiter-screen/{candidate_id}", response_class=HTMLResponse)\n'
    'def recruiter_screen(candidate_id: str):\n'
    '    cid_js = candidate_id.replace("\'", "\\\\\'")  \n'
    '    _html = ("""' + NEW_HTML + '""").replace("__CID__", cid_js)\n'
    '    return _html\n\n\n'
)

new_src = src[:si] + new_func + src[ei:]

try:
    compile(new_src, 'app.py', 'exec')
    print("Syntax OK")
except SyntaxError as e:
    lines = new_src.splitlines()
    lineno = e.lineno or 0
    print(f"SYNTAX ERROR at line {lineno}: {e}")
    for i in range(max(0, lineno-3), min(len(lines), lineno+3)):
        print(f"  {i+1:4d}: {lines[i]}")
    sys.exit(1)

with open('E:/Dev/resume_intelligence/app.py', 'w', encoding='utf-8') as f:
    f.write(new_src)

print(f"Done. app.py is now {new_src.count(chr(10))+1} lines.")

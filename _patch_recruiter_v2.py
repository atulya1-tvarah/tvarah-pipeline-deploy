#!/usr/bin/env python3
"""Patch recruiter screen: replace question generation with instant 3-section form."""
import sys

with open('E:/Dev/resume_intelligence/app.py', encoding='utf-8') as f:
    src = f.read()

# ── Helpers ────────────────────────────────────────────────────────────────────
def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        print(f'ERROR: {label} found {n} times (expected 1)')
        print('  First 80 chars of search string:', repr(old[:80]))
        sys.exit(1)
    return text.replace(old, new, 1)

# ── 1. Add new CSS before recruiter </style> ───────────────────────────────────

OLD_CSS = '.status-msg{font-size:13px;color:var(--muted);min-height:20px}\n</style>\n</head><body>'

NEW_CSS = '''.status-msg{font-size:13px;color:var(--muted);min-height:20px}
/* STRUCTURED QUESTION SECTIONS */
.qs-sec{margin-bottom:24px}
.qs-sec-hdr{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:var(--warn);padding:6px 0 10px;border-bottom:1px solid var(--line);margin-bottom:14px}
.qi-blk{background:rgba(7,17,27,.5);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:8px;display:flex;align-items:flex-start;gap:12px;transition:border-color .15s}
.qi-blk:hover{border-color:rgba(255,195,107,.3)}
.qi-num{width:22px;height:22px;border-radius:50%;background:linear-gradient(135deg,var(--warn),#ff9f43);color:#0a1624;font-size:11px;font-weight:900;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px}
.qi-body{flex:1;min-width:0}
.qi-title{font-size:13px;font-weight:700;color:var(--text);margin-bottom:8px}
.qi-inp{background:#06101c;border:1px solid var(--line);border-radius:8px;padding:7px 10px;color:var(--text);font-size:13px;font-family:inherit;outline:none;transition:border-color .15s}
.qi-inp:focus{border-color:var(--warn)}
.qi-ta{width:100%;background:#06101c;border:1px solid var(--line);border-radius:8px;padding:8px 10px;color:var(--text);font-size:13px;font-family:inherit;outline:none;resize:vertical;min-height:60px;transition:border-color .15s}
.qi-ta:focus{border-color:var(--warn)}
.qi-sel{background:#06101c;border:1px solid var(--line);border-radius:8px;padding:7px 10px;color:var(--text);font-size:13px;outline:none;cursor:pointer}
.qi-sel:focus{border-color:var(--warn)}
.qi-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
.qi-lbl{font-size:11px;color:var(--muted);white-space:nowrap}
.qi-chip{display:inline-flex;align-items:center;gap:5px;background:#0d1e30;border:1px solid var(--line);border-radius:20px;padding:4px 12px;font-size:11px;font-weight:600;color:var(--muted);cursor:pointer;transition:all .15s;user-select:none}
.qi-chip.on{background:rgba(138,180,255,.12);border-color:var(--accent2);color:var(--accent2)}
.qi-skill-tag{background:rgba(138,180,255,.1);border:1px solid #1e3a6b;border-radius:6px;padding:2px 8px;font-size:11px;color:var(--accent2);display:inline-block;margin-right:4px;margin-bottom:4px}
</style>
</head><body>'''

src = replace_once(src, OLD_CSS, NEW_CSS, 'CSS block')

# ── 2. Replace async loadQs() ──────────────────────────────────────────────────

OLD_LOADQS = (
    "async function loadQs(){\n"
    "  if(!_analysis){document.getElementById('qArea').innerHTML='<div style=\"color:var(--muted);padding:14px\">Analysis not loaded yet.</div>';return;}\n"
    "  document.getElementById('qArea').innerHTML='<div style=\"text-align:center;padding:40px 0;color:var(--muted)\"><span class=\"spin\"></span>&nbsp; Generating questions&hellip;</div>';\n"
    "  document.getElementById('scoreBtn').disabled=true;\n"
    "  _qs=[];_scored={};\n"
    "  try{\n"
    "    const r=await fetch('/generateInterviewQuestions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({analysis:_analysis})});\n"
    "    const d=await r.json();\n"
    "    const all=d.questions||[];\n"
    "    /* prefer recruiter-stage questions; fall back to all */\n"
    "    _qs=all.filter(q=>q.stage==='recruiter'||q.stage==='phone'||q.stage==='phone_screen');\n"
    "    if(!_qs.length)_qs=all.slice(0,8);\n"
    "    renderQs();\n"
    "  }catch(e){document.getElementById('qArea').innerHTML='<div style=\"color:var(--red);padding:14px\">Error generating questions: '+esc(String(e))+'</div>';}\n"
    "}"
)

NEW_LOADQS = (
    "function loadQs(){\n"
    "  if(!_analysis){document.getElementById('qArea').innerHTML='<div style=\"color:var(--muted);padding:14px\">Analysis not loaded yet.</div>';return;}\n"
    "  _qs=[];_scored={};\n"
    "  renderQs();\n"
    "}"
)

src = replace_once(src, OLD_LOADQS, NEW_LOADQS, 'loadQs')

# ── 3. Replace renderQs() (recruiter only — uses 'No questions generated') ─────

OLD_RENDERQS = (
    "function renderQs(){\n"
    "  if(!_qs.length){document.getElementById('qArea').innerHTML='<div style=\"color:var(--muted);padding:14px\">No questions generated.</div>';return;}\n"
    "  document.getElementById('qArea').innerHTML=_qs.map((q,i)=>{\n"
    "    const tc=q.priority==='high'?'th':q.priority==='low'?'tl':'tm';\n"
    "    return '<div class=\"q-card\" id=\"qc_'+i+'\">'"\
    "\n      +'<div style=\"display:flex;align-items:flex-start;gap:0;margin-bottom:4px\">'\n"
    "      +'<span class=\"q-num\">'+(i+1)+'</span>'\n"
    "      +'<div style=\"flex:1\">'\n"
    "      +'<span class=\"q-tag '+tc+'\">'+(q.priority||'medium')+'</span>'\n"
    "      +(q.rubric_param?'<span class=\"q-tag\" style=\"background:#0d1e30;color:var(--muted);border:1px solid var(--line)\">'+pLabel(q.rubric_param)+'</span>':'')\n"
    "      +(q.skill?'<span class=\"q-tag\" style=\"background:#0d1830;color:var(--accent2);border:1px solid #1e3a6b\">'+esc(q.skill)+'</span>':'')\n"
    "      +'</div></div>'\n"
    "      +'<div class=\"q-text\">'+esc(q.question)+'</div>'\n"
    "      +(q.what_it_tests?'<div style=\"font-size:11px;color:var(--muted);margin-bottom:10px\"><b style=\"color:var(--text)\">Tests:</b> '+esc(q.what_it_tests)+'</div>':'')\n"
    "      +'<textarea class=\"ans\" id=\"ans_'+i+'\" rows=\"3\" placeholder=\"Type the candidate response here...\" oninput=\"checkReady()\"></textarea>'\n"
    "      +'<div class=\"score-result\" id=\"sr_'+i+'\"></div>'\n"
    "      +'</div>';\n"
    "  }).join('');\n"
    "  document.getElementById('scoreBtn').disabled=false;\n"
    "}"
)

NEW_RENDERQS = r"""function renderQs(){
  const sa=_analysis.skill_analysis||_analysis.skills||{};
  const rs=_analysis.rubric_scorecard||{};
  const flags=rs.reject_flags||[];
  const expItems=(_analysis.experience_analysis&&_analysis.experience_analysis.items)||(_analysis.experience&&_analysis.experience.items)||[];
  const topSkills=(sa.top_skills||[]).slice(0,6);
  let num=0;
  function qb(id,title,inputHtml){num++;return '<div class="qi-blk"><div class="qi-num">'+num+'</div><div class="qi-body"><div class="qi-title">'+title+'</div>'+inputHtml+'</div></div>';}

  /* -- Section 1: General Information -- */
  const genQs=[
    {id:'gen_loc_curr',title:'Current Location',ph:'City / Country'},
    {id:'gen_loc_pref',title:'Preferred Location',ph:'Willing to relocate to?'},
    {id:'gen_ctc_curr',title:'Current CTC',ph:'Current package (e.g. 12 LPA)'},
    {id:'gen_ctc_exp',title:'Expected CTC',ph:'Expected package'},
    {id:'gen_workmode',title:'Work Mode Preference',ph:'Remote / Hybrid / In-office'},
    {id:'gen_company',title:'Company Type Preference',ph:'Startup / MNC / Product / Service'},
    {id:'gen_reason',title:'Reason for Change',ph:'Why looking for a new opportunity?'},
    {id:'gen_role',title:'Expected Role / Title',ph:'What role are they targeting?'},
    {id:'gen_offers',title:'Active Offers / Notice Period',ph:'Competing offers? Notice period?'}
  ];
  let s1='<div class="qs-sec"><div class="qs-sec-hdr">&#128204; Section 1 &mdash; General Information</div>';
  genQs.forEach(function(q){
    _qs.push({id:q.id,section:'general',question:q.title,rubric_param:'',skill:'',label:q.title});
    s1+=qb(q.id,q.title,'<input type="text" class="qi-inp" style="width:100%" id="'+q.id+'" placeholder="'+q.ph+'" oninput="checkReady()">');
  });
  s1+='</div>';

  /* -- Section 2: Missing Information -- */
  let s2='<div class="qs-sec"><div class="qs-sec-hdr">&#9888; Section 2 &mdash; Missing Information</div>';
  const missList=[];
  if(flags.length){flags.forEach(function(f){missList.push({q:String(f),rp:'career_progression'});});}
  if(expItems.length>=1)missList.push({q:'Walk me through your most impactful project at '+esc(expItems[0].company||'your last role'),rp:'project_explanation'});
  if(expItems.length>=2)missList.push({q:'Describe a key project at '+esc(expItems[1].company||'your second role'),rp:'project_explanation'});
  if(!missList.length){s2+='<div style="color:var(--accent);font-size:13px;padding:8px 4px">&#10003; No significant gaps detected in the resume.</div>';}
  else{
    missList.forEach(function(m,i){
      const tid='miss_'+i;
      _qs.push({id:tid,section:'missing',question:m.q,rubric_param:m.rp||'',skill:'',label:m.q});
      s2+=qb(tid,m.q,'<textarea class="qi-ta" id="'+tid+'" rows="2" placeholder="Candidate response..." oninput="checkReady()"></textarea>');
    });
  }
  s2+='</div>';

  /* -- Section 3: Skill-Based Questions -- */
  let s3='<div class="qs-sec"><div class="qs-sec-hdr">&#128295; Section 3 &mdash; Skill-Based Questions</div>';
  if(topSkills.length){
    topSkills.forEach(function(sk,si){
      const sname=esc(typeof sk==='string'?sk:(sk.skill||sk.name||('Skill '+(si+1))));
      const yid='skill_'+si+'_yoe',pid='skill_'+si+'_prof',lid='skill_'+si+'_last',nid='skill_'+si+'_note';
      _qs.push({id:nid,section:'skill',question:'Experience and proficiency in '+sname,rubric_param:'skill_depth',skill:sname,label:sname+' experience'});
      s3+='<div class="qi-blk">'
        +'<div class="qi-num" style="background:linear-gradient(135deg,var(--accent2),#a78bfa);color:#04111d">'+(si+1)+'</div>'
        +'<div class="qi-body">'
        +'<div class="qi-title"><span class="qi-skill-tag">'+sname+'</span></div>'
        +'<div class="qi-row">'
        +'<span class="qi-lbl">YoE</span>'
        +'<input type="number" class="qi-inp" style="width:65px" id="'+yid+'" min="0" max="30" placeholder="0" oninput="checkReady()">'
        +'<span class="qi-lbl">Proficiency</span>'
        +'<select class="qi-sel" id="'+pid+'">'
        +'<option value="">Select</option><option>Beginner</option><option>Intermediate</option><option>Advanced</option><option>Expert</option>'
        +'</select>'
        +'<span class="qi-lbl">Last used</span>'
        +'<input type="text" class="qi-inp" style="width:110px" id="'+lid+'" placeholder="Current / 1yr" oninput="checkReady()">'
        +'</div>'
        +'<textarea class="qi-ta" id="'+nid+'" rows="2" placeholder="Additional notes (optional)..." oninput="checkReady()"></textarea>'
        +'</div></div>';
    });
  }
  const cpid='cloud_ans';
  _qs.push({id:cpid,section:'skill',question:'Cloud platform experience and certifications',rubric_param:'skill_depth',skill:'Cloud',label:'Cloud platforms'});
  s3+='<div class="qi-blk">'
    +'<div class="qi-num" style="background:linear-gradient(135deg,var(--accent2),#a78bfa);color:#04111d">'+(topSkills.length+1)+'</div>'
    +'<div class="qi-body">'
    +'<div class="qi-title">Cloud Platforms</div>'
    +'<div class="qi-row" style="margin-bottom:10px">'
    +'<span class="qi-chip" id="chip_aws" onclick="toggleChip(this)">AWS</span>'
    +'<span class="qi-chip" id="chip_azure" onclick="toggleChip(this)">Azure</span>'
    +'<span class="qi-chip" id="chip_gcp" onclick="toggleChip(this)">GCP</span>'
    +'<span class="qi-chip" id="chip_k8s" onclick="toggleChip(this)">Kubernetes</span>'
    +'<span class="qi-chip" id="chip_docker" onclick="toggleChip(this)">Docker</span>'
    +'</div>'
    +'<textarea class="qi-ta" id="'+cpid+'" rows="2" placeholder="Cloud experience, certifications, years of use..." oninput="checkReady()"></textarea>'
    +'</div></div>';
  s3+='</div>';

  document.getElementById('qArea').innerHTML=s1+s2+s3;
  document.getElementById('scoreBtn').disabled=false;
}

function toggleChip(el){el.classList.toggle('on');}

function getAns(q){
  if(q.section==='skill'&&q.id!=='cloud_ans'&&q.id.slice(-5)==='_note'){
    const base=q.id.slice(0,-5);
    const yoe=(document.getElementById(base+'_yoe')?document.getElementById(base+'_yoe').value:'').trim();
    const prof=(document.getElementById(base+'_prof')?document.getElementById(base+'_prof').value:'');
    const last=(document.getElementById(base+'_last')?document.getElementById(base+'_last').value:'').trim();
    const note=(document.getElementById(q.id)?document.getElementById(q.id).value:'').trim();
    const parts=[];
    if(yoe)parts.push(yoe+' years');
    if(prof)parts.push(prof+' proficiency');
    if(last)parts.push('last used: '+last);
    if(note)parts.push(note);
    return parts.join(', ');
  }
  if(q.id==='cloud_ans'){
    const chips=[];
    ['chip_aws','chip_azure','chip_gcp','chip_k8s','chip_docker'].forEach(function(cid){
      const el=document.getElementById(cid);if(el&&el.classList.contains('on'))chips.push(el.textContent.trim());
    });
    const note=(document.getElementById(q.id)?document.getElementById(q.id).value:'').trim();
    return (chips.length?chips.join(', ')+'. ':'')+note;
  }
  return (document.getElementById(q.id)?document.getElementById(q.id).value:'').trim();
}"""

src = replace_once(src, OLD_RENDERQS, NEW_RENDERQS, 'renderQs')

# ── 4. Replace checkReady() ────────────────────────────────────────────────────

OLD_CHECKREADY = (
    "function checkReady(){\n"
    "  const any=_qs.some((_,i)=>(document.getElementById('ans_'+i)?.value||'').trim());\n"
    "  document.getElementById('scoreBtn').disabled=!any;\n"
    "}"
)

NEW_CHECKREADY = "function checkReady(){document.getElementById('scoreBtn').disabled=false;}"

src = replace_once(src, OLD_CHECKREADY, NEW_CHECKREADY, 'checkReady')

# ── 5. Replace scoreAll() – position-based (avoids exact-char matching issues) ─

# Find start position of scoreAll
sa_start = src.find('\nasync function scoreAll(){')
if sa_start == -1:
    print('ERROR: async function scoreAll(){ not found')
    sys.exit(1)

# Find end: the blank line before loadAll()
sa_end = src.find('\n\nloadAll();\n', sa_start)
if sa_end == -1:
    print('ERROR: loadAll() boundary not found after scoreAll')
    sys.exit(1)

NEW_SCOREALL = (
    "async function scoreAll(){\n"
    "  const btn=document.getElementById('scoreBtn');\n"
    "  btn.disabled=true;btn.innerHTML='<span class=\"spin\"></span>&nbsp; Scoring\u2026';\n"
    "  document.getElementById('statusMsg').textContent='Scoring answers\u2026';\n"
    "  _scored={};\n"
    "  /* Build recruiter notes from general info fields */\n"
    "  const notes=_qs.filter(function(q){return q.section==='general';}).map(function(q){\n"
    "    const v=(document.getElementById(q.id)?document.getElementById(q.id).value:'').trim();\n"
    "    return v?q.label+': '+v:'';\n"
    "  }).filter(Boolean).join(' | ');\n"
    "  /* Score non-general questions */\n"
    "  const toScore=_qs.filter(function(q){return q.section!=='general';});\n"
    "  const promises=toScore.map(async function(q,i){\n"
    "    const ans=getAns(q);if(!ans)return;\n"
    "    try{\n"
    "      const r=await fetch('/scoreQuestionAnswer',{method:'POST',headers:{'Content-Type':'application/json'},\n"
    "        body:JSON.stringify({question:q.question,theme:q.rubric_param||'',answer_transcript:ans,skill:q.skill||'',candidate_context:'',candidate_id:CID})});\n"
    "      const sc=await r.json();_scored[i]=sc;\n"
    "    }catch(e){}\n"
    "  });\n"
    "  await Promise.all(promises);\n"
    "  document.getElementById('statusMsg').textContent='Applying scores to rubric\u2026';\n"
    "  const scoredArr=Object.values(_scored);\n"
    "  let overrides={};\n"
    "  if(scoredArr.length){\n"
    "    try{\n"
    "      const r=await fetch('/applyCallScores',{method:'POST',headers:{'Content-Type':'application/json'},\n"
    "        body:JSON.stringify({candidate_id:CID,question_scores:scoredArr,stage:'recruiter',recruiter_notes:notes})});\n"
    "      const d=await r.json();\n"
    "      overrides=d.rubric_param_overrides||{};\n"
    "    }catch(e){}\n"
    "  }\n"
    "  document.getElementById('statusMsg').textContent='Saving updated score\u2026';\n"
    "  try{\n"
    "    const r=await fetch('/updateStageScore',{method:'POST',headers:{'Content-Type':'application/json'},\n"
    "      body:JSON.stringify({candidate_id:CID,stage:'recruiter',stage_overrides:overrides,recruiter_notes:notes})});\n"
    "    const d=await r.json();\n"
    "    if(r.ok){\n"
    "      const s100=d.stage_score_100??d.new_total;\n"
    "      const prev=parseFloat(document.getElementById('scoreLive').textContent)||0;\n"
    "      document.getElementById('scoreLive').textContent=s100;\n"
    "      document.getElementById('scoreLbl').textContent='After Phone Screen';\n"
    "      document.getElementById('newScoreVal').textContent=s100;\n"
    "      const diff=Math.round((s100-prev)*10)/10;\n"
    "      document.getElementById('scoreDelta').textContent='Resume score was '+prev+' now '+s100+'/100 ('+(diff>=0?'+':'')+diff+' pts from recruiter screen)';\n"
    "      document.getElementById('updatedPanel').style.display='';\n"
    "      document.getElementById('statusMsg').textContent='';\n"
    "      try{await fetch('/api/pipeline/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:CID,stage:'Telephonic'})});}catch(e){}\n"
    "    }else{\n"
    "      document.getElementById('statusMsg').textContent='Save error: '+(d.detail||r.statusText);\n"
    "    }\n"
    "  }catch(e){document.getElementById('statusMsg').textContent='Error: '+e;}\n"
    "  btn.innerHTML='Score &amp; Update Score &#8594;';\n"
    "  btn.disabled=false;\n"
    "}"
)

# Replace: from sa_start+1 (skip leading \n) to sa_end
src = src[:sa_start + 1] + NEW_SCOREALL + src[sa_end:]

# ── Verify Python syntax ───────────────────────────────────────────────────────
try:
    compile(src, 'app.py', 'exec')
    print('Syntax OK')
except SyntaxError as e:
    print(f'SYNTAX ERROR: {e}')
    lines = src.splitlines()
    lineno = e.lineno or 0
    for i in range(max(0, lineno-4), min(len(lines), lineno+4)):
        print(f'  {i+1:4d}: {lines[i]}')
    sys.exit(1)

with open('E:/Dev/resume_intelligence/app.py', 'w', encoding='utf-8') as f:
    f.write(src)

print(f'Done. app.py is now {src.count(chr(10))+1} lines.')

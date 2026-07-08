"""Extraction micro-service — accepts PDF/DOCX upload, returns structured JSON.

Run:  uvicorn extractor_service:app --port 8002 --reload
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from pdf_to_json_extractor import pdf_to_resume_json

app = FastAPI(title="Resume Extractor Service", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_ALLOWED = {".pdf", ".docx", ".doc"}

HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Resume Extractor</title>
  <style>
    :root{--bg:#07111b;--card:#0f1b2d;--line:#24415e;--text:#f2f6fb;--muted:#9fb2c8;--accent:#6ae3c1;--warn:#ffc36b}
    *{box-sizing:border-box}
    body{margin:0;font-family:Aptos,Segoe UI,system-ui,sans-serif;background:#07111b;color:#f2f6fb;display:flex;align-items:center;justify-content:center;min-height:100vh}
    .card{background:#0f1b2d;border:1px solid #24415e;border-radius:24px;padding:40px;max-width:540px;width:100%;text-align:center}
    h1{margin:0 0 8px;font-size:22px;color:#6ae3c1}
    p{color:#9fb2c8;margin:0 0 28px;font-size:14px}
    .dropzone{border:2px dashed #24415e;border-radius:16px;padding:40px 20px;cursor:pointer;transition:.2s;margin-bottom:20px}
    .dropzone:hover,.dropzone.over{border-color:#6ae3c1;background:rgba(106,227,193,.05)}
    .dropzone input{display:none}
    .dropzone label{cursor:pointer;font-size:14px;color:#9fb2c8}
    .dropzone label span{color:#6ae3c1;font-weight:600}
    #fileName{margin-top:10px;font-size:13px;color:#6ae3c1;min-height:18px}
    button{background:linear-gradient(135deg,#6ae3c1,#8ab4ff);color:#04111d;border:none;border-radius:12px;padding:12px 32px;font-size:15px;font-weight:700;cursor:pointer;width:100%}
    button:disabled{opacity:.5;cursor:not-allowed}
    #status{margin-top:18px;font-size:13px;color:#9fb2c8;min-height:20px}
    #result{display:none;margin-top:20px;text-align:left}
    .dl-btn{display:inline-block;margin-top:12px;background:#1a3d2b;color:#6ae3c1;border:1px solid #6ae3c1;border-radius:10px;padding:8px 20px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none}
    pre{background:#060e18;border:1px solid #1d334b;border-radius:10px;padding:14px;font-size:11px;max-height:300px;overflow:auto;color:#9fb2c8;margin-top:10px}
  </style>
</head>
<body>
<div class="card">
  <h1>Resume Extractor</h1>
  <p>Upload a PDF or DOCX resume — get back structured JSON ready for analysis.</p>
  <div class="dropzone" id="dz">
    <input type="file" id="fileInput" accept=".pdf,.docx,.doc">
    <label for="fileInput">Drop file here or <span>browse</span></label>
    <div id="fileName"></div>
  </div>
  <button id="uploadBtn" disabled onclick="upload()">Extract Resume</button>
  <div id="status"></div>
  <div id="result">
    <a class="dl-btn" id="dlBtn" download="extracted.json">Download JSON</a>
    <details><summary style="cursor:pointer;color:#8ab4ff;font-size:13px;margin-top:10px">Preview JSON</summary><pre id="preview"></pre></details>
  </div>
</div>
<script>
  const fi=document.getElementById('fileInput');
  const dz=document.getElementById('dz');
  const btn=document.getElementById('uploadBtn');
  const fn=document.getElementById('fileName');
  let _file=null;

  fi.onchange=e=>{setFile(e.target.files[0])};
  dz.ondragover=e=>{e.preventDefault();dz.classList.add('over')};
  dz.ondragleave=()=>dz.classList.remove('over');
  dz.ondrop=e=>{e.preventDefault();dz.classList.remove('over');setFile(e.dataTransfer.files[0])};

  function setFile(f){
    if(!f) return;
    _file=f; fn.textContent=f.name; btn.disabled=false;
    document.getElementById('result').style.display='none';
    document.getElementById('status').textContent='';
  }

  async function upload(){
    if(!_file) return;
    btn.disabled=true;
    document.getElementById('status').textContent='Extracting…';
    document.getElementById('result').style.display='none';
    const fd=new FormData();
    fd.append('file',_file);
    try{
      const res=await fetch('/extract',{method:'POST',body:fd});
      const data=await res.json();
      if(!res.ok){document.getElementById('status').textContent='Error: '+(data.detail||res.statusText);btn.disabled=false;return;}
      document.getElementById('status').textContent='Extraction complete.';
      const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
      const url=URL.createObjectURL(blob);
      const dl=document.getElementById('dlBtn');
      dl.href=url;
      dl.download=_file.name.replace(/\\.[^.]+$/,'')+'.json';
      document.getElementById('preview').textContent=JSON.stringify(data,null,2).slice(0,3000)+'…';
      document.getElementById('result').style.display='';
    }catch(e){
      document.getElementById('status').textContent='Error: '+e;
    }
    btn.disabled=false;
  }
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


@app.get("/health")
def health():
    return {"status": "ok", "service": "extractor"}


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{suffix}'. Use PDF or DOCX.")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        result = pdf_to_resume_json(tmp_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)

    return JSONResponse(content=result)

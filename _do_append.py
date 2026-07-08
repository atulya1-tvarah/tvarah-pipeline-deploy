#!/usr/bin/env python3
"""Append recruiter-screen and panel-screen routes to app.py using __CID__ placeholder."""

import sys

# ── Read the existing _patch_screens.py to extract RECRUITER_HTML / PANEL_HTML ──
with open('E:/Dev/resume_intelligence/_patch_screens.py', encoding='utf-8') as f:
    patch_src = f.read()

# Execute only the variable definitions (stop before the `with open(` block)
ns = {}
exec(patch_src.split('\nwith open')[0], ns)
RECRUITER_HTML = ns['RECRUITER_HTML']
PANEL_HTML     = ns['PANEL_HTML']

# ── Transform: replace the broken CID injection with __CID__ placeholder ──
CID_PATTERN = '""" + r"""{CID_JS}""" + r"""'
END_RETURN   = '"""\n    return _html'
END_REPLACE  = '""".replace("__CID__", cid_js)\n    return _html'

recruiter_code = RECRUITER_HTML.replace(CID_PATTERN, '__CID__').replace(END_RETURN, END_REPLACE)
panel_code     = PANEL_HTML.replace(CID_PATTERN, '__CID__').replace(END_RETURN, END_REPLACE)

# ── Build the full function text ──
RECRUITER_FUNC = f'''

# ---------------------------------------------------------------------------
# Recruiter Screen — phone screen scoring page (dark theme, orange accent)
# ---------------------------------------------------------------------------

@app.get("/recruiter-screen/{{candidate_id}}", response_class=HTMLResponse)
def recruiter_screen(candidate_id: str):  # noqa: C901
    cid_js = candidate_id.replace("'", "\\'")
{recruiter_code}
'''

PANEL_FUNC = f'''

# ---------------------------------------------------------------------------
# Panel Screen — panel interview scoring page (dark theme, blue accent)
# ---------------------------------------------------------------------------

@app.get("/panel-screen/{{candidate_id}}", response_class=HTMLResponse)
def panel_screen(candidate_id: str):  # noqa: C901
    cid_js = candidate_id.replace("'", "\\'")
{panel_code}
'''

# ── Read app.py ──
with open('E:/Dev/resume_intelligence/app.py', encoding='utf-8') as f:
    content = f.read()

if '/recruiter-screen/{candidate_id}' in content:
    print("Routes already present — nothing to do.")
    sys.exit(0)

new_content = content.rstrip('\n') + '\n' + RECRUITER_FUNC + PANEL_FUNC

# ── Syntax-check before writing ──
try:
    compile(new_content, 'app.py', 'exec')
    print("Syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
    # Show context
    lines = new_content.splitlines()
    lineno = e.lineno or 0
    for i in range(max(0, lineno-3), min(len(lines), lineno+3)):
        print(f"  {i+1:4d}: {lines[i]}")
    sys.exit(1)

with open('E:/Dev/resume_intelligence/app.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print(f"Done. app.py is now {new_content.count(chr(10))+1} lines.")
print("Routes added: /recruiter-screen/{candidate_id}  /panel-screen/{candidate_id}")

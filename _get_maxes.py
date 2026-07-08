from rubric_engine import compute_rubric_score, STAGE_MAP
r = compute_rubric_score({}, {}, {'experience_entries':[], 'total_years':5}, {}, {'education_entries':[]}, {})
bd = r['breakdown']
print('=== EXPERIENCE ===')
for k,v in bd['experience'].items():
    if isinstance(v,dict) and 'max' in v:
        print(f'  {k}: max={v["max"]} stage={STAGE_MAP.get(k,"resume")}')
print()
print('=== SKILLS ===')
for k,v in bd['skills'].items():
    if isinstance(v,dict) and 'max' in v:
        print(f'  {k}: max={v["max"]} stage={STAGE_MAP.get(k,"resume")}')
print()
print('=== EDUCATION CORE ===')
for k,v in bd['education'].items():
    if k == 'bonus': continue
    if isinstance(v,dict) and 'max' in v:
        print(f'  {k}: max={v["max"]} stage={STAGE_MAP.get(k,"resume")}')
print()
print('=== EDUCATION BONUS ===')
for k,v in (bd['education'].get('bonus') or {}).items():
    if isinstance(v,dict) and 'max' in v:
        print(f'  {k}: max={v["max"]} stage={STAGE_MAP.get(k,"resume")}')

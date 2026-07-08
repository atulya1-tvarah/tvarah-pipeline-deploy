"""Verification script for 40+45+15=100 rubric framework (3-stage model)."""
import sys

def fail(msg):
    print(f"  FAIL: {msg}")
    sys.exit(1)

def ok(msg):
    print(f"  OK  : {msg}")

from rubric_engine import (
    compute_rubric_score, apply_stage_update,
    _PANEL_SKILL_KEYS, STAGE_MAP, RECRUITER_UPDATABLE, PANEL_UPDATABLE,
)
from interview_question_engine import RUBRIC_PARAM_HELP, build_interview_questions

# ── Dummy inputs ──────────────────────────────────────────────────────────────
_exp = {"experience_entries": [], "total_years": 5, "fast_learner": True, "yearly_skill_learning": [2020,2021,2022]}
_skills = {"skill_rows": [
    {"skill": "Python", "evidence_level": "ADVANCED", "recency": "CURRENT", "years_experience": 4, "open_source_signal": True},
    {"skill": "AWS",    "evidence_level": "HANDS_ON",  "recency": "RECENT",  "years_experience": 3, "open_source_signal": True},
    {"skill": "SQL",    "evidence_level": "HANDS_ON",  "recency": "RECENT",  "years_experience": 5, "open_source_signal": False},
]}
_edu = {"education_entries": [{"degree": "B.Tech", "gpa_band": "EXCELLENT"}],
        "highest_institute_tier": "TIER_1", "top_institutes": ["IIT Delhi"],
        "highest_degree": "BACHELOR", "education_gaps": []}
_bert = {}

result = compute_rubric_score({}, {}, _exp, {}, _edu, _bert)

print("\n=== 1. max_scores ===")
ms = result["max_scores"]
assert ms == {"experience": 40, "skills": 45, "education": 15, "total": 100}, f"Wrong max_scores: {ms}"
ok(f"max_scores = {ms}")

print("\n=== 2. Section ceilings ===")
assert result["experience_score"] <= 40, f"exp > 40: {result['experience_score']}"
ok(f"experience_score = {result['experience_score']} <= 40")
assert result["skills_score"] <= 45, f"skills > 45: {result['skills_score']}"
ok(f"skills_score = {result['skills_score']} <= 45")
assert result["education_score"] <= 15, f"edu > 15: {result['education_score']}"
ok(f"education_score = {result['education_score']} <= 15")
assert result["total_score"] <= 100, f"total > 100: {result['total_score']}"
ok(f"total_score = {result['total_score']} <= 100")

print("\n=== 3. Stage scores ===")
ss = result["stage_scores"]
# recruiter_can_add = remaining addable pts (max - auto_score) across all recruiter-upgradeable params.
# E16/E17 auto-score international_exposure and coding_community at resume stage;
# remaining pts depend on whether signals were found in the test fixture.
assert ss["recruiter_can_add"] <= 15, f"recruiter_can_add={ss['recruiter_can_add']} > 15 (theoretical max)"
assert ss["recruiter_can_add"] >= 0, f"recruiter_can_add={ss['recruiter_can_add']} < 0"
ok(f"recruiter_can_add = {ss['recruiter_can_add']} (max 15, actual depends on E16/E17 auto-scores)")
assert ss["panel_can_add"] == 13, f"panel_can_add={ss['panel_can_add']}, want 13"
ok(f"panel_can_add = {ss['panel_can_add']} (comm+domain+prob_solving)")
assert ss["resume_max"] <= 120, f"resume_max={ss['resume_max']} seems too high"
ok(f"resume_max = {ss['resume_max']}")
expected_recruiter_pending = ["coding_community", "international_exposure", "linkedin_activity",
                              "mentorship_signal", "project_explanation", "stakeholder_management"]
assert sorted(ss["recruiter_pending_params"]) == expected_recruiter_pending, \
    f"recruiter_pending_params mismatch: {sorted(ss['recruiter_pending_params'])}"
ok(f"recruiter_pending_params = {sorted(ss['recruiter_pending_params'])}")
assert sorted(ss["panel_pending_params"]) == ["communication_skills", "domain_skills", "problem_solving"]
ok(f"panel_pending_params = {ss['panel_pending_params']}")

print("\n=== 4. Skills breakdown keys ===")
sk = result["breakdown"]["skills"]
assert "skills_learning_acumen" in sk, "skills_learning_acumen missing"
ok("skills_learning_acumen present")
assert "yoy_learning" not in sk, "yoy_learning still present — rename failed"
ok("yoy_learning absent (renamed correctly)")
assert "project_explanation" in sk, "project_explanation missing"
ok("project_explanation present in skills")
assert "unique_skill_combos" not in sk, "unique_skill_combos still present"
ok("unique_skill_combos absent")
assert "bonus" not in sk, "old bonus pool still present"
ok("bonus pool absent from skills")
for k in ("communication_skills", "domain_skills", "problem_solving"):
    assert k in sk, f"{k} missing from skills"
ok("panel params present: communication_skills, domain_skills, problem_solving")

print("\n=== 5. Panel params start at 0 ===")
for k in ("communication_skills", "domain_skills", "problem_solving"):
    assert sk[k]["score"] == 0, f"{k} score != 0: {sk[k]['score']}"
ok("All 3 panel params start at 0")
assert sk["project_explanation"]["score"] == 0, "project_explanation should start at 0"
ok("project_explanation starts at 0 (recruiter fills)")

print("\n=== 6. Param stage tags ===")
assert sk["project_explanation"].get("stage") == "recruiter", f"project_explanation stage: {sk['project_explanation'].get('stage')}"
ok("project_explanation.stage = recruiter")
assert sk["skills_learning_acumen"].get("stage") == "resume", f"skills_learning_acumen stage: {sk['skills_learning_acumen'].get('stage')}"
ok("skills_learning_acumen.stage = resume")
for k in ("communication_skills", "domain_skills", "problem_solving"):
    assert sk[k].get("stage") == "panel", f"{k} stage wrong: {sk[k].get('stage')}"
ok("panel params all have stage=panel")

print("\n=== 7. Skills param maxima ===")
# Current scoring model (40+45+15=100): 20+10+5+5+5+4+3+5+5+3 = 65 gross (clamped to 45)
assert sk["skill_list_years"]["max"] == 20, f"skill_list_years max={sk['skill_list_years']['max']}"
ok("skill_list_years max=20")
assert sk["skill_depth"]["max"] == 10, f"skill_depth max={sk['skill_depth']['max']}"
ok("skill_depth max=10")
assert sk["skill_recency"]["max"] == 5, f"skill_recency max={sk['skill_recency']['max']}"
ok("skill_recency max=5")
assert sk["skills_learning_acumen"]["max"] == 5, f"skills_learning_acumen max={sk['skills_learning_acumen']['max']}"
ok("skills_learning_acumen max=5")
assert sk["certifications"]["max"] == 5, f"certifications max={sk['certifications']['max']}"
ok("certifications max=5")
assert sk["coding_community"]["max"] == 4, f"coding_community max={sk['coding_community']['max']}"
ok("coding_community max=4 (E17: auto-scored from resume signals)")
assert sk["project_explanation"]["max"] == 3, f"project_explanation max={sk['project_explanation']['max']}"
ok("project_explanation max=3")
assert sk["communication_skills"]["max"] == 5, f"communication_skills max={sk['communication_skills']['max']}"
ok("communication_skills max=5")
assert sk["domain_skills"]["max"] == 5, f"domain_skills max={sk['domain_skills']['max']}"
ok("domain_skills max=5")
assert sk["problem_solving"]["max"] == 3, f"problem_solving max={sk['problem_solving']['max']}"
ok("problem_solving max=3")

print("\n=== 8. Skills section ceiling ===")
# skills_score is clamped to 45 regardless of gross param max sum
assert result["skills_score"] <= 45, f"skills_score={result['skills_score']} > 45"
ok(f"skills_score = {result['skills_score']} <= 45 ceiling")

print("\n=== 9. Education structure ===")
edu_bd = result["breakdown"]["education"]
for k in ("institute_tier", "degree_level", "education_job_relevance", "education_gap"):
    assert k in edu_bd, f"edu core param missing: {k}"
ok("All 4 education core params present")
assert "bonus" in edu_bd, "bonus sub-dict missing from education"
bonus = edu_bd["bonus"]
for k in ("exec_education", "patents_publications", "linkedin_activity", "extra_curriculars"):
    assert k in bonus, f"edu bonus param missing: {k}"
ok("All 4 education bonus params present")
assert edu_bd["institute_tier"]["max"] == 5
ok("institute_tier max=5")
assert edu_bd["degree_level"]["max"] == 2
ok("degree_level max=2")
assert edu_bd["education_job_relevance"]["max"] == 2
ok("education_job_relevance max=2")
assert edu_bd["education_gap"]["max"] == 1
ok("education_gap max=1")
assert bonus["exec_education"]["max"] == 1.25, f"exec_education max={bonus['exec_education']['max']}"
ok("exec_education max=1.25")
assert bonus["patents_publications"]["max"] == 2.5, f"patents_publications max={bonus['patents_publications']['max']}"
ok("patents_publications max=2.5")
assert bonus["linkedin_activity"]["max"] == 1
ok("linkedin_activity max=1")
assert bonus["extra_curriculars"]["max"] == 1.25, f"extra_curriculars max={bonus['extra_curriculars']['max']}"
ok("extra_curriculars max=1.25")

print("\n=== 10. Education total <= 15 ===")
edu_core_max = sum(v["max"] for k, v in edu_bd.items() if isinstance(v, dict) and "max" in v and k != "bonus")
edu_bonus_max = sum(v["max"] for v in bonus.values() if isinstance(v, dict) and "max" in v)
assert edu_core_max == 10, f"edu core max = {edu_core_max}, want 10"
ok(f"Education core max = {edu_core_max}")
assert edu_bonus_max == 6.0, f"edu bonus max = {edu_bonus_max}, want 6.0 (exec=1.25 + patents=2.5 + linkedin=1 + extras=1.25)"
ok(f"Education bonus max = {edu_bonus_max}")
assert result["education_score"] <= 15
ok(f"education_score = {result['education_score']} <= 15")

print("\n=== 11. Experience params ===")
exp_bd = result["breakdown"]["experience"]
assert exp_bd["career_progression"]["max"] == 4, f"career_progression max={exp_bd['career_progression']['max']}"
ok("career_progression max=4")
assert exp_bd["stability"]["max"] == 4, f"stability max={exp_bd['stability']['max']}"
ok("stability max=4")
assert exp_bd["international_exposure"]["max"] == 2
ok("international_exposure max=2 (E16: auto-scored when signal detected)")

print("\n=== 12. STAGE_MAP correctness ===")
assert STAGE_MAP.get("skills_learning_acumen") == "resume"
ok("STAGE_MAP: skills_learning_acumen = resume")
assert STAGE_MAP.get("project_explanation") == "recruiter"
ok("STAGE_MAP: project_explanation = recruiter")
assert STAGE_MAP.get("communication_skills") == "panel"
ok("STAGE_MAP: communication_skills = panel")
assert STAGE_MAP.get("linkedin_activity") == "recruiter"
ok("STAGE_MAP: linkedin_activity = recruiter")

print("\n=== 13. _PANEL_SKILL_KEYS ===")
assert _PANEL_SKILL_KEYS == {"communication_skills", "domain_skills", "problem_solving"}
ok(f"_PANEL_SKILL_KEYS = {_PANEL_SKILL_KEYS}")

print("\n=== 14. RECRUITER_UPDATABLE / PANEL_UPDATABLE ===")
assert "project_explanation" in RECRUITER_UPDATABLE
ok("project_explanation in RECRUITER_UPDATABLE")
assert "skills_learning_acumen" in RECRUITER_UPDATABLE
ok("skills_learning_acumen in RECRUITER_UPDATABLE")
assert "project_explanation" in PANEL_UPDATABLE
ok("project_explanation in PANEL_UPDATABLE")
assert "skills_learning_acumen" in PANEL_UPDATABLE
ok("skills_learning_acumen in PANEL_UPDATABLE")

print("\n=== 15. apply_stage_update — recruiter scores project_explanation ===")
updated = apply_stage_update(result, "recruiter", {"project_explanation": 2.5}, "test_cand")
assert updated["breakdown"]["skills"]["project_explanation"]["score"] == 2.5
ok("project_explanation updated to 2.5 by recruiter")
assert updated["skills_score"] <= 45
ok(f"skills_score after recruiter update = {updated['skills_score']} <= 45")

print("\n=== 16. apply_stage_update — panel scores communication_skills ===")
updated2 = apply_stage_update(updated, "panel", {"communication_skills": 4, "domain_skills": 4, "problem_solving": 2}, "test_cand")
assert updated2["breakdown"]["skills"]["communication_skills"]["score"] == 4
ok("communication_skills updated to 4 by panel")
assert updated2["skills_score"] <= 45
ok(f"skills_score after panel update = {updated2['skills_score']} <= 45")

print("\n=== 17. interview_question_engine — stage split ===")
from interview_question_engine import build_interview_questions
qs = build_interview_questions(result)
r_params = {q["rubric_param"] for q in qs["recruiter_questions"]}
p_params  = {q["rubric_param"] for q in qs["panel_questions"]}
assert "project_explanation" in r_params, f"project_explanation not in recruiter Qs: {r_params}"
ok("project_explanation appears in recruiter questions")
assert "project_explanation" not in p_params, f"project_explanation still in panel Qs"
ok("project_explanation absent from panel questions")
assert "skills_learning_acumen" in r_params, f"skills_learning_acumen not in recruiter Qs"
ok("skills_learning_acumen appears in recruiter questions")
assert "communication_skills" in p_params, f"communication_skills not in panel Qs"
ok("communication_skills in panel questions")
print(f"  Recruiter questions: {len(qs['recruiter_questions'])} | Panel questions: {len(qs['panel_questions'])}")

print("\n=== 18. RUBRIC_PARAM_HELP ===")
assert "skills_learning_acumen" in RUBRIC_PARAM_HELP
assert "yoy_learning" not in RUBRIC_PARAM_HELP
ok("skills_learning_acumen in RUBRIC_PARAM_HELP, yoy_learning absent")
assert RUBRIC_PARAM_HELP["project_explanation"]["stage"] == "recruiter"
ok("RUBRIC_PARAM_HELP: project_explanation stage=recruiter")
assert RUBRIC_PARAM_HELP["skills_learning_acumen"]["max"] == 3
ok("RUBRIC_PARAM_HELP: skills_learning_acumen max=3")

print("\n" + "="*50)
print(f"  ALL CHECKS PASSED")
print("="*50)

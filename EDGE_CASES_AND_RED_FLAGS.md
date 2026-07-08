# Resume Intelligence — Edge Cases & Red Flags Sheet
> **Status: IMPLEMENTED** — 2026-06-13
> Created: 2026-06-11 | Based on product review transcript (Sandeep / Aditya / SaiRupa)
>
> **Implemented in:** `rubric_engine.py`
> - A1–A10 archetype detection: `detect_archetype()` function
> - Dynamic weight reallocation: `WEIGHT_TABLE` + `archetype_total_score` in output
> - E4 (edu break cross-ref): `_detect_career_breaks(edu_entries=...)` + `_is_edu_overlap()`
> - E5 (parental break): `possible_parental_breaks` classification (soft note, no penalty)
> - E3 (TIER_1→TIER_1 stability): edge_case_notes in stability breakdown
> - E7 (startup exit): TIER_4/5 + <18m + no gap → not penalised
> - E15 (internal promotions): same-company sequential detection
> - E8/R6 (cert farming): `_cert_farming_flag` guard, cert score capped at 2
> - R1–R20 red flags: `detect_red_flags()` function, output in `red_flags` key

---

## Core Insight (from transcript)

> "Different types of candidates need different scoring mechanisms."
> A Tier 5 college grad who went Google → Amazon → Meta cannot have the same score as an IIT grad with no notable company.
> **Experience is his strength.** Education should be scored out of ~5; Experience out of ~50.

This requires **dynamic weight reallocation** — the 100-point total stays the same, but the internal split between Education / Experience / Skills shifts based on the candidate **archetype** detected at parse time.

---

## Section 1: Candidate Archetypes & Dynamic Weight Rules

| # | Archetype | Trigger Condition | Education Weight | Experience Weight | Skills Weight | Notes |
|---|-----------|------------------|-----------------|-------------------|---------------|-------|
| A1 | **Elite College, Strong Company** | Institute TIER_1/2 + best_company TIER_1/2 | 15 (default) | 40 (default) | 45 (default) | Baseline — no reallocation |
| A2 | **Weak College, FAANG/Top Company** | Institute TIER_4/5/UNKNOWN + best_company TIER_1 | **5** (capped) | **52** (+12 shifted) | **43** | Experience is the story. Education almost irrelevant |
| A3 | **Weak College, Mid-Tier Company** | Institute TIER_4/5 + best_company TIER_2/3 | **8** | **47** (+7) | **45** | Moderate reallocation |
| A4 | **Elite College, No/Weak Company** | Institute TIER_1 + best_company TIER_3/4/5 | **20** (+5) | **37** (-3) | **43** | Education differentiates; penalize for no brand-name leverage |
| A5 | **Fresh Graduate (0–1 yr exp)** | total_years_of_experience ≤ 1 | **30** (+15) | **10** | **60** (+15) | Skills + Education dominate; experience almost absent |
| A6 | **Senior / 10+ YoE** | total_years_of_experience ≥ 10 | **8** | **47** | **45** | Education irrelevant at senior level; execution track record matters |
| A7 | **PhD / Research Track** | Degree = PhD or count(publications) ≥ 2 | **25** (+10) | **30** | **45** | Research depth substitutes for company tenure |
| A8 | **Domain Switcher** | education domain ≠ job domain for 2+ recent roles | **5** | **45** | **50** (+5) | Skills prove the switch is real |
| A9 | **Founder / Serial Entrepreneur** | Role titles contain Founder/Co-Founder/CEO across 2+ entries | **10** | **42** | **48** | Company tier undefined — judge by scale/funding signals |
| A10 | **Consultant / Contractor** | 3+ short stints (<18m) at different companies, same skill cluster | **12** | **40** (adjusted stability) | **48** | Stability scoring adjusted — short stints ≠ job-hopping |

---

## Section 2: Edge Cases — Scoring Behaviour

| # | Edge Case | Current Behaviour | Expected Behaviour | Parameters Affected |
|---|-----------|------------------|-------------------|---------------------|
| E1 | **Tier 5 college → FAANG trajectory** | Education scores low, company_tier scores high, but total may not reflect true value | Archetype A2 kicks in; education capped at 5 pts; experience_weight boosted | institute_tier, company_tier, overall_weight_split |
| E2 | **IIT grad, never worked at brand-name company** | Education maxes, experience mid | Archetype A4; education gets small bonus; experience penalised for not leveraging pedigree | institute_tier (+bonus), company_tier (flag: "elite grad, sub-par company") |
| E3 | **Google → Amazon → Meta (same domain)** | Stability high, progression high | career_progression = FAST_TRACK; stability should NOT penalise for moves — all TIER_1 | stability algo: exempt same-tier lateral TIER_1→TIER_1 from job-hopping flag |
| E4 | **Career break = MBA / higher education** | Detected as gap → penalised | If break overlaps with education entry → no penalty; flag as "Educational break — verified" | career_breaks, education_gap cross-reference |
| E5 | **Career break = maternity/paternity** | Detected as gap → penalised | ≤18m gap with no hostile context → soft flag only, not score reduction | career_breaks (add reason-classification: parental/personal/health) |
| E6 | **10 YoE but same role, no progression** | Experience scores high due to YoE | BERT career_progression = LATERAL/DECLINING → penalty on career_progression param | career_progression (BERT blend), title_velocity |
| E7 | **Short stints at startups that shut down** | Treated as job-hopping | Company status check: if startup + <18m + no subsequent gap → not flagged as hopper | stability (add startup_exit_context flag) |
| E8 | **Certification-heavy, low real experience** | certifications boost score | If certs > 5 but skill_depth ≤ FOUNDATIONAL → flag "cert farming"; cap cert score at 2 | certifications, skill_depth (add guard) |
| E9 | **10+ YoE applying for junior role** | Overexperienced detected at YoE ratio | Hard flag: "Significantly overqualified — risk of early exit"; show recruiter alert | overall_experience (yoe ratio < 0.5 from above) |
| E10 | **2 YoE applying for senior role** | Underexperienced at YoE ratio | Flag: "Underqualified on experience — evaluate skills heavily"; shift weight to skills | overall_experience (yoe ratio < 0.7), weight shift to skill_depth |
| E11 | **Non-IT degree → IT career (domain switch)** | education_job_relevance = FOUNDATIONAL → low score | If 3+ years of IT experience post-switch → relevance = MEDIUM; experience compensates | education_job_relevance, archetype A8 |
| E12 | **No degree but 10+ YoE at top companies** | Education defaults to 3.0 → fair | Archetype A6 + A2 combined: education capped at 5; experience/skills boosted | institute_tier = NONE handled gracefully, no penalty beyond default |
| E13 | **Multiple short stints + consulting pattern** | Flagged as job-hopper | Archetype A10: if skill cluster is consistent across stints → relabel as "consultant pattern" | stability (add role_cluster_consistency check) |
| E14 | **PhD with 2 YoE industry** | Fresh to industry despite age | Compare graduation year vs experience start; PhD publication/research = substitute for experience | career_progression, education weight boosted (A7) |
| E15 | **Same company, multiple role changes** | May look like job-hopping on naive parse | Internal promotions = positive progression signal, not instability | stability (detect same employer across sequential roles) |
| E16 | **Founder exit → FAANG** | Founder role scored poorly (no company tier) | Founder role: check scale signals (team size, funding, product launched) → score as TIER_2 equivalent if startup metrics found | company_tier for founder roles |
| E17 | **Skills listed without projects or experience** | skill_list_years scores them | Skills with zero job/project context → downweight to AWARENESS in depth scoring | skill_depth (add "context-less skill" penalty) |
| E18 | **Resume with only 1 job, very short** | All categories low | Trigger A5 (fresh grad logic) regardless of claimed YoE | Archetype detection: YoE ≤ 1 OR job_count = 1 |
| E19 | **International experience (onsite visa)** | Detected heuristically, recruiter fills | Auto-extract onsite/visa/relocation keywords; pre-fill international_exposure = 1 (recruiter confirms) | international_exposure auto-signal |
| E20 | **Two degrees from different tiers** | Best tier wins | Use best tier for institute_tier score; flag secondary degree separately for recruiter | education multi-entry handling (already exists — verify logic) |

---

## Section 3: Red Flags

### 3A — Hard Red Flags (auto-reject or strong reject recommendation)

| # | Red Flag | Detection Logic | Action |
|---|----------|----------------|--------|
| R1 | **3+ career breaks > 3 months each** | career_breaks count ≥ 3 | REJECT flag; shown to recruiter with explanation |
| R2 | **Declining company tier trajectory** | company_tier sequence: TIER_1 → TIER_3 → TIER_4+ over last 3 roles | Flag: "Downward brand trajectory — investigate reason" |
| R3 | **Title inflation without scope growth** | Title = "Director/VP/Head" but team_size = 0 and no reports/ownership verbs | Flag: "Title inflation suspected — probe in recruiter screen" |
| R4 | **Buzzword resume, no quantified impact** | project_1/2 score < 4 + awards_recognition = 0 + no numbers in descriptions | Flag: "Low impact evidence — may be responsibility-lister, not achiever" |
| R5 | **Severe overqualification** | YoE ratio < 0.5 (candidate has 2× the experience asked for) | Flag: "Significantly overqualified — high risk of early exit or salary mismatch" |
| R6 | **Cert farming** | certifications ≥ 5 AND skill_depth avg ≤ FOUNDATIONAL | Flag: "Certification-heavy, low demonstrated depth — may not translate to real skill" |
| R7 | **Skill cluster mismatch with claimed role** | BERT role_family_prior confidence > 0.7 AND predicted family ≠ JD target family | Flag: "BERT signals different role family than applied JD — possible wrong-fit application" |
| R8 | **No verifiable output in 5+ year career** | project count = 0 AND awards = 0 AND certifications = 0 | Flag: "No verifiable deliverables found in resume" |

### 3B — Soft Red Flags (recruiter attention, not auto-reject)

| # | Red Flag | Detection Logic | Action |
|---|----------|----------------|--------|
| R9 | **Frequent lateral moves (no growth)** | BERT career_progression = LATERAL for 2+ consecutive roles | Soft flag: "Lateral-only history — assess growth mindset in screen" |
| R10 | **Job-hopping pattern** | hop_rate > 1.5 roles/yr OR 2+ stints < 12m | Soft flag: "Job-hopping pattern — explore reasons; context matters (startups/layoffs)" |
| R11 | **Large education gap (>12m to first job)** | education_gap > 12 months | Soft flag: "Long entry gap — may indicate difficulty entering workforce; verify" |
| R12 | **Overloaded skills list (>30 skills)** | len(skills) > 30 | Soft flag: "Inflated skills list — depth likely shallow; probe in technical screen" |
| R13 | **No progression in 5+ years at same company** | Same company, same/similar title for ≥ 5 years | Soft flag: "Stagnation risk — no visible growth; assess internal scope" |
| R14 | **Domain switch without bridge skills** | Archetype A8 + skill_depth avg < HANDS_ON | Soft flag: "Domain switch without strong bridge skills — higher onboarding risk" |
| R15 | **Gaps aligned with recession years** | Gap overlaps 2020 (COVID) or known layoff wave | Context note: "Gap likely COVID/industry layoff — do not penalise automatically" |
| R16 | **Founder-only history, no employee experience** | All roles = Founder/CEO, never a team contributor | Soft flag: "Never been an employee — may struggle with org structures; verify cultural fit" |
| R17 | **Elite college, mediocre career after** | Institute TIER_1 + best_company TIER_4/5 + YoE > 5 | Soft flag: "High-pedigree grad, below-pedigree career — investigate reason; could be lifestyle choice or performance" |
| R18 | **Very recent skills only (no depth history)** | All APPLIED skills added in last 12 months | Soft flag: "Skills appear recently acquired — may lack real depth; probe in technical screen" |
| R19 | **No online presence or community activity** | coding_community = 0 + no GitHub/LinkedIn/Kaggle links | Soft flag (for tech roles): "No community engagement — lower for open-source/research roles" |
| R20 | **Project descriptions identical or templated** | High string similarity between project_1 and project_2 descriptions | Soft flag: "Possible copy-paste project descriptions — probe ownership in screen" |

---

## Section 4: Candidate Intent Signals (from transcript — "what is he looking for")

> "We need to identify what he is looking for — company type, remuneration, everything. Then show that candidate only to those companies where he will definitely join."

| Signal | Detection Method | Use |
|--------|-----------------|-----|
| **Target company type** | Keywords in objective/summary: "startup", "FAANG", "MNC", "product company", "service" | Filter sourcing pipeline |
| **Growth vs. stability seeker** | BERT career_progression label: FAST_TRACK = growth-seeker; LATERAL/STABLE = stability | Recommend fast-growth vs. stable companies |
| **Remote/onsite preference** | Keywords: "remote", "hybrid", "onsite", "relocate", "open to relocation" | Filter by work mode |
| **Compensation range signal** | inferred from current company tier + YoE + location | Pre-screen salary fit |
| **Domain preference** | BERT role_family + last 2 job domains | Only show JDs in matching domain |
| **Leadership aspiration** | mentorship_signal = FORMAL/LEAD + stakeholder = C_LEVEL | Prioritise people-manager JDs |
| **IC vs. Manager track** | Title progression: IC titles only vs. manager titles | Route to IC or managerial JDs |

---

## Section 5: Proposed Weight Reallocation Logic (pseudocode)

```
function detect_archetype(candidate):
  tier = candidate.best_company_tier        # TIER_1..5
  edu = candidate.institute_tier            # TIER_1..5 / UNKNOWN
  yoe = candidate.total_years_of_experience
  is_phd = candidate.highest_degree == "PhD"
  is_founder = "Founder" in candidate.all_titles
  is_switcher = candidate.education_domain != candidate.job_domain

  if yoe <= 1:              return ARCHETYPE_A5   # Fresh grad
  if is_phd:                return ARCHETYPE_A7   # PhD
  if is_founder:            return ARCHETYPE_A9   # Founder
  if is_switcher:           return ARCHETYPE_A8   # Domain switch
  if yoe >= 10:             return ARCHETYPE_A6   # Senior
  if edu in [4,5] and tier == 1:  return ARCHETYPE_A2  # Weak edu + FAANG
  if edu in [4,5] and tier == 2:  return ARCHETYPE_A3  # Weak edu + mid-tier
  if edu == 1 and tier in [3,4,5]: return ARCHETYPE_A4 # Elite edu + weak company
  return ARCHETYPE_A1       # Default: baseline

WEIGHT_TABLE = {
  A1: {edu: 15, exp: 40, skills: 45},
  A2: {edu:  5, exp: 52, skills: 43},
  A3: {edu:  8, exp: 47, skills: 45},
  A4: {edu: 20, exp: 37, skills: 43},
  A5: {edu: 30, exp: 10, skills: 60},
  A6: {edu:  8, exp: 47, skills: 45},
  A7: {edu: 25, exp: 30, skills: 45},
  A8: {edu:  5, exp: 45, skills: 50},
  A9: {edu: 10, exp: 42, skills: 48},
  A10:{edu: 12, exp: 40, skills: 48},
}
```

---

## Open Questions (for approval discussion)

1. Should archetype detection be **automatic** (hidden) or should recruiters be able to **override** the detected archetype?
2. For **A2 (Tier 5 + FAANG)**: cap education at 5 pts absolute, or cap at 5 as a proportion of the reallocated total?
3. Should **soft red flags** be shown to the candidate (feedback) or only to the recruiter?
4. For the **pipeline matching** feature: where does "candidate intent" data come from — resume parse only, or a separate candidate intake form?
5. **Stability scoring** for A10 (Consultants): should we require external proof of contract nature (LinkedIn "Contract" label) or trust internal heuristic?

---

*Next step: Get approval on this sheet → implement archetype detection + dynamic weights + red flag enhancements in rubric_engine.py*

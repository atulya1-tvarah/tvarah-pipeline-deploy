from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_MODEL_CACHE: dict[str, tuple[Any, Any, Any]] = {}


def _encoder_backend() -> str:
    backend = os.getenv("EVIDENCE_ENCODER_BACKEND", "transformers").strip().lower()
    if backend != "transformers":
        return backend
    try:
        import transformers  # noqa: F401

        return "transformers"
    except Exception:
        return "lexical"


def _trained_models_dir() -> Path:
    return Path(os.getenv("TRAINED_MODELS_DIR", "trained_models_v3"))


def _task_model_path(task: str) -> Path:
    return _trained_models_dir() / task


def _load_classifier(task: str) -> tuple[Any, Any, Any] | None:
    cached = _MODEL_CACHE.get(task)
    if cached is not None:
        return cached
    model_path = _task_model_path(task)
    if not model_path.exists():
        _MODEL_CACHE[task] = None
        return None
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except Exception:
        _MODEL_CACHE[task] = None
        return None
    tokenizer = AutoTokenizer.from_pretrained(str(model_path))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_path))
    model.eval()
    cache_entry = (tokenizer, model, torch)
    _MODEL_CACHE[task] = cache_entry
    return cache_entry


def _predict_text(task: str, text: str) -> dict[str, Any] | None:
    bundle = _load_classifier(task)
    if not bundle or not text.strip():
        return None
    tokenizer, model, torch = bundle
    encoded = tokenizer(
        text,
        truncation=True,
        padding="max_length",
        max_length=min(int(getattr(tokenizer, "model_max_length", 768) or 768), 768),
        return_tensors="pt",
    )
    with torch.no_grad():
        logits = model(**encoded).logits
        probs = torch.softmax(logits, dim=-1)[0]
    top_index = int(torch.argmax(probs).item())
    confidence = float(probs[top_index].item())
    label_map = getattr(model.config, "id2label", {}) or {}
    label = label_map.get(top_index, str(top_index))
    ranked = sorted(
        [
            {
                "label": label_map.get(index, str(index)),
                "probability": round(float(prob.item()), 4),
            }
            for index, prob in enumerate(probs)
        ],
        key=lambda item: item["probability"],
        reverse=True,
    )
    return {
        "label": label,
        "confidence": round(confidence, 4),
        "candidates": ranked[:3],
        "source": "bert_model",
        "model_path": str(_task_model_path(task)),
    }


def _resume_text_for_classifier(overview: dict[str, Any], experience: dict[str, Any]) -> str:
    sections = [
        f"Summary: {overview.get('profile_summary') or ''}",
        f"Location: {overview.get('location') or ''}",
        f"Titles: {', '.join(experience.get('titles', [])[:6])}",
        f"Companies: {', '.join(experience.get('companies', [])[:6])}",
        f"Business impacts: {', '.join(str(item) for item in experience.get('business_impacts', [])[:8])}",
    ]
    return "\n".join(section for section in sections if section.strip())


def _experience_text(item: dict[str, Any]) -> str:
    return " | ".join(
        str(part).strip()
        for part in [
            item.get("title"),
            item.get("company"),
            item.get("start_date"),
            item.get("end_date"),
            item.get("description"),
        ]
        if str(part or "").strip()
    )


def _skill_text(skill: dict[str, Any]) -> str:
    evidence_lines = []
    for ctx in skill.get("contexts", [])[:3]:
        evidence_lines.append(
            " | ".join(
                str(part).strip()
                for part in [
                    ctx.get("title"),
                    ctx.get("company"),
                    ctx.get("project_type"),
                    ctx.get("context"),
                ]
                if str(part or "").strip()
            )
        )
    return "\n".join(
        [
            f"Skill: {skill.get('skill') or ''}",
            f"Cluster: {skill.get('cluster') or ''}",
            f"Evidence: {skill.get('evidence_level') or ''}",
            f"Depth: {skill.get('depth_label') or ''}",
            f"Weighted years: {skill.get('years_of_usage') or 0}",
            f"Raw years: {skill.get('raw_years_of_usage') or 0}",
            f"Contexts: {skill.get('matched_context_count') or 0}",
            f"Recency: {skill.get('recency') or ''}",
            "Samples: " + " ; ".join(line for line in evidence_lines if line),
        ]
    )


def infer_bert_priors(
    overview: dict[str, Any],
    normalized_resume: dict[str, Any],
    evidence_map: dict[str, Any],
    semantic: dict[str, Any],
    experience: dict[str, Any],
    dna: dict[str, Any],
) -> dict[str, Any]:
    resume_text = _resume_text_for_classifier(overview, experience)
    role_prior = _predict_text("role_family", resume_text)
    dna_prior = _predict_text("dna_fit", resume_text)
    project_type_priors = []
    skill_depth_priors = []
    for item in (normalized_resume.get("experience") or [])[:8]:
        if not isinstance(item, dict):
            continue
        prediction = _predict_text("project_type", _experience_text(item))
        if prediction:
            project_type_priors.append(
                {
                    "company": item.get("company"),
                    "title": item.get("title"),
                    "predicted_project_type": prediction["label"],
                    "confidence": prediction["confidence"],
                    "candidates": prediction["candidates"],
                }
            )
    for skill in sorted(
        [item for item in evidence_map.values() if item.get("evidence_level") != "NONE"],
        key=lambda item: (
            ["NONE", "MENTION", "WEAK", "APPLIED", "DEEP", "EXPERT"].index(item.get("evidence_level", "NONE")),
            item.get("years_of_usage", 0),
            item.get("matched_context_count", 0),
        ),
        reverse=True,
    )[:12]:
        prediction = _predict_text("skill_depth", _skill_text(skill))
        if prediction:
            skill_depth_priors.append(
                {
                    "skill": skill.get("skill"),
                    "predicted_depth_label": prediction["label"],
                    "confidence": prediction["confidence"],
                    "candidates": prediction["candidates"],
                }
            )
    # career_progression: ordered title sequence
    roles = (normalized_resume.get("experience") or [])[:8]
    cp_text = " | ".join(
        f"{r.get('title', '')} @ {r.get('company', '')} ({r.get('start_date', '')}–{r.get('end_date', 'present')})"
        for r in roles
        if isinstance(r, dict) and r.get("title")
    )
    career_progression_prior = _predict_text("career_progression", cp_text) if cp_text.strip() else None

    # stakeholder_management: top job descriptions
    descriptions = [
        str(r.get("description") or "").strip()
        for r in roles
        if isinstance(r, dict) and r.get("description")
    ]
    sm_text = "\n---\n".join(descriptions[:5])
    stakeholder_prior = _predict_text("stakeholder_management", sm_text[:3000]) if sm_text.strip() else None

    # mentorship_signal: team/management language from job titles + descriptions
    ms_segments = []
    for r in roles[:5]:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "").strip()
        desc = str(r.get("description") or "").strip()
        if title or desc:
            ms_segments.append(f"{title}: {desc}"[:800])
    ms_text = "\n---\n".join(ms_segments)
    mentorship_prior = _predict_text("mentorship_signal", ms_text[:3000]) if ms_text.strip() else None

    return {
        "role_family_prior": role_prior
        or {
            "label": semantic.get("top_role_family"),
            "confidence": 0.0,
            "candidates": [],
            "source": "heuristic_fallback",
        },
        "dna_prior": dna_prior
        or {
            "label": dna.get("primary_dna"),
            "confidence": 0.0,
            "candidates": [],
            "source": "heuristic_fallback",
        },
        "project_type_priors": project_type_priors,
        "skill_depth_priors": skill_depth_priors,
        "career_progression_prior": career_progression_prior or {
            "label": None, "confidence": 0.0, "candidates": [], "source": "model_unavailable",
        },
        "stakeholder_prior": stakeholder_prior or {
            "label": None, "confidence": 0.0, "candidates": [], "source": "model_unavailable",
        },
        "mentorship_prior": mentorship_prior or {
            "label": None, "confidence": 0.0, "candidates": [], "source": "model_unavailable",
        },
    }


def _skill_prior_0_to_5(skill: dict[str, Any]) -> int:
    score = 0
    level = str(skill.get("evidence_level", "NONE")).upper()
    depth = str(skill.get("depth_label", "")).upper()
    years = float(skill.get("years_of_usage") or 0)
    contexts = int(skill.get("matched_context_count") or 0)
    recency = str(skill.get("recency", "UNKNOWN")).upper()
    if level in {"APPLIED", "DEEP", "EXPERT"}:
        score += 2
    if level in {"DEEP", "EXPERT"}:
        score += 1
    if depth in {"ADVANCED", "ARCHITECT_LEVEL"}:
        score += 1
    if years >= 4 or contexts >= 2:
        score += 1
    if recency == "RECENT":
        score += 1
    return max(0, min(5, score))


def _skill_packet(skill: dict[str, Any]) -> dict[str, Any]:
    context_summaries = []
    for ctx in skill.get("contexts", [])[:3]:
        context_summaries.append(
            {
                "company": ctx.get("company"),
                "title": ctx.get("title"),
                "project_type": ctx.get("project_type"),
                "evidence_level": ctx.get("evidence_level"),
                "reasons": ctx.get("reasons", [])[:4],
            }
        )
    return {
        "skill": skill.get("skill"),
        "cluster": skill.get("cluster"),
        "evidence_level": skill.get("evidence_level"),
        "depth_label": skill.get("depth_label"),
        "prior_score_0_to_5": _skill_prior_0_to_5(skill),
        "weighted_years": skill.get("years_of_usage"),
        "raw_years": skill.get("raw_years_of_usage"),
        "recency": skill.get("recency"),
        "contexts": skill.get("matched_context_count"),
        "project_contexts": skill.get("project_contexts", [])[:3],
        "architecture_signal": bool(skill.get("architecture_signal")),
        "coding_signal": bool(skill.get("coding_signal")),
        "open_source_signal": bool(skill.get("open_source_signal")),
        "evidence_reasons": skill.get("reasons", [])[:6],
        "evidence_samples": context_summaries,
    }


def _short_confidence(confidence: float | int | None) -> str:
    value = float(confidence or 0)
    if value >= 0.8:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


def _summarize_role_prior(prior: dict[str, Any]) -> str:
    label = str(prior.get("label") or "UNKNOWN").replace("_", " ")
    confidence = float(prior.get("confidence") or 0)
    candidates = prior.get("candidates", [])[:2]
    if candidates:
        alt = ", ".join(
            f"{str(item.get('label') or '').replace('_', ' ')} {item.get('probability')}"
            for item in candidates
            if item.get("label")
        )
        return f"BERT leans {label.title()} with {_short_confidence(confidence)} confidence; nearest labels: {alt}."
    return f"BERT leans {label.title()} with {_short_confidence(confidence)} confidence."


def _summarize_dna_prior(prior: dict[str, Any]) -> str:
    label = str(prior.get("label") or "UNKNOWN").replace("_", " ")
    confidence = float(prior.get("confidence") or 0)
    return f"BERT reads the operating pattern as {label.title()} with {_short_confidence(confidence)} confidence."


def _summarize_project_prior(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "Role").strip()
    company = str(item.get("company") or "Unknown company").strip()
    project_type = str(item.get("predicted_project_type") or "UNKNOWN").replace("_", " ")
    confidence = float(item.get("confidence") or 0)
    return f"{title} at {company} looks most like {project_type.title()} work with {_short_confidence(confidence)} confidence."


def _summarize_skill_prior(item: dict[str, Any]) -> str:
    skill = str(item.get("skill") or "Skill").strip()
    depth = str(item.get("predicted_depth_label") or "UNKNOWN").replace("_", " ")
    confidence = float(item.get("confidence") or 0)
    return f"BERT rates {skill} around {depth.title()} depth with {_short_confidence(confidence)} confidence."


def build_evidence_packets(
    overview: dict[str, Any],
    evidence_map: dict[str, Any],
    semantic: dict[str, Any],
    experience: dict[str, Any],
    dna: dict[str, Any],
    bert_priors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ranked_skills = sorted(
        [item for item in evidence_map.values() if item.get("evidence_level") != "NONE"],
        key=lambda item: (
            ["NONE", "MENTION", "WEAK", "APPLIED", "DEEP", "EXPERT"].index(item.get("evidence_level", "NONE")),
            item.get("years_of_usage", 0),
            item.get("matched_context_count", 0),
        ),
        reverse=True,
    )
    top_role_scores = semantic.get("role_family_scores", [])[:5]
    bert_priors = bert_priors or {}
    return {
        "encoder_backend": _encoder_backend(),
        "encoder_model": os.getenv("BERT_ENCODER_MODEL", "answerdotai/ModernBERT-base"),
        "candidate_profile": {
            "name": overview.get("name"),
            "location": overview.get("location"),
            "profile_summary": overview.get("profile_summary"),
        },
        "skill_packets": [_skill_packet(skill) for skill in ranked_skills[:12]],
        "role_packets": [
            {
                "role_family": item.get("role_family"),
                "score_signal": item.get("score"),
                "must_have_hits": item.get("must_have_hits"),
                "matched_clusters": item.get("matched_clusters", [])[:5],
            }
            for item in top_role_scores
        ],
        "experience_packet": {
            "total_experience_years": experience.get("total_experience_years"),
            "titles": experience.get("titles", [])[:5],
            "companies": experience.get("companies", [])[:5],
            "progression": experience.get("progression"),
            "same_company_growth": experience.get("same_company_growth"),
            "client_facing": experience.get("client_facing"),
            "international_exposure": experience.get("international_exposure"),
            "decision_maker": experience.get("decision_maker"),
            "fast_learner": experience.get("fast_learner"),
            "complexity_signal_score": experience.get("complexity_signal_score"),
            "leadership_signal_score": experience.get("leadership_signal_score"),
            "problem_solving_signal_score": experience.get("problem_solving_signal_score"),
            "project_types": experience.get("project_types", [])[:5],
            "business_impacts": experience.get("business_impacts", [])[:6],
        },
        "semantic_packet": {
            "top_role_family": semantic.get("top_role_family"),
            "skill_consistency_score": semantic.get("skill_consistency_score"),
            "weak_skill_count": semantic.get("weak_skill_count"),
            "inferred_strength_areas": semantic.get("inferred_skills", [])[:6],
        },
        "dna_packet": {
            "primary_dna": dna.get("primary_dna"),
            "consulting_score": dna.get("consulting_score"),
            "product_score": dna.get("product_score"),
            "domain_specialist_score": dna.get("domain_specialist_score"),
        },
        "bert_priors": bert_priors,
        "bert_readout": {
            "role_family_summary": _summarize_role_prior(bert_priors.get("role_family_prior", {})),
            "dna_summary": _summarize_dna_prior(bert_priors.get("dna_prior", {})),
            "project_type_summaries": [
                _summarize_project_prior(item)
                for item in bert_priors.get("project_type_priors", [])[:5]
                if isinstance(item, dict)
            ],
            "skill_depth_summaries": [
                _summarize_skill_prior(item)
                for item in bert_priors.get("skill_depth_priors", [])[:8]
                if isinstance(item, dict)
            ],
        },
    }

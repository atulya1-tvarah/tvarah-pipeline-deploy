
from __future__ import annotations

from utils import flatten_text, normalize_text
from taxonomy import ROLE_DNA_AFFINITY

# ─── Signal keywords per DNA type ───────────────────────────────────────────

_DNA_SIGNALS: dict[str, list[str]] = {
    "CONSULTING": [
        "client", "stakeholder", "advisory", "consulting", "delivery",
        "project-based", "multiple clients", "presented to", "engagement",
        "managed client", "client delivery", "client project", "business consulting",
    ],
    "PRODUCT": [
        "product", "feature", "roadmap", "user", "retention", "growth",
        "platform", "saas", "b2c", "b2b", "launch", "product analytics",
        "product manager", "product owner", "product development", "user experience",
    ],
    "DOMAIN_SPECIALIST": [
        "hedge fund", "retail", "marketing", "supply chain", "fintech",
        "healthcare", "insurance", "bfsi", "manufacturing", "logistics",
        "pharma", "ecommerce", "e-commerce", "quant", "capital markets",
    ],
    "RESEARCH": [
        "research", "publications", "papers", "academic", "thesis",
        "conference", "arxiv", "journal", "novel", "ablation",
        "peer-reviewed", "published", "research paper", "phd",
    ],
    "PLATFORM_INFRA": [
        "infrastructure", "platform engineering", "sre", "devops",
        "reliability", "kubernetes", "terraform", "cloud ops",
        "site reliability", "infra", "on-call", "incident response",
        "observability", "monitoring", "data platform",
    ],
}

# Fallback catch-all when no strong signal
_HYBRID_FALLBACK = "HYBRID"


def _zone_texts(resume_data: dict) -> dict[str, str]:
    """Split resume into three weighted zones."""
    title_parts: list[str] = []
    description_parts: list[str] = []
    skills_parts: list[str] = []

    for exp in resume_data.get("experience", []) or []:
        if not isinstance(exp, dict):
            continue
        title = normalize_text(exp.get("title") or exp.get("role") or "")
        if title:
            title_parts.append(title)
        for key in ("description", "responsibilities", "summary", "highlights", "bullets"):
            val = exp.get(key)
            if isinstance(val, str) and val.strip():
                description_parts.append(val)
            elif isinstance(val, list):
                description_parts.extend(str(v) for v in val if v)
        skills_val = exp.get("skills")
        if isinstance(skills_val, list):
            skills_parts.extend(str(s) for s in skills_val if s)
        elif isinstance(skills_val, str):
            skills_parts.append(skills_val)

    # Top-level skills section
    for key in ("skills", "technical_skills", "core_skills", "key_skills"):
        val = resume_data.get(key)
        if isinstance(val, list):
            skills_parts.extend(str(s) for s in val if s)
        elif isinstance(val, str):
            skills_parts.append(val)

    # Profile / summary
    for key in ("summary", "profile", "objective", "about"):
        val = resume_data.get(key)
        if isinstance(val, str) and val.strip():
            description_parts.append(val)

    return {
        "title": " ".join(title_parts).lower(),
        "description": " ".join(description_parts).lower(),
        "skills": " ".join(skills_parts).lower(),
    }


def _weighted_dna_scores(zones: dict[str, str]) -> dict[str, float]:
    """Compute weighted score per DNA type across all zones."""
    # Weights: title × 3, skills × 1.5, description × 1
    weights = {"title": 3.0, "skills": 1.5, "description": 1.0}
    scores: dict[str, float] = {dna: 0.0 for dna in _DNA_SIGNALS}

    for dna, keywords in _DNA_SIGNALS.items():
        for zone_name, zone_text in zones.items():
            w = weights[zone_name]
            hits = sum(1 for kw in keywords if kw in zone_text)
            scores[dna] += hits * w

    return scores


def _dna_confidence(primary_score: float, secondary_score: float) -> str:
    if primary_score == 0:
        return "LOW"
    if secondary_score == 0:
        return "HIGH"
    ratio = primary_score / max(secondary_score, 0.001)
    if ratio >= 2.0:
        return "HIGH"
    if ratio >= 1.3:
        return "MEDIUM"
    return "LOW"


def _dna_fit_label(primary_dna: str, top_role_family: str) -> str:
    preferred = ROLE_DNA_AFFINITY.get(top_role_family)
    if preferred is None:
        return "NEUTRAL"
    if primary_dna == preferred:
        return "ALIGNED"
    # HYBRID is a partial match for most roles
    if primary_dna == "HYBRID" or preferred == "HYBRID":
        return "PARTIAL"
    return "MISALIGNED"


def classify_dna(resume_data: dict, top_role_family: str = "UNKNOWN") -> dict:
    zones = _zone_texts(resume_data)
    raw_scores = _weighted_dna_scores(zones)

    total_score = sum(raw_scores.values())

    # Sort DNA types by score descending
    ranked = sorted(raw_scores.items(), key=lambda x: x[1], reverse=True)
    primary_dna, primary_score = ranked[0]
    secondary_dna, secondary_score = ranked[1] if len(ranked) > 1 else (None, 0.0)

    # If all scores are zero → HYBRID
    if primary_score == 0:
        primary_dna = _HYBRID_FALLBACK
        secondary_dna = None
        dna_confidence = "LOW"
        dna_strength_pct = 0
    else:
        dna_confidence = _dna_confidence(primary_score, secondary_score)
        dna_strength_pct = round((primary_score / total_score) * 100) if total_score > 0 else 0

    # Secondary only if meaningful (> 30% of primary)
    if secondary_score < primary_score * 0.30 or secondary_score == 0:
        secondary_dna = None

    dna_fit = _dna_fit_label(primary_dna, top_role_family)

    # Human-readable reason
    top_signals = [kw for kw in _DNA_SIGNALS.get(primary_dna, []) if kw in (zones["title"] + zones["description"] + zones["skills"])][:3]
    if top_signals:
        dna_reason = f"Primary {primary_dna} signal driven by: {', '.join(top_signals)}."
    else:
        dna_reason = f"No strong directional signal; defaulting to {primary_dna}."

    return {
        "primary_dna": primary_dna,
        "secondary_dna": secondary_dna,
        "dna_confidence": dna_confidence,
        "dna_strength_pct": dna_strength_pct,
        "dna_fit": dna_fit,
        "dna_reason": dna_reason,
        "consulting_score": round(raw_scores["CONSULTING"], 2),
        "product_score": round(raw_scores["PRODUCT"], 2),
        "domain_specialist_score": round(raw_scores["DOMAIN_SPECIALIST"], 2),
        "research_score": round(raw_scores["RESEARCH"], 2),
        "platform_infra_score": round(raw_scores["PLATFORM_INFRA"], 2),
    }

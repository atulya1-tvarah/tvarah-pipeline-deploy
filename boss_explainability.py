def build_boss_explainability(summary: dict) -> dict:
    return {
        "score_dimensions": [
            {
                "dimension": "Skills",
                "what_is_considered": [
                    "skill presence",
                    "evidence strength",
                    "years of usage",
                    "recency",
                    "project type",
                    "architecture vs execution",
                ],
            },
            {
                "dimension": "Experience",
                "what_is_considered": [
                    "career progression",
                    "promotion/progression",
                    "complexity growth",
                    "end-to-end ownership",
                ],
            },
            {
                "dimension": "Business Impact",
                "what_is_considered": [
                    "quantified outcomes",
                    "percentages",
                    "cost savings",
                    "delivery impact",
                ],
            },
            {
                "dimension": "DNA Fit",
                "what_is_considered": [
                    "consulting signals",
                    "product signals",
                    "domain specialization",
                    "stakeholder/client-facing exposure",
                ],
            },
            {
                "dimension": "Post-Interview Validation",
                "what_is_considered": [
                    "claim validation",
                    "answer depth",
                    "architecture clarity",
                    "business articulation",
                ],
            },
        ],
        "evidence_rules": {
            "MENTION": "Skill is listed but no meaningful work evidence is shown.",
            "WEAK": "Some weak contextual mention, but limited proof of application.",
            "APPLIED": "Skill used in actual project/task with concrete implementation signals.",
            "DEEP": "Strong applied evidence with complexity, optimization, or advanced usage.",
            "EXPERT": "Deep ownership, architecture, design, or advanced decision-making evidence.",
        },
        "why_scores_change_after_interview": [
            "weak claims can be validated through strong answers",
            "strong resume claims can be weakened if answers are shallow",
            "architecture and ownership depth are often clearer after verbal validation",
        ],
    }
from __future__ import annotations
from .models import HiringManagerInputs
from .scoring import compute_match


def run_matching_orchestration(resume_json: dict, jd_json: dict, hmi_dict: dict | None = None) -> dict:
    hmi = HiringManagerInputs(**(hmi_dict or {}))
    return compute_match(resume_json, jd_json, hmi)

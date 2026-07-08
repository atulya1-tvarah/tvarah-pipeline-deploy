"""Find and analyse resume PDFs discovered for sourced GitHub candidates."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("resume_intelligence.sourcing.resume_finder")


def analyze_from_pdf_bytes(
    pdf_bytes: bytes,
    candidate_id: str,
    candidate_profile: dict,
) -> dict | None:
    """Run the full Tvarah pipeline on raw PDF bytes.

    Returns the analysis dict on success, None on failure.
    Also persists to candidate_analyses/ via save_candidate_analysis().
    """
    if not pdf_bytes or len(pdf_bytes) < 500:
        return None

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)

        from pdf_to_json_extractor import pdf_to_resume_json
        payload = pdf_to_resume_json(tmp_path)
        if not payload:
            logger.warning("pdf_to_resume_json returned empty for %s", candidate_id)
            return None

        # Inject GitHub sourcing metadata so the analysis is enriched
        payload.setdefault("candidate_overview", {})
        ov = payload["candidate_overview"]
        if not ov.get("name"):
            ov["name"] = candidate_profile.get("display_name") or candidate_id
        if not ov.get("email"):
            ov["email"] = candidate_profile.get("email") or ""
        if not ov.get("location"):
            ov["location"] = candidate_profile.get("location") or ""

        from models import ResumeInput
        from engine import analyze_resume

        resume_input = ResumeInput.from_any(payload)
        result = analyze_resume(resume_input)

        # Tag the analysis with sourcing origin
        result["candidate_id"] = candidate_id
        result["_sourcing"] = {
            "github_username": candidate_profile.get("github_username", ""),
            "github_url": candidate_profile.get("github_url", ""),
            "sourcing_score": candidate_profile.get("sourcing_score"),
            "resume_source": "github_pdf",
        }

        from candidate_analysis_store import save_candidate_analysis
        save_candidate_analysis(candidate_id, result)

        logger.info("Resume analysis complete for %s — score: %s", candidate_id,
                    (result.get("rubric_scorecard") or {}).get("total_score"))
        return result

    except Exception as exc:
        logger.warning("Resume analysis failed for %s: %s", candidate_id, exc)
        return None
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def try_fetch_and_analyze(
    candidate_profile: dict,
) -> dict[str, Any]:
    """Download the resume PDF (if URL known) and run Tvarah analysis.

    Returns a status dict:
      {status: "analyzed"|"pdf_found"|"not_found"|"error",
       candidate_id: str,
       resume_score: float|None,
       band: str|None}
    """
    username = candidate_profile.get("github_username", "")
    candidate_id = f"gh_{username}"
    resume_url = candidate_profile.get("resume_pdf_url") or ""

    if not resume_url:
        return {"status": "not_found", "candidate_id": candidate_id,
                "resume_score": None, "band": None}

    try:
        from sourcing.github_sourcer import download_resume_pdf
        pdf_bytes = download_resume_pdf(resume_url)
    except Exception as exc:
        logger.warning("PDF download failed for %s: %s", username, exc)
        return {"status": "error", "candidate_id": candidate_id,
                "resume_score": None, "band": None}

    if not pdf_bytes:
        return {"status": "pdf_found", "candidate_id": candidate_id,
                "resume_score": None, "band": None}

    analysis = analyze_from_pdf_bytes(pdf_bytes, candidate_id, candidate_profile)
    if not analysis:
        return {"status": "error", "candidate_id": candidate_id,
                "resume_score": None, "band": None}

    rubric = analysis.get("rubric_scorecard") or {}
    stage = rubric.get("stage_scores") or {}
    score = stage.get("resume_score_100") or rubric.get("total_score")
    band = rubric.get("overall_band") or ""

    return {
        "status": "analyzed",
        "candidate_id": candidate_id,
        "resume_score": score,
        "band": band,
    }

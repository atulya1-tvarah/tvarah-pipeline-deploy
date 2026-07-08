
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class StageUpdateInput(BaseModel):
    candidate_id: str
    stage: str  # "recruiter" | "panel"
    stage_overrides: dict[str, Any]
    recruiter_notes: str | None = None


class CandidateSearchQuery(BaseModel):
    role_family: str | None = None
    min_score: int | None = None
    max_score: int | None = None
    skills: list[str] = Field(default_factory=list)
    company_tier_max: int | None = None
    yoe_min: float | None = None
    yoe_max: float | None = None
    dna: str | None = None


class ClientFitRequest(BaseModel):
    analysis: dict[str, Any]
    client_id: str
    role_id: str

class QuestionAnswerInput(BaseModel):
    question: str
    theme: str
    answer_transcript: str
    skill: str = ""
    candidate_context: str = ""
    candidate_id: str | None = None


class CallScoresInput(BaseModel):
    candidate_id: str
    question_scores: list[dict[str, Any]]
    stage: str = "recruiter"
    recruiter_notes: str | None = None


class ResumeInput(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_any(cls, payload: dict[str, Any]) -> "ResumeInput":
        if "data" in payload and isinstance(payload["data"], dict):
            return cls(**payload)
        return cls(data=payload)


class SkillWeight(BaseModel):
    skill: str
    weight: float = 1.0


class JobPostingInput(BaseModel):
    title: str
    yoe_min: float | None = None
    yoe_max: float | None = None
    preferred_dna: str | None = None
    role_family: str | None = None
    mandatory_skills: list[SkillWeight] = Field(default_factory=list)
    nice_to_have_skills: list[SkillWeight] = Field(default_factory=list)
    description: str = ""


class PipelineMoveInput(BaseModel):
    candidate_id: str
    stage: str
    notes: str | None = None


class ResumeFeedback(BaseModel):
    candidate_name: str | None = None
    source_file: str | None = None
    role_family_shown: str | None = None
    recruiter_decision: str | None = None
    recruiter_bucket: str | None = None
    corrected_role_family: str | None = None
    corrected_score: int | None = None
    corrected_band: str | None = None
    strengths_confirmed: list[str] = Field(default_factory=list)
    skills_needing_correction: list[str] = Field(default_factory=list)
    gaps_confirmed: list[str] = Field(default_factory=list)
    call_outcome: str | None = None
    interview_outcome: str | None = None
    joined: bool | None = None
    notes: str | None = None
    raw_analysis: dict[str, Any] = Field(default_factory=dict)

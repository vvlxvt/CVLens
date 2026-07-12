from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from db.resumes_repo import ALLOWED_SECTIONS


class ResumeIn(BaseModel):
    """Shape accepted by POST /resumes/upload — matches parser.py's build_cases() output."""

    resume_id: str

    experience: Optional[str] = None
    skills: Optional[str] = None
    about_me_summary_raw: Optional[str] = None
    # role_position_raw: Optional[str] = None

    full_name: Optional[str] = None
    role_position: Optional[str] = None
    about_summary: Optional[str] = None
    about_llm: Optional[str] = None
    about_prompt_id: Optional[int] = None

    feedback_raw: Optional[str] = None
    feedback_summary: Optional[str] = None
    feedback_sections: Optional[list[str]] = None
    feedback_llm: Optional[str] = None
    feedback_prompt_id: Optional[int] = None

    @field_validator("feedback_sections")
    @classmethod
    def validate_sections(cls, value):
        if value is None:
            return value
        invalid = [s for s in value if s not in ALLOWED_SECTIONS]
        if invalid:
            raise ValueError(
                f"Invalid feedback_sections {invalid}; allowed: {ALLOWED_SECTIONS}"
            )
        return value


class ResumeOut(ResumeIn):
    """Shape returned by GET endpoints — adds DB-generated fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class UploadResult(BaseModel):
    received: int
    saved: int
    saved_ids: list[str]


class DeleteResult(BaseModel):
    deleted: int
    deleted_ids: list[str]


class PaginatedResumes(BaseModel):
    total: int
    skip: int
    limit: int
    items: list[ResumeOut]
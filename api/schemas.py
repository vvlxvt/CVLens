from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from db.resumes_repo import ALLOWED_SECTIONS


class ResumeIn(BaseModel):
    """
    Shape of a single parsed+processed resume row. No longer the upload
    payload (see TelegramExportIn) — this is the base for ResumeOut, i.e.
    what a DB row looks like when returned by GET endpoints.
    """

    resume_id: str

    experience: Optional[str] = None
    skills: Optional[str] = None
    about_me_summary_raw: Optional[str] = None
    role_position_raw: Optional[str] = None

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


class TelegramExportIn(BaseModel):
    """
    Upload payload for POST /resumes/upload — the raw Telegram chat export
    JSON (i.e. the full contents of result.json, unmodified): a list of
    message objects under "messages". Message fields vary a lot depending
    on type (text/photo/document/service message/etc), so they're kept as
    loose dicts here — parser.build_cases() does the actual field-level
    parsing, same as when running parser.py directly from the CLI.
    """

    messages: list[dict]


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
    skipped_ids: list[
        str
    ]  # parsed OK, but no clear feedback (feedback_sections is null)
    saved_pdf_files: list[str] = []  # uploaded PDFs written to extract/data/
    skipped_pdf_files: list[str] = []  # uploaded files that weren't .pdf


class DeleteResult(BaseModel):
    deleted: int
    deleted_ids: list[str]


class ResumeCard(BaseModel):
    """Lightweight shape for the list/card-grid view — just enough to render
    a card. Click-through to the full record uses GET /resumes/{resume_id}
    (ResumeOut, all DB fields)."""

    resume_id: str
    role_position: Optional[str] = None
    feedback_summary: Optional[str] = None
    feedback_sections: Optional[list[str]] = None
    llm: Optional[str] = (
        None  # the model that produced feedback_summary/feedback_sections
    )


class PaginatedResumeCards(BaseModel):
    total: int
    skip: int
    limit: int
    items: list[ResumeCard]


class PromptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    version: str
    system_text: str
    user_template: str
    created_at: datetime


class PromptCreateIn(BaseModel):
    system_text: str = Field(min_length=1)
    user_template: str = Field(min_length=1)


class ParsedCVOut(BaseModel):
    """The uploaded query CV's parsed sections — no feedback fields, since
    this is a brand-new candidate that hasn't been reviewed yet."""

    full_name: Optional[str] = None
    role_position: str = ""
    skills: str = ""
    about_me_summary: str = ""
    experience: str = ""


class SearchMatchOut(BaseModel):
    """One historically-reviewed resume found via vector similarity search."""

    resume_id: str
    score: float
    role_position: str = ""
    skills: str = ""
    about_me_summary: str = ""
    experience: str = ""
    feedback_summary: str = ""
    feedback_raw: str = ""
    feedback_sections: list[str] = []
    llm: str = ""


class SearchResponse(BaseModel):
    parsed_cv: ParsedCVOut
    top_match: Optional[SearchMatchOut] = None
    other_matches: list[SearchMatchOut] = []


class ReindexResult(BaseModel):
    indexed: int

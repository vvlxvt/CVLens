from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Prompt(Base):
    """
    Stores every prompt (system + user template) ever used, versioned.
    A resume's about_/feedback_ fields reference the exact prompt that
    produced them, so results can be traced and selectively re-run when
    a prompt changes.
    """

    __tablename__ = "prompts"

    id = Column(Integer, primary_key=True)
    name = Column(
        String, nullable=False
    )  # e.g. 'intro_extraction', 'feedback_extraction'
    version = Column(String, nullable=False)  # e.g. 'v1', 'v2', '2026-07-07'
    system_text = Column(Text, nullable=False)
    user_template = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_prompts_name_version"),
    )

    def __repr__(self) -> str:
        return f"<Prompt id={self.id} name={self.name!r} version={self.version!r}>"


class Resume(Base):
    """
    One row per CV. Raw parsed sections + LLM-derived fields.

    Two separate (llm, prompt) pairs are tracked because the "about/intro"
    extraction and the "feedback" extraction are independent pipelines that
    may run on different models or prompt versions at different times.
    """

    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True)
    resume_id = Column(
        String, unique=True, nullable=False, index=True
    )  # external/source id (matches Qdrant point id)

    # raw parsed CV sections
    experience = Column(Text)
    skills = Column(Text)
    about_me_summary_raw = Column(Text)  # raw "about me" text as parsed from CV
    # role_position_raw = Column(String)  # raw role/title as parsed from CV

    # LLM-derived: intro/header extraction (full_name, role_position, summary)
    full_name = Column(String)
    role_position = Column(String)
    about_summary = Column(Text)  # LLM-generated 1-2 sentence summary
    about_llm = Column(String, index=True)  # model used for about/intro extraction
    about_prompt_id = Column(Integer, ForeignKey("prompts.id"), index=True)
    about_prompt = relationship("Prompt", foreign_keys=[about_prompt_id])

    # raw recruiter feedback + LLM-derived extraction
    feedback_raw = Column(Text)  # original recruiter feedback text
    feedback_summary = Column(Text)  # LLM-generated 1-2 line summary
    feedback_sections = Column(
        JSON(none_as_null=True)
    )  # list[str] or None, e.g. ["skills", "experience"]
    feedback_llm = Column(String, index=True)  # model used for feedback extraction
    feedback_prompt_id = Column(Integer, ForeignKey("prompts.id"), index=True)
    feedback_prompt = relationship("Prompt", foreign_keys=[feedback_prompt_id])

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_resumes_about_llm_prompt", "about_llm", "about_prompt_id"),
        Index("idx_resumes_feedback_llm_prompt", "feedback_llm", "feedback_prompt_id"),
    )

    def __repr__(self) -> str:
        return f"<Resume id={self.id} resume_id={self.resume_id!r}>"


class CVReview(Base):
    """
    Stores generated reviews for uploaded CVs.

    This is the observability and feedback-loop layer for the review prompt:
    every run keeps the parsed CV, retrieved examples, generated review, and
    later the user's rating/comment for quality comparison across prompt
    versions and retrieval settings.
    """

    __tablename__ = "cv_reviews"

    id = Column(Integer, primary_key=True)
    uploaded_filename = Column(String)
    parsed_cv = Column(JSON(none_as_null=True), nullable=False)
    similar_examples = Column(JSON(none_as_null=True), nullable=False)
    review = Column(JSON(none_as_null=True), nullable=False)
    llm = Column(String, index=True)
    user_rating = Column(String, index=True)
    user_comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<CVReview id={self.id} uploaded_filename={self.uploaded_filename!r}>"

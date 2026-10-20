from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamp - SQLite's DateTime column stores no tz info."""
    return datetime.now(UTC).replace(tzinfo=None)


ALLOWED_SECTIONS = ("role_position", "skills", "about_me_summary", "experience", "formatting")


class Prompt(Base):
    """
    Stores every prompt (system + user template) ever used, versioned.
    A resume's about_/feedback_ fields reference the exact prompt that
    produced them, so results can be traced and selectively re-run when
    a prompt changes.
    """

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # e.g. 'intro_extraction', 'feedback_extraction'
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)  # e.g. 'v1', 'v2'
    system_text: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # external/source id (matches Qdrant point id)
    resume_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)

    # raw parsed CV sections
    experience: Mapped[str | None] = mapped_column(Text)
    skills: Mapped[str | None] = mapped_column(Text)
    # raw "about me" text as parsed from CV
    about_me_summary_raw: Mapped[str | None] = mapped_column(Text)

    # LLM-derived: intro/header extraction (full_name, role_position, summary)
    full_name: Mapped[str | None] = mapped_column(String)
    role_position: Mapped[str | None] = mapped_column(String)
    # LLM-generated 1-2 sentence summary
    about_summary: Mapped[str | None] = mapped_column(Text)
    # model used for about/intro extraction
    about_llm: Mapped[str | None] = mapped_column(String, index=True)
    about_prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.id"), index=True)
    about_prompt: Mapped["Prompt | None"] = relationship("Prompt", foreign_keys=[about_prompt_id])

    # raw recruiter feedback + LLM-derived extraction
    feedback_raw: Mapped[str | None] = mapped_column(Text)  # original recruiter feedback text
    feedback_summary: Mapped[str | None] = mapped_column(Text)  # LLM-generated 1-2 line summary
    # list[str] or None, e.g. ["skills", "experience"]
    feedback_sections: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    # model used for feedback extraction
    feedback_llm: Mapped[str | None] = mapped_column(String, index=True)
    feedback_prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.id"), index=True)
    feedback_prompt: Mapped["Prompt | None"] = relationship(
        "Prompt", foreign_keys=[feedback_prompt_id]
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uploaded_filename: Mapped[str | None] = mapped_column(String)
    parsed_cv: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    similar_examples: Mapped[list] = mapped_column(JSON(none_as_null=True), nullable=False)
    review: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    llm: Mapped[str | None] = mapped_column(String, index=True)
    user_rating: Mapped[str | None] = mapped_column(String, index=True)
    user_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )

    def __repr__(self) -> str:
        return f"<CVReview id={self.id} uploaded_filename={self.uploaded_filename!r}>"


class CVReviewRuleSet(Base):
    """
    Versioned auto-learned rules for the CV review prompt.

    Each row is a snapshot of the active rule set generated from accumulated
    cv_reviews feedback. The base review prompt can later include the latest
    active snapshot without losing the history of how rules changed.
    """

    __tablename__ = "cv_review_rule_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    rules: Mapped[list] = mapped_column(JSON(none_as_null=True), nullable=False)
    source_review_ids: Mapped[list] = mapped_column(JSON(none_as_null=True), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )

    def __repr__(self) -> str:
        return f"<CVReviewRuleSet id={self.id} version={self.version!r}>"


class CVReviewRuleDiff(Base):
    """
    Git-like diff between two rule set versions.

    Stores added/changed/removed rule groups so the UI can show prompt
    evolution as a concise history rather than a black-box replacement.
    """

    __tablename__ = "cv_review_rule_diffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_rule_set_id: Mapped[int | None] = mapped_column(ForeignKey("cv_review_rule_sets.id"))
    to_rule_set_id: Mapped[int] = mapped_column(
        ForeignKey("cv_review_rule_sets.id"),
        nullable=False,
        index=True,
    )
    added_rules: Mapped[list] = mapped_column(JSON(none_as_null=True), nullable=False)
    changed_rules: Mapped[list] = mapped_column(JSON(none_as_null=True), nullable=False)
    removed_rules: Mapped[list] = mapped_column(JSON(none_as_null=True), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )

    from_rule_set: Mapped["CVReviewRuleSet | None"] = relationship(
        "CVReviewRuleSet",
        foreign_keys=[from_rule_set_id],
    )
    to_rule_set: Mapped["CVReviewRuleSet"] = relationship(
        "CVReviewRuleSet",
        foreign_keys=[to_rule_set_id],
    )

    def __repr__(self) -> str:
        return (
            f"<CVReviewRuleDiff id={self.id} "
            f"from={self.from_rule_set_id!r} to={self.to_rule_set_id!r}>"
        )
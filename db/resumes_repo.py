from sqlalchemy import exists, func, literal_column, or_, select
from sqlalchemy.orm import Session

from db.models import ALLOWED_SECTIONS, Resume

__all__ = ["ALLOWED_SECTIONS"]


def _clean_sections(sections: list[str] | None) -> list[str] | None:
    if not sections:
        return None
    filtered = [s for s in sections if s in ALLOWED_SECTIONS]
    return filtered or None


def create(session: Session, data: dict) -> int:
    """Insert a new resume row. Returns the new row's id."""
    data = dict(data)
    data["feedback_sections"] = _clean_sections(data.get("feedback_sections"))

    resume = Resume(**data)
    session.add(resume)
    session.flush()  # populates resume.id before commit
    return resume.id


def upsert(session: Session, data: dict) -> int:
    """
    Insert a new resume row, or update an existing one (matched by resume_id)
    with whatever fields are provided. Use this instead of create() when the
    same source data (e.g. a parser re-run) might already be in the DB.
    """
    data = dict(data)
    if "feedback_sections" in data:
        data["feedback_sections"] = _clean_sections(data.get("feedback_sections"))

    resume = session.scalars(
        select(Resume).where(Resume.resume_id == data["resume_id"])
    ).first()
    if resume:
        for key, value in data.items():
            setattr(resume, key, value)
        session.flush()
        return resume.id

    resume = Resume(**data)
    session.add(resume)
    session.flush()
    return resume.id


def get_by_resume_id(session: Session, resume_id: str) -> Resume | None:
    resume = session.scalars(
        select(Resume).where(Resume.resume_id == resume_id)
    ).first()
    if resume:
        session.expunge(resume)  # detach so it's usable after session closes
    return resume


def update_feedback(
    session: Session,
    resume_id: str,
    feedback_summary: str | None,
    feedback_sections: list[str] | None,
    llm: str,
    prompt_id: int,
) -> bool:
    """Returns True if a row was updated, False if resume_id not found."""
    resume = session.scalars(
        select(Resume).where(Resume.resume_id == resume_id)
    ).first()
    if not resume:
        return False
    resume.feedback_summary = feedback_summary
    resume.feedback_sections = _clean_sections(feedback_sections)
    resume.feedback_llm = llm
    resume.feedback_prompt_id = prompt_id
    return True


def update_about(
    session: Session,
    resume_id: str,
    full_name: str | None,
    role_position: str | None,
    about_summary: str | None,
    llm: str,
    prompt_id: int,
) -> bool:
    resume = session.scalars(
        select(Resume).where(Resume.resume_id == resume_id)
    ).first()
    if not resume:
        return False
    resume.full_name = full_name
    resume.role_position = role_position
    resume.about_summary = about_summary
    resume.about_llm = llm
    resume.about_prompt_id = prompt_id
    return True


def get_stale_feedback(
    session: Session, current_llm: str, current_prompt_id: int
) -> list[Resume]:
    """Resumes whose feedback extraction used a different model or prompt
    version — or never ran at all (NULL columns count as stale too)."""
    rows = session.scalars(
        select(Resume).where(
            or_(
                Resume.feedback_llm.is_(None),
                Resume.feedback_llm != current_llm,
                Resume.feedback_prompt_id.is_(None),
                Resume.feedback_prompt_id != current_prompt_id,
            )
        )
    ).all()
    session.expunge_all()
    return list(rows)


def get_stale_about(
    session: Session, current_llm: str, current_prompt_id: int
) -> list[Resume]:
    """Resumes whose intro/about extraction used a different model or prompt
    version — or never ran at all (NULL columns count as stale too)."""
    rows = session.scalars(
        select(Resume).where(
            or_(
                Resume.about_llm.is_(None),
                Resume.about_llm != current_llm,
                Resume.about_prompt_id.is_(None),
                Resume.about_prompt_id != current_prompt_id,
            )
        )
    ).all()
    session.expunge_all()
    return list(rows)


def list_by_feedback_section(session: Session, section: str) -> list[Resume]:
    """Resumes where feedback_sections (JSON array) contains the given section.

    Uses SQLite JSON1 json_each() to filter at the database level instead of
    loading all rows into Python. This scales to large tables.
    """
    je = func.json_each(Resume.feedback_sections).alias("je")
    subq = exists(select(1).select_from(je).where(literal_column("value") == section))
    rows = session.scalars(
        select(Resume).where(
            Resume.feedback_sections.isnot(None),
            subq,
        )
    ).all()
    session.expunge_all()
    return list(rows)


def list_all(session: Session) -> list[Resume]:
    """All resumes in the DB. Used by the Qdrant (re)indexing pipeline."""
    rows = session.scalars(select(Resume)).all()
    session.expunge_all()
    return list(rows)


def list_feedback_llms(session: Session) -> list[str]:
    """Distinct LLM names used by feedback parsing."""
    values = session.scalars(select(Resume.feedback_llm).distinct()).all()
    return sorted(value for value in values if value)


def list_paginated(
    session: Session,
    skip: int = 0,
    limit: int = 50,
    llm: str | None = None,
    has_feedback: bool | None = None,
    section: str | None = None,
) -> tuple[list[Resume], int]:
    """
    Filtered/paginated listing for the API.

    When `section` is given, filtering uses SQLite JSON1 json_each() at the
    database level, so pagination and count are efficient without loading
    the whole table into Python.
    """
    stmt = select(Resume)
    if llm:
        stmt = stmt.where(Resume.feedback_llm == llm)
    if has_feedback is True:
        stmt = stmt.where(Resume.feedback_sections.isnot(None))
    elif has_feedback is False:
        stmt = stmt.where(Resume.feedback_sections.is_(None))

    if section:
        je = func.json_each(Resume.feedback_sections).alias("je")
        subq = exists(select(1).select_from(je).where(literal_column("value") == section))
        stmt = stmt.where(
            Resume.feedback_sections.isnot(None),
            subq,
        )

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(Resume.id).offset(skip).limit(limit)).all()
    session.expunge_all()
    return list(rows), total


def delete(session: Session, resume_id: str) -> bool:
    resume = session.scalars(
        select(Resume).where(Resume.resume_id == resume_id)
    ).first()
    if not resume:
        return False
    session.delete(resume)
    return True

from db.connection import get_session
from db.models import Resume

ALLOWED_SECTIONS = ("role_position", "skills", "about_me_summary", "experience", "formatting")


def _clean_sections(sections: list | None) -> list | None:
    if not sections:
        return None
    filtered = [s for s in sections if s in ALLOWED_SECTIONS]
    return filtered or None


def create(data: dict) -> int:
    """Insert a new resume row. Returns the new row's id."""
    data = dict(data)
    data["feedback_sections"] = _clean_sections(data.get("feedback_sections"))

    with get_session() as session:
        resume = Resume(**data)
        session.add(resume)
        session.flush()  # populates resume.id before commit
        return resume.id


def upsert(data: dict) -> int:
    """
    Insert a new resume row, or update an existing one (matched by resume_id)
    with whatever fields are provided. Use this instead of create() when the
    same source data (e.g. a parser re-run) might already be in the DB.
    """
    data = dict(data)
    if "feedback_sections" in data:
        data["feedback_sections"] = _clean_sections(data.get("feedback_sections"))

    with get_session() as session:
        resume = (
            session.query(Resume)
            .filter(Resume.resume_id == data["resume_id"])
            .first()
        )
        if resume:
            for key, value in data.items():
                setattr(resume, key, value)
            session.flush()
            return resume.id

        resume = Resume(**data)
        session.add(resume)
        session.flush()
        return resume.id


def get_by_resume_id(resume_id: str) -> Resume | None:
    with get_session() as session:
        resume = session.query(Resume).filter(Resume.resume_id == resume_id).first()
        if resume:
            session.expunge(resume)  # detach so it's usable after session closes
        return resume


def update_feedback(
    resume_id: str,
    feedback_summary: str,
    feedback_sections: list | None,
    llm: str,
    prompt_id: int,
) -> bool:
    """Returns True if a row was updated, False if resume_id not found."""
    with get_session() as session:
        resume = session.query(Resume).filter(Resume.resume_id == resume_id).first()
        if not resume:
            return False
        resume.feedback_summary = feedback_summary
        resume.feedback_sections = _clean_sections(feedback_sections)
        resume.feedback_llm = llm
        resume.feedback_prompt_id = prompt_id
        return True


def update_about(
    resume_id: str,
    full_name: str | None,
    role_position: str | None,
    about_summary: str | None,
    llm: str,
    prompt_id: int,
) -> bool:
    with get_session() as session:
        resume = session.query(Resume).filter(Resume.resume_id == resume_id).first()
        if not resume:
            return False
        resume.full_name = full_name
        resume.role_position = role_position
        resume.about_summary = about_summary
        resume.about_llm = llm
        resume.about_prompt_id = prompt_id
        return True


def get_stale_feedback(current_llm: str, current_prompt_id: int) -> list[Resume]:
    """Resumes whose feedback extraction used a different model or prompt version."""
    with get_session() as session:
        rows = (
            session.query(Resume)
            .filter(
                (Resume.feedback_llm != current_llm)
                | (Resume.feedback_prompt_id != current_prompt_id)
            )
            .all()
        )
        session.expunge_all()
        return rows


def get_stale_about(current_llm: str, current_prompt_id: int) -> list[Resume]:
    """Resumes whose intro/about extraction used a different model or prompt version."""
    with get_session() as session:
        rows = (
            session.query(Resume)
            .filter(
                (Resume.about_llm != current_llm)
                | (Resume.about_prompt_id != current_prompt_id)
            )
            .all()
        )
        session.expunge_all()
        return rows


def list_by_feedback_section(section: str) -> list[Resume]:
    """Resumes where feedback_sections (JSON array) contains the given section."""
    with get_session() as session:
        # SQLite JSON column comes back as a Python list, so filter in Python
        # after a coarse SQL LIKE prefilter (avoids loading rows with no JSON at all).
        rows = (
            session.query(Resume)
            .filter(Resume.feedback_sections.isnot(None))
            .all()
        )
        session.expunge_all()
        return [r for r in rows if r.feedback_sections and section in r.feedback_sections]


def list_all() -> list[Resume]:
    """All resumes in the DB. Used by the Qdrant (re)indexing pipeline."""
    with get_session() as session:
        rows = session.query(Resume).all()
        session.expunge_all()
        return rows


def list_llms() -> list[str]:
    """Distinct LLM names used by either CV parsing or feedback parsing."""
    with get_session() as session:
        values = set()
        for (value,) in session.query(Resume.about_llm).distinct():
            if value:
                values.add(value)
        for (value,) in session.query(Resume.feedback_llm).distinct():
            if value:
                values.add(value)
        return sorted(values)


def list_paginated(
    skip: int = 0,
    limit: int = 50,
    llm: str | None = None,
    has_feedback: bool | None = None,
    section: str | None = None,
) -> tuple[list[Resume], int]:
    """
    Filtered/paginated listing for the API.

    Note: when `section` is given, filtering happens in Python (SQLite has
    no portable ORM-level "JSON array contains" query), so the whole
    matching set is loaded before slicing. Fine for realistic CV volumes;
    revisit (e.g. a join table) if this table grows into the hundreds of
    thousands of rows.
    """
    with get_session() as session:
        query = session.query(Resume)
        if llm:
            query = query.filter(
                (Resume.about_llm == llm) | (Resume.feedback_llm == llm)
            )
        if has_feedback is True:
            query = query.filter(Resume.feedback_sections.isnot(None))
        elif has_feedback is False:
            query = query.filter(Resume.feedback_sections.is_(None))

        if section:
            rows = query.order_by(Resume.id).all()
            session.expunge_all()
            matching = [
                r for r in rows if r.feedback_sections and section in r.feedback_sections
            ]
            total = len(matching)
            return matching[skip : skip + limit], total

        total = query.count()
        rows = query.order_by(Resume.id).offset(skip).limit(limit).all()
        session.expunge_all()
        return rows, total


def delete(resume_id: str) -> bool:
    with get_session() as session:
        resume = session.query(Resume).filter(Resume.resume_id == resume_id).first()
        if not resume:
            return False
        session.delete(resume)
        return True

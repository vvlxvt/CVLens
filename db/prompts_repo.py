from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Prompt


def create(
    session: Session, name: str, version: str, system_text: str, user_template: str
) -> int:
    prompt = Prompt(
        name=name,
        version=version,
        system_text=system_text,
        user_template=user_template,
    )
    session.add(prompt)
    session.flush()
    return prompt.id


def get_or_create(
    session: Session, name: str, version: str, system_text: str, user_template: str
) -> int:
    """Idempotent: reuses the existing row for (name, version) if present."""
    prompt = session.scalars(
        select(Prompt).where(Prompt.name == name, Prompt.version == version)
    ).first()
    if prompt:
        return prompt.id
    return create(session, name, version, system_text, user_template)


def get_latest(session: Session, name: str) -> Prompt | None:
    prompt = session.scalars(
        select(Prompt).where(Prompt.name == name).order_by(Prompt.created_at.desc())
    ).first()
    if prompt:
        session.expunge(prompt)  # detach so it's usable after the session closes
    return prompt


def list_all(session: Session, name: str) -> list[Prompt]:
    """All versions for `name`, oldest first — used by the prompt-editing
    web form's prev/next version navigation."""
    rows = session.scalars(
        select(Prompt).where(Prompt.name == name).order_by(Prompt.id.asc())
    ).all()
    session.expunge_all()
    return list(rows)


def create_next_version(
    session: Session, name: str, system_text: str, user_template: str
) -> Prompt:
    """
    Saves a new prompt version for `name`, auto-numbered as one past the
    current highest version for that name (v1, v2, v3, ...). Used by the
    prompt-editing web form: every save is a new row (full history kept),
    never an overwrite of an existing one.
    """
    existing_versions = session.scalars(
        select(Prompt.version).where(Prompt.name == name)
    ).all()
    numbers = [
        int(version[1:])
        for version in existing_versions
        if version.startswith("v") and version[1:].isdigit()
    ]
    next_version = f"v{max(numbers) + 1}" if numbers else "v1"

    prompt = Prompt(
        name=name,
        version=next_version,
        system_text=system_text,
        user_template=user_template,
    )
    session.add(prompt)
    session.flush()
    session.refresh(prompt)
    session.expunge(prompt)
    return prompt
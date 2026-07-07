from typing import Optional

from db.connection import get_session
from db.models import Prompt


def create(name: str, version: str, system_text: str, user_template: str) -> int:
    with get_session() as session:
        prompt = Prompt(
            name=name,
            version=version,
            system_text=system_text,
            user_template=user_template,
        )
        session.add(prompt)
        session.flush()
        return prompt.id


def get_or_create(name: str, version: str, system_text: str, user_template: str) -> int:
    """Idempotent: reuses the existing row for (name, version) if present."""
    with get_session() as session:
        prompt = (
            session.query(Prompt)
            .filter(Prompt.name == name, Prompt.version == version)
            .first()
        )
        if prompt:
            return prompt.id
        prompt = Prompt(
            name=name,
            version=version,
            system_text=system_text,
            user_template=user_template,
        )
        session.add(prompt)
        session.flush()
        return prompt.id


def get_latest(name: str) -> Optional[Prompt]:
    with get_session() as session:
        prompt = (
            session.query(Prompt)
            .filter(Prompt.name == name)
            .order_by(Prompt.created_at.desc())
            .first()
        )
        if prompt:
            session.expunge(prompt)
        return prompt

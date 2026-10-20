"""FastAPI-level dependencies: DB session per request and Bearer-token auth."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from core.config import settings
from db.connection import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """
    One SQLAlchemy session per request: commit on success, rollback on
    error, always close — the canonical FastAPI + sync SQLAlchemy pattern.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


async def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    """Checks `Authorization: Bearer <API_TOKEN>` on data routes.

    Disabled when API_TOKEN is empty (pure-local usage).
    """
    if not settings.api_token:
        return
    if authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=401, detail="Missing or invalid API token")

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import CVReview


def create(
    session: Session,
    *,
    uploaded_filename: str | None,
    parsed_cv: dict[str, Any],
    similar_examples: list[dict[str, Any]],
    review: dict[str, Any],
    llm: str,
) -> int:
    """Persist one generated CV review and return its DB id."""
    row = CVReview(
        uploaded_filename=uploaded_filename,
        parsed_cv=parsed_cv,
        similar_examples=similar_examples,
        review=review,
        llm=llm,
    )
    session.add(row)
    session.flush()
    return row.id


def add_feedback(
    session: Session, review_id: int, user_rating: str, user_comment: str | None
) -> bool:
    """Attach user/HR feedback to a generated review."""
    row = session.get(CVReview, review_id)
    if row is None:
        return False
    row.user_rating = user_rating
    row.user_comment = user_comment
    return True


def list_with_user_feedback(session: Session, limit: int = 50) -> list[CVReview]:
    """Reviews that can teach the prompt: rating plus a written comment."""
    rows = session.scalars(
        select(CVReview)
        .where(CVReview.user_rating.isnot(None))
        .where(CVReview.user_comment.isnot(None))
        .order_by(CVReview.id.desc())
        .limit(limit)
    ).all()
    session.expunge_all()
    return list(rows)

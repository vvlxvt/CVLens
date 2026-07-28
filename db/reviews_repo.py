from typing import Any

from db.connection import get_session
from db.models import CVReview


def create(
    *,
    uploaded_filename: str | None,
    parsed_cv: dict[str, Any],
    similar_examples: list[dict[str, Any]],
    review: dict[str, Any],
    llm: str,
) -> int:
    """Persist one generated CV review and return its DB id."""
    with get_session() as session:
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


def add_feedback(review_id: int, user_rating: str, user_comment: str | None) -> bool:
    """Attach user/HR feedback to a generated review."""
    with get_session() as session:
        row = session.get(CVReview, review_id)
        if row is None:
            return False
        row.user_rating = user_rating
        row.user_comment = user_comment
        return True

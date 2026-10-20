from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import CVReviewRuleDiff, CVReviewRuleSet


def _version_number(version: str) -> int | None:
    if version.startswith("v") and version[1:].isdigit():
        return int(version[1:])
    return None


def _next_version(existing_versions: list[str]) -> str:
    numbers = [
        number
        for version in existing_versions
        if (number := _version_number(version)) is not None
    ]
    return f"v{max(numbers) + 1}" if numbers else "v1"


def get_latest_active(session: Session) -> CVReviewRuleSet | None:
    row = session.scalars(
        select(CVReviewRuleSet)
        .where(CVReviewRuleSet.status == "active")
        .order_by(CVReviewRuleSet.id.desc())
    ).first()
    if row:
        session.expunge(row)
    return row


def list_rule_sets(session: Session, limit: int = 20) -> list[CVReviewRuleSet]:
    rows = session.scalars(
        select(CVReviewRuleSet).order_by(CVReviewRuleSet.id.desc()).limit(limit)
    ).all()
    session.expunge_all()
    return list(rows)


def list_diffs(session: Session, limit: int = 20) -> list[CVReviewRuleDiff]:
    _ensure_initial_diff(session)
    rows = session.scalars(
        select(CVReviewRuleDiff).order_by(CVReviewRuleDiff.id.desc()).limit(limit)
    ).all()
    session.expunge_all()
    return list(rows)


def _ensure_initial_diff(session: Session) -> None:
    has_diff = session.scalar(select(CVReviewRuleDiff.id).limit(1))
    if has_diff:
        return

    first_rule_set = session.scalars(
        select(CVReviewRuleSet).order_by(CVReviewRuleSet.id.asc())
    ).first()
    if first_rule_set is None:
        return

    session.add(
        CVReviewRuleDiff(
            from_rule_set_id=None,
            to_rule_set_id=first_rule_set.id,
            added_rules=first_rule_set.rules or [],
            changed_rules=[],
            removed_rules=[],
            summary=first_rule_set.summary or "Initial rule set",
        )
    )
    session.flush()


def create_next_rule_set(
    session: Session,
    *,
    rules: list[dict[str, Any]],
    source_review_ids: list[int],
    added_rules: list[dict[str, Any]] | None = None,
    changed_rules: list[dict[str, Any]] | None = None,
    removed_rules: list[dict[str, Any]] | None = None,
    summary: str | None = None,
    diff_summary: str | None = None,
) -> CVReviewRuleSet:
    existing_versions = list(
        session.scalars(select(CVReviewRuleSet.version)).all()
    )
    previous = session.scalars(
        select(CVReviewRuleSet)
        .where(CVReviewRuleSet.status == "active")
        .order_by(CVReviewRuleSet.id.desc())
    ).first()

    row = CVReviewRuleSet(
        version=_next_version(existing_versions),
        rules=rules,
        source_review_ids=source_review_ids,
        summary=summary,
        status="active",
    )
    session.add(row)
    session.flush()

    diff = CVReviewRuleDiff(
        from_rule_set_id=previous.id if previous else None,
        to_rule_set_id=row.id,
        added_rules=added_rules or [],
        changed_rules=changed_rules or [],
        removed_rules=removed_rules or [],
        summary=diff_summary,
    )
    session.add(diff)
    session.flush()
    session.refresh(row)
    session.expunge(row)
    return row

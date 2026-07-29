from typing import Any

from db.connection import get_session
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


def get_latest_active() -> CVReviewRuleSet | None:
    with get_session() as session:
        row = (
            session.query(CVReviewRuleSet)
            .filter(CVReviewRuleSet.status == "active")
            .order_by(CVReviewRuleSet.id.desc())
            .first()
        )
        if row:
            session.expunge(row)
        return row


def list_rule_sets(limit: int = 20) -> list[CVReviewRuleSet]:
    with get_session() as session:
        rows = (
            session.query(CVReviewRuleSet)
            .order_by(CVReviewRuleSet.id.desc())
            .limit(limit)
            .all()
        )
        session.expunge_all()
        return rows


def list_diffs(limit: int = 20) -> list[CVReviewRuleDiff]:
    with get_session() as session:
        rows = (
            session.query(CVReviewRuleDiff)
            .order_by(CVReviewRuleDiff.id.desc())
            .limit(limit)
            .all()
        )
        session.expunge_all()
        return rows


def create_next_rule_set(
    *,
    rules: list[dict[str, Any]],
    source_review_ids: list[int],
    added_rules: list[dict[str, Any]] | None = None,
    changed_rules: list[dict[str, Any]] | None = None,
    removed_rules: list[dict[str, Any]] | None = None,
    summary: str | None = None,
    diff_summary: str | None = None,
) -> CVReviewRuleSet:
    with get_session() as session:
        existing_versions = [row.version for row in session.query(CVReviewRuleSet).all()]
        previous = (
            session.query(CVReviewRuleSet)
            .filter(CVReviewRuleSet.status == "active")
            .order_by(CVReviewRuleSet.id.desc())
            .first()
        )

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

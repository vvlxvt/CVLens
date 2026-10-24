"""Versioned auto-learned review rules (build + browse)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from openai import OpenAIError

from api.deps import DbSession
from api.reviewing import (
    _build_rule_rebuild_prompt,
    _llm_provider_error_detail,
    _normalize_rule_payload,
)
from core.rate_limit import llm_rate_limiter, read_rate_limiter
from api.schemas import (
    ReviewRuleDiffOut,
    ReviewRuleGeneration,
    ReviewRuleRebuildIn,
    ReviewRuleRebuildResult,
    ReviewRuleSetOut,
)
from db import review_rules_repo, reviews_repo
from response import generate_response

router = APIRouter(tags=["review-rules"])


@router.post(
    "/review-rules/rebuild",
    response_model=ReviewRuleRebuildResult,
    dependencies=[Depends(llm_rate_limiter.check)],
)
def rebuild_review_rules(session: DbSession, body: ReviewRuleRebuildIn | None = None):
    options = body or ReviewRuleRebuildIn()
    reviewed_cases = reviews_repo.list_with_user_feedback(session, limit=options.limit)
    if not reviewed_cases:
        raise HTTPException(
            status_code=400,
            detail="No cv_reviews with user_rating and user_comment found",
        )

    current = review_rules_repo.get_latest_active(session)
    current_rules = current.rules if current else []
    source_review_ids = [row.id for row in reviewed_cases]
    system, prompt = _build_rule_rebuild_prompt(current_rules, reviewed_cases)

    try:
        raw_rules, applied_model = generate_response(
            prompt,
            json_mode=True,
            system=system,
        )
        generated = ReviewRuleGeneration.model_validate(raw_rules)
        generated = _normalize_rule_payload(generated, source_review_ids)
    except OpenAIError as e:
        raise HTTPException(
            status_code=502,
            detail=_llm_provider_error_detail(e),
        ) from e
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM returned an invalid rule payload: {e}",
        ) from e

    if not generated.rules:
        raise HTTPException(
            status_code=502,
            detail="LLM returned an empty rule set; nothing was saved",
        )

    rule_set = review_rules_repo.create_next_rule_set(
        session,
        rules=generated.rules,
        source_review_ids=source_review_ids,
        added_rules=generated.added_rules,
        changed_rules=generated.changed_rules,
        removed_rules=generated.removed_rules,
        summary=generated.summary,
        diff_summary=generated.diff_summary,
    )
    return ReviewRuleRebuildResult(
        rule_set=rule_set,
        used_review_count=len(reviewed_cases),
        llm=applied_model,
    )


@router.get(
    "/review-rules/latest",
    response_model=ReviewRuleSetOut | None,
    dependencies=[Depends(read_rate_limiter.check)],
)
def get_latest_review_rule_set(session: DbSession):
    return review_rules_repo.get_latest_active(session)


@router.get(
    "/review-rules",
    response_model=list[ReviewRuleSetOut],
    dependencies=[Depends(read_rate_limiter.check)],
)
def list_review_rule_sets(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return review_rules_repo.list_rule_sets(session, limit=limit)


@router.get(
    "/review-rules/diffs",
    response_model=list[ReviewRuleDiffOut],
    dependencies=[Depends(read_rate_limiter.check)],
)
def list_review_rule_diffs(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return review_rules_repo.list_diffs(session, limit=limit)

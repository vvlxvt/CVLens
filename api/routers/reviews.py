"""CV review endpoint (few-shot RAG review) and review feedback."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from openai import OpenAIError

import vector_search
from api.deps import DbSession
from api.reviewing import (
    _build_review_prompt,
    _generate_structured_review,
    _llm_provider_error_detail,
    _match_from_point,
    _parse_uploaded_query_cv,
)
from api.schemas import (
    ParsedCVOut,
    ReviewFeedbackIn,
    ReviewFeedbackResult,
    ReviewResponse,
    SimilarReviewExample,
)
from db import review_rules_repo, reviews_repo

router = APIRouter(tags=["reviews"])


@router.post("/resumes/review", response_model=ReviewResponse)
def review_resume(
    session: DbSession,
    file: Annotated[
        UploadFile,
        File(description="Query CV (PDF) to review with similar historical examples"),
    ],
    limit: Annotated[
        int,
        Form(description="Number of similar reviewed CVs to use as few-shot examples"),
    ] = 5,
    skills: Annotated[
        str,
        Form(description='Optional comma-separated skills filter, e.g. "python,django"'),
    ] = "",
    language: Annotated[str, Form(description='Review language: "ru" or "en"')] = "ru",
):
    """
    Reviews a new CV using retrieval-augmented few-shot learning:
    parse uploaded PDF, retrieve similar historically reviewed CVs, pass
    their CV+feedback pairs as examples to the LLM, and return a structured
    review. This endpoint saves the generated review (without user feedback)
    to the DB so it can later be rated and used for rule learning.
    """
    language = language.lower().strip()
    if language not in {"ru", "en"}:
        raise HTTPException(status_code=400, detail='language must be "ru" or "en"')

    content = file.file.read()
    query_case = _parse_uploaded_query_cv(file, content)

    example_limit = max(1, min(limit, 10))
    skills_list = [s.strip() for s in skills.split(",") if s.strip()] or None
    points = vector_search.search_similar(
        query_case,
        limit=example_limit,
        skills=skills_list,
    )
    examples = [
        SimilarReviewExample(**match.model_dump())
        for point in points
        if (match := _match_from_point(session, point))
    ]

    active_rule_set = review_rules_repo.get_latest_active(session)
    system, prompt = _build_review_prompt(
        query_case,
        examples,
        language=language,
        active_rules=active_rule_set.rules if active_rule_set else None,
    )
    try:
        review, applied_model = _generate_structured_review(prompt, system)
    except OpenAIError as e:
        raise HTTPException(
            status_code=502,
            detail=_llm_provider_error_detail(e),
        ) from e
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=502,
            detail=str(e)
            if "Ollama" in str(e)
            else f"LLM returned an invalid review payload: {e}",
        ) from e

    review_id = reviews_repo.create(
        session,
        uploaded_filename=Path(file.filename or "uploaded_cv.pdf").name,
        parsed_cv=ParsedCVOut(**query_case).model_dump(),
        similar_examples=[example.model_dump() for example in examples],
        review=review.model_dump(),
        llm=applied_model,
    )

    return ReviewResponse(
        review_id=review_id,
        parsed_cv=ParsedCVOut(**query_case),
        examples=examples,
        review=review,
        llm=applied_model,
    )


@router.post("/reviews/{review_id}/feedback", response_model=ReviewFeedbackResult)
def add_review_feedback(
    session: DbSession, review_id: int, body: ReviewFeedbackIn
):
    updated = reviews_repo.add_feedback(
        session,
        review_id=review_id,
        user_rating=body.rating,
        user_comment=body.comment,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Review {review_id!r} not found")
    return ReviewFeedbackResult(updated=True)

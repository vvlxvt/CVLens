"""Prompt version management (intro_extraction / feedback_extraction)."""

from fastapi import APIRouter, HTTPException

from api.deps import DbSession
from api.schemas import PromptCreateIn, PromptOut
from db import prompts_repo

router = APIRouter(tags=["prompts"])

# "intro_extraction" (about) and "feedback_extraction" (feedback) are the
# only two prompt names the pipeline actually uses — response.py fetches
# whichever is latest at call time. Every save here creates a NEW version
# (v1, v2, ...) rather than overwriting, so prompt history is never lost
# and DB rows already processed with an older version stay traceable via
# their about_prompt_id/feedback_prompt_id.

PROMPT_NAMES = {"intro_extraction", "feedback_extraction"}

# Each prompt's user_template is filled via str.format() with exactly this
# placeholder — validated at save time so a typo (e.g. {into_text}) is
# caught immediately instead of blowing up mid-batch later.
PROMPT_PLACEHOLDER = {
    "intro_extraction": "intro_text",
    "feedback_extraction": "feedback_text",
}


def _check_known_prompt_name(name: str):
    if name not in PROMPT_NAMES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown prompt name {name!r}. Expected one of {sorted(PROMPT_NAMES)}",
        )


@router.get("/prompts/{name}", response_model=list[PromptOut])
def list_prompt_versions(session: DbSession, name: str):
    """All saved versions for `name`, oldest first — for the web form's
    version history navigation (prev/next through past prompts)."""
    _check_known_prompt_name(name)
    return prompts_repo.list_all(session, name)


@router.get("/prompts/{name}/latest", response_model=PromptOut)
def get_latest_prompt(session: DbSession, name: str):
    _check_known_prompt_name(name)
    prompt = prompts_repo.get_latest(session, name)
    if prompt is None:
        raise HTTPException(
            status_code=404,
            detail=f"No prompt saved yet for {name!r}. Run `python -m db.seed_prompts` or create one here.",
        )
    return prompt


@router.post("/prompts/{name}", response_model=PromptOut)
def create_prompt_version(session: DbSession, name: str, body: PromptCreateIn):
    _check_known_prompt_name(name)

    placeholder = PROMPT_PLACEHOLDER[name]
    try:
        body.user_template.format(**{placeholder: ""})
    except (KeyError, IndexError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=(
                f"user_template is invalid: {e}. "
                f"It must be fillable with just the '{{{placeholder}}}' placeholder "
                f"(no other {{...}} placeholders)."
            ),
        ) from e

    return prompts_repo.create_next_version(session, name, body.system_text, body.user_template)

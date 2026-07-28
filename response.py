import json
import os

import openai
from dotenv import load_dotenv
from ollama import chat
from openai import OpenAI

from db import prompts_repo

load_dotenv(override=True)

groq_api_key = os.getenv("GROQ_API_KEY")

openai_client = OpenAI(
    api_key=groq_api_key,
    base_url="https://api.groq.com/openai/v1",
)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
ollama_num_gpu = int(os.getenv("OLLAMA_NUM_GPU", "0"))

MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL_NAME", "llama3.2:3b")

# Sticky flag: once the cloud provider fails once (rate limit, exhausted
# quota, auth issue, connection error — anything), assume it's unusable
# for the rest of this process and go straight to Ollama from then on.
# Without this, every subsequent call would re-attempt (and wait on) a
# provider that's already known to be down, slowing everything down.
_force_ollama = False


def _call_groq(
    prompt: str,
    json_mode: bool,
    system: str | None,
    model: str | None = None,
):
    if not openai_client:
        raise ValueError("GROQ_API_KEY is missing")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": 0,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = openai_client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content
    return json.loads(content) if json_mode else content


def _call_ollama(
    prompt: str,
    json_mode: bool,
    system: str | None,
    model: str | None = None,
):
    default_system = (
        "You are a strict information extraction engine. "
        "You ALWAYS return ONLY valid JSON. No markdown. No text."
    )

    options = {
        "num_gpu": ollama_num_gpu,
        "temperature": 0,
    }

    response = chat(
        model=model or OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system or default_system},
            {"role": "user", "content": prompt},
        ],
        options=options,
        format="json" if json_mode else "",
    )

    content = response["message"]["content"]

    if not content or not content.strip():
        raise ValueError("Empty response from LLM")

    return json.loads(content.strip()) if json_mode else content.strip()


def configured_llm_options() -> list[str]:
    return list(dict.fromkeys([MODEL, OLLAMA_MODEL]))


def generate_response(
    prompt: str,
    json_mode: bool = False,
    system: str | None = None,
    model: str | None = None,
):
    """
    Returns (result, applied_model) — the model actually used for THIS call,
    since a run can start on Groq/OpenAI and fall back to Ollama partway
    through if the cloud provider runs out of tokens/quota.

    If LLM_PROVIDER="openai", tries Groq/OpenAI first. On any failure
    (rate limit, exhausted quota, auth error, network error — anything
    from the openai SDK), logs why and falls back to the local Ollama
    model for this call and every call after it in this process.
    """
    global _force_ollama

    if model:
        if model == OLLAMA_MODEL or ":" in model:
            result = _call_ollama(prompt, json_mode, system, model=model)
            return result, model
        result = _call_groq(prompt, json_mode, system, model=model)
        return result, model

    if LLM_PROVIDER == "openai" and not _force_ollama:
        try:
            result = _call_groq(prompt, json_mode, system)
            return result, MODEL
        except openai.OpenAIError as e:
            print(
                f"[response] Groq/OpenAI call failed ({type(e).__name__}: {e}); "
                f"falling back to Ollama ({OLLAMA_MODEL}) for the rest of this run."
            )
            _force_ollama = True

    result = _call_ollama(prompt, json_mode, system)
    return result, OLLAMA_MODEL


def _load_prompt(name: str):
    """Fetches the current (latest) prompt version for `name` from the DB.
    Prompts are managed via the web form (POST /prompts/{name}) — there is
    no hardcoded fallback here on purpose, so editing a prompt in the DB is
    the single source of truth. Run `python -m db.seed_prompts` once on a
    fresh DB to seed the initial versions."""
    prompt = prompts_repo.get_latest(name)
    if prompt is None:
        raise RuntimeError(
            f"No prompt found in the DB for {name!r}. "
            f"Run `python -m db.seed_prompts` to seed the initial versions, "
            f"or create one via POST /prompts/{name}."
        )
    return prompt


def build_intro_prompt(intro_text: str) -> tuple[str, str, int]:
    prompt = _load_prompt("intro_extraction")
    user = prompt.user_template.format(intro_text=intro_text)
    return prompt.system_text, user, prompt.id


def extract_intro_data(intro_lines: str):
    """Returns (fields_dict, applied_model, prompt_id)."""
    system, user, prompt_id = build_intro_prompt(intro_lines)
    result, applied_model = generate_response(user, json_mode=True, system=system)
    return result, applied_model, prompt_id


def build_feedback_prompt(feedback_text: str) -> tuple[str, str, int]:
    prompt = prompts_repo.get_latest("feedback_extraction")
    if prompt is None:
        raise RuntimeError(
            "No prompt found in the DB for 'feedback_extraction'. "
            "Run `python -m db.seed_prompts` to seed the initial versions, "
            "or create one via POST /prompts/feedback_extraction."
        )
    user = prompt.user_template.format(feedback_text=feedback_text)
    return prompt.system_text, user, prompt.id


def extract_feedback_data(feedback_text: str, model: str | None = None):
    """Returns (fields_dict, applied_model, prompt_id)."""
    system, user, prompt_id = build_feedback_prompt(feedback_text)
    result, applied_model = generate_response(
        user,
        json_mode=True,
        system=system,
        model=model,
    )
    return result, applied_model, prompt_id

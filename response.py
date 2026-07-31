import json
import os

import openai
from dotenv import load_dotenv
from ollama import chat
from openai import OpenAI

from db import prompts_repo

load_dotenv(override=True)

groq_api_key = os.getenv("GROQ_API_KEY")
official_openai_api_key = os.getenv("OPENAI_API_KEY")

groq_client = (
    OpenAI(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    if groq_api_key
    else None
)
official_openai_client = (
    OpenAI(api_key=official_openai_api_key) if official_openai_api_key else None
)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
ollama_num_gpu = int(os.getenv("OLLAMA_NUM_GPU", "0"))

MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1")
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
    if not groq_api_key:
        raise ValueError("GROQ_API_KEY is missing")
    if groq_client is None:
        raise ValueError("Groq client is not configured")

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

    response = groq_client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content
    return json.loads(content) if json_mode else content


def _call_openai(
    prompt: str,
    json_mode: bool,
    system: str | None,
    model: str | None = None,
):
    if not official_openai_api_key:
        raise ValueError("OPENAI_API_KEY is missing")
    if official_openai_client is None:
        raise ValueError("OpenAI client is not configured")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": model or OPENAI_MODEL,
        "messages": messages,
        "temperature": 0,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = official_openai_client.chat.completions.create(**kwargs)
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
    return list(dict.fromkeys([MODEL, OPENAI_MODEL, OLLAMA_MODEL]))


def _fallback_to_ollama(
    prompt: str,
    json_mode: bool,
    system: str | None,
    failed_provider: str,
    error: Exception,
    allow_ollama: bool = True,
):
    if not allow_ollama:
        raise ValueError(
            f"{failed_provider} failed and Ollama fallback is disabled"
        ) from error

    print(
        f"[response] {failed_provider} failed ({type(error).__name__}: {error}); "
        f"falling back to Ollama ({OLLAMA_MODEL})."
    )
    result = _call_ollama(prompt, json_mode, system)
    return result, OLLAMA_MODEL


def _fallback_to_groq_then_ollama(
    prompt: str,
    json_mode: bool,
    system: str | None,
    failed_provider: str,
    error: Exception,
    allow_ollama: bool = True,
):
    global _force_ollama

    print(
        f"[response] {failed_provider} failed ({type(error).__name__}: {error}); "
        "trying the next configured LLM."
    )

    if groq_api_key and not _force_ollama:
        try:
            result = _call_groq(prompt, json_mode, system)
            return result, MODEL
        except (openai.OpenAIError, ValueError) as groq_error:
            print(
                f"[response] Groq/OpenAI-compatible call failed "
                f"({type(groq_error).__name__}: {groq_error}); "
                f"{'falling back to Ollama (' + OLLAMA_MODEL + ')' if allow_ollama else 'Ollama fallback is disabled'} "
                "for the rest of this run."
            )
            if allow_ollama:
                _force_ollama = True

    if not allow_ollama:
        raise ValueError(
            f"{failed_provider} failed and no cloud fallback was available"
        ) from error

    result = _call_ollama(prompt, json_mode, system)
    return result, OLLAMA_MODEL


def generate_response(
    prompt: str,
    json_mode: bool = False,
    system: str | None = None,
    model: str | None = None,
    allow_ollama: bool = True,
):
    """
    Returns (result, applied_model) — the model actually used for THIS call,
    since a run can start on Groq/OpenAI and fall back to Ollama partway
    through if the cloud provider runs out of tokens/quota.

    If LLM_PROVIDER="official_openai", uses the user's OpenAI API key.
    If LLM_PROVIDER="openai", keeps the historical Groq/OpenAI-compatible
    path and falls back to Ollama after the first provider failure.
    """
    global _force_ollama

    if model:
        if model == OLLAMA_MODEL or ":" in model:
            if not allow_ollama:
                raise ValueError("Ollama model is disabled for this operation")
            result = _call_ollama(prompt, json_mode, system, model=model)
            return result, model
        if model == OPENAI_MODEL:
            try:
                result = _call_openai(prompt, json_mode, system, model=model)
                return result, model
            except (openai.OpenAIError, ValueError) as e:
                return _fallback_to_groq_then_ollama(
                    prompt,
                    json_mode,
                    system,
                    "Official OpenAI",
                    e,
                    allow_ollama=allow_ollama,
                )
        try:
            result = _call_groq(prompt, json_mode, system, model=model)
            return result, model
        except (openai.OpenAIError, ValueError) as e:
            return _fallback_to_ollama(
                prompt,
                json_mode,
                system,
                "Groq",
                e,
                allow_ollama=allow_ollama,
            )

    if LLM_PROVIDER == "official_openai":
        try:
            result = _call_openai(prompt, json_mode, system)
            return result, OPENAI_MODEL
        except (openai.OpenAIError, ValueError) as e:
            return _fallback_to_groq_then_ollama(
                prompt,
                json_mode,
                system,
                "Official OpenAI",
                e,
                allow_ollama=allow_ollama,
            )

    if LLM_PROVIDER == "openai" and (not _force_ollama or not allow_ollama):
        try:
            result = _call_groq(prompt, json_mode, system)
            return result, MODEL
        except openai.OpenAIError as e:
            print(
                f"[response] Groq/OpenAI call failed ({type(e).__name__}: {e}); "
                f"falling back to Ollama ({OLLAMA_MODEL}) for the rest of this run."
            )
            if allow_ollama:
                _force_ollama = True

    if not allow_ollama:
        raise ValueError("No cloud LLM provider was available and Ollama fallback is disabled")

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


def extract_intro_data(intro_lines: str, model: str | None = None):
    """Returns (fields_dict, applied_model, prompt_id)."""
    system, user, prompt_id = build_intro_prompt(intro_lines)
    result, applied_model = generate_response(
        user,
        json_mode=True,
        system=system,
        model=model,
    )
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

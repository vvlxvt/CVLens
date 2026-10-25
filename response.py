import json
import logging
import re
from ast import literal_eval

import openai
import requests
from ollama import chat
from openai import OpenAI

from core.config import settings
from db import prompts_repo
from db.connection import get_session

logger = logging.getLogger(__name__)

groq_api_key = settings.groq_api_key
openrouter_api_key = settings.open_router_key

_groq_client: OpenAI | None = None
_openrouter_client: OpenAI | None = None


def get_groq_client() -> OpenAI | None:
    """Ленивая инициализация Groq-клиента — создаётся при первом использовании."""
    global _groq_client
    if _groq_client is None and groq_api_key:
        _groq_client = OpenAI(
            api_key=groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _groq_client


def get_openrouter_client() -> OpenAI | None:
    """Ленивая инициализация OpenRouter-клиента — создаётся при первом использовании."""
    global _openrouter_client
    if _openrouter_client is None and openrouter_api_key:
        _openrouter_client = OpenAI(
            api_key=openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
        )
    return _openrouter_client


LLM_PROVIDER = settings.llm_provider
ollama_num_gpu = settings.ollama_num_gpu

DECOMMISSIONED_MODEL_REPLACEMENTS = {
    "llama-3.3-70b-versatile": "qwen/qwen3.8-27b",
}


def _replace_decommissioned_model(model: str) -> str:
    return DECOMMISSIONED_MODEL_REPLACEMENTS.get(model, model)


MODEL = _replace_decommissioned_model(settings.groq_model)
# Актуальная бесплатная модель по умолчанию
OPENROUTER_MODEL = settings.openrouter_model
OPENAI_MODEL = OPENROUTER_MODEL

OLLAMA_MODEL = settings.ollama_model_name

_force_ollama = False


def _parse_json_content(content: str):
    text = (content or "").strip()
    if not text:
        raise ValueError("Empty response from LLM")

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as first_error:
        starts = [
            index
            for index, char in enumerate(text)
            if char in {"{", "["}
        ]
        decoder = json.JSONDecoder()
        for start in starts:
            candidate = text[start:]
            try:
                result, _ = decoder.raw_decode(candidate)
                return result
            except json.JSONDecodeError:
                pass

            literal_candidate = _balanced_literal_candidate(text, start)
            if literal_candidate is None:
                continue
            try:
                result = literal_eval(literal_candidate)
            except (SyntaxError, ValueError):
                continue
            if isinstance(result, dict | list):
                return result

        raise ValueError("LLM response did not contain valid JSON") from first_error


def _balanced_literal_candidate(text: str, start: int) -> str | None:
    opening = text[start]
    closing_by_opening = {"{": "}", "[": "]"}
    if opening not in closing_by_opening:
        return None

    stack = [closing_by_opening[opening]]
    quote: str | None = None
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
            continue
        if char in closing_by_opening:
            stack.append(closing_by_opening[char])
            continue
        if stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return text[start:index + 1]
    return None


def _call_groq(
    prompt: str,
    json_mode: bool,
    system: str | None,
    model: str | None = None,
):
    if not groq_api_key:
        raise ValueError("GROQ_API_KEY is missing")

    client = get_groq_client()
    if client is None:
        raise ValueError("Groq client is not configured")

    messages = []

    if system:
        messages.append({
            "role": "system",
            "content": system
        })

    messages.append({
        "role": "user",
        "content": prompt
    })

    kwargs = {
            "model": model or MODEL,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 800,  # Ограничиваем генерацию, чтобы влезть в лимит 1000 OTPM
            "reasoning_effort": "none",
        }

    if json_mode:
        kwargs["response_format"] = {
            "type": "json_object"
        }

    response = client.chat.completions.create(**kwargs)

    content = response.choices[0].message.content

    return _parse_json_content(content) if json_mode else content


def _is_groq_json_validation_error(error: Exception) -> bool:
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        error_body = body.get("error", body)
        if error_body.get("code") == "json_validate_failed":
            return True
    return "json_validate_failed" in str(error)


def _call_groq_with_json_retry(
    prompt: str,
    json_mode: bool,
    system: str | None,
    model: str | None = None,
):
    try:
        return _call_groq(prompt, json_mode, system, model=model)
    except openai.BadRequestError as e:
        if not json_mode or not _is_groq_json_validation_error(e):
            raise

        logger.warning(
            "[response] Groq JSON response_format validation failed; "
            "retrying without provider-enforced JSON mode."
        )
        content = _call_groq(prompt, False, system, model=model)
        logger.debug("\n===== RAW GROQ RESPONSE =====\n%s\n===== END RAW RESPONSE =====\n", content)

        return _parse_json_content(content)


# Список заведомо нерабочих или платных моделей для исключения
BLOCKED_FREE_MODELS = {
    "meta-llama/llama-3.1-8b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "deepseek/deepseek-r1:free",
}

# Гарантированно работающие бесплатные текстовые модели
DEFAULT_FREE_MODELS = [
    "google/gemma-2-9b-it:free",
    "qwen/qwen-2-7b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "openrouter/auto",
]


def get_active_free_openrouter_models() -> list[str]:
    """Запрашивает актуальный список бесплатных моделей OpenRouter и фильтрует устаревшие."""
    try:
        response = requests.get("https://openrouter.ai/api/v1/models", timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", [])
            free_models = [
                m["id"]
                for m in data
                if (
                    m["id"].endswith(":free")
                    or (
                        m.get("pricing", {}).get("prompt") == "0"
                        and m.get("pricing", {}).get("completion") == "0"
                    )
                )
                and m["id"] not in BLOCKED_FREE_MODELS
            ]
            if free_models:
                return free_models
    except Exception as e:
        logger.warning("[response] Failed to fetch dynamic free models: %s", e)

    return DEFAULT_FREE_MODELS


def _call_openrouter(
    prompt: str,
    json_mode: bool,
    system: str | None,
    model: str | None = None,
):
    if not openrouter_api_key:
        raise ValueError("OPEN_ROUTER_KEY is missing")
    client = get_openrouter_client()
    if client is None:
        raise ValueError("OpenRouter client is not configured")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})

    user_content = prompt
    if json_mode:
        user_content += "\n\nIMPORTANT: Respond strictly in valid JSON format."

    messages.append({"role": "user", "content": user_content})

    kwargs = {
        "messages": messages,
        "temperature": 0,
    }

    # Формируем список кандидатов для последовательного перебора
    candidate_models = []
    if model and model not in BLOCKED_FREE_MODELS:
        candidate_models.append(model)

    active_free = get_active_free_openrouter_models()
    for m in active_free:
        if m not in candidate_models:
            candidate_models.append(m)

    if "openrouter/auto" not in candidate_models:
        candidate_models.append("openrouter/auto")

    last_exception = None

    for current_model in candidate_models:
        try:
            kwargs["model"] = current_model
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content

            parsed = _parse_json_content(content) if json_mode else content
            return parsed, current_model
        except Exception as e:
            logger.warning(
                "[response] OpenRouter model '%s' failed (%s: %s). Trying fallback...",
                current_model,
                type(e).__name__,
                e,
            )
            last_exception = e

    raise last_exception or ValueError("All OpenRouter models failed")

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

    return _parse_json_content(content) if json_mode else content.strip()


def configured_llm_options() -> list[str]:
    return list(
        dict.fromkeys(
            [
                _replace_decommissioned_model(MODEL),
                OPENROUTER_MODEL,
                OLLAMA_MODEL,
            ]
        )
    )


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

    logger.warning(
        "[response] %s failed (%s: %s); falling back to Ollama (%s).",
        failed_provider,
        type(error).__name__,
        error,
        OLLAMA_MODEL,
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

    logger.warning(
        "[response] %s failed (%s: %s); trying the next configured LLM.",
        failed_provider,
        type(error).__name__,
        error,
    )

    if groq_api_key and not _force_ollama:
        try:
            result = _call_groq_with_json_retry(prompt, json_mode, system)
            return result, MODEL
        except (openai.OpenAIError, ValueError) as groq_error:
            logger.warning(
                "[response] Groq/OpenAI-compatible call failed (%s: %s); %s for the rest of this run.",
                type(groq_error).__name__,
                groq_error,
                "falling back to Ollama (%s)" % OLLAMA_MODEL if allow_ollama else "Ollama fallback is disabled",
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
) -> tuple[str, str]:
    """
    Unified LLM interface.

    When `model` is given, that exact model is used (subject to the
    `allow_ollama` guard).

    When `model` is None — the default for CV review — providers are tried
    in order: Groq → OpenRouter → local Ollama.
    """
    if model:
        model = _replace_decommissioned_model(model)
        if model == OLLAMA_MODEL or (":" in model and not model.endswith(":free")):
            # Explicit Ollama model requested.
            if not allow_ollama and LLM_PROVIDER != "ollama":
                raise ValueError("Ollama model is disabled for this operation")
            result = _call_ollama(prompt, json_mode, system, model=model)
            return result, model
        if model == OPENROUTER_MODEL or model.endswith(":free"):
            try:
                result, used_model = _call_openrouter(prompt, json_mode, system, model=model)
                return result, used_model
            except (openai.OpenAIError, ValueError) as e:
                return _fallback_to_groq_then_ollama(
                    prompt, json_mode, system, "OpenRouter", e, allow_ollama=allow_ollama,
                )
        try:
            result = _call_groq_with_json_retry(prompt, json_mode, system, model=model)
            return result, model
        except (openai.OpenAIError, ValueError) as e:
            return _fallback_to_ollama(
                prompt, json_mode, system, "Groq", e, allow_ollama=allow_ollama,
            )

    # No explicit model: Groq → OpenRouter → Ollama (review default).
    if get_groq_client():
        try:
            result = _call_groq_with_json_retry(prompt, json_mode, system)
            return result, MODEL
        except (openai.OpenAIError, ValueError) as e:
            logger.warning(
                "[response] Groq failed (%s: %s); trying OpenRouter...",
                type(e).__name__,
                e,
            )

    if get_openrouter_client():
        try:
            result, used_model = _call_openrouter(prompt, json_mode, system)
            return result, used_model
        except (openai.OpenAIError, ValueError) as e:
            logger.warning(
                "[response] OpenRouter failed (%s: %s); falling back to Ollama (%s)...",
                type(e).__name__,
                e,
                OLLAMA_MODEL,
            )

    if not allow_ollama:
        raise ValueError(
            "No cloud LLM provider was available and Ollama fallback is disabled"
        )

    result = _call_ollama(prompt, json_mode, system)
    return result, OLLAMA_MODEL


def _load_prompt(name: str):
    with get_session() as session:
        prompt = prompts_repo.get_latest(session, name)
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


def extract_intro_data(
    intro_lines: str,
    model: str | None = None,
    allow_ollama: bool = True,
):
    system, user, prompt_id = build_intro_prompt(intro_lines)
    result, applied_model = generate_response(
        user,
        json_mode=True,
        system=system,
        model=model,
        allow_ollama=allow_ollama,
    )
    return result, applied_model, prompt_id


def build_feedback_prompt(feedback_text: str) -> tuple[str, str, int]:
    with get_session() as session:
        prompt = prompts_repo.get_latest(session, "feedback_extraction")
    if prompt is None:
        raise RuntimeError(
            "No prompt found in the DB for 'feedback_extraction'. "
            "Run `python -m db.seed_prompts` to seed the initial versions, "
            "or create one via POST /prompts/feedback_extraction."
        )
    user = prompt.user_template.format(feedback_text=feedback_text)
    return prompt.system_text, user, prompt.id


def extract_feedback_data(
    feedback_text: str,
    model: str | None = None,
    allow_ollama: bool = True,
):
    system, user, prompt_id = build_feedback_prompt(feedback_text)
    result, applied_model = generate_response(
        user,
        json_mode=True,
        system=system,
        model=model,
        allow_ollama=allow_ollama,
    )
    return result, applied_model, prompt_id
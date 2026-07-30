import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from db import prompts_repo, resumes_repo
from response import (
    LLM_PROVIDER,
    MODEL,
    OLLAMA_MODEL,
    OPENAI_MODEL,
    extract_feedback_data,
    extract_intro_data,
)

try:
    import fitz
except ImportError as exc:
    raise RuntimeError("PyMuPDF is required. Install: pip install PyMuPDF") from exc


DATA_DIR = Path(__file__).resolve().parent / "data"
INPUT_PATH = DATA_DIR / "result.json"

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

# What SHOULD be used if the preferred provider is healthy — used only to
# detect staleness (see _is_up_to_date). The model actually recorded on
# each DB row (about_llm/feedback_llm) always reflects what really ran for
# that call, which can differ if generate_response() falls back. About/summary
# extraction is strongest-first; feedback keeps the configured provider policy.
PREFERRED_ABOUT_MODEL = OPENAI_MODEL
PREFERRED_FEEDBACK_MODEL = MODEL if LLM_PROVIDER == "openai" else OLLAMA_MODEL

ADMINS = {
    "Aleksandr Valuev",
    "Maksim Pozharskiy",
    'Evgeny V',
    'Polina (Полина🪷) Kornilova',
    "Artem K",
    "Anna [job offer USA \U0001f1fa\U0001f1f8] Naumova",
    "Evgeniia Kapustina",
}


CV_SECTION_ALIASES: dict[str, set[str]] = {
    "about_me_summary": {
        "about",
        "about me",
        "additional information",
        "summary",
        "profile",
        "professional summary",
        "objective",
    },
    "experience": {
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "career history",
    },
    "skills": {
        "skills",
        "technical skills",
        "tech skills",
        "technologies",
        "technology stack",
        "tech stack",
        "stack",
    },
}

IGNORED_SECTIONS: set[str] = {
    "achievements",
    "certificates",
    "certifications",
    "education",
    "languages",
    "leadership",
    "projects",
    "volunteering",
}

SECTION_BY_ALIAS: dict[str, str] = {  # получаем обратный индекс
    alias: section
    for section, aliases in CV_SECTION_ALIASES.items()
    for alias in aliases
}

EMOJI_PATTERN = re.compile(
    "["
    "\U0001f1e6-\U0001f1ff"
    "\U0001f300-\U0001faff"
    "\U00002700-\U000027bf"
    "\U00002600-\U000026ff"
    "\U0000fe0f"
    "]+"
)

# Паттерны контактных строк (пропускаем при поиске intro-саммари)
CONTACT_PATTERNS = (
    r"@",
    r"https?://",
    r"linkedin",
    r"github",
    r"gitlab",
    r"telegram",
    r"t\.me",
    r"\bphone\b",
    r"\bemail\b",
    r"\+\d[\d\s\-()\u00a0]{5,}",
    r"\btel\b",
)

# Паттерны дат для strip_company_dates
DATE_RANGE_RE = re.compile(
    r"(?:"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    r"|\u044f\u043d\u0432|\u0444\u0435\u0432|\u043c\u0430\u0440|\u0430\u043f\u0440"
    r"|\u043c\u0430\u0439|\u0438\u044e\u043d|\u0438\u044e\u043b|\u0430\u0432\u0433"
    r"|\u0441\u0435\u043d|\u043e\u043a\u0442|\u043d\u043e\u044f|\u0434\u0435\u043a)"
    r"\.?\s*)?"
    r"\d{4}"
    r"(?:\s*[-\u2013\u2014]\s*"
    r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    r"|\u044f\u043d\u0432|\u0444\u0435\u0432|\u043c\u0430\u0440|\u0430\u043f\u0440"
    r"|\u043c\u0430\u0439|\u0438\u044e\u043d|\u0438\u044e\u043b|\u0430\u0432\u0433"
    r"|\u0441\u0435\u043d|\u043e\u043a\u0442|\u043d\u043e\u044f|\u0434\u0435\u043a)"
    r"\.?\s*)?"
    r"(?:\d{4}|present|\u043d\u0430\u0441\u0442\u043e\u044f\u0449\u0435\u0435"
    r"|\u043d\.\u0432\.|\u0441\u0435\u0439\u0447\u0430\u0441)"
    r")?",
    re.IGNORECASE,
)
SHORT_DATE_RE = re.compile(r"\b\d{1,2}[./]\d{4}\b")


# ---------------------------------------------------------------------------
# Утилиты текста
# ---------------------------------------------------------------------------


def extract_message_text(message: dict) -> str:
    """Возвращает текст сообщения (строка или список частей)."""
    text = message.get("text", "")
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts = []
        for part in text:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(part.get("text", ""))
        return "".join(parts)
    return ""


def clean_feedback_text(text: str) -> str:
    """Минимальная чистка фидбэка: эмодзи, невидимые символы, пробелы."""
    text = unicodedata.normalize("NFKC", text)
    text = EMOJI_PATTERN.sub("", text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_cv_text(text: str) -> str:
    """
    Нормализует сырой текст PDF-резюме.

    Выход: блоки разделённые \n\n, где каждый блок начинается с заголовка
    (или является преамбулой). Внутри блока строки разделены \n.

    Структура выхода:
        <преамбула — имя, роль, контакты, summary>
        \n\n
        <Заголовок1>
        строка
        строка
        \n\n
        <Заголовок2>
        строка
        ...
    """
    # --- Нормализация unicode ---
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # --- Дата-диапазоны склеенные переносами ---
    text = re.sub(r"(\w+)\s*\n\s*([\u2013\-\u2014])\s*\n?\s*(\w+)", r"\1 \2 \3", text)

    # --- Пробелы вокруг дефиса ---
    text = re.sub(r"\s+-\s+(?=[a-z])", "-", text)
    text = re.sub(r"\s+-\s+(?=[A-Z])", " - ", text)
    text = re.sub(r"(\d)\s+-\s+(\d)", r"\1-\2", text)

    # --- Пробел перед пунктуацией ---
    text = re.sub(r"\s+([.,;:])", r"\1", text)

    # --- Горизонтальные пробелы ---
    text = re.sub(r"[ \t]+", " ", text)

    # --- Пробелы по краям строк ---
    text = "\n".join(line.strip() for line in text.splitlines())

    # --- Убираем маркеры буллетов в начале строки ---
    text = re.sub(r"(?m)^[\u2022\u2217\u00b7\u2023\u25aa\u25b8]\s*", "", text)
    text = re.sub(r"(?m)^[-\u2013\u2014]\s+(?=\S)", "", text)

    # --- Убираем пустые строки ---
    lines = [ln for ln in text.splitlines() if ln.strip()]

    # --- Разбиваем на блоки по заголовкам ---
    _all_headings: set[str] = set(SECTION_BY_ALIAS) | IGNORED_SECTIONS

    def _is_heading(line: str) -> bool:
        # Inline field-labels ("Tech Stack:", "Languages:") end with a colon
        # and often appear WITHIN another section (e.g. a per-job "Tech
        # Stack:" line inside EXPERIENCE) — genuine section titles in resumes
        # essentially never end with a colon. Without this guard, a label
        # whose text happens to match a section alias (e.g. "tech stack" is
        # an alias for "skills") gets mistaken for a new top-level section
        # boundary, and everything after it — including the NEXT job's
        # entire description — gets swept into the wrong bucket.
        if line.strip().endswith(":"):
            return False

        normalized = (
            unicodedata.normalize("NFKC", line)
            .lower()
            .strip(" .:-\u2013\u2014|\u2022\u00b7")
        )
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized in _all_headings

    blocks: list[list[str]] = []
    current: list[str] = []

    for ln in lines:
        if _is_heading(ln):
            if current:
                blocks.append(current)
            current = [ln]  # заголовок начинает новый блок
        else:
            current.append(ln)

    if current:
        blocks.append(current)

    return "\n\n".join("\n".join(block) for block in blocks).strip()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def is_pdf_message(message: dict) -> bool:
    return (
        message.get("mime_type") == "application/pdf"
        or str(message.get("file", "")).lower().endswith(".pdf")
        or str(message.get("file_name", "")).lower().endswith(".pdf")
    )


def english_ratio(text: str) -> float:
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)

    if not letters:
        return 0

    english = sum(ch.isascii() for ch in letters)
    return english / len(letters)


def extract_pdf_text(file_url: str, data_dir: Path = DATA_DIR) -> str:
    if not file_url:
        return ""
    
    # Берем только 'resume.pdf', игнорируя 'files/' из file_url
    pdf_path = data_dir / Path(file_url).name
    
    if not pdf_path.exists():
        # Добавим лог, чтобы сразу видеть, если файл реально потерялся
        print(f"[warning] PDF file not found: {pdf_path}")
        return ""
        
    with fitz.open(pdf_path) as doc:
        return "\n".join(page.get_text("text", sort=True) for page in doc)


# ---------------------------------------------------------------------------
# Парсинг секций
# ---------------------------------------------------------------------------


def normalize_heading(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.lower().strip(" .:-\u2013\u2014|\u2022\u00b7")
    text = re.sub(r"\s+", " ", text)
    return text


def detect_section(line: str) -> str | None:
    """None = обычная строка; '__ignored__' = секция которую пропускаем."""
    # Same guard as clean_cv_text's _is_heading — a colon-terminated line is
    # an inline field-label, not a genuine section title.
    if line.strip().endswith(":"):
        return None

    normalized = normalize_heading(line)
    if normalized in SECTION_BY_ALIAS:
        return SECTION_BY_ALIAS[normalized]
    if normalized in IGNORED_SECTIONS:
        return "__ignored__"
    return None


def parse_cv_sections(file_url: str, data_dir: Path = DATA_DIR) -> dict:
    """
    Разбирает PDF на секции. Возвращает поля, готовые лечь в таблицу resumes:
        experience, skills, about_me_summary_raw,
        full_name, role_position, about_summary,
        about_llm, about_prompt_id
    """
    empty = {
        "experience": "",
        "skills": "",
        "about_me_summary_raw": "",
        "full_name": None,
        "role_position": None,
        "about_summary": None,
        "about_llm": None,
        "about_prompt_id": None,
    }

    raw_text = extract_pdf_text(file_url, data_dir)
    if not raw_text:
        return empty

    text = clean_cv_text(raw_text)
    if english_ratio(text[:300]) < 0.9:
        return {**empty, "noeng": 1}

    buckets: dict[str, list[str]] = {
        k: [] for k in ("experience", "skills", "about_me_summary")
    }
    intro_lines: list[str] = []

    for block in text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue

        detected = detect_section(
            lines[0]
        )  # первая строка блока — заголовок или преамбула

        if detected == "__ignored__":
            continue

        if detected and detected in buckets:
            buckets[detected].extend(lines[1:])  # заголовок не включаем в контент
            continue

        if detected is None and not intro_lines:
            intro_lines = lines  # первый блок без заголовка — преамбула

    parsed = {key: "\n".join(val) for key, val in buckets.items()}
    explicit_about_raw = parsed.pop("about_me_summary")
    intro_raw = "\n".join(intro_lines).strip()
    about_me_summary_raw = explicit_about_raw or intro_raw

    intro_text = "\n".join(intro_lines) + "\n" + explicit_about_raw
    fields, about_model, about_prompt_id = extract_intro_data(
        intro_text,
        model=PREFERRED_ABOUT_MODEL,
    )  # ({full_name, role_position, summary}, model, prompt_id)

    return {
        **parsed,
        "about_me_summary_raw": about_me_summary_raw,
        "full_name": fields.get("full_name"),
        "role_position": fields.get("role_position"),
        "about_summary": fields.get("summary") or about_me_summary_raw,
        "about_llm": about_model,
        "about_prompt_id": about_prompt_id,
    }


# ---------------------------------------------------------------------------
# Сборка кейсов
# ---------------------------------------------------------------------------


MAX_WORKERS = 8  # concurrent CVs in flight; LLM calls are I/O-bound (network/inference wait), so threads help


def _group_messages_by_cv(messages: list[dict]) -> dict[int, dict]:
    """
    Pure-Python, no LLM/PDF work: groups admin feedback messages under
    their parent CV message. Returns {pid: {"file": ..., "feedback_raw": ...}}.
    """
    msgs_by_id: dict[int, dict] = {m["id"]: m for m in messages if "id" in m}
    grouped: dict[int, dict] = {}

    for msg in messages:
        if (msg.get("from") or "") not in ADMINS:
            continue
        parent_id = msg.get("reply_to_message_id")
        if not parent_id:
            continue
        parent = msgs_by_id.get(parent_id)
        if not parent or not is_pdf_message(parent):
            continue

        pid = parent["id"]
        if pid not in grouped:
            grouped[pid] = {"file": parent.get("file", ""), "_feedback_parts": []}

        fb = clean_feedback_text(extract_message_text(msg))
        if fb:
            grouped[pid]["_feedback_parts"].append(fb)

    for entry in grouped.values():
        entry["feedback_raw"] = "\n\n".join(entry.pop("_feedback_parts"))

    return grouped


def _is_up_to_date(resume_id: str, feedback_raw: str, about_prompt_id: int, feedback_prompt_id: int) -> bool:
    """True if this CV is already in the DB with the preferred model/prompt
    version and the same feedback text — safe to skip reprocessing. A row
    saved via an Ollama fallback will NOT count as up to date once the
    preferred provider is healthy again, so it gets reprocessed/upgraded."""
    existing = resumes_repo.get_by_resume_id(resume_id)
    if existing is None:
        return False
    return (
        existing.about_llm == PREFERRED_ABOUT_MODEL
        and existing.about_prompt_id == about_prompt_id
        and existing.feedback_llm == PREFERRED_FEEDBACK_MODEL
        and existing.feedback_prompt_id == feedback_prompt_id
        and (existing.feedback_raw or "") == feedback_raw
    )


def _process_one_cv(pid: int, file_url: str, feedback_raw: str, data_dir: Path) -> dict | None:
    """Runs PDF parsing + both LLM extractions for a single CV. Returns
    a case dict ready for the DB, or None if parsing failed (non-English CV)
    or the whole CV couldn't be processed (logged, not raised — one bad CV
    shouldn't take down the rest of the batch)."""
    try:
        t0 = time.perf_counter()
        sections = parse_cv_sections(file_url, data_dir)
        t_pdf_and_intro = time.perf_counter() - t0
        if sections.get("noeng"):
            return None

        t1 = time.perf_counter()
        if feedback_raw:
            fb_fields, feedback_model, feedback_prompt_id = extract_feedback_data(feedback_raw)
            feedback_summary = fb_fields.get("feedback_summary")
            feedback_sections = fb_fields.get("feedback_sections")
        else:
            feedback_summary = None
            feedback_sections = None
            feedback_model = None  # no LLM call was made — nothing to attribute
            feedback_prompt_id = None
        t_feedback = time.perf_counter() - t1

        print(
            f"[timing] resume_id={pid} pdf+intro_llm={t_pdf_and_intro:.1f}s feedback_llm={t_feedback:.1f}s"
        )

        return {
            "resume_id": str(pid),
            **sections,
            "feedback_raw": feedback_raw,
            "feedback_summary": feedback_summary,
            "feedback_sections": feedback_sections,
            "feedback_llm": feedback_model,
            "feedback_prompt_id": feedback_prompt_id,
        }
    except Exception as e:  # noqa: BLE001
        print(f"[error] resume_id={pid} failed and was skipped: {type(e).__name__}: {e}")
        return None


def build_cases(
    messages: list[dict],
    data_dir: Path = DATA_DIR,
    max_workers: int = MAX_WORKERS,
    on_case_processed=None,
) -> list[dict]:
    """
    Строит кейсы: одно CV + склеенный фидбэк от одного или нескольких админов,
    прогоняет фидбэк через LLM-экстракцию (feedback_summary/feedback_sections).

    CV, уже сохранённые в БД с текущей моделью/версией промпта и тем же
    текстом фидбэка, пропускаются без обращения к LLM. Обработка новых/
    изменившихся CV идёт параллельно (LLM-вызовы — это ожидание сети/
    инференса, поэтому потоки, а не процессы, дают выигрыш).

    on_case_processed: если передан, вызывается СРАЗУ по готовности каждого
    CV (например, чтобы сохранить его в БД немедленно) — до того, как
    обработка остальных CV в этой пачке завершится. Это значит, что даже
    если позже в этом же прогоне что-то фатально сломается (кончились
    токены и у облака, и локальная модель недоступна; процесс прибили;
    и т.п.), все уже обработанные к этому моменту CV не теряются — они
    уже сохранены. Повторный запуск подхватит только необработанные/
    непрошедшие через _is_up_to_date CV, не трогая то, что уже готово.

    Структура кейса соответствует колонкам таблицы resumes.
    """
    # print("BUILD DATA_DIR:", data_dir.resolve())
    grouped = _group_messages_by_cv(messages)

    about_prompt = prompts_repo.get_latest("intro_extraction")
    feedback_prompt = prompts_repo.get_latest("feedback_extraction")
    if about_prompt is None or feedback_prompt is None:
        raise RuntimeError(
            "No prompts found in the DB. Run `python -m db.seed_prompts` once to seed "
            "the initial versions, or create them via the prompt-editing web form / "
            "POST /prompts/{intro_extraction,feedback_extraction}."
        )
    about_prompt_id = about_prompt.id
    feedback_prompt_id = feedback_prompt.id

    todo = {}
    skipped = 0
    for pid, entry in grouped.items():
        if _is_up_to_date(str(pid), entry["feedback_raw"], about_prompt_id, feedback_prompt_id):
            skipped += 1
            continue
        todo[pid] = entry

    if skipped:
        print(f"Skipping {skipped} already-processed CV(s) (same model/prompt/feedback)")
    if not todo:
        return []

    cases = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_process_one_cv, pid, entry["file"], entry["feedback_raw"], data_dir): pid
            for pid, entry in todo.items()
        }
        for future in as_completed(futures):
            pid = futures[future]
            try:
                case = future.result()
            except Exception as e:  # noqa: BLE001
                # _process_one_cv already catches its own errors internally
                # and returns None — this is a defensive fallback in case
                # something outside that try/except still blows up.
                print(f"[error] resume_id={pid} raised unexpectedly and was skipped: {e}")
                continue

            if case is None:
                continue

            if on_case_processed is not None:
                try:
                    on_case_processed(case)
                except Exception as e:  # noqa: BLE001
                    print(f"[error] resume_id={pid}: on_case_processed callback failed: {e}")

            cases.append(case)

    return cases


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def load_messages(path: Path = INPUT_PATH) -> list[dict]:
    # получаю словарь с сообщениями из result.json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["messages"]


def _save_one_case(case: dict) -> bool:
    """
    Upserts one case into the resumes table, skipping it if the feedback
    couldn't be tied to any concrete CV section (feedback_sections is None
    — e.g. empty feedback, an off-topic question, or a link with no real
    critique). Returns True if the case was saved.
    """
    if case.get("feedback_sections") is None:
        print(f"Skipping resume_id={case['resume_id']!r}: no clear feedback (feedback_sections is null)")
        return False
    resumes_repo.upsert(case)
    return True


def save_cases_to_db(cases: list[dict]) -> int:
    """
    Bulk-saves an already-built list of cases. Prefer passing
    _save_one_case directly as build_cases()'s on_case_processed callback
    instead — that saves each CV the moment it's ready instead of waiting
    for the whole batch, so a later failure (e.g. every LLM provider
    becomes unavailable) doesn't lose CVs that were already processed.
    """
    saved = sum(_save_one_case(c) for c in cases)
    print(f"Skipped {len(cases) - saved} case(s) with unclear/no feedback")
    return saved


def upload_file_via_api(json_path: Path = INPUT_PATH, api_base_url: str = API_BASE_URL) -> dict:
    """
    Uploads result.json to the API's /resumes/upload endpoint as a file
    (multipart/form-data — the same way a web form's file picker would).
    The API runs build_cases() itself (PDF parsing + LLM extraction) and
    upserts the results — this function does no local processing, just
    the HTTP call.

    Use this when the API server has access to the CV PDFs on its own
    filesystem (extract/data/) — same assumption as running parser.py
    directly. Returns the API's UploadResult as a dict.
    """
    with open(json_path, "rb") as f:
        try:
            response = requests.post(
                f"{api_base_url}/resumes/upload",
                files={"file": (json_path.name, f, "application/json")},
                timeout=600,  # the API runs the full LLM pipeline synchronously — can take a while
            )
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"Could not reach the API at {api_base_url}. Is it running? (uvicorn api.main:app)"
            ) from e

    if response.status_code in (400, 422):
        raise RuntimeError(f"API rejected the upload ({response.status_code}): {response.json()}")
    response.raise_for_status()

    result = response.json()
    print(
        f"API processed {result['received']} CV(s): saved {result['saved']}, "
        f"skipped {len(result['skipped_ids'])} with unclear feedback"
    )
    return result


if __name__ == "__main__":
    # Pick ONE of these — not both:
    upload_file_via_api()                                                   # server does build_cases() + save
    # build_cases(load_messages(), on_case_processed=_save_one_case)        # write locally as each CV finishes, no API needed

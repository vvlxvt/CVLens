import json
import re
import unicodedata
from pathlib import Path


from response import extract_intro_data, MODEL

try:
    import fitz
except ImportError as exc:
    raise RuntimeError("PyMuPDF is required. Install: pip install PyMuPDF") from exc


DATA_DIR = Path(__file__).resolve().parent / "data"
INPUT_PATH = DATA_DIR / "result.json"
OUTPUT_PATH = DATA_DIR / "cases.json"

ADMINS = {
    "Aleksandr Valuev",
    "Artem K",
    "Anna [job offer USA \U0001f1fa\U0001f1f8] Naumova",
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
    pdf_path = data_dir / file_url
    if not pdf_path.exists():
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
    normalized = normalize_heading(line)
    if normalized in SECTION_BY_ALIAS:
        return SECTION_BY_ALIAS[normalized]
    if normalized in IGNORED_SECTIONS:
        return "__ignored__"
    return None


def parse_cv_sections(file_url: str, data_dir: Path = DATA_DIR) -> dict:
    """
    Разбирает PDF на секции. Возвращает:
        skills, experience, about_me_summary
    """
    raw_text = extract_pdf_text(file_url, data_dir)
    if not raw_text:
        return {k: "" for k in ("experience", "skills", "about_me_summary")}

    text = clean_cv_text(raw_text)
    if english_ratio(text[:300]) < 0.9:
        return {"noeng": 1}

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

    result = {key: "\n".join(val) for key, val in buckets.items()}

    summary = result["about_me_summary"]
    intro_text = "\n".join(intro_lines) + '\n' + summary
    fields = extract_intro_data(intro_text)
    result["role_position"] = fields["role_position"]
    if not result["about_me_summary"]:
        result["about_me_summary"] = fields["summary"]

    return result


# ---------------------------------------------------------------------------
# Сборка кейсов
# ---------------------------------------------------------------------------


def build_cases(messages: list[dict], data_dir: Path = DATA_DIR) -> list[dict]:
    """
    Строит кейсы: одно CV + склеенный фидбэк от одного или нескольких админов.

    Структура кейса:
        id              — id сообщения с CV в Telegram
        role_position   — желаемая роль/позиция
        skills          — технические навыки (строка)
        about_me_summary — саммари «о себе» (с заголовком или из преамбулы)
        experience      — опыт без компаний и дат
        feedback        — ответы всех админов, склеенные через двойной перенос
    """
    msgs_by_id: dict[int, dict] = {m["id"]: m for m in messages if "id" in m}
    raw: dict[int, dict] = {}

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
        if pid not in raw:
            sections = parse_cv_sections(parent.get("file", ""), data_dir)
            if sections.get("noeng"):
                continue
            raw[pid] = {"id": pid, **sections, "_feedback_parts": []}

        fb = clean_feedback_text(extract_message_text(msg))
        if fb:
            raw[pid]["_feedback_parts"].append(fb)

    cases = []
    for entry in raw.values():
        entry["feedback"] = "\n\n".join(entry.pop("_feedback_parts"))
        cases.append(entry)

    return cases[:5]


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def load_messages(path: Path = INPUT_PATH) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["messages"]


def save_cases(cases: list[dict], path: Path = OUTPUT_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)




if __name__ == "__main__":
    cases = build_cases(load_messages())
    save_cases(cases)
    print(f"Saved {len(cases)} cases to {OUTPUT_PATH}")
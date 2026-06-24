import json
import re
import unicodedata
from pathlib import Path

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
    "role_position": {
        "role",
        "position",
        "job title",
        "desired position",
        "title",
        "\u0440\u043e\u043b\u044c",
        "\u043f\u043e\u0437\u0438\u0446\u0438\u044f",
        "\u0434\u043e\u043b\u0436\u043d\u043e\u0441\u0442\u044c",
        "\u0436\u0435\u043b\u0430\u0435\u043c\u0430\u044f \u0434\u043e\u043b\u0436\u043d\u043e\u0441\u0442\u044c",
        "\u0441\u043f\u0435\u0446\u0438\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u044f",
    },
    "skills": {
        "skills",
        "technical skills",
        "tech skills",
        "technologies",
        "technology stack",
        "tech stack",
        "stack",
        "\u043d\u0430\u0432\u044b\u043a\u0438",
        "\u0442\u0435\u0445\u043d\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u043d\u0430\u0432\u044b\u043a\u0438",
        "\u043a\u043b\u044e\u0447\u0435\u0432\u044b\u0435 \u043d\u0430\u0432\u044b\u043a\u0438",
        "\u0441\u0442\u0435\u043a",
        "\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0438",
    },
    "about_me_summary": {
        "about",
        "about me",
        "additional information",
        "summary",
        "profile",
        "professional summary",
        "objective",
        "\u043e \u0441\u0435\u0431\u0435",
        "\u043e\u0431\u043e \u043c\u043d\u0435",
        "\u0434\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u0430\u044f \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f",
        "\u043f\u0440\u043e\u0444\u0438\u043b\u044c",
        "\u0440\u0435\u0437\u044e\u043c\u0435",
        "\u043a\u0440\u0430\u0442\u043a\u043e",
    },
    "experience": {
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "career history",
        "\u043e\u043f\u044b\u0442",
        "\u043e\u043f\u044b\u0442 \u0440\u0430\u0431\u043e\u0442\u044b",
        "\u043f\u0440\u043e\u0444\u0435\u0441\u0441\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u043f\u044b\u0442",
        "\u043a\u0430\u0440\u044c\u0435\u0440\u0430",
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
    "\u0434\u043e\u0441\u0442\u0438\u0436\u0435\u043d\u0438\u044f",
    "\u043e\u0431\u0440\u0430\u0437\u043e\u0432\u0430\u043d\u0438\u0435",
    "\u043f\u0440\u043e\u0435\u043a\u0442\u044b",
    "\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442\u044b",
    "\u044f\u0437\u044b\u043a\u0438",
}

SECTION_BY_ALIAS: dict[str, str] = {
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
    """Нормализует сырой текст PDF-резюме."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Восстанавливаем буллеты
    text = re.sub(
        r"(?<!\n)([\u2022\u2217\u00b7\u2023\u25aa\u25b8\-\u2013])\s*", r"\n\1 ", text
    )

    # Дата-диапазоны на разных строках
    text = re.sub(r"(\w+)\s*\n\s*([\u2013\-\u2014])\s*\n?\s*(\w+)", r"\1 \2 \3", text)

    # Пробелы вокруг дефиса
    text = re.sub(r"\s+-\s+(?=[a-z])", "-", text)
    text = re.sub(r"\s+-\s+(?=[A-Z])", " - ", text)
    text = re.sub(r"(\d)\s+-\s+(\d)", r"\1-\2", text)

    # Пробел перед пунктуацией
    text = re.sub(r"\s+([.,;:])", r"\1", text)

    # Горизонтальные пробелы
    text = re.sub(r"[ \t]+", " ", text)

    # Не более двух переносов
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Пробелы по краям строк
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)

    # Убираем маркеры буллетов в начале строки
    text = re.sub(r"(?m)^[\u2022\u2217\u00b7\u2023\u25aa\u25b8]\s*", "", text)
    text = re.sub(r"(?m)^[-\u2013\u2014]\s+(?=\S)", "", text)

    # Все известные заголовки секций
    _all_headings: set[str] = set(SECTION_BY_ALIAS) | IGNORED_SECTIONS

    def _is_heading(line: str) -> bool:
        normalized = (
            unicodedata.normalize("NFKC", line)
            .lower()
            .strip(" .:-\u2013\u2014|\u2022\u00b7")
        )
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized in _all_headings

    def split_on_headings(block: str) -> list[str]:
        """Если внутри абзаца встречается заголовок — разрезаем на подблоки."""
        lines = block.splitlines()
        sub: list[str] = []
        result: list[str] = []
        for ln in lines:
            if _is_heading(ln) and sub:
                result.append("\n".join(sub))
                sub = [ln]
            else:
                sub.append(ln)
        if sub:
            result.append("\n".join(sub))
        return result

    def merge_block(block: str) -> str:
        blines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(blines) <= 1:
            return blines[0] if blines else ""
        result = []
        for ln in blines:
            if ln and ln[-1] not in ".!?:,":
                ln += "."
            result.append(ln)
        return " ".join(result)

    # Сначала защищаем заголовки, потом склеиваем блоки
    raw_paragraphs = text.split("\n\n")
    protected: list[str] = []
    for p in raw_paragraphs:
        protected.extend(split_on_headings(p))

    paragraphs = [merge_block(p) for p in protected]
    return "\n\n".join(paragraphs).strip()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def is_pdf_message(message: dict) -> bool:
    return (
        message.get("mime_type") == "application/pdf"
        or str(message.get("file", "")).lower().endswith(".pdf")
        or str(message.get("file_name", "")).lower().endswith(".pdf")
    )


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


def is_contact_line(line: str) -> bool:
    return any(re.search(p, line, re.IGNORECASE) for p in CONTACT_PATTERNS)


def is_name_line(line: str) -> bool:
    """Эвристика для ФИО: 2–4 слова, все с заглавной, нет цифр."""
    words = line.split()
    if not (2 <= len(words) <= 4):
        return False
    if re.search(r"[0-9@/\\|]", line):
        return False
    return all(w[0].isupper() for w in words if w)


def extract_intro_summary(intro_lines: list[str]) -> tuple[str, str]:
    """
    Из преамбулы (до первой именованной секции) извлекает:
      - role_position: первая короткая строка после ФИО/контактов
      - about_me_summary: блок связного текста (без заголовка)
    """
    role = ""
    summary_parts: list[str] = []
    past_header = False

    for line in intro_lines:
        line = line.strip(" |")
        if not line:
            continue

        if is_contact_line(line):
            past_header = True
            continue

        if is_name_line(line) and not past_header:
            past_header = True
            continue

        past_header = True

        # Короткая строка без конечного знака препинания → роль
        if not role and len(line) <= 90 and not re.search(r"[.!?]$", line):
            role = line
            continue

        # Длинная строка или заканчивается точкой → саммари
        if len(line) > 40 or re.search(r"[.!?]$", line):
            summary_parts.append(line)

    return role, " ".join(summary_parts).strip()


def looks_like_company_line(line: str) -> bool:
    """
    True если строка — заголовок позиции/компании или строка с датой,
    которую нужно убрать из блока experience.
    """
    if len(line) > 120:
        return False
    has_date = bool(DATE_RANGE_RE.search(line) or SHORT_DATE_RE.search(line))
    if has_date:
        return True
    # Короткая строка с заглавной, без точки, без глаголов → заголовок
    if len(line) <= 60 and not line.endswith(".") and line[:1].isupper():
        verb_re = re.compile(
            r"\b(разработ|внедр|реализ|созда|оптимиз|вел|участв"
            r"|participated|developed|built|led|managed|designed"
            r"|implemented|improved|created|maintained|delivered"
            r"|established|launched|reduced|increased)\b",
            re.IGNORECASE,
        )
        if not verb_re.search(line):
            return True
    return False


def strip_company_dates(experience_text: str) -> str:
    """Убирает строки с компаниями/датами, оставляет описания."""
    if not experience_text:
        return ""
    lines = [ln.strip() for ln in experience_text.splitlines() if ln.strip()]
    kept = [ln for ln in lines if not looks_like_company_line(ln)]
    return " ".join(kept).strip()


def parse_cv_sections(file_url: str, data_dir: Path = DATA_DIR) -> dict:
    """
    Разбирает PDF на секции. Возвращает:
        role_position, skills, about_me_summary, experience
    Из experience убраны строки компаний и дат.
    """
    raw_text = extract_pdf_text(file_url, data_dir)
    if not raw_text:
        return {
            k: "" for k in ("role_position", "skills", "about_me_summary", "experience")
        }

    text = clean_cv_text(raw_text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    buckets: dict[str, list[str]] = {
        k: [] for k in ("role_position", "skills", "about_me_summary", "experience")
    }
    current_section: str | None = None
    intro_lines: list[str] = []

    for line in lines:
        detected = detect_section(line)

        if detected == "__ignored__":
            current_section = "__ignored__"
            continue

        if detected:
            current_section = detected
            continue

        if current_section and current_section in buckets:
            buckets[current_section].append(line)
        elif current_section is None:
            intro_lines.append(line)
        # else: __ignored__ section — пропускаем

    result = {key: " ".join(val) for key, val in buckets.items()}
    result["experience"] = strip_company_dates(result["experience"])

    # Из преамбулы берём роль и саммари, если не нашли в явных секциях
    intro_role, intro_summary = extract_intro_summary(intro_lines)
    if not result["role_position"]:
        result["role_position"] = intro_role
    if not result["about_me_summary"] and intro_summary:
        result["about_me_summary"] = intro_summary

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
            raw[pid] = {"id": pid, **sections, "_feedback_parts": []}

        fb = clean_feedback_text(extract_message_text(msg))
        if fb:
            raw[pid]["_feedback_parts"].append(fb)

    cases = []
    for entry in raw.values():
        entry["feedback"] = "\n\n".join(entry.pop("_feedback_parts"))
        cases.append(entry)

    return cases


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def load_messages(path: Path = INPUT_PATH) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["messages"]


def save_cases(cases: list[dict], path: Path = OUTPUT_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)


cases = build_cases(load_messages())

if __name__ == "__main__":
    save_cases(cases)
    print(f"Saved {len(cases)} cases to {OUTPUT_PATH}")

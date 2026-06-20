import json
import re
import unicodedata
from pathlib import Path

try:
    import fitz
except ImportError as exc:
    raise RuntimeError(
        "PyMuPDF is required for PDF parsing. Install it with: pip install PyMuPDF"
    ) from exc


DATA_DIR = Path(__file__).resolve().parent / "data"
INPUT_PATH = DATA_DIR / "result.json"
OUTPUT_PATH = DATA_DIR / "cases.json"

ADMINS = {
    "Aleksandr Valuev",
    "Artem K",
    "Anna [job offer USA 🇺🇸] Naumova",
}

CV_SECTION_ALIASES = {
    "role_position": {
        "role",
        "position",
        "job title",
        "desired position",
        "title",
        "роль",
        "позиция",
        "должность",
        "желаемая должность",
        "специализация",
    },
    "skills": {
        "skills",
        "technical skills",
        "tech skills",
        "technologies",
        "technology stack",
        "tech stack",
        "stack",
        "навыки",
        "технические навыки",
        "ключевые навыки",
        "стек",
        "технологии",
    },
    "about_me_summary": {
        "about",
        "about me",
        "additional information",
        "summary",
        "profile",
        "professional summary",
        "objective",
        "о себе",
        "обо мне",
        "дополнительная информация",
        "профиль",
        "резюме",
        "кратко",
    },
    "experience": {
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "career history",
        "опыт",
        "опыт работы",
        "профессиональный опыт",
        "карьера",
    },
}

IGNORED_SECTION_ALIASES = {
    "achievements",
    "certificates",
    "certifications",
    "education",
    "languages",
    "leadership",
    "projects",
    "volunteering",
    "достижения",
    "образование",
    "проекты",
    "сертификаты",
    "языки",
}

SECTION_BY_ALIAS = {
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


def extract_text(message):
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


def clean_feedback_text(text):
    text = unicodedata.normalize("NFKC", text)
    text = EMOJI_PATTERN.sub("", text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_cv_text(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_pdf_message(message):
    file_url = message.get("file", "")
    mime_type = message.get("mime_type", "")
    file_name = message.get("file_name", "")

    return (
        mime_type == "application/pdf"
        or file_url.lower().endswith(".pdf")
        or file_name.lower().endswith(".pdf")
    )


def pdf_path_from_url(file_url):
    if not file_url:
        return None

    return DATA_DIR / file_url


def extract_pdf_text(file_url):
    pdf_path = pdf_path_from_url(file_url)
    if not pdf_path or not pdf_path.exists():
        return ""

    with fitz.open(pdf_path) as document:
        return "\n".join(page.get_text("text", sort=True) for page in document)


def normalize_heading(text):
    text = unicodedata.normalize("NFKC", text)
    text = text.lower().strip(" :-–—|•·")
    text = re.sub(r"\s+", " ", text)
    return text


def detect_section(line):
    normalized = normalize_heading(line)
    if normalized in SECTION_BY_ALIAS:
        return SECTION_BY_ALIAS[normalized]

    return None


def normalize_section_text(lines):
    text = "\n".join(line.strip() for line in lines if line.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_role_from_intro(intro_lines):
    skipped_patterns = (
        r"@",
        r"https?://",
        r"linkedin",
        r"github",
        r"telegram",
        r"phone",
        r"email",
        r"тел",
        r"почт",
    )

    for line in intro_lines:
        clean_line = line.strip(" |")
        if not clean_line:
            continue
        if any(
            re.search(pattern, clean_line, re.IGNORECASE)
            for pattern in skipped_patterns
        ):
            continue
        if len(clean_line) > 90:
            continue
        return clean_line

    return ""


def parse_cv_pdf(file_url):
    raw_text = extract_pdf_text(file_url)
    text = clean_cv_text(raw_text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    data = {
        "role_position": "",
        "skills": "",
        "about_me_summary": "",
        "experience": "",
    }

    current_section = None
    intro_lines = []

    for line in lines:
        detected_section = detect_section(line)
        if detected_section:
            current_section = detected_section
            continue

        if current_section:
            data[current_section] = normalize_section_text(
                [data[current_section], line]
            )
        else:
            intro_lines.append(line)

    if not data["role_position"]:
        data["role_position"] = extract_role_from_intro(intro_lines)

    return data


def build_cases(messages):
    messages_by_id = {message["id"]: message for message in messages if "id" in message}

    cases_by_id = {}

    for message in messages:
        if message.get("from") not in ADMINS:
            continue

        parent = messages_by_id.get(message.get("reply_to_message_id"))
        if not parent or not is_pdf_message(parent):
            continue

        parent_id = parent["id"]
        case = cases_by_id.setdefault(
            parent_id,
            {
                "id": parent_id,
                "data": parse_cv_pdf(parent.get("file")),
                "feedback": "",
                "_feedback_parts": [],
            },
        )

        feedback_text = clean_feedback_text(extract_text(message))
        if feedback_text:
            case["_feedback_parts"].append(feedback_text)

    cases = []
    for case in cases_by_id.values():
        case["feedback"] = "\n\n".join(case.pop("_feedback_parts"))
        cases.append(case)

    return cases


def load_messages(path=INPUT_PATH):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data["messages"]


def save_cases(cases, path=OUTPUT_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)


cases = build_cases(load_messages())


if __name__ == "__main__":
    save_cases(cases)
    print(f"Saved {len(cases)} cases to {OUTPUT_PATH}")

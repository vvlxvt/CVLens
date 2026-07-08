from dotenv import load_dotenv
from openai import OpenAI
from ollama import chat
import json
import os


from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "extract" / "data"

load_dotenv()

groq_api_key = os.getenv("GROQ_API_KEY")

openai_client = OpenAI(
    api_key=groq_api_key,
    base_url="https://api.groq.com/openai/v1",
)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
ollama_num_gpu = int(os.getenv("OLLAMA_NUM_GPU", "0"))


MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL_NAME", "llama3.2:3b")


def generate_response(prompt: str, json_mode: bool = False, system: str | None = None):

    default_system = (
        "You are a strict information extraction engine. "
        "You ALWAYS return ONLY valid JSON. No markdown. No text."
    )

    if LLM_PROVIDER == "openai":
        if not openai_client:
            raise ValueError("OPENAI_API_KEY is missing")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = openai_client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        return json.loads(content) if json_mode else content

    # Default: Ollama
    options = {
        "num_gpu": ollama_num_gpu,
        "temperature": 0,
    }

    response = chat(
        model=OLLAMA_MODEL,
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


SYSTEM = """You are a JSON extraction bot.
You extract structured data from CV header text.
You MUST respond with valid JSON only.
No markdown. No explanations. No extra text.
Output MUST start with { and end with }.
Output MUST contain ONLY these three keys: full_name, role_position, summary.
Do NOT add any other keys."""

USER_TEMPLATE = """Extract data from this CV header.

RULES:
- full_name: person's full name only (e.g. "John Smith"). NOT a job title. If unclear → null
- role_position: job title or position (e.g. "Software Engineer", "Product Manager"). NOT a name. If missing → null
- summary: 1-2 sentence professional summary if present. If missing → null
- Use JSON null (not the string "null") for missing values
- Output ONLY these three keys, nothing else

EXAMPLE OUTPUT:
{{
  "full_name": "John Smith",
  "role_position": "Backend Engineer",
  "summary": "5 years of experience building scalable APIs."
}}

CV HEADER:
{intro_text}"""


def build_intro_prompt(intro_text: str) -> tuple[str, str]:
    return SYSTEM, USER_TEMPLATE.format(intro_text=intro_text)


def extract_intro_data(intro_lines: str) -> dict:
    print("=== INTRO TEXT ===")
    print(repr(intro_lines))
    print("==================")
    system, user = build_intro_prompt(intro_lines)
    return generate_response(user, json_mode=True, system=system)


FEEDBACK_SYSTEM = """You are a JSON extraction bot.
You extract structured data from recruiter feedback about a CV/resume.
You MUST respond with valid JSON only.
No markdown. No explanations. No extra text.
Output MUST start with { and end with }.
Output MUST contain ONLY these two keys: feedback_summary, feedback_sections.
Do NOT add any other keys."""

FEEDBACK_USER_TEMPLATE = """Extract data from this recruiter feedback about a CV.
Feedback may be in Russian or English, may be messy (copy-pasted with ">>" separators,
line breaks, quoted bullet points from the CV), and may not relate to the CV at all
(a side question, a link, small talk).

RULES:
- feedback_summary: 1-2 sentence summary of what the recruiter is actually saying,
  written in the same language as the feedback. If the feedback is empty, is just a
  side question unrelated to CV content (e.g. "Is there a Google office in Kazakhstan?"),
  or is just a link/video recommendation with no direct CV critique → null
- feedback_sections: a JSON array of CV sections the feedback gives critique/advice about.
  Allowed values ONLY:
    "experience"        - work history, bullet points, achievements, metrics, wording of duties
    "skills"             - tech stack / skills list, outdated or irrelevant technologies
    "about_me_summary"   - the "about me" / professional summary section
    "role_position"      - job title / desired position framing
    "formatting"          - layout, length, links, contact info, structure, visual presentation
  If the feedback is about the CV as a whole (general career advice) or gives no concrete
  CV critique (a question, off-topic remark, link) → null (not an empty array)
- Use JSON null (not the string "null") for missing/unclear values
- Output ONLY these two keys, nothing else

EXAMPLES:

Feedback: "Что это?"
Output:
{{
  "feedback_summary": "Рекрутер не понимает, о чём идёт речь.",
  "feedback_sections": null
}}

Feedback: "У тебя в резюме: WinForms, WebForms, WPF, XAML, SVN. Где мой 2007й"
Output:
{{
  "feedback_summary": "Стек в резюме выглядит устаревшим для рынка 2026 года.",
  "feedback_sections": ["skills"]
}}

Feedback: "15 критических прям много лишнего, много буллетов ужимаются в 1... Fixed critical video call bugs - буллет ни о чём"
Output:
{{
  "feedback_summary": "Опыт перегружен лишними деталями, часть буллетов не несёт ценности и требует сокращения.",
  "feedback_sections": ["experience"]
}}

Feedback: "LinkedIn можно сделать красивую ссылку в настройках."
Output:
{{
  "feedback_summary": "Совет оформить ссылку на LinkedIn как гиперссылку вместо сырого текста.",
  "feedback_sections": ["formatting"]
}}

RECRUITER FEEDBACK:
{feedback_text}"""


def build_feedback_prompt(feedback_text: str) -> tuple[str, str]:
    return FEEDBACK_SYSTEM, FEEDBACK_USER_TEMPLATE.format(feedback_text=feedback_text)


ALLOWED_SECTIONS = (
    "role_position",
    "skills",
    "about_me_summary",
    "experience",
    "formatting",
)


def extract_feedback_data(feedback_text: str) -> dict:
    print("=== FEEDBACK TEXT ===")
    print(repr(feedback_text))
    print("=====================")
    system, user = build_feedback_prompt(feedback_text)
    result = generate_response(user, json_mode=True, system=system)

    sections = result.get("feedback_sections")

    if sections:
        # keep only allowed values, drop anything the model hallucinated
        filtered = [s for s in sections if s in ALLOWED_SECTIONS]
        result["feedback_sections"] = filtered or None
    else:
        result["feedback_sections"] = None

    return result


if __name__ == "__main__":
    import json as _json

    with open(DATA_DIR / "cases.json", encoding="utf-8") as f:
        cases = _json.load(f)

    for case in cases:
        result = extract_feedback_data(case["feedback"])

        print(case["id"], "->", result)

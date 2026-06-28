from dotenv import load_dotenv
from openai import OpenAI
from ollama import chat
import json
import os

load_dotenv()

groq_api_key = os.getenv("GROQ_API_KEY")

openai_client = OpenAI(
    api_key=groq_api_key,
    base_url="https://api.groq.com/openai/v1",
)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
ollama_num_gpu = int(os.getenv("OLLAMA_NUM_GPU", "0"))

MODEL = "llama-3.3-70b-versatile"
OLLAMA_MODEL = "llama3.2:3b"
# MODEL = "llama-3.1-8b-instant"


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

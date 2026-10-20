"""Central application settings.

Single source of truth for every configurable value: reads the `.env` file
in the project root plus real environment variables (env vars win), so
secret keys stay out of the code and defaults stay in one place.
"""

from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API security --------------------------------------------------------
    # Bearer token required for data routes; empty = auth disabled (local dev).
    api_token: str = ""
    # Comma-separated browser origins allowed by CORS; empty = same-origin only.
    cors_origins: Annotated[list[str], NoDecode] = []

    # --- Upload limits (bytes / counts) -------------------------------------
    max_upload_bytes: int = 100 * 1024 * 1024
    max_result_json_bytes: int = 50 * 1024 * 1024
    max_pdf_bytes: int = 20 * 1024 * 1024
    max_pdf_files_per_upload: int = 200

    # --- Database ------------------------------------------------------------
    # Absolute path: the DB must not depend on the process working directory.
    db_path: Path = ROOT_DIR / "resumes.db"

    # --- Qdrant ---------------------------------------------------------------
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_verify_ssl: bool = False

    # --- LLM provider ---------------------------------------------------------
    # ollama (default, local) | openai (Groq) | openrouter / official_openai
    llm_provider: str = "ollama"
    ollama_model_name: str = "llama3.2:3b"
    ollama_num_gpu: int = 0
    groq_api_key: str | None = None
    groq_model: str = "qwen/qwen3.8-27b"
    open_router_key: str | None = None
    openrouter_model: str = "openrouter/auto"

    # --- Parser ---------------------------------------------------------------
    # Base URL used by extract/parser.py when calling the API.
    api_base_url: str = "http://127.0.0.1:8000"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("qdrant_verify_ssl", mode="before")
    @classmethod
    def _parse_bool_aliases(cls, value: object) -> object:
        # Accept the "true/1/yes" spellings documented in .env.
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return value


settings = Settings()

"""HTML pages of the web UI (public; they only serve static shells)."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "web"))


def _render(request: Request, name: str):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={"request": request},
    )


@router.get("/")
def serve_index(request: Request):
    """Serve the main CV listing page."""
    return _render(request, "index.html")


@router.get("/upload")
def serve_upload(request: Request):
    """Serve the upload page."""
    return _render(request, "upload.html")


@router.get("/prompts")
def serve_prompts(request: Request):
    """Serve the prompts management page."""
    return _render(request, "prompts.html")


@router.get("/rules")
def serve_review_rules(request: Request):
    """Serve the review rule evolution page."""
    return _render(request, "review_rules.html")


@router.get("/search")
def serve_search(request: Request):
    """Serve the vector similarity search page."""
    return _render(request, "search.html")

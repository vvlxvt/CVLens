"""Тесты для db.resumes_repo — CRUD операции с резюме."""

import pytest

from db.connection import SessionLocal
from db.models import Base
from db import resumes_repo
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def repo_session():
    """Интеграционная сессия с real SQLite in-memory для тестов репозитория."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    yield session
    session.close()


class TestUpsert:
    """Тесты функции upsert — вставка и обновление резюме."""

    def test_creates_new_resume(self, repo_session):
        """Новое резюме создаётся и возвращает id."""
        data = {
            "resume_id": "123",
            "full_name": "John Doe",
            "role_position": "Developer",
        }
        new_id = resumes_repo.upsert(repo_session, data)
        assert new_id is not None
        assert isinstance(new_id, int)

    def test_updates_existing_resume(self, repo_session):
        """Существующее резюме обновляется по resume_id."""
        initial = {"resume_id": "456", "full_name": "Old Name", "role_position": "Junior"}
        resumes_repo.upsert(repo_session, initial)

        updated = {"resume_id": "456", "full_name": "New Name", "role_position": "Senior"}
        new_id = resumes_repo.upsert(repo_session, updated)

        assert new_id is not None
        # Проверяем, что не создалась новая строка
        count = repo_session.query(resumes_repo.Resume).filter_by(resume_id="456").count()
        assert count == 1

    def test_get_by_resume_id_returns_none_when_missing(self, repo_session):
        """get_by_resume_id возвращает None для несуществующего ID."""
        result = resumes_repo.get_by_resume_id(repo_session, "nonexistent")
        assert result is None

    def test_get_by_resume_id_returns_resume(self, repo_session):
        """get_by_resume_id возвращает существующее резюме."""
        data = {"resume_id": "789", "full_name": "Test User", "skills": "Python"}
        resumes_repo.upsert(repo_session, data)

        result = resumes_repo.get_by_resume_id(repo_session, "789")
        assert result is not None
        assert result.resume_id == "789"
        assert result.full_name == "Test User"
        assert result.skills == "Python"

    def test_upsert_cleans_feedback_sections(self, repo_session):
        """feedback_sections очищается от значений вне ALLOWED_SECTIONS."""
        data = {
            "resume_id": "101",
            "feedback_sections": ["skills", "invalid_section", "experience", "also_invalid"],
        }
        resumes_repo.upsert(repo_session, data)

        result = resumes_repo.get_by_resume_id(repo_session, "101")
        assert result.feedback_sections == ["skills", "experience"]


class TestUpdateFeedback:
    """Тесты для update_feedback и update_about."""

    def test_update_feedback_modifies_fields(self, repo_session):
        """update_feedback обновляет поля фидбэка."""
        resumes_repo.upsert(repo_session, {"resume_id": "200", "full_name": "User"})
        success = resumes_repo.update_feedback(
            repo_session, resume_id="200",
            feedback_summary="Great candidate",
            feedback_sections=["skills"],
            llm="test-model",
            prompt_id=1,
        )
        assert success is True
        result = resumes_repo.get_by_resume_id(repo_session, "200")
        assert result.feedback_summary == "Great candidate"
        assert result.feedback_sections == ["skills"]
        assert result.feedback_llm == "test-model"

    def test_update_feedback_returns_false_when_missing(self, repo_session):
        """update_feedback возвращает False для несуществующего резюме."""
        success = resumes_repo.update_feedback(
            repo_session, resume_id="999",
            feedback_summary="x", feedback_sections=None,
            llm="model", prompt_id=1,
        )
        assert success is False

    def test_update_about_modifies_fields(self, repo_session):
        """update_about обновляет поля about/intro."""
        resumes_repo.upsert(repo_session, {"resume_id": "201", "full_name": "User"})
        success = resumes_repo.update_about(
            repo_session, resume_id="201",
            full_name="New Name", role_position="Lead",
            about_summary="Summary text", llm="model", prompt_id=1,
        )
        assert success is True
        result = resumes_repo.get_by_resume_id(repo_session, "201")
        assert result.full_name == "New Name"
        assert result.role_position == "Lead"
        assert result.about_summary == "Summary text"


class TestListPaginated:
    """Тесты для list_paginated."""

    def test_returns_paginated_results(self, repo_session):
        """Возвращаются страница и общее количество."""
        for i in range(25):
            resumes_repo.upsert(repo_session, {"resume_id": str(i), "full_name": f"User {i}"})

        rows, total = resumes_repo.list_paginated(repo_session, skip=0, limit=10)
        assert total == 25
        assert len(rows) == 10

    def test_second_page(self, repo_session):
        """Вторая страница возвращает следующие записи."""
        for i in range(25):
            resumes_repo.upsert(repo_session, {"resume_id": str(i), "full_name": f"User {i}"})

        rows, _ = resumes_repo.list_paginated(repo_session, skip=10, limit=10)
        assert len(rows) == 10
        assert rows[0].resume_id == "10"


class TestListByFeedbackSection:
    """Тесты для list_by_feedback_section — SQL JSON1 фильтрация."""

    def test_returns_resumes_with_matching_section(self, repo_session):
        """Возвращаются только резюме с указанной секцией в feedback_sections."""
        resumes_repo.upsert(repo_session, {
            "resume_id": "1", "feedback_sections": ["skills", "experience"],
        })
        resumes_repo.upsert(repo_session, {
            "resume_id": "2", "feedback_sections": ["skills"],
        })
        resumes_repo.upsert(repo_session, {
            "resume_id": "3", "feedback_sections": ["experience"],
        })
        resumes_repo.upsert(repo_session, {
            "resume_id": "4", "feedback_sections": None,
        })

        result = resumes_repo.list_by_feedback_section(repo_session, "skills")
        ids = sorted(r.resume_id for r in result)
        assert ids == ["1", "2"]

    def test_returns_empty_when_no_match(self, repo_session):
        """Возвращается пустой список, если ни у кого нет такой секции."""
        resumes_repo.upsert(repo_session, {
            "resume_id": "1", "feedback_sections": ["skills"],
        })
        result = resumes_repo.list_by_feedback_section(repo_session, "formatting")
        assert result == []

    def test_ignores_null_sections(self, repo_session):
        """Резюме с NULL feedback_sections не попадают в результат."""
        resumes_repo.upsert(repo_session, {
            "resume_id": "1", "feedback_sections": None,
        })
        resumes_repo.upsert(repo_session, {
            "resume_id": "2", "feedback_sections": ["skills"],
        })
        result = resumes_repo.list_by_feedback_section(repo_session, "skills")
        assert len(result) == 1
        assert result[0].resume_id == "2"


class TestListPaginatedWithSection:
    """Тесты для list_paginated с фильтром section (SQL JSON1)."""

    def test_paginated_section_filter(self, repo_session):
        """Фильтрация по секции работает с пагинацией и корректным total."""
        for i in range(15):
            sections = ["skills"] if i % 2 == 0 else ["experience"]
            resumes_repo.upsert(repo_session, {
                "resume_id": str(i), "feedback_sections": sections,
            })

        rows, total = resumes_repo.list_paginated(
            repo_session, skip=0, limit=5, section="skills"
        )
        assert total == 8  # even numbers 0,2,4,6,8,10,12,14
        assert len(rows) == 5
        assert all(r.feedback_sections and "skills" in r.feedback_sections for r in rows)

    def test_paginated_section_second_page(self, repo_session):
        """Вторая страницы с фильтром по секции."""
        for i in range(15):
            sections = ["skills"] if i % 2 == 0 else ["experience"]
            resumes_repo.upsert(repo_session, {
                "resume_id": str(i), "feedback_sections": sections,
            })

        rows, total = resumes_repo.list_paginated(
            repo_session, skip=5, limit=5, section="skills"
        )
        assert total == 8
        assert len(rows) == 3  # only 3 remaining after skip=5
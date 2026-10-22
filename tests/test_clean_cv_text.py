"""Тесты для чистки текста резюме и определения секций."""

import pytest

from extract.parser import clean_cv_text, detect_section, SECTION_BY_ALIAS


class TestCleanCvText:
    """Тесты функции clean_cv_text — нормализация текста PDF-резюме."""

    def test_normalizes_unicode_and_whitespace(self):
        """Лишние пробелы и невидимые символы удаляются."""
        raw = "Hello   World\u200b\ufeff\n\n  Section  \n  content  "
        result = clean_cv_text(raw)
        assert "\u200b" not in result
        assert "\ufeff" not in result
        assert "   " not in result  # нет тройных пробелов

    def test_removes_bullet_markers(self):
        """Маркеры буллетов в начале строк удаляются."""
        raw = "\u2022 skill one\n\u2023 skill two\n\u00b7 skill three"
        result = clean_cv_text(raw)
        assert "\u2022" not in result
        assert "\u2023" not in result
        assert "\u00b7" not in result

    def test_preserves_section_structure(self):
        """Блоки разделены двойными переносами строк."""
        raw = "Preamble text\n\nSkills\nPython\nDjango\n\nExperience\nDev at Corp"
        result = clean_cv_text(raw)
        blocks = result.split("\n\n")
        assert any("Skills" in b for b in blocks)
        assert any("Experience" in b for b in blocks)

    def test_empty_string_returns_empty(self):
        """Пустая строка возвращает пустую строку."""
        assert clean_cv_text("") == ""

    def test_only_whitespace_returns_empty(self):
        """Строка из одних пробелов возвращает пустую строку."""
        assert clean_cv_text("   \n\n  ") == ""


class TestDetectSection:
    """Тесты функции detect_section — распознавание заголовков секций."""

    @pytest.mark.parametrize("alias", ["skills", "technical skills", "tech stack", "stack"])
    def test_recognizes_skills_aliases(self, alias):
        """Различные алиасы секции навыков распознаются как 'skills'."""
        assert detect_section(alias) == "skills"

    @pytest.mark.parametrize("alias", ["experience", "work experience", "employment history", "career history"])
    def test_recognizes_experience_aliases(self, alias):
        """Различные алиасы секции опыта распознаются как 'experience'."""
        assert detect_section(alias) == "experience"

    @pytest.mark.parametrize("alias", ["about", "about me", "summary", "profile", "objective"])
    def test_recognizes_about_aliases(self, alias):
        """Алиасы секции about_me_summary распознаются."""
        assert detect_section(alias) == "about_me_summary"

    def test_ignored_sections_return_marker(self):
        """Игнорируемые секции возвращают '__ignored__'."""
        for section in ("education", "certificates", "languages", "projects"):
            assert detect_section(section) == "__ignored__"

    def test_colon_terminated_line_is_not_heading(self):
        """Строка, заканчивающаяся на ':', не считается заголовком секции."""
        assert detect_section("Name:") is None
        assert detect_section("Email:") is None

    def test_unknown_line_returns_none(self):
        """Неизвестная строка возвращает None."""
        assert detect_section("Some random text") is None
        assert detect_section("") is None

    def test_case_insensitive(self):
        """Распознавание не зависит от регистра."""
        assert detect_section("SKILLS") == "skills"
        assert detect_section("Experience") == "experience"

    def test_with_whitespace_and_punctuation(self):
        """Лишние пробелы и пунктуация по краям игнорируются."""
        assert detect_section("  Skills  ") == "skills"
        assert detect_section("- Skills -") == "skills"
        assert detect_section("| Experience |") == "experience"
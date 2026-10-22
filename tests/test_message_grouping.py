"""Тесты для _group_messages_by_cv — парсинг фидбэка из Telegram экспорта."""

import pytest

from extract.parser import _group_messages_by_cv, ADMINS

# Используем реальные имена из константы ADMINS
ADMIN_NAME = next(iter(ADMINS))  # первое имя из множества администраторов
SECOND_ADMIN = None
for name in ADMINS:
    if name != ADMIN_NAME:
        SECOND_ADMIN = name
        break


class TestGroupMessagesByCv:
    """Тесты группировки сообщений админов под родительские CV-сообщения."""

    def test_groups_admin_feedback_under_pdf_parent(self):
        """Сообщение админа с reply_to_message_id на PDF группируется под родительское CV."""
        messages = [
            {"id": 1, "from": ADMIN_NAME, "mime_type": "application/pdf", "file": "cv1.pdf"},
            {"id": 2, "from": ADMIN_NAME, "reply_to_message_id": 1, "text": "Good candidate"},
            {"id": 3, "from": "Not Admin", "reply_to_message_id": 1, "text": "Irrelevant"},
        ]
        result = _group_messages_by_cv(messages)
        assert 1 in result
        assert result[1]["feedback_raw"] == "Good candidate"

    def test_ignores_messages_from_non_admins(self):
        """Сообщения не из списка ADMINS игнорируются."""
        messages = [
            {"id": 1, "from": ADMIN_NAME, "mime_type": "application/pdf", "file": "cv1.pdf"},
            {"id": 2, "from": "Random User", "reply_to_message_id": 1, "text": "Should be ignored"},
        ]
        result = _group_messages_by_cv(messages)
        assert result == {}

    def test_ignores_feedback_without_pdf_parent(self):
        """Ответ на не-PDF сообщение игнорируется."""
        messages = [
            {"id": 1, "from": ADMIN_NAME, "text": "Just a message"},
            {"id": 2, "from": ADMIN_NAME, "reply_to_message_id": 1, "text": "Feedback on non-PDF"},
        ]
        result = _group_messages_by_cv(messages)
        assert result == {}

    @pytest.mark.skipif(SECOND_ADMIN is None, reason="Нужен как минимум второй админ")
    def test_multiple_admins_feedback_combined(self):
        """Фидбэк от нескольких админов склеивается в одно поле feedback_raw."""
        messages = [
            {"id": 1, "from": ADMIN_NAME, "mime_type": "application/pdf", "file": "cv1.pdf"},
            {"id": 2, "from": ADMIN_NAME, "reply_to_message_id": 1, "text": "First part"},
            {"id": 3, "from": SECOND_ADMIN, "reply_to_message_id": 1, "text": "Second part"},
        ]
        result = _group_messages_by_cv(messages)
        assert "First part" in result[1]["feedback_raw"]
        assert "Second part" in result[1]["feedback_raw"]

    def test_feedback_without_text_is_skipped(self):
        """Сообщения без текста не добавляются в фидбэк."""
        messages = [
            {"id": 1, "from": ADMIN_NAME, "mime_type": "application/pdf", "file": "cv1.pdf"},
            {"id": 2, "from": ADMIN_NAME, "reply_to_message_id": 1},  # no text
        ]
        result = _group_messages_by_cv(messages)
        # Группа создаётся, но feedback_raw пустой, так как нет текста
        assert 1 in result
        assert result[1]["feedback_raw"] == ""

    @pytest.mark.skipif(SECOND_ADMIN is None, reason="Нужен как минимум второй админ")
    def test_multiple_cvs_grouped_independently(self):
        """Разные CV группируются независимо."""
        messages = [
            {"id": 1, "from": ADMIN_NAME, "mime_type": "application/pdf", "file": "cv1.pdf"},
            {"id": 2, "from": ADMIN_NAME, "reply_to_message_id": 1, "text": "Feedback for CV1"},
            {"id": 3, "from": ADMIN_NAME, "mime_type": "application/pdf", "file": "cv2.pdf"},
            {"id": 4, "from": ADMIN_NAME, "reply_to_message_id": 3, "text": "Feedback for CV2"},
        ]
        result = _group_messages_by_cv(messages)
        assert len(result) == 2
        assert result[1]["feedback_raw"] == "Feedback for CV1"
        assert result[3]["feedback_raw"] == "Feedback for CV2"

    def test_empty_messages_list(self):
        """Пустой список возвращает пустой словарь."""
        assert _group_messages_by_cv([]) == {}

    def test_message_without_id_is_skipped(self):
        """Сообщение без поля id пропускается при построении индекса."""
        messages = [
            {"from": ADMIN_NAME, "text": "No id"},
            {"id": 1, "from": ADMIN_NAME, "mime_type": "application/pdf", "file": "cv.pdf"},
            {"id": 2, "from": ADMIN_NAME, "reply_to_message_id": 1, "text": "Feedback"},
        ]
        result = _group_messages_by_cv(messages)
        assert 1 in result
        assert result[1]["feedback_raw"] == "Feedback"

    def test_admins_constant_contains_expected_names(self):
        """Константа ADMINS содержит ожидаемые имена администраторов."""
        assert "Aleksandr Valuev" in ADMINS
        assert "Maksim Pozharskiy" in ADMINS
        assert len(ADMINS) > 0
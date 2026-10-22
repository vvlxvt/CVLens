"""Тесты для _parse_json_content — восстановления JSON из ответов LLM."""

import pytest

from response import _parse_json_content


class TestParseJsonContent:
    """Тесты парсинга JSON-контента, возвращаемого LLM."""

    def test_valid_json_parses(self):
        """Валидный JSON парсится без проблем."""
        result = _parse_json_content('{"full_name": "John Doe", "role_position": "Developer"}')
        assert result == {"full_name": "John Doe", "role_position": "Developer"}

    def test_json_in_markdown_block(self):
        """JSON в markdown-блоке ```json ... ``` извлекается корректно."""
        content = """```json
        {"full_name": "Jane Smith", "role_position": "Engineer"}
        ```"""
        result = _parse_json_content(content)
        assert result == {"full_name": "Jane Smith", "role_position": "Engineer"}

    def test_json_with_extra_text_before(self):
        """JSON с текстом до него восстанавливается через raw_decode."""
        content = 'Here is the result: {"key": "value"}'
        result = _parse_json_content(content)
        assert result == {"key": "value"}

    def test_json_with_extra_text_after(self):
        """JSON с текстом после него восстанавливается."""
        content = '{"key": "value"} That is all.'
        result = _parse_json_content(content)
        assert result == {"key": "value"}

    def test_array_json_parses(self):
        """JSON-массив парсится корректно."""
        result = _parse_json_content('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_empty_string_raises_value_error(self):
        """Пустая строка вызывает ValueError."""
        with pytest.raises(ValueError, match="Empty response from LLM"):
            _parse_json_content("")

    def test_whitespace_only_raises_value_error(self):
        """Строка из пробелов вызывает ValueError."""
        with pytest.raises(ValueError, match="Empty response from LLM"):
            _parse_json_content("   ")

    def test_invalid_json_raises_value_error(self):
        """Невалидный JSON вызывает ValueError."""
        with pytest.raises(ValueError, match="LLM response did not contain valid JSON"):
            _parse_json_content("This is not JSON at all")

    def test_nested_json_recovers(self):
        """Вложенный JSON с лишним текстом восстанавливается."""
        content = 'Prefix text {"outer": {"inner": "value"}} suffix text'
        result = _parse_json_content(content)
        assert result == {"outer": {"inner": "value"}}

    def test_json_with_quotes_in_values(self):
        """JSON с кавычками внутри значений парсится."""
        content = '{"summary": "He said \\"hello\\" and left"}'
        result = _parse_json_content(content)
        assert result == {"summary": 'He said "hello" and left'}

    def test_literal_eval_fallback(self):
        """Python literal с одинарными кавычками восстанавливается через literal_eval."""
        content = "{'key': 'value', 'number': 42}"
        result = _parse_json_content(content)
        assert result == {"key": "value", "number": 42}
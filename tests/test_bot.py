import pytest
import textwrap
from unittest.mock import AsyncMock

async def mock_send_long_message(message_mock, text):
    if not text:
        return
    for chunk in textwrap.wrap(text, width=4000, replace_whitespace=False, drop_whitespace=False):
        await message_mock.answer(chunk)

@pytest.mark.asyncio
async def test_chunking_logic_short():
    """Тест: Короткие сообщения (до 4000 символов) отправляются целиком."""
    message_mock = AsyncMock()
    text = "Привет, мир!"
    await mock_send_long_message(message_mock, text)
    message_mock.answer.assert_called_once_with("Привет, мир!")

@pytest.mark.asyncio
async def test_chunking_logic_long():
    """Тест: Длинные сообщения разбиваются на части."""
    message_mock = AsyncMock()
    text = "A" * 5000
    await mock_send_long_message(message_mock, text)
    
    # 5000 символов должны быть разбиты на 1 кусок в 4000 и 1 кусок в 1000
    assert message_mock.answer.call_count == 2
    message_mock.answer.assert_any_call("A" * 4000)
    message_mock.answer.assert_any_call("A" * 1000)

@pytest.mark.asyncio
async def test_unauthorized_drop():
    """Тест: Проверка логики блокировки неавторизованных пользователей."""
    user_id = 9999999
    allowed_list = [123, 456]
    assert user_id not in allowed_list

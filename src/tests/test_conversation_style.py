from src.ai.conversation_style import (
    is_normal_mode,
    normalize_normal_response,
    normal_chat_style_instruction,
    style_mode_label,
)


def test_normal_chat_style_is_compact_and_conversational():
    instruction = normal_chat_style_instruction()
    assert "kurumsal" in instruction
    assert "1-3" in instruction
    assert "do\u011frudan gir" in instruction
    assert "kapan" in instruction
    assert "tek bir do\u011fal takip sorusu" in instruction
    assert "70 kelimeyi" in instruction
    assert "[telegram_user_id=...]" in instruction
    assert "genel grup analizi" in instruction
    assert "biÅŸey lazÄ±m mÄ±" in instruction
    assert "ne istiyorsun" in instruction
    assert "randomu" in instruction
    assert "nokta, virgÃ¼l" in instruction


def test_normal_mode_is_selectable_without_touching_other_modes():
    assert is_normal_mode("normal")
    assert is_normal_mode("su\u00e7 orta\u011f\u0131")
    assert style_mode_label("normal") == "Normal / su\u00e7 orta\u011f\u0131"
    assert not is_normal_mode("roast")


def test_normal_response_removes_service_bot_closers_and_relaxes_punctuation():
    value = normalize_normal_response("tamam hallederiz!!! BaÅŸka bir ÅŸey var mÄ±???")
    assert value == "tamam hallederiz!"


def test_normal_response_does_not_rewrite_normal_question_or_words():
    value = normalize_normal_response("bugÃ¼n ne istiyorsun? ben kahve iÃ§iyorum")
    assert value == "bugÃ¼n ne istiyorsun? ben kahve iÃ§iyorum"

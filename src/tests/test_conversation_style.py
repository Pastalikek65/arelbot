from src.ai.conversation_style import (
    chat_style_instruction,
    is_normal_mode,
    normal_chat_style_instruction,
    normalize_normal_response,
    style_mode_label,
)
from src.ai.personality import PersonalityPolicy
from src.ai.roast_repertoire import ROAST_PHRASES, roast_repertoire_instruction


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
    assert "bişey lazım mı" in instruction
    assert "ne istiyorsun" in instruction
    assert "randomu" in instruction
    assert "nokta, virgül" in instruction


def test_normal_mode_is_selectable_without_touching_other_modes():
    assert is_normal_mode("normal")
    assert is_normal_mode("su\u00e7 orta\u011f\u0131")
    assert style_mode_label("normal") == "Normal"
    assert not is_normal_mode("roast")


def test_normal_response_removes_service_bot_closers_and_relaxes_punctuation():
    value = normalize_normal_response("tamam hallederiz!!! Başka bir şey var mı???")
    assert value == "tamam hallederiz!"


def test_normal_response_does_not_rewrite_normal_question_or_words():
    value = normalize_normal_response("bugün ne istiyorsun? ben kahve içiyorum")
    assert value == "bugün ne istiyorsun? ben kahve içiyorum"


def test_roast_repertoire_is_diverse_and_varies_without_leaking_into_normal():
    assert len(ROAST_PHRASES) >= 24
    assert len(set(ROAST_PHRASES)) == len(ROAST_PHRASES)
    first = roast_repertoire_instruction("yine son güne bıraktım", 0)
    assert first == roast_repertoire_instruction("yine son güne bıraktım", 0)
    assert len({roast_repertoire_instruction("yine son güne bıraktım", turn)
                for turn in range(6)}) >= 3
    assert "ROAST REPERTUVARI" in first
    assert "ROAST REPERTUVARI" in chat_style_instruction(
        PersonalityPolicy().for_mode("roast"), "roast", "yine son güne bıraktım", 0)
    assert "ROAST REPERTUVARI" not in chat_style_instruction(
        PersonalityPolicy().for_mode("normal"), "normal", "yine son güne bıraktım", 0)

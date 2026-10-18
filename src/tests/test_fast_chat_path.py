from src.chat_engine import ChatEngine, _normalize_tool_calls, _requires_live_web_search


def test_identity_questions_are_answered_by_stable_arel_identity():
    assert ChatEngine._is_identity_query("Hangi model ve sa\u011flay\u0131c\u0131yla \u00e7al\u0131\u015f\u0131yorsun?") is True
    assert ChatEngine._is_identity_query("Bug\u00fcn nas\u0131ls\u0131n?") is False


def test_casual_chat_does_not_attach_tool_schemas():
    assert ChatEngine._should_attach_tools("merhaba, bugün nasılsın?") is False


def test_explicit_data_request_attaches_tool_schemas():
    assert ChatEngine._should_attach_tools("İstanbul hava durumu") is True
    assert ChatEngine._should_attach_tools("şu bağlantıyı incele https://example.com") is True



def test_current_exam_date_question_attaches_tools_and_requires_live_search():
    query = "KPSS orta\u00f6\u011fretim s\u0131nav\u0131 tarihi ne zaman?"
    assert _requires_live_web_search(query) is True
    assert ChatEngine._should_attach_tools(query) is True


def test_typed_weather_question_stays_on_weather_feature_route():
    assert _requires_live_web_search("\u0130stanbul hava durumu") is False


def test_explicit_web_search_request_is_live_even_without_known_entity():
    assert _requires_live_web_search("internette ara Arelbot s\u00fcr\u00fcm notlar\u0131") is True


def test_model_tool_calls_are_bounded_and_malformed_json_is_rejected():
    assert _normalize_tool_calls([{
        "id": "call-1",
        "function": {"name": "weather", "arguments": '{"city":"\u0130stanbul"}'},
    }]) == [{"id": "call-1", "name": "weather", "arguments": {"city": "\u0130stanbul"}}]
    assert _normalize_tool_calls([{
        "id": "call-1",
        "function": {"name": "weather", "arguments": "{"},
    }]) is None
    assert _normalize_tool_calls([{
        "id": "call-1",
        "function": {"name": "weather", "arguments": "x" * 16_385},
    }]) is None

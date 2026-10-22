"""Exercise real handler wiring, not isolated gallery constructors."""
from unittest.mock import MagicMock


def test_orchestrator_constructs_all_card_galleries_without_cookie_key(monkeypatch):
    import src.orchestrator as module

    engine = MagicMock()
    engine.db.available = False
    monkeypatch.setattr(module, "ChatEngine", lambda *args: engine)
    monkeypatch.setattr(module, "KeyManager", lambda **kwargs: MagicMock())
    monkeypatch.setattr(module, "McpManager", lambda *args: MagicMock())
    monkeypatch.setattr(module.Config, "TELEGRAM_TOKEN", "12345:fixture-bot-for-startup")
    monkeypatch.setattr(module.Config, "ENCRYPTION_KEY", None)
    orchestrator = module.Orchestrator()
    assert orchestrator.card_table_handler.inline_service is orchestrator.card_table_inline_service
    assert orchestrator.command_handler.batak_inline_service is orchestrator.batak_inline_service
    assert orchestrator.callback_handler.batak_inline_service is orchestrator.batak_inline_service
    assert orchestrator.uno_inline_service.secret == orchestrator.batak_inline_service.secret
    assert len(orchestrator.card_table_inline_service.file_ids) == 105

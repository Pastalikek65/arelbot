"""
Test fixtures ve konfigürasyon.
"""
import pytest


@pytest.fixture
def mock_update():
    class MockUser:
        id = 12345
        first_name = "Test"
        full_name = "Test User"
        username = "testuser"

    class MockChat:
        id = -1001234567890
        type = "supergroup"

    class MockMessage:
        text = "/test"
        chat = MockChat()
        from_user = MockUser()

        async def reply_text(self, text, parse_mode=None):
            return text

    class MockUpdate:
        effective_user = MockUser()
        effective_chat = MockChat()
        effective_message = MockMessage()
        message = MockMessage()

    return MockUpdate()


@pytest.fixture
def mock_context():
    class MockBot:
        username = "arel_test_bot"

        async def get_me(self):
            return self

        async def send_message(self, chat_id, text, parse_mode=None):
            return text

    class MockJobQueue:
        def run_once(self, callback, when, chat_id=None, data=None, name=None):
            pass

        def get_jobs_by_name(self, name):
            return []

    class MockContext:
        bot = MockBot()
        bot_info = None
        job_queue = MockJobQueue()
        chat_data = {}
        user_data = {}
        args = []

    return MockContext()


@pytest.fixture
def config_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "test:token")
    monkeypatch.setenv("FREEMODEL_KEY", "test-freemodel-key")
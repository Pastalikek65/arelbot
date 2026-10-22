"""
Tool Testleri - Tüm tool'ların temel davranışlarını test eder.
"""
import pytest
import httpx
from unittest.mock import Mock, AsyncMock, patch


class TestBaseToolInterface:
    """BaseTool arayüzü testleri."""

    def test_tool_has_required_attributes(self):
        """Tool'lar name ve description attribute'larına sahip olmalı."""
        from src.tools.web_search_tool import WebSearchTool
        from src.tools.memory_tool import MemoryTool
        from src.tools.web_reader_tool import WebReaderTool

        mock_key_manager = Mock()
        mock_key_manager.get_key = Mock(return_value="test_key")

        tools = [
            WebSearchTool(mock_key_manager),
            WebReaderTool(),
        ]

        for tool in tools:
            assert hasattr(tool, 'name'), f"{tool.__class__.__name__} missing 'name'"
            assert hasattr(tool, 'description'), f"{tool.__class__.__name__} missing 'description'"
            assert hasattr(tool, 'parameters'), f"{tool.__class__.__name__} missing 'parameters'"
            assert hasattr(tool, 'execute'), f"{tool.__class__.__name__} missing 'execute'"
            assert isinstance(tool.name, str), f"{tool.__class__.__name__}.name must be str"
            assert isinstance(tool.description, str), f"{tool.__class__.__name__}.description must be str"

    def test_tool_to_openai_def_format(self):
        """Tool'lar OpenAI formatında doğru yapı döndürmeli."""
        from src.tools.web_reader_tool import WebReaderTool

        tool = WebReaderTool()
        openai_def = tool.to_openai_def()

        assert "type" in openai_def
        assert openai_def["type"] == "function"
        assert "function" in openai_def
        assert "name" in openai_def["function"]
        assert "description" in openai_def["function"]
        assert "parameters" in openai_def["function"]

    def test_tool_to_gemini_def_format(self):
        """Tool'lar Gemini formatında doğru yapı döndürmeli."""
        from src.tools.web_reader_tool import WebReaderTool

        tool = WebReaderTool()
        gemini_def = tool.to_gemini_def()

        assert "name" in gemini_def
        assert "description" in gemini_def
        assert "parameters" in gemini_def


class TestMemoryTool:
    """MemoryTool testleri."""

    @pytest.fixture
    def mock_db(self):
        db = Mock()
        db.save_fact = AsyncMock()
        db.get_facts = AsyncMock(return_value=[])
        return db

    @pytest.fixture
    def memory_tool(self, mock_db):
        from src.tools.memory_tool import MemoryTool
        return MemoryTool(mock_db)

    @pytest.mark.asyncio
    async def test_remember_fact(self, memory_tool, mock_db):
        """Bilgi kaydetme testi."""
        result = await memory_tool.execute(
            action="remember",
            fact_key="favorite_team",
            user_id=12345,
            fact_value="Galatasaray"
        )

        assert "result" in result
        assert "Galatasaray" in result["result"]
        mock_db.save_fact.assert_called_once_with(12345, "favorite_team", "Galatasaray")

    @pytest.mark.asyncio
    async def test_remember_without_value_returns_error(self, memory_tool):
        """Value olmadan remember çağrıldığında hata dönmeli."""
        result = await memory_tool.execute(
            action="remember",
            fact_key="favorite_team",
            user_id=12345,
            fact_value=None
        )

        assert "error" in result

    @pytest.mark.asyncio
    async def test_recall_returns_dict(self, memory_tool, mock_db):
        """Recall fonksiyonu Dict döndürmeli."""
        mock_db.get_facts = AsyncMock(return_value=[
            {"key": "favorite_team", "value": "Galatasaray"}
        ])

        result = await memory_tool.execute(
            action="recall",
            fact_key="favorite_team",
            user_id=12345
        )

        assert "result" in result
        assert "Galatasaray" in result["result"]

    @pytest.mark.asyncio
    async def test_invalid_action_returns_error(self, memory_tool):
        """Geçersiz action için hata dönmeli."""
        result = await memory_tool.execute(
            action="invalid",
            fact_key="test",
            user_id=12345
        )

        assert "error" in result


class TestWebReaderTool:
    """WebReaderTool testleri."""

    @pytest.fixture
    def web_reader(self):
        from src.tools.web_reader_tool import WebReaderTool
        return WebReaderTool()

    @pytest.mark.asyncio
    async def test_execute_returns_dict(self, web_reader):
        from src.features.providers.web_reader import WebReaderProvider

        async def handler(request):
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<p>Test content</p>",
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            web_reader.provider = WebReaderProvider(client=client, resolve_host=False)
            result = await web_reader.execute(url="https://example.com")

        assert isinstance(result, dict)
        assert "result" in result
        assert "Test content" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_handles_error(self, web_reader):
        from src.features.providers.web_reader import WebReaderProvider

        async def handler(request):
            raise RuntimeError("Connection failed")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            web_reader.provider = WebReaderProvider(client=client, resolve_host=False)
            result = await web_reader.execute(url="https://example.com")

        assert isinstance(result, dict)
        assert result["error"] == "web_reader_source_unavailable"


class TestSpatulaTools:
    """Spatula tool'larının temel testleri."""

    def test_all_spatula_tools_have_proper_interface(self):
        """Tüm spatula tool'ları doğru arayüze sahip olmalı."""
        from src.tools.spatula_tool import (
            PharmacyTool, FuelTool, CurrencyTool, EarthquakeTool,
            BimDealsTool, NewsTool, WeatherTool, PrayerTimesTool,
            FlightTool, StoryTool
        )

        tools = [
            PharmacyTool(), FuelTool(), CurrencyTool(), EarthquakeTool(),
            BimDealsTool(), NewsTool(), WeatherTool(), PrayerTimesTool(),
            FlightTool(), StoryTool()
        ]

        for tool in tools:
            assert hasattr(tool, 'name')
            assert hasattr(tool, 'description')
            assert hasattr(tool, 'parameters')
            assert hasattr(tool, 'execute')
            assert isinstance(tool.name, str)
            assert len(tool.name) > 0


class TestGamesTool:
    """GamesTool testleri."""

    @pytest.fixture
    def games_tool(self):
        from src.tools.games_tool import GamesTool
        return GamesTool()

    def test_tool_has_proper_interface(self, games_tool):
        """GamesTool doğru arayüze sahip olmalı."""
        assert games_tool.name == "games_tool"
        assert "game_type" in str(games_tool.parameters)

    @pytest.mark.asyncio
    async def test_zar_command_returns_result(self, games_tool):
        """Zar atma komutu sonuç döndürmeli."""
        result = await games_tool.execute(game_type="zar")
        assert result is not None
        assert isinstance(result, (str, dict))

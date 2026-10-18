from typing import Dict, List, Any
from .tools.base import BaseTool
from src.security.log_redaction import summarize_exception
from src.security.error_codes import normalize_error_code
import logging

logger = logging.getLogger(__name__)

class ToolManager:
    def __init__(self):
        self.tools: Dict[str, BaseTool] = {}
        self.allowed_tools: set[str] | None = None
        # Provider-backed AI tools are routed through FeatureExecutor so the
        # model shares the typed provider, cache, health and audit path used by
        # Telegram commands. Local tools keep their direct service path.
        self._feature_executor = None
        self._feature_by_tool: dict[str, str] = {}
        self._feature_permissions: dict[str, str] = {}

    def bind_feature_executor(self, feature_executor: Any, feature_registry: Any) -> None:
        """Bind the typed feature runtime without importing feature modules."""
        self._feature_executor = feature_executor
        self._feature_by_tool = {
            str(definition.input_model): str(definition.key)
            for definition in feature_registry.definitions()
            if definition.provider_key and definition.input_model
        }
        self._feature_permissions = {}
        for definition in feature_registry.definitions():
            # Local tools may use their feature key as the actual tool name;
            # input_model names a request schema, not necessarily a tool.
            for name in {str(definition.key), str(definition.input_model or "")}:
                if name in self.tools:
                    self._feature_permissions[name] = str(definition.permission or "user")

    @staticmethod
    def _permission_error(tool_name: str, context: Dict[str, Any] | None, permission: str) -> dict[str, str] | None:
        """Enforce manifest scope before a model can execute a tool.

        Telegram handlers perform richer administrator checks for interactive
        callbacks. AI calls only receive a bounded session context, so scoped
        permissions fail closed when that context is absent or insufficient.
        """

        context = context or {}
        session_id = context.get("session_id")
        chat_type = str(context.get("chat_type") or "").casefold()
        is_group = chat_type in {"group", "supergroup"}
        if not is_group and isinstance(session_id, int):
            is_group = session_id < 0
        is_private = chat_type == "private" or (isinstance(session_id, int) and session_id >= 0)

        allowed = True
        if permission in {"user_private", "private"}:
            allowed = is_private
        elif permission in {"group_user", "group_admin"}:
            allowed = is_group
            if allowed and permission == "group_admin":
                allowed = bool(context.get("is_group_admin"))
        elif permission in {"admin", "owner"}:
            allowed = bool(context.get("is_admin"))
        elif permission == "system_after_user_action":
            allowed = bool(context.get("system_action"))
        elif permission == "user_or_group_admin":
            allowed = is_private or bool(context.get("is_group_admin"))

        if allowed:
            return None
        logger.warning(
            "Blocked tool outside manifest permission: %s (%s)",
            tool_name,
            permission,
        )
        return {
            "error": "TOOL_PERMISSION_DENIED",
            "error_code": "TOOL_PERMISSION_DENIED",
        }

    def set_allowlist(self, tool_names: set[str] | None) -> None:
        self.allowed_tools = set(tool_names) if tool_names else None

    def register_tool(self, tool: BaseTool):
        """Registers a tool instance."""
        self.tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")

    def get_tool(self, tool_name: str) -> BaseTool:
        """Retrieves a tool by name."""
        return self.tools.get(tool_name)

    def get_gemini_tools_def(self) -> List[Dict[str, Any]]:
        """Returns the list of tool definitions for Gemini configuration."""
        return [{"function_declarations": [t.to_gemini_def() for t in self._iter_ai_tools()]}]

    def get_openai_tools_def(self) -> List[Dict[str, Any]]:
        """Returns the list of tool definitions for OpenAI/Groq configuration."""
        return [t.to_openai_def() for t in self._iter_ai_tools()]

    def _iter_ai_tools(self) -> list[BaseTool]:
        """Return only tools exposed to AI provider tool-calling."""
        if self.allowed_tools is None:
            return list(self.tools.values())
        return [tool for name, tool in self.tools.items() if name in self.allowed_tools]

    @staticmethod
    def _sanitize_direct_result(result: Any) -> Dict[str, Any]:
        """Bound untyped tool error payloads before returning them to the model."""

        if not isinstance(result, dict):
            return {"result": result}
        if not result.get("error"):
            return result
        code = normalize_error_code(result.get("error_code") or result.get("error"), "TOOL_FAILED")
        return {
            "error": "Araç isteği güvenli biçimde tamamlanamadı.",
            "error_code": code,
        }

    async def execute_tool(self, tool_name: str, args: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Executes a registered tool.
        Context can provide extra info like session_id that model might not pass perfectly.
        """
        if tool_name not in self.tools:
            logger.warning(f"Attempted to execute unknown tool: {tool_name}")
            return {"error": f"Tool '{tool_name}' not found."}
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            logger.warning("Blocked tool outside AI allowlist: %s", tool_name)
            return {"error": "TOOL_NOT_ALLOWED", "error_code": "TOOL_NOT_ALLOWED"}

        permission = self._feature_permissions.get(tool_name)
        if permission:
            denied = self._permission_error(tool_name, context, permission)
            if denied is not None:
                return denied
        
        try:
            # Context is injected before either execution path. The model can
            # never select another user's memory/document namespace.
            call_args = args.copy()
            if context:
                if tool_name == "group_analytics" and "session_id" in context:
                    call_args["session_id"] = context["session_id"]
                if tool_name in {"memory_tool", "document_search"} and "user_id" in context:
                    call_args["user_id"] = context["user_id"]

            feature_key = self._feature_by_tool.get(tool_name)
            if self._feature_executor is not None and feature_key:
                result = await self._feature_executor.execute(feature_key, call_args)
                if not result.ok:
                    return {
                        "error": result.error_message or "Feature could not be completed.",
                        "error_code": result.error_code or "FEATURE_FAILED",
                        "correlation_id": result.correlation_id,
                    }
                payload = dict(result.data) if isinstance(result.data, dict) else {"result": result.data}
                # Give the model enough provenance to cite source/freshness;
                # provider internals and secrets remain outside this payload.
                payload.update(
                    {
                        "feature_key": feature_key,
                        "source": result.source,
                        "source_url": result.source_url,
                        "fetched_at": result.fetched_at.isoformat() if result.fetched_at else None,
                        "stale": result.stale,
                        "cache_hit": result.cache_hit,
                        "correlation_id": result.correlation_id,
                        "metadata": dict(result.metadata),
                    }
                )
                return payload

            tool = self.tools[tool_name]
            if hasattr(tool, 'execute'):
                return self._sanitize_direct_result(await tool.execute(**call_args))
        except Exception as e:
            logger.error("Error executing tool %s: %s", tool_name, summarize_exception(e, label="tool_execution"))
            return {"error": "tool_execution_failed", "error_code": "TOOL_EXECUTION_FAILED"}

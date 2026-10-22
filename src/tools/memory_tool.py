from typing import Dict, Any
import logging
from .base import BaseTool
import inspect
from ..security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)

class MemoryTool(BaseTool):
    """Tool for Arel to remember and recall persistent facts about users."""
    
    def __init__(self, db):
        super().__init__(
            name="memory_tool",
            description="Use this to save or look up important information about a user (like their hobbies, favorite team, city, or name)."
        )
        self.db = db

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["remember", "recall"],
                    "description": "Choose 'remember' to save a new fact or 'recall' to fetch existing ones."
                },
                "fact_key": {
                    "type": "string",
                    "description": "The category/key of the information (e.g., 'favorite_team', 'hobby', 'work')."
                },
                "fact_value": {
                    "type": "string",
                    "description": "The actual value to remember (only required for 'remember')."
                },
                "user_id": {
                    "type": "integer",
                    "description": "The Telegram User ID of the person."
                }
            },
            "required": ["action", "fact_key", "user_id"]
        }

    async def execute(self, action: str, fact_key: str, user_id: int, fact_value: str = None, **kwargs) -> Dict[str, Any]:
        try:
            consent_method = getattr(self.db, "get_memory_consent", None)
            consent = True
            if consent_method:
                consent_result = consent_method(user_id)
                consent = await consent_result if inspect.isawaitable(consent_result) else True
            if action == "remember":
                if not fact_value:
                    return {"error": "value is required to remember a fact."}
                if not consent:
                    return {"error": "Kişisel hafıza kapalı. Önce /hafiza on komutuyla açık rıza verin."}
                
                # Use high-level MongoDB method
                await self.db.save_fact(user_id, fact_key, fact_value)
                return {"result": f"Anlaşıldı! Bunu aklıma yazdım: {fact_key} = {fact_value}"}

            elif action == "recall":
                if not consent:
                    return {"result": "Kişisel hafıza bu kullanıcı için etkin değil."}
                # Use high-level MongoDB method
                facts = await self.db.get_facts(user_id, fact_key)
                
                if not facts:
                    return {"result": f"{user_id} hakkında henüz bu bilgiyi bilmiyorum."}
                
                result = f"{user_id} hakkında bildiklerim:\n"
                for f in facts:
                    result += f"- {f['key']}: {f['value']}\n"
                return {"result": result}
            
            else:
                return {"error": "memory_operation_failed"}

        except Exception as e:
            logger.error("MemoryTool error: %s", summarize_exception(e, label="memory"))
            return {"error": "memory_operation_failed"}

from typing import Dict, Any
from .base import BaseTool
from ..database import Database
from ..security.log_redaction import summarize_exception
import logging

logger = logging.getLogger(__name__)

class GroupAnalyticsTool(BaseTool):
    def __init__(self, db: Database):
        super().__init__(
            name="group_analytics",
            description="Analyzes chat history to provide statistics about the group, such as top active users, message counts, and general mood."
        )
        self.db = db

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "integer", 
                    "description": "The current chat session ID (provided by system, but model can verify)."
                },
                "analysis_type": {
                    "type": "string",
                    "description": "Type of analysis: 'top_users', 'total_messages', 'activity_summary'",
                    "enum": ["top_users", "total_messages", "activity_summary"]
                }
            },
            "required": ["analysis_type"]
        }

    async def execute(self, session_id: int, analysis_type: str) -> Dict[str, Any]:
        """
        Executes analytics queries on MongoDB.
        """
        try:
            result = {}
            
            if analysis_type == "top_users" or analysis_type == "activity_summary":
                # Use the new high-level MongoDB method
                top_users = await self.db.get_top_users(session_id, limit=5)
                result["top_users"] = top_users

            if analysis_type == "total_messages" or analysis_type == "activity_summary":
                # Use the new high-level MongoDB method
                total = await self.db.get_total_messages(session_id)
                result["total_messages"] = total

            return result
        except Exception as e:
            logger.error("GroupAnalyticsTool failed: %s", summarize_exception(e, label="group_analytics"))
            return {"error": "group_analytics_failed"}

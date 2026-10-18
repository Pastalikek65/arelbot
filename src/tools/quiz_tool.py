from typing import Dict, Any
import logging
import random
from .base import BaseTool
from ..security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)

class QuizTool(BaseTool):
    """Tool for Arel to host interactive trivia/quiz games in groups."""
    
    def __init__(self):
        super().__init__(
            name="quiz_tool",
            description="Use this to start a trivia quiz game or ask a random question to the group."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category for the quiz (e.g., General, Science, History, Sports)."
                },
                "difficulty": {
                    "type": "string",
                    "enum": ["easy", "medium", "hard"],
                    "description": "Difficulty level."
                }
            },
            "required": []
        }

    async def execute(self, category: str = "Genel Kültür", difficulty: str = "medium", **kwargs) -> Dict[str, Any]:
        try:
            logger.info(f"Starting quiz: {category} ({difficulty})")
            
            result = (
                f"🎯 <b>BİLGİ YARIŞMASI BAŞLIYOR!</b>\n\n"
                f"Kategori: {category}\n"
                f"Zorluk: {difficulty}\n\n"
                f"Lütfen şimdi gruba harika bir soru sor ve şıkları belirt. "
                f"İlk doğru cevabı vereni tebrik etmeyi unutma!"
            )
            return {"result": result}
        except Exception as e:
            logger.error("QuizTool error: %s", summarize_exception(e, label="quiz"))
            return {"error": "quiz_start_failed"}

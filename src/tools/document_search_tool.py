from typing import Dict, Any
from .base import BaseTool
from ..rag_engine import RAGEngine
from ..ai.prompt_policy import untrusted_context
import logging

logger = logging.getLogger(__name__)

class DocumentSearchTool(BaseTool):
    def __init__(self, rag_engine: RAGEngine):
        super().__init__(
            name="document_search",
            description="Searches through the caller's uploaded PDF, DOCX and UTF-8 text documents. Use this for a file the user shared or private document knowledge."
        )
        self.rag_engine = rag_engine

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string", 
                    "description": "The specific question or topic to search for in the documents."
                }
            },
            "required": ["query"]
        }

    async def execute(self, query: str, user_id: int | None = None) -> Dict[str, Any]:
        try:
            results = await self.rag_engine.search(query, user_id=user_id)
            # Retrieved document text is untrusted data.  Keep the boundary
            # explicit before it is placed into the model's tool transcript.
            return {
                "result": untrusted_context(results),
                "metadata": {"untrusted": True, "source": "user_document"},
            }
        except Exception:
            logger.warning("DocumentSearchTool failed")
            return {
                "error": "DOCUMENT_SEARCH_ERROR",
                "result": "Belge aramas\u0131 ba\u015far\u0131s\u0131z.",
                "metadata": {"untrusted": True, "source": "user_document"},
            }

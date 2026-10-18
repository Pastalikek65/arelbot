import logging
from typing import Dict, Any
from .base import BaseTool
from ..security.log_redaction import summarize_exception, summarize_private_text

logger = logging.getLogger(__name__)

class ImageGenerationTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="image_generation",
            description="Kullanıcının isteğine göre yapay zeka ile görsel oluşturur. Fotoğraf, çizim, illüstrasyon veya sanat eseri üretebilir."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string", 
                    "description": "Görselin detaylı İngilizce açıklaması (Örn: 'A realistic wolf running in a dark forest, cinematic lighting')."
                }
            },
            "required": ["prompt"]
        }

    async def execute(self, prompt: str = None, **kwargs) -> Dict[str, Any]:
        """
        Since the Orchestrator handles the actual ImageRouter call via a handover tag,
        this tool just returns the tag that Orchestrator intercepts.
        """
        try:
            if not prompt:
                return {"error": "Görsel açıklaması (prompt) gerekli."}
            
            logger.info("Image generation requested: %s", summarize_private_text(prompt, label="image_prompt"))
            
            # We return the special tag that Orchestrator looks for in the final response.
            # This allows us to use the existing photo sending logic without refactoring everything.
            return {
                "status": "success",
                "message": "Görsel üretim sistemi aktifleştirildi.",
                "handover_tag": f"<GENERATE_IMAGE>{prompt}</GENERATE_IMAGE>"
            }
        except Exception as e:
            logger.error("ImageGenerationTool error: %s", summarize_exception(e, label="image_generation"))
            return {"error": "image_generation_failed"}

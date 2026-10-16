from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseTool(ABC):
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """
        Returns JSON schema for function parameters (Gemini format).
        """
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        Executes the tool logic with provided arguments.
        Returns a dictionary result.
        """
        pass

    def to_gemini_def(self) -> Dict[str, Any]:
        """
        Converts tool definition to Gemini function declaration format.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

    def to_openai_def(self) -> Dict[str, Any]:
        """
        Converts tool definition to OpenAI/Groq function tool format.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }

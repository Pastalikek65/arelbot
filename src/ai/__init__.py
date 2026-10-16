"""Provider-neutral AI contracts and telemetry."""

from src.ai.provider_base import (
    AIProvider,
    AIProviderRequestError,
    AudioInput,
    CompletionRequest,
    CompletionResponse,
    EmbeddingResponse,
    Transcription,
    VisionRequest,
)
from src.ai.usage_tracker import UsageTracker
from src.ai.prompt_policy import untrusted_context
from src.ai.tts_provider import EdgeTTSProvider, SpeechArtifact, SpeechProvider, SpeechSynthesisRequest
from src.ai.providers.cloudflare_image import CloudflareImageProvider, ImageGenerationRequest, ImageProviderError
from src.ai.openai_compatible import (
    AIProviderConfigurationError,
    AIProviderProtocolError,
    OpenAICompatibleProvider,
)

__all__ = [
    "AIProvider", "AudioInput", "CompletionRequest", "CompletionResponse",
    "EmbeddingResponse", "Transcription", "VisionRequest", "UsageTracker",
    "untrusted_context", "EdgeTTSProvider", "SpeechArtifact", "SpeechProvider",
    "SpeechSynthesisRequest", "CloudflareImageProvider", "ImageGenerationRequest", "ImageProviderError",
    "OpenAICompatibleProvider", "AIProviderConfigurationError", "AIProviderProtocolError", "AIProviderRequestError",
]
from .router import AIProviderRouter, AIRouter, AIProviderUnavailable

__all__ += ["AIProviderRouter", "AIRouter", "AIProviderUnavailable"]

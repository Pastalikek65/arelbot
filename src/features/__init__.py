"""Arelbot feature contracts and domain services.

The feature package is deliberately independent from Telegram.  Telegram
handlers, inline queries and AI tool calls all consume the same typed result
objects from this package.
"""

from .contracts import DataProvider, FeatureDefinition, FeatureResult, ProviderHealth
from .registry import FeatureRegistry
from .runtime import ProviderRuntime
from .executor import FeatureExecutor

__all__ = [
    "DataProvider",
    "FeatureDefinition",
    "FeatureResult",
    "FeatureRegistry",
    "ProviderHealth",
    "ProviderRuntime",
    "FeatureExecutor",
]

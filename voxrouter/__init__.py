"""VoxRouter — roteador de voz unificado para modelos de geração de voz por IA."""

__version__ = "0.1.0"

from .router import AllProvidersFailed, UnknownModel, VoiceRouter
from .api import create_app

__all__ = ["VoiceRouter", "AllProvidersFailed", "UnknownModel", "create_app", "__version__"]

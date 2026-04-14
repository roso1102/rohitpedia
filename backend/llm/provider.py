from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, prompt: str, max_tokens: int = 2048, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a completion and return a normalized response dictionary."""
        raise NotImplementedError

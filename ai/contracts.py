from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TaskType(StrEnum):
    CHAT = "chat"
    TEXT_PROMPT = "text_prompt"
    IMAGE_PROMPT = "image_prompt"
    PRODUCT_SEARCH = "product_search"
    STOCK_NARRATIVE = "stock_narrative"
    STOCK_RESEARCH = "stock_research"
    FUNDAMENTAL_RESEARCH = "fundamental_research"


@dataclass(frozen=True)
class AIResponse:
    text: str
    provider: str
    model: str
    grounded: bool = False
    raw: Any = None


class ProviderError(RuntimeError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class GroundingUnavailable(ProviderError):
    pass

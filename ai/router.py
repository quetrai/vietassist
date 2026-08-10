from __future__ import annotations

import logging
from typing import Any

from ai.contracts import AIResponse, ProviderError, TaskType
from ai.providers.google import GoogleProvider
from ai.providers.openai_compatible import OpenAICompatibleProvider
from core.config import settings

logger = logging.getLogger(__name__)


class AIRouter:
    def __init__(self) -> None:
        self.groq = OpenAICompatibleProvider(
            name="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            timeout=settings.ai_timeout_sec,
            concurrency=settings.groq_max_concurrency,
        )
        self.openrouter = OpenAICompatibleProvider(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            timeout=settings.ai_timeout_sec,
            concurrency=settings.openrouter_max_concurrency,
            headers={
                "HTTP-Referer": "https://github.com/traique/vietassist",
                "X-Title": "VietAssist",
            },
        )
        self.google = GoogleProvider(
            settings.google_api_key, settings.google_model, settings.google_max_concurrency
        )

    async def close(self) -> None:
        await self.groq.close()
        await self.openrouter.close()
        await self.google.close()

    async def text(
        self,
        task: TaskType,
        messages: list[dict[str, Any]],
        *,
        system: str,
        temperature: float = 0.5,
    ) -> AIResponse:
        """Chat thường: Groq trước (nhanh nhất), OpenRouter nếu Groq lỗi, Gemini là tầng
        fallback cuối cùng nếu cả hai đều lỗi/chưa cấu hình — để bot vẫn trả lời được
        thay vì im lặng hoàn toàn."""
        errors: list[str] = []
        for provider in (self.groq, self.openrouter, getattr(self, "google", None)):
            if provider is None:
                continue
            try:
                return await provider.generate(messages, system=system, temperature=temperature)
            except ProviderError as exc:
                errors.append(str(exc))
                logger.warning("Provider fallback (task=%s): %s", task.value, exc)
        raise ProviderError(
            f"[{task.value}] " + ("; ".join(errors) or "Không có provider khả dụng")
        )

    async def deep_report(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str,
        temperature: float = 0.4,
    ) -> AIResponse:
        """Báo cáo sâu: ưu tiên OpenRouter (model cho phân tích dài), Groq là fallback,
        Gemini là tầng fallback cuối cùng nếu cả hai đều lỗi/chưa cấu hình."""
        errors: list[str] = []
        for provider in (self.openrouter, self.groq, getattr(self, "google", None)):
            if provider is None:
                continue
            try:
                return await provider.generate(messages, system=system, temperature=temperature)
            except ProviderError as exc:
                errors.append(str(exc))
                logger.warning("Provider fallback (deep): %s", exc)
        raise ProviderError("; ".join(errors) or "Không có provider khả dụng")

    async def image_prompt(self, path: str, instruction: str) -> AIResponse:
        return await self.google.image_to_prompt(path, instruction)

    async def product_search(self, query: str) -> AIResponse:
        if not query.strip():
            raise ValueError("Thiếu sản phẩm cần tìm")
        prompt = (
            "Dùng Google Search tìm giá bán hiện tại tại Việt Nam cho sản phẩm sau. "
            "Chỉ nêu giá có trong kết quả tìm kiếm, kèm cửa hàng, thời điểm và nguồn. "
            "Nếu không xác minh được, nói rõ chưa tra được; tuyệt đối không ước lượng.\n\n" + query
        )
        return await self.google.grounded_search(prompt)

    async def macro_news(self, query: str) -> AIResponse:
        if not query.strip():
            raise ValueError("Thiếu câu hỏi cần tra")
        prompt = (
            "Dùng Google Search tra tin tức/vĩ mô mới nhất liên quan câu hỏi sau, trong bối cảnh "
            "thị trường chứng khoán Việt Nam. Chỉ nêu thông tin có trong kết quả tìm kiếm, kèm "
            "thời điểm và nguồn. Nếu không xác minh được, nói rõ chưa tra được.\n\n" + query
        )
        return await self.google.grounded_search(prompt)


router = AIRouter()

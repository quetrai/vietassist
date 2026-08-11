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
        # Groq Compound có web search tích hợp, dùng riêng cho các tác vụ
        # bắt buộc dữ liệu thời gian thực như /gia và tin tức.
        self.groq_realtime = OpenAICompatibleProvider(
            name="groq-realtime",
            base_url="https://api.groq.com/openai/v1",
            api_key=settings.groq_api_key,
            model="groq/compound-mini",
            timeout=settings.ai_timeout_sec,
            concurrency=max(1, min(settings.groq_max_concurrency, 4)),
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
        await self.groq_realtime.close()
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
            "Tra giá bán hiện tại của sản phẩm sau tại Việt Nam bằng web search. "
            "Ưu tiên website chính hãng và nhà bán lẻ lớn. "
            "Trả lời tiếng Việt. Nêu đúng phiên bản/dung lượng nếu xác định được, "
            "giá hiện tại hoặc giá đang khuyến mãi, tên cửa hàng, thời điểm kiểm tra "
            "và nguồn. Nếu có nhiều mức giá, liệt kê các mức đáng tin cậy. "
            "Không được tự đoán giá. Nếu không tìm thấy dữ liệu đủ mới, nói rõ chưa xác minh được.\n\n"
            + query
        )

        errors: list[str] = []
        # Primary: Groq Compound Mini có web search native.
        try:
            return await self.groq_realtime.generate(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
            )
        except ProviderError as exc:
            errors.append(str(exc))
            logger.warning("Realtime product search via Groq failed: %s", exc)

        # Fallback: Google grounding nếu GOOGLE_API_KEY đang được cấu hình.
        try:
            return await self.google.grounded_search(prompt)
        except ProviderError as exc:
            errors.append(str(exc))
            logger.warning("Realtime product search via Google failed: %s", exc)

        raise ProviderError("product search failed: " + " | ".join(errors))

    async def macro_news(self, query: str) -> AIResponse:
        if not query.strip():
            raise ValueError("Thiếu câu hỏi cần tra")

        prompt = (
            "Tra cứu web theo thời gian thực cho yêu cầu sau. "
            "Nếu yêu cầu chỉ nói 'tin tức hôm nay' hoặc tương đương, hãy tổng hợp các tin "
            "đáng chú ý trong ngày hiện tại, ưu tiên Việt Nam, kinh tế, chứng khoán, công nghệ. "
            "Trả lời tiếng Việt. Mỗi tin cần có tiêu đề, thời điểm/ngày, tóm tắt ngắn và nguồn. "
            "Chỉ dùng thông tin tìm được trên web; không bịa và không dùng kiến thức cũ để giả làm tin mới. "
            "Nếu không xác minh được thông tin hiện tại, nói rõ.\n\n" + query
        )

        errors: list[str] = []
        try:
            return await self.groq_realtime.generate(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
            )
        except ProviderError as exc:
            errors.append(str(exc))
            logger.warning("Realtime news search via Groq failed: %s", exc)

        try:
            return await self.google.grounded_search(prompt)
        except ProviderError as exc:
            errors.append(str(exc))
            logger.warning("Realtime news search via Google failed: %s", exc)

        raise ProviderError("news search failed: " + " | ".join(errors))


router = AIRouter()

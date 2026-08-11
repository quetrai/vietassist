from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ai.contracts import AIResponse, GroundingUnavailable, ProviderError, ProviderUnavailable, TaskType
from ai.providers.google import GoogleProvider
from ai.providers.openai_compatible import OpenAICompatibleProvider
from core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ProviderState:
    ok: int = 0
    errors: int = 0
    consecutive_errors: int = 0
    opened_until: float = 0.0


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
        self.groq_realtime = OpenAICompatibleProvider(
            name="groq-realtime",
            base_url="https://api.groq.com/openai/v1",
            api_key=settings.groq_api_key,
            model=settings.groq_realtime_model,
            timeout=settings.ai_timeout_sec,
            concurrency=settings.groq_realtime_max_concurrency,
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
            settings.google_api_key,
            settings.google_model,
            settings.google_max_concurrency,
        )
        self._health: dict[str, ProviderState] = {
            "groq": ProviderState(),
            "groq-realtime": ProviderState(),
            "openrouter": ProviderState(),
            "google": ProviderState(),
        }

    async def close(self) -> None:
        await self.groq.close()
        await self.groq_realtime.close()
        await self.openrouter.close()
        await self.google.close()

    def _available(self, provider_name: str) -> bool:
        state = self._health[provider_name]
        return time.monotonic() >= state.opened_until

    def _success(self, provider_name: str) -> None:
        state = self._health[provider_name]
        state.ok += 1
        state.consecutive_errors = 0
        state.opened_until = 0.0

    def _failure(self, provider_name: str) -> None:
        state = self._health[provider_name]
        state.errors += 1
        state.consecutive_errors += 1
        if state.consecutive_errors >= settings.provider_circuit_breaker_failures:
            state.opened_until = time.monotonic() + settings.provider_circuit_breaker_cooldown_sec

    async def _first_available(
        self,
        providers: tuple[Any, ...],
        operation: Callable[[Any], Awaitable[AIResponse]],
        *,
        task: str,
    ) -> AIResponse:
        errors: list[str] = []
        skipped = 0
        for provider in providers:
            if not self._available(provider.name):
                skipped += 1
                continue
            try:
                response = await operation(provider)
                self._success(provider.name)
                return response
            except (ProviderUnavailable, GroundingUnavailable) as exc:
                errors.append(str(exc))
            except ProviderError as exc:
                self._failure(provider.name)
                errors.append(str(exc))
                logger.warning("Provider fallback task=%s provider=%s: %s", task, provider.name, exc)
        if skipped == len(providers):
            raise ProviderError(f"[{task}] tất cả provider đang trong thời gian hồi phục")
        raise ProviderError(f"[{task}] " + ("; ".join(errors) or "Không có provider khả dụng"))

    async def text(
        self,
        task: TaskType,
        messages: list[dict[str, Any]],
        *,
        system: str,
        temperature: float = 0.5,
    ) -> AIResponse:
        return await self._first_available(
            (self.groq, self.openrouter, self.google),
            lambda provider: provider.generate(messages, system=system, temperature=temperature),
            task=task.value,
        )

    async def deep_report(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str,
        temperature: float = 0.4,
    ) -> AIResponse:
        return await self._first_available(
            (self.openrouter, self.groq, self.google),
            lambda provider: provider.generate(messages, system=system, temperature=temperature),
            task="deep",
        )

    def health_snapshot(self) -> dict[str, dict[str, int | str]]:
        now = time.monotonic()
        return {
            name: {
                "ok": state.ok,
                "errors": state.errors,
                "consecutive_errors": state.consecutive_errors,
                "state": "open" if state.opened_until > now else "closed",
            }
            for name, state in self._health.items()
        }

    async def image_prompt(self, path: str, instruction: str) -> AIResponse:
        if not self._available("google"):
            raise ProviderError("google đang trong thời gian hồi phục")
        try:
            response = await self.google.image_to_prompt(path, instruction)
        except ProviderUnavailable:
            raise
        except ProviderError:
            self._failure("google")
            raise
        self._success("google")
        return response

    async def product_search(self, query: str) -> AIResponse:
        if not query.strip():
            raise ValueError("Thiếu sản phẩm cần tìm")
        prompt = (
            "Bạn đang xử lý một yêu cầu TRA GIÁ THỜI GIAN THỰC tại Việt Nam. "
            "Bắt buộc dùng web search. Chỉ trả lời bằng dữ liệu vừa tìm được từ web. "
            "Không dùng kiến thức trong model để suy đoán giá hiện tại. "
            "Ưu tiên website chính hãng và nhà bán lẻ lớn. "
            "Nêu đúng phiên bản/dung lượng nếu xác định được, giá hiện tại/khuyến mãi, "
            "tên nhà bán lẻ, thời điểm kiểm tra và nguồn/link nếu có. "
            "Nếu không tìm thấy kết quả đủ mới hoặc không xác minh được giá, hãy nói rõ "
            "KHÔNG TÌM THẤY DỮ LIỆU GIÁ ĐÃ XÁC MINH và không đưa ra con số đoán.\n\n"
            f"Yêu cầu: {query}"
        )
        errors: list[str] = []
        if self._available("google"):
            try:
                response = await self.google.grounded_search(prompt, require_evidence=True)
                self._success("google")
                return response
            except (ProviderUnavailable, GroundingUnavailable) as exc:
                errors.append(str(exc))
            except ProviderError as exc:
                self._failure("google")
                errors.append(str(exc))
                logger.warning("Realtime product search via Google failed: %s", exc)
        if self._available("groq-realtime"):
            try:
                response = await self.groq_realtime.generate_realtime(
                    [{"role": "user", "content": prompt}], temperature=0.1
                )
                self._success("groq-realtime")
                return response
            except (ProviderUnavailable, GroundingUnavailable) as exc:
                errors.append(str(exc))
            except ProviderError as exc:
                self._failure("groq-realtime")
                errors.append(str(exc))
                logger.warning("Realtime product search via Groq failed: %s", exc)
        raise ProviderError("product search failed: " + " | ".join(errors))

    async def macro_news(self, query: str) -> AIResponse:
        if not query.strip():
            raise ValueError("Thiếu câu hỏi cần tra")
        prompt = (
            "Bạn đang xử lý một yêu cầu TIN TỨC/THÔNG TIN THỜI GIAN THỰC. "
            "Bắt buộc dùng web search. Chỉ sử dụng thông tin có trong kết quả web vừa tìm được. "
            "TUYỆT ĐỐI KHÔNG dùng kiến thức cũ để giả làm tin mới và không bịa ngày tháng. "
            "Nếu người dùng hỏi 'tin tức hôm nay', 'tin mới nhất' hoặc tương đương, "
            "chỉ đưa các bài có ngày xuất bản phù hợp với ngày hiện tại hoặc rất gần hiện tại "
            "và phải ghi rõ ngày/giờ nếu nguồn cung cấp. Loại bỏ kết quả cũ, không rõ ngày hoặc "
            "không thể xác minh. Ưu tiên nguồn báo chí/chính thống uy tín. "
            "Mỗi tin cần có tiêu đề, ngày/giờ nếu có, tóm tắt ngắn và nguồn. "
            "Nếu không có kết quả đủ mới và xác minh được, trả lời rõ rằng không có dữ liệu "
            "thời gian thực đã xác minh; KHÔNG được thay bằng tin cũ.\n\n"
            f"Yêu cầu: {query}"
        )
        errors: list[str] = []
        if self._available("google"):
            try:
                response = await self.google.grounded_search(prompt, require_evidence=True)
                self._success("google")
                return response
            except (ProviderUnavailable, GroundingUnavailable) as exc:
                errors.append(str(exc))
            except ProviderError as exc:
                self._failure("google")
                errors.append(str(exc))
                logger.warning("Realtime news search via Google failed: %s", exc)
        if self._available("groq-realtime"):
            try:
                response = await self.groq_realtime.generate_realtime(
                    [{"role": "user", "content": prompt}], temperature=0.1
                )
                self._success("groq-realtime")
                return response
            except (ProviderUnavailable, GroundingUnavailable) as exc:
                errors.append(str(exc))
            except ProviderError as exc:
                self._failure("groq-realtime")
                errors.append(str(exc))
                logger.warning("Realtime news search via Groq failed: %s", exc)
        raise ProviderError("news search failed: " + " | ".join(errors))


router = AIRouter()

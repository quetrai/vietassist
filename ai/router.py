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
        # 9Router — chỉ được ưu tiên khi gọi text() với prefer_router9=True (tức user đã
        # /ai on). Luôn khởi tạo giống các provider khác; nếu thiếu ROUTER9_API_KEY,
        # provider.generate() tự raise ProviderUnavailable và rơi xuống fallback bình
        # thường, không cần kiểm tra riêng ở đây.
        self.router9 = OpenAICompatibleProvider(
            name="router9",
            base_url=settings.router9_base_url,
            api_key=settings.router9_api_key,
            model=settings.router9_model,
            timeout=settings.ai_timeout_sec,
            concurrency=settings.router9_max_concurrency,
        )

    async def close(self) -> None:
        await self.groq.close()
        await self.groq_realtime.close()
        await self.openrouter.close()
        await self.google.close()
        await self.router9.close()

    async def text(
        self,
        task: TaskType,
        messages: list[dict[str, Any]],
        *,
        system: str,
        temperature: float = 0.5,
        prefer_router9: bool = False,
    ) -> AIResponse:
        errors: list[str] = []
        providers: list[Any] = []
        if prefer_router9:
            providers.append(getattr(self, "router9", None))
        if task == TaskType.STOCK_NARRATIVE:
            # Bao cao /stock la tien that cua khach, payload dai (JSON deterministic +
            # nhieu quy tac tieng Viet phuc tap). groq_model mac dinh la mot model mo 20B
            # (xem core/config.py) - thinh thoang lan ngon ngu (chen tu tieng Trung/Phap/
            # Y...) hoac echo nguyen van JSON khi phai xu ly payload dai kieu nay, kieu
            # loi hiem gap hon o cac model lon hon tren OpenRouter/Google. Uu tien 2
            # provider do truoc, Groq van la fallback cuoi thay vi thu dau tien.
            providers.extend([self.openrouter, getattr(self, "google", None), self.groq])
        else:
            providers.extend([self.groq, self.openrouter, getattr(self, "google", None)])
        for provider in providers:
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

    async def translate(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str,
        temperature: float = 0.3,
    ) -> AIResponse:
        """Dùng riêng cho /dich. Hệ thống prompt mang theo NGUYÊN VĂN tài liệu tham chiếu
        văn phong (reference/nhat_viet_translation.md, hàng chục nghìn ký tự) nên ưu tiên
        OpenRouter trước (context dài nhất trong 3 provider hiện có), rồi mới rơi về Groq/
        Google — cùng thứ tự với deep_report vì lý do tương tự (payload lớn)."""
        errors: list[str] = []
        for provider in (self.openrouter, self.groq, getattr(self, "google", None)):
            if provider is None:
                continue
            try:
                return await provider.generate(messages, system=system, temperature=temperature)
            except ProviderError as exc:
                errors.append(str(exc))
                logger.warning("Provider fallback (translation): %s", exc)
        raise ProviderError(
            f"[{TaskType.TRANSLATION.value}] " + ("; ".join(errors) or "Không có provider khả dụng")
        )

    async def image_prompt(self, path: str, instruction: str) -> AIResponse:
        return await self.google.image_to_prompt(path, instruction)

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

        # Google Search grounding là nguồn realtime chính.
        try:
            return await self.google.grounded_search(prompt, require_evidence=True)
        except ProviderError as exc:
            errors.append(str(exc))
            logger.warning("Realtime product search via Google failed: %s", exc)

        # Groq Compound Mini là fallback realtime thứ hai.
        try:
            return await self.groq_realtime.generate_realtime(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
            )
        except ProviderError as exc:
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

        # Google Search grounding là nguồn realtime chính.
        try:
            return await self.google.grounded_search(prompt, require_evidence=True)
        except ProviderError as exc:
            errors.append(str(exc))
            logger.warning("Realtime news search via Google failed: %s", exc)

        # Groq Compound Mini là fallback realtime thứ hai.
        try:
            return await self.groq_realtime.generate_realtime(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
            )
        except ProviderError as exc:
            errors.append(str(exc))
            logger.warning("Realtime news search via Groq failed: %s", exc)

        raise ProviderError("news search failed: " + " | ".join(errors))


router = AIRouter()

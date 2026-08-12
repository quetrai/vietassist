from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from ai.contracts import AIResponse, GroundingUnavailable, ProviderError, ProviderUnavailable

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
logger = logging.getLogger(__name__)

# Gemini chỉ chấp nhận role "user"/"model" — map từ role kiểu OpenAI ("assistant") sang.
_ROLE_MAP = {"user": "user", "assistant": "model"}


def _to_gemini_contents(messages: list[dict[str, Any]]) -> list[types.Content]:
    contents: list[types.Content] = []
    for message in messages:
        role = _ROLE_MAP.get(message.get("role", "user"), "user")
        text = (message.get("content") or "").strip()
        if not text:
            continue
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text)]))
    return contents


def _image_part(path: str) -> types.Part:
    file_path = Path(path)
    data = file_path.read_bytes()
    if not data:
        raise ProviderError("Ảnh rỗng")
    if len(data) > _MAX_IMAGE_BYTES:
        raise ProviderError("Ảnh vượt quá giới hạn 10 MB")

    suffix = file_path.suffix.lower()
    mime_by_suffix = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    mime = mime_by_suffix.get(suffix)
    if mime is None:
        raise ProviderError("Định dạng ảnh không được hỗ trợ")

    # Detect common magic bytes so a renamed non-image file is not sent to the model.
    valid = (
        (mime == "image/jpeg" and data[:3] == b"\xff\xd8\xff")
        or (mime == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
        or (mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        or (mime == "image/gif" and data[:6] in (b"GIF87a", b"GIF89a"))
    )
    if not valid:
        raise ProviderError("Nội dung file không khớp định dạng ảnh")
    return types.Part.from_bytes(data=data, mime_type=mime)


class GoogleProvider:
    def __init__(self, api_key: str, model: str, concurrency: int) -> None:
        self.api_key = api_key
        self.model = model
        self.client = genai.Client(api_key=api_key) if api_key else None
        self.semaphore = asyncio.Semaphore(max(1, concurrency))

    async def close(self) -> None:
        if self.client is None:
            return
        close = getattr(getattr(self.client, "aio", None), "aclose", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        temperature: float = 0.5,
        max_tokens: int = 2048,
    ) -> AIResponse:
        """Chat text thuần (không grounding) — dùng làm fallback cuối cùng khi Groq và
        OpenRouter đều lỗi. Cùng chữ ký với OpenAICompatibleProvider.generate() để router
        có thể gọi đồng nhất qua cùng một vòng lặp fallback."""
        if self.client is None:
            raise ProviderUnavailable("google chưa được cấu hình")
        contents = _to_gemini_contents(messages)
        if not contents:
            raise ProviderError("google: không có nội dung để gửi")
        config = types.GenerateContentConfig(
            temperature=temperature,
            maxOutputTokens=max_tokens,
            systemInstruction=system or None,
        )
        async with self.semaphore:
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
                if not response.text:
                    raise ProviderError("google trả kết quả rỗng")
                return AIResponse(response.text.strip(), "google", self.model, raw=response)
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(f"google: {type(exc).__name__}") from exc

    @staticmethod
    def _has_grounding_metadata(response: Any) -> bool:
        """Accept a response only when Gemini reports Google Search grounding metadata."""
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            metadata = getattr(candidate, "grounding_metadata", None)
            if metadata is None:
                continue
            queries = getattr(metadata, "web_search_queries", None)
            chunks = getattr(metadata, "grounding_chunks", None)
            supports = getattr(metadata, "grounding_supports", None)
            if queries or chunks or supports:
                return True
        return False

    async def grounded_search(self, prompt: str, *, require_evidence: bool = True) -> AIResponse:
        if self.client is None:
            raise GroundingUnavailable("Google API chưa được cấu hình")

        # Keep the configured model first, then stable fallbacks that support Search grounding.
        models = []
        for model in (self.model, "gemini-2.5-flash"):
            if model and model not in models:
                models.append(model)

        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )

        errors: list[str] = []
        async with self.semaphore:
            for model in models:
                try:
                    response = await self.client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=config,
                    )
                    text = (response.text or "").strip()
                    if not text:
                        errors.append(f"{model}: empty response")
                        continue
                    if require_evidence and not self._has_grounding_metadata(response):
                        errors.append(f"{model}: không có grounding metadata")
                        continue
                    return AIResponse(
                        text,
                        "google",
                        model,
                        True,
                        response,
                    )
                except Exception as exc:
                    error = f"{model}: {type(exc).__name__}: {exc}"
                    errors.append(error)
                    logger.warning("Google Search grounding failed: %s", error)

        raise ProviderError("Google Search grounding failed: " + " | ".join(errors))

    async def image_to_prompt(self, path: str, instruction: str) -> AIResponse:
        if self.client is None:
            raise ProviderUnavailable("Google API chưa được cấu hình")
        try:
            part = _image_part(path)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"image-read: {type(exc).__name__}") from exc
        contents = [part, instruction]
        async with self.semaphore:
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model, contents=contents
                )
                if not response.text:
                    raise ProviderError("Google trả kết quả rỗng")
                return AIResponse(response.text.strip(), "google", self.model, raw=response)
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(f"google: {type(exc).__name__}") from exc

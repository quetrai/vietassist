from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from ai.contracts import AIResponse, ProviderError, ProviderUnavailable

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        concurrency: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = httpx.AsyncClient(timeout=timeout)
        self.semaphore = asyncio.Semaphore(max(1, concurrency))
        self.extra_headers = headers or {}


    @staticmethod
    def _has_web_evidence(data: Any) -> bool:
        """Require evidence from a built-in realtime search tool before accepting output."""
        if isinstance(data, dict):
            for key in ("executed_tools", "search_results", "web_search", "citations", "sources"):
                value = data.get(key)
                if value:
                    return True
            return any(OpenAICompatibleProvider._has_web_evidence(v) for v in data.values())
        if isinstance(data, list):
            return any(OpenAICompatibleProvider._has_web_evidence(v) for v in data)
        return False

    async def generate_realtime(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AIResponse:
        """Call a provider that must perform web search; never silently fall back to model memory."""
        if not self.api_key:
            raise ProviderUnavailable(f"{self.name} chưa được cấu hình")
        payload_messages = ([{"role": "system", "content": system}] if system else []) + messages
        headers = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}
        payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with self.semaphore:
            try:
                response = await self.client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
                response.raise_for_status()
                data = response.json()
                if not self._has_web_evidence(data):
                    raise ProviderError(f"{self.name}: không có bằng chứng web realtime")
                text = data["choices"][0]["message"]["content"]
                if not text:
                    raise ProviderError(f"{self.name} trả kết quả rỗng")
                return AIResponse(text=text.strip(), provider=self.name, model=self.model, grounded=True, raw=data)
            except ProviderError:
                raise
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise ProviderError(f"{self.name}: {type(exc).__name__}") from exc

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _parse_sse_content(raw: str) -> str:
        """Một số gateway (vd 9Router khi provider phía sau chỉ hỗ trợ streaming) trả về
        text/event-stream ('data: {...}\\n\\n') ngay cả khi request không xin stream. Gom lại
        nội dung delta/message từ các chunk JSON trong luồng SSE đó."""
        pieces: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            for choice in chunk.get("choices", []):
                delta_content = (choice.get("delta") or {}).get("content")
                if delta_content:
                    pieces.append(delta_content)
                msg_content = (choice.get("message") or {}).get("content")
                if msg_content:
                    pieces.append(msg_content)
        return "".join(pieces)

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        temperature: float = 0.5,
        max_tokens: int = 2048,
    ) -> AIResponse:
        if not self.api_key:
            raise ProviderUnavailable(f"{self.name} chưa được cấu hình")
        payload_messages = ([{"role": "system", "content": system}] if system else []) + messages
        headers = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}
        payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        async with self.semaphore:
            try:
                response = await self.client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" in content_type or response.text.lstrip().startswith("data:"):
                    # Gateway bỏ qua "stream": false và trả SSE (thường do provider phía
                    # sau chỉ hỗ trợ streaming) -> parse thủ công thay vì response.json().
                    text = self._parse_sse_content(response.text)
                    data: Any = {"sse_raw_len": len(response.text)}
                else:
                    data = response.json()
                    text = data["choices"][0]["message"]["content"]
                    # Cảnh báo NGAY khi model bị cắt vì hết max_tokens - trước đây không
                    # log gì cả, text bị cắt giữa chừng vẫn trả về "thành công" như bình
                    # thường nên rất khó chẩn đoán (chính là bug đã gặp với /stock).
                    finish_reason = data.get("choices", [{}])[0].get("finish_reason")
                    if finish_reason == "length":
                        logger.warning(
                            "%s: response bị cắt do hết max_tokens=%s - cân nhắc tăng max_tokens "
                            "cho tác vụ này",
                            self.name,
                            max_tokens,
                        )
                if not text:
                    raise ProviderError(f"{self.name} trả kết quả rỗng")
                return AIResponse(text=text.strip(), provider=self.name, model=self.model, raw=data)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise ProviderError(f"{self.name}: {type(exc).__name__}") from exc

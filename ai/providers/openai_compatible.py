from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ai.contracts import AIResponse, ProviderError, ProviderUnavailable


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
        }
        async with self.semaphore:
            try:
                response = await self.client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
                response.raise_for_status()
                data = response.json()
                text = data["choices"][0]["message"]["content"]
                if not text:
                    raise ProviderError(f"{self.name} trả kết quả rỗng")
                return AIResponse(text=text.strip(), provider=self.name, model=self.model, raw=data)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise ProviderError(f"{self.name}: {type(exc).__name__}") from exc

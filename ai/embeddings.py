from __future__ import annotations

from google import genai
from google.genai import types

from ai.contracts import ProviderError
from core.config import settings

EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_DIM = 768

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.google_api_key:
            raise ProviderError("Thiếu GOOGLE_API_KEY — cần để tính embedding cho knowledge base")
        _client = genai.Client(api_key=settings.google_api_key)
    return _client


async def embed(text: str) -> list[float]:
    client = _get_client()
    try:
        response = await client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
        )
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"embedding: {type(exc).__name__}") from exc

    if not response.embeddings or not response.embeddings[0].values:
        raise ProviderError("embedding: Google trả về vector rỗng")

    values = list(response.embeddings[0].values)
    if len(values) != EMBEDDING_DIM:
        raise ProviderError(
            f"embedding: kích thước vector không đúng ({len(values)} != {EMBEDDING_DIM})"
        )
    return values

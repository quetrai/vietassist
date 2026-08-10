from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

from ai import embeddings
from core import database
from core.config import settings

logger = logging.getLogger(__name__)

CHUNK_MAX_CHARS = 1200
CHUNK_OVERLAP = 150
TOP_K = 5

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)

# Cache trong RAM: có chunk nào đã index chưa. Tránh việc mỗi tin nhắn chat (kể cả chat
# phiếm chẳng liên quan gì kiến thức) đều tốn 1 lần gọi Google embedding API + round-trip
# mạng chỉ để rồi search ra 0 kết quả vì KB đang rỗng. None = chưa biết, sẽ tự kiểm tra
# (rẻ, chỉ 1 query EXISTS) ở lần retrieve() đầu tiên rồi cache lại; được cập nhật ngay
# sau mỗi lần reindex() để không cần đợi tin nhắn tiếp theo mới phát hiện KB vừa có nội
# dung (hoặc vừa bị xoá hết).
_has_content: bool | None = None


async def _knowledge_available() -> bool:
    global _has_content
    if _has_content is None:
        try:
            _has_content = await database.has_knowledge_content()
        except Exception:
            logger.warning(
                "Không kiểm tra được knowledge base có nội dung hay không", exc_info=True
            )
            return False
    return _has_content


def _iter_source_files() -> list[Path]:
    root = Path(settings.knowledge_base_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def _split_by_heading(text: str) -> list[tuple[str, str]]:
    """Chia file theo heading Markdown (#, ##, ...). Trả list (heading, nội dung đoạn đó).
    Phần trước heading đầu tiên (nếu có) được gán heading rỗng."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]
    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        sections.append(("", text[: matches[0].start()]))
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((match.group(2).strip(), text[start:end]))
    return sections


def _split_long(heading: str, content: str) -> list[tuple[str, str]]:
    """Cắt tiếp 1 section nếu vẫn dài hơn CHUNK_MAX_CHARS, có overlap để không mất ngữ cảnh
    ở ranh giới cắt."""
    content = content.strip()
    if not content:
        return []
    if len(content) <= CHUNK_MAX_CHARS:
        return [(heading, content)]
    chunks: list[tuple[str, str]] = []
    start = 0
    while start < len(content):
        end = min(start + CHUNK_MAX_CHARS, len(content))
        piece = content[start:end].strip()
        if piece:
            chunks.append((heading, piece))
        if end == len(content):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def chunk_file(text: str) -> list[tuple[str, str]]:
    """Chia nội dung 1 file .md thành các đoạn (heading, content) vừa đủ nhỏ để embed.
    Hàm thuần, không I/O — dễ test độc lập."""
    chunks: list[tuple[str, str]] = []
    for heading, section in _split_by_heading(text):
        chunks.extend(_split_long(heading, section))
    return chunks


async def reindex(*, force: bool = False) -> str:
    """Quét toàn bộ .md trong KNOWLEDGE_BASE_DIR (đệ quy), tính embedding cho file nào đổi
    nội dung (so sha256) rồi lưu vào Postgres/pgvector. Gọi qua lệnh /kbreindex sau khi cập
    nhật tài liệu trên GitHub + deploy lại, hoặc tự chạy 1 lần lúc app khởi động.
    `force=True` để tính lại toàn bộ dù hash không đổi (vd đổi model embedding)."""
    if not settings.google_api_key:
        return "Thiếu GOOGLE_API_KEY — không thể tính embedding cho knowledge base."
    files = _iter_source_files()
    if not files:
        return f"Không có file .md nào trong thư mục '{settings.knowledge_base_dir}'."
    root = Path(settings.knowledge_base_dir)
    seen_sources: set[str] = set()
    updated = skipped = failed = 0
    for path in files:
        source = str(path.relative_to(root))
        seen_sources.add(source)
        # read_text là I/O đồng bộ — chạy trong thread pool để không block event loop
        # (reindex chạy như background task lúc startup, xen kẽ với webhook đang tới).
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if not force:
            existing_hash = await database.get_knowledge_source_hash(source)
            if existing_hash == content_hash:
                skipped += 1
                continue
        chunks = chunk_file(text)
        if not chunks:
            continue
        try:
            embedded = [
                (heading, content, await embeddings.embed(content)) for heading, content in chunks
            ]
        except Exception:
            logger.exception("Lỗi tính embedding cho %s, bỏ qua file này lần này", source)
            failed += 1
            continue
        await database.replace_knowledge_source(source, content_hash, embedded)
        updated += 1
    for source in await database.list_knowledge_sources():
        if source not in seen_sources:
            await database.delete_knowledge_source(source)
    global _has_content
    try:
        _has_content = await database.has_knowledge_content()
    except Exception:
        _has_content = None  # để retrieve() tự kiểm tra lại ở lần gọi tiếp theo
    return f"Đã cập nhật {updated} file, giữ nguyên {skipped} file không đổi, lỗi {failed} file."


async def retrieve(query: str) -> str:
    """Tìm TOP_K đoạn liên quan nhất tới câu hỏi để làm ngữ cảnh cho system prompt. Trả ''
    nếu chưa cấu hình được (thiếu key, chưa reindex lần nào, lỗi tạm thời...) — để chat vẫn
    trả lời bình thường, chỉ là không có thêm ngữ cảnh tham khảo."""
    if not settings.google_api_key or not query.strip():
        return ""
    if not await _knowledge_available():
        return ""
    try:
        vector = await embeddings.embed(query)
        rows = await database.search_knowledge(vector, TOP_K)
    except Exception:
        logger.warning("Không tra được knowledge base cho câu hỏi hiện tại", exc_info=True)
        return ""
    if not rows:
        return ""
    parts = []
    for row in rows:
        label = str(row["source"]) + (f" — {row['heading']}" if row["heading"] else "")
        parts.append(f"[{label}]\n{row['content']}")
    return "\n\n".join(parts)

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from ai import router
from ai.contracts import TaskType
from core import database
from core.models import User

GROUP_COMMANDS = {"/nhom", "/nhomzalo", "/themnhom", "/xoanhom", "/tongket", "/dangnoi"}
_IMAGE_TIMEOUT = httpx.Timeout(30.0)
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REDIRECTS = 3
_DEFAULT_IMAGE_SUFFIX = ".jpg"
_ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_MAX_TRANSCRIPT_CHARS = 20000
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True)
class ZaloEvent:
    event_id: str
    sender_id: str
    text: str
    kind: str = "direct"
    group_id: str | None = None
    group_name: str | None = None
    message_id: str | None = None
    sender_name: str = ""
    sent_at: datetime | None = None
    image_url: str | None = None


async def resolve_user(sender_id: str) -> User | None:
    return await database.zalo_lookup(sender_id)


async def _validate_image_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Image URL must use HTTPS")
    host = parsed.hostname.rstrip(".")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Image host cannot be resolved") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError("Image URL resolves to a private or reserved address")


async def download_image(url: str) -> str:
    current_url = url
    async with httpx.AsyncClient(timeout=_IMAGE_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            await _validate_image_url(current_url)
            response = await client.get(current_url)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Image redirect missing location")
                current_url = str(response.url.join(location))
                continue
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise ValueError("Invalid image content length") from exc
                if declared_size > _MAX_IMAGE_BYTES:
                    raise ValueError("Image is too large")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type and not content_type.startswith("image/"):
                raise ValueError("URL does not point to an image")
            data = response.content
            if len(data) > _MAX_IMAGE_BYTES:
                raise ValueError("Image is too large")
            break
        else:
            raise ValueError("Too many image redirects")
    suffix = Path(urlsplit(current_url).path).suffix.lower()
    if suffix not in _ALLOWED_IMAGE_SUFFIXES:
        suffix = _DEFAULT_IMAGE_SUFFIX
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
    return path


def _build_transcript(lines: list[str], max_chars: int) -> tuple[str, bool]:
    total = 0
    kept: list[str] = []
    truncated = False
    for line in reversed(lines):
        total += len(line) + 1
        if total > max_chars:
            truncated = True
            break
        kept.append(line)
    kept.reverse()
    return "\n".join(kept), truncated


def is_group_command(text: str) -> bool:
    parts = text.split(maxsplit=1)
    return bool(parts) and parts[0].lower() in GROUP_COMMANDS


async def require_group_admin(user: User) -> str | None:
    if not user.can_use_group_summary:
        return "Tính năng tổng kết nhóm chỉ dành cho quản trị viên."
    return None


async def summarize_group(user: User, alias: str, period: str) -> str:
    denied = await require_group_admin(user)
    if denied:
        return denied
    group_id = await database.zalo_group_id_for(alias)
    if group_id is None:
        return "Không tìm thấy nhóm đã bật allowlist."
    period_end = datetime.now(UTC)
    period_start = period_end - (timedelta(days=7) if period == "7d" else timedelta(hours=24))
    db = await database.pool()
    rows = await db.fetch(
        """SELECT sender_name,content,sent_at FROM zalo_group_messages
        WHERE group_id=$1 AND sent_at >= $2
        ORDER BY sent_at ASC LIMIT 1000""",
        group_id,
        period_start,
    )
    if not rows:
        return "Không có tin nhắn phù hợp trong nhóm được phép."
    lines = [f"{row['sender_name']}: {row['content']}" for row in rows]
    transcript, truncated = _build_transcript(lines, _MAX_TRANSCRIPT_CHARS)
    truncated_note = (
        "\n[Đã lược bớt các tin nhắn cũ hơn do transcript quá dài]\n" if truncated else ""
    )
    prompt = (
        "Tóm tắt cuộc trò chuyện nhóm chứng khoán. Nêu diễn biến chính, mã được bàn nhiều, "
        "quan điểm trái chiều, dữ kiện cần kiểm chứng và hành động của admin. Không quảng cáo môi giới.\n"
        + truncated_note
        + "\n"
        + transcript
    )
    response = await router.text(
        TaskType.CHAT,
        [{"role": "user", "content": prompt}],
        system="Chỉ tóm tắt nội dung được cung cấp; không thêm dữ kiện bên ngoài.",
        temperature=0.2,
    )
    await database.zalo_save_summary(group_id, user.id, period_start, period_end, response.text)
    return response.text


async def today_discussion(user: User, alias: str) -> str:
    """Trả về nguyên văn (không qua AI tóm tắt) các tin nhắn của nhóm TỪ ĐẦU NGÀY
    hôm nay (giờ Việt Nam) đến hiện tại - khác /tongket ở chỗ đây là transcript
    thô, dùng khi muốn đọc lại đúng những gì đã được nhắn thay vì bản tóm tắt AI."""
    denied = await require_group_admin(user)
    if denied:
        return denied
    group_id = await database.zalo_group_id_for(alias)
    if group_id is None:
        return "Không tìm thấy nhóm đã bật allowlist."
    now_vn = datetime.now(_VN_TZ)
    start_of_day_vn = now_vn.replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = start_of_day_vn.astimezone(UTC)
    db = await database.pool()
    rows = await db.fetch(
        """SELECT sender_name,content,sent_at FROM zalo_group_messages
        WHERE group_id=$1 AND sent_at >= $2
        ORDER BY sent_at ASC LIMIT 1000""",
        group_id,
        period_start,
    )
    if not rows:
        return "Chưa có tin nhắn nào trong nhóm hôm nay."
    lines = [
        f"[{row['sent_at'].astimezone(_VN_TZ):%H:%M}] {row['sender_name']}: {row['content']}"
        for row in rows
    ]
    transcript, truncated = _build_transcript(lines, _MAX_TRANSCRIPT_CHARS)
    truncated_note = (
        "\n[Đã lược bớt các tin nhắn cũ hơn trong ngày do quá dài]\n" if truncated else ""
    )
    header = f"💬 Thảo luận hôm nay ({now_vn:%d/%m/%Y}) — {len(rows)} tin nhắn\n"
    return header + truncated_note + "\n" + transcript

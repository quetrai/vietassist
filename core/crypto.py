from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None
_warned_missing_key = False


def _derive_key(secret: str) -> bytes:
    """SETTINGS_ENC_KEY có thể là chuỗi bất kỳ do người vận hành đặt (không nhất thiết
    đúng định dạng key của Fernet) — băm SHA-256 rồi urlsafe-base64 để luôn ra đúng 32
    byte mà Fernet yêu cầu, bất kể chuỗi gốc dài ngắn thế nào."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet | None:
    global _fernet, _warned_missing_key
    if not settings.settings_enc_key:
        if not _warned_missing_key:
            logger.warning(
                "SETTINGS_ENC_KEY chưa được cấu hình — session Zalo (cookie đăng nhập) sẽ "
                "được lưu KHÔNG mã hoá trong database. Đặt SETTINGS_ENC_KEY (chuỗi bất kỳ, "
                "đủ dài) trước khi dùng ở production."
            )
            _warned_missing_key = True
        return None
    if _fernet is None:
        _fernet = Fernet(_derive_key(settings.settings_enc_key))
    return _fernet


def encrypt(plaintext: str) -> str:
    """Mã hoá 1 chuỗi để lưu at-rest. Nếu chưa cấu hình SETTINGS_ENC_KEY, trả về nguyên
    văn kèm cảnh báo (degrade an toàn thay vì raise, để không chặn tính năng đăng nhập
    Zalo của người mới bắt đầu chưa set biến này) — nhưng luôn khuyến nghị cấu hình."""
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    """Giải mã chuỗi đã lưu. Chấp nhận cả giá trị cũ chưa từng được mã hoá (trước khi
    SETTINGS_ENC_KEY được bật) bằng cách trả nguyên văn nếu giải mã thất bại — tránh
    việc bật mã hoá sau này làm hỏng session đã lưu trước đó."""
    fernet = _get_fernet()
    if fernet is None:
        return value
    try:
        return fernet.decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return value

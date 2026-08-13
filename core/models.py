from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Channel(StrEnum):
    TELEGRAM = "telegram"
    ZALO = "zalo"
    ZOOM = "zoom"


class Role(StrEnum):
    ROOT = "root"
    ZALO_ADMIN = "zalo_admin"
    ZOOM_ADMIN = "zoom_admin"
    USER = "user"


@dataclass(frozen=True)
class User:
    id: str
    channel: Channel
    external_id: str
    role: Role
    active: bool = True
    rag_enabled: bool = True

    @property
    def can_use_group_summary(self) -> bool:
        # Role la thuoc tinh cua user, khong phai kenh: admin Zalo hay admin Zoom
        # deu duoc dung tinh nang nhom Zalo (/nhom, /tongket, /dangnoi...), giong
        # nhu comment trong services/commands.py::_cmd_tongket.
        return self.role in {Role.ROOT, Role.ZALO_ADMIN, Role.ZOOM_ADMIN}

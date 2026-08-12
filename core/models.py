from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Channel(StrEnum):
    TELEGRAM = "telegram"
    ZALO = "zalo"


class Role(StrEnum):
    ROOT = "root"
    ZALO_ADMIN = "zalo_admin"
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
        return self.role in {Role.ROOT, Role.ZALO_ADMIN}

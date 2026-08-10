from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _integer(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} phải là số nguyên") from exc


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "").strip()
    telegram_owner_id: int = _integer("TELEGRAM_OWNER_ID", 0)
    database_url: str = os.getenv("DATABASE_URL", "").strip()
    settings_enc_key: str = os.getenv("SETTINGS_ENC_KEY", "").strip()
    groq_api_key: str = os.getenv("GROQ_API_KEY", "").strip()
    # llama-3.3-70b-versatile bị Groq khai tử 16/08/2026 — gpt-oss-20b là model free nhanh
    # nhất (LPU, ~1000 tok/s) phù hợp chat hàng ngày tần suất cao.
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "").strip()
    # nemotron-3-super free tier: 262K context, cân bằng tốc độ/chất lượng, đủ sâu cho
    # deep_report (phân tích cổ phiếu/tổng kết nhóm) lẫn làm fallback chat thường.
    openrouter_model: str = os.getenv(
        "OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"
    ).strip()
    google_api_key: str = os.getenv("GOOGLE_API_KEY", "").strip()
    # gemini-2.5-flash sẽ bị Google khai tử 16/10/2026 — gemini-3.6-flash là bản Flash mới
    # nhất còn free tier, vẫn hỗ trợ grounding (/gia, /vimo) và vision (ảnh→prompt).
    google_model: str = os.getenv("GOOGLE_MODEL", "gemini-3.6-flash").strip()
    ai_timeout_sec: int = _integer("AI_TIMEOUT_SEC", 45)
    chat_history_turns: int = _integer("CHAT_HISTORY_TURNS", 10)
    groq_max_concurrency: int = _integer("GROQ_MAX_CONCURRENCY", 8)
    openrouter_max_concurrency: int = _integer("OPENROUTER_MAX_CONCURRENCY", 4)
    google_max_concurrency: int = _integer("GOOGLE_MAX_CONCURRENCY", 4)
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "").strip()
    webhook_base_url: str = os.getenv("WEBHOOK_BASE_URL", "").strip()
    bridge_secret: str = os.getenv("BRIDGE_SECRET", "").strip()
    zalo_enabled: bool = _boolean("ZALO_ENABLED")
    zalo_control_port: int = _integer("ZALO_CONTROL_PORT", 9901)
    zalo_daily_digest_hour: int = _integer("ZALO_DAILY_DIGEST_HOUR", 21)
    stock_cache_ttl_sec: int = _integer("STOCK_CACHE_TTL_SEC", 90)
    knowledge_base_dir: str = os.getenv("KNOWLEDGE_BASE_DIR", "knowledge").strip()

    def validate(self, *, webhook: bool = False) -> None:
        required = {
            "TELEGRAM_TOKEN": self.telegram_token,
            "TELEGRAM_OWNER_ID": self.telegram_owner_id,
            "DATABASE_URL": self.database_url,
        }
        if not (self.groq_api_key or self.openrouter_api_key):
            required["GROQ_API_KEY hoặc OPENROUTER_API_KEY"] = ""
        if webhook:
            required.update(
                {"WEBHOOK_SECRET": self.webhook_secret, "WEBHOOK_BASE_URL": self.webhook_base_url}
            )
        if self.zalo_enabled:
            required.update(
                {"BRIDGE_SECRET": self.bridge_secret, "SETTINGS_ENC_KEY": self.settings_enc_key}
            )
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError("Thiếu cấu hình: " + ", ".join(missing))
        ranges = {
            "TELEGRAM_OWNER_ID": (1, None, self.telegram_owner_id),
            "AI_TIMEOUT_SEC": (1, 300, self.ai_timeout_sec),
            "CHAT_HISTORY_TURNS": (1, 100, self.chat_history_turns),
            "GROQ_MAX_CONCURRENCY": (1, 64, self.groq_max_concurrency),
            "OPENROUTER_MAX_CONCURRENCY": (1, 64, self.openrouter_max_concurrency),
            "GOOGLE_MAX_CONCURRENCY": (1, 64, self.google_max_concurrency),
            "STOCK_CACHE_TTL_SEC": (0, 3600, self.stock_cache_ttl_sec),
        }
        if self.zalo_enabled:
            ranges["ZALO_CONTROL_PORT"] = (1024, 65535, self.zalo_control_port)
            ranges["ZALO_DAILY_DIGEST_HOUR"] = (0, 23, self.zalo_daily_digest_hour)
        invalid = [
            f"{name}={value} (phải trong khoảng {minimum}..{maximum})"
            for name, (minimum, maximum, value) in ranges.items()
            if value < minimum or (maximum is not None and value > maximum)
        ]
        if invalid:
            raise RuntimeError("Cấu hình không hợp lệ: " + "; ".join(invalid))


settings = Settings()

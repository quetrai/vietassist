from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from decimal import Decimal

import asyncpg

from core import crypto
from core.config import settings
from core.models import Channel, Role, User

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                settings.database_url, min_size=1, max_size=10, statement_cache_size=0
            )
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def migrate() -> None:
    db = await pool()
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          channel TEXT NOT NULL,
          external_id TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'user',
          active BOOLEAN NOT NULL DEFAULT TRUE,
          rag_enabled BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE(channel, external_id)
        );
        ALTER TABLE users ADD COLUMN IF NOT EXISTS rag_enabled BOOLEAN NOT NULL DEFAULT TRUE;
        CREATE TABLE IF NOT EXISTS chat_messages (
          id BIGSERIAL PRIMARY KEY,
          user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role TEXT NOT NULL CHECK (role IN ('user','assistant')),
          content TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS chat_messages_user_time
          ON chat_messages(user_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS user_memory (
          user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          facts JSONB NOT NULL DEFAULT '[]'::jsonb,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS stock_holdings (
          user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          symbol TEXT NOT NULL,
          quantity NUMERIC NOT NULL CHECK (quantity > 0),
          average_price NUMERIC NOT NULL CHECK (average_price > 0),
          stop_price NUMERIC,
          target_price NUMERIC,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY(user_id, symbol)
        );
        ALTER TABLE stock_holdings ADD COLUMN IF NOT EXISTS stop_price NUMERIC;
        ALTER TABLE stock_holdings ADD COLUMN IF NOT EXISTS target_price NUMERIC;
        CREATE TABLE IF NOT EXISTS processed_events (
          channel TEXT NOT NULL,
          event_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('processing','completed','failed')),
          attempts INT NOT NULL DEFAULT 1,
          locked_until TIMESTAMPTZ,
          lease_token UUID,
          last_error TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          completed_at TIMESTAMPTZ,
          PRIMARY KEY(channel, event_id)
        );
        ALTER TABLE processed_events ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'completed';
        ALTER TABLE processed_events ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 1;
        ALTER TABLE processed_events ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;
        ALTER TABLE processed_events ADD COLUMN IF NOT EXISTS lease_token UUID;
        ALTER TABLE processed_events ADD COLUMN IF NOT EXISTS last_error TEXT;
        ALTER TABLE processed_events ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
        UPDATE processed_events SET status = 'completed', completed_at = COALESCE(completed_at, created_at)
        WHERE status NOT IN ('processing', 'failed', 'completed');
        CREATE TABLE IF NOT EXISTS zalo_groups (
          group_id TEXT PRIMARY KEY,
          alias TEXT UNIQUE,
          enabled BOOLEAN NOT NULL DEFAULT FALSE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS zalo_group_messages (
          id BIGSERIAL PRIMARY KEY,
          group_id TEXT NOT NULL REFERENCES zalo_groups(group_id) ON DELETE CASCADE,
          external_message_id TEXT NOT NULL,
          sender_name TEXT,
          content TEXT NOT NULL,
          sent_at TIMESTAMPTZ NOT NULL,
          UNIQUE(group_id, external_message_id)
        );
        CREATE TABLE IF NOT EXISTS zalo_users (
          user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          display_name TEXT NOT NULL DEFAULT '',
          role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
          paired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS zalo_users_single_admin
          ON zalo_users ((1)) WHERE role = 'admin';
        CREATE TABLE IF NOT EXISTS zalo_group_summaries (
          id BIGSERIAL PRIMARY KEY,
          group_id TEXT NOT NULL REFERENCES zalo_groups(group_id) ON DELETE CASCADE,
          requested_by UUID REFERENCES users(id) ON DELETE SET NULL,
          period_start TIMESTAMPTZ NOT NULL,
          period_end TIMESTAMPTZ NOT NULL,
          content TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS zalo_group_summaries_group_time
          ON zalo_group_summaries(group_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS notes (
          id BIGSERIAL PRIMARY KEY,
          user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          content TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS notes_user_time ON notes(user_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS reminders (
          id BIGSERIAL PRIMARY KEY,
          user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          content TEXT NOT NULL,
          remind_at TIMESTAMPTZ NOT NULL,
          sent BOOLEAN NOT NULL DEFAULT FALSE,
          attempts INT NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending',
          lease_until TIMESTAMPTZ,
          last_error TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS reminders_due ON reminders(remind_at) WHERE NOT sent;
        ALTER TABLE reminders ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0;
        ALTER TABLE reminders ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
        ALTER TABLE reminders ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;
        ALTER TABLE reminders ADD COLUMN IF NOT EXISTS last_error TEXT;
        CREATE TABLE IF NOT EXISTS zoom_users (
          user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          display_name TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
          paired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS zalo_session (
          id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
          cookie_json TEXT NOT NULL,
          imei TEXT NOT NULL,
          user_agent TEXT NOT NULL DEFAULT 'Mozilla/5.0',
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    await _migrate_knowledge_base(db)


async def _migrate_knowledge_base(db: asyncpg.Pool) -> None:
    """Bảng cho tính năng knowledge base retrieval (pgvector). Tách riêng + try/except: nếu
    Supabase project chưa bật extension `vector` (hoặc bản pgvector quá cũ để hỗ trợ HNSW),
    lỗi ở đây không được làm sập cả app — mọi tính năng khác (chat, stock, nhắc nhở, Zalo...)
    vẫn phải chạy bình thường, chỉ riêng tra cứu tài liệu tạm không hoạt động."""
    try:
        await db.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_sources (
              source TEXT PRIMARY KEY,
              content_hash TEXT NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
              id BIGSERIAL PRIMARY KEY,
              source TEXT NOT NULL,
              chunk_index INT NOT NULL,
              heading TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL,
              embedding vector(768) NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS knowledge_chunks_source ON knowledge_chunks(source);
            """
        )
    except asyncpg.PostgresError:
        logger.warning(
            "Không tạo được bảng knowledge base — có thể Supabase project chưa bật extension "
            "'vector' (Database > Extensions > vector). Tính năng tra cứu tài liệu sẽ tạm "
            "không hoạt động, các tính năng khác không bị ảnh hưởng.",
            exc_info=True,
        )
        return
    try:
        await db.execute(
            "CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding ON knowledge_chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    except asyncpg.PostgresError:
        logger.warning(
            "Không tạo được index HNSW cho knowledge_chunks (pgvector bản cũ?); tra cứu vẫn "
            "hoạt động nhưng có thể chậm hơn khi dữ liệu lớn."
        )


async def get_or_create_user(channel: Channel, external_id: str, role: Role) -> User:
    db = await pool()
    row = await db.fetchrow(
        """
        INSERT INTO users(channel, external_id, role)
        VALUES($1,$2,$3)
        ON CONFLICT(channel, external_id) DO UPDATE
          SET role = CASE WHEN users.role = 'root' THEN users.role ELSE EXCLUDED.role END
        RETURNING id::text, channel, external_id, role, active, rag_enabled
        """,
        channel.value,
        external_id,
        role.value,
    )
    return User(
        row["id"],
        Channel(row["channel"]),
        row["external_id"],
        Role(row["role"]),
        row["active"],
        row["rag_enabled"],
    )


async def set_rag_enabled(user_id: str, enabled: bool) -> None:
    """Bật/tắt tra cứu knowledge base (RAG) riêng cho 1 user. Khi tắt, chat() bỏ hẳn bước
    gọi Google Embedding API + search_knowledge cho user đó — dùng khi chỉ chat phiếm,
    không cần tra tài liệu, để tiết kiệm quota."""
    db = await pool()
    await db.execute("UPDATE users SET rag_enabled = $1 WHERE id = $2", enabled, user_id)


PROCESSED_EVENTS_RETENTION_DAYS = 30
GROUP_MESSAGES_RETENTION_DAYS = 90


async def cleanup_old_data() -> dict[str, int]:
    """Dọn các bảng chỉ-thêm (append-only) không có cơ chế xoá tự nhiên, để không phình
    to vô hạn theo thời gian. `processed_events` chỉ cần giữ đủ lâu hơn khoảng retry tối
    đa của webhook/gateway (30 ngày dư sức). `zalo_group_messages` giữ lâu hơn (90 ngày)
    vì còn dùng cho /tongket 7d; điều chỉnh nếu cần giữ lịch sử nhóm lâu hơn."""
    db = await pool()
    events_deleted = await db.execute(
        f"DELETE FROM processed_events WHERE created_at < NOW() - INTERVAL "
        f"'{PROCESSED_EVENTS_RETENTION_DAYS} days'"
    )
    messages_deleted = await db.execute(
        f"DELETE FROM zalo_group_messages WHERE sent_at < NOW() - INTERVAL "
        f"'{GROUP_MESSAGES_RETENTION_DAYS} days'"
    )
    return {
        "processed_events": _rowcount(events_deleted),
        "zalo_group_messages": _rowcount(messages_deleted),
    }


def _rowcount(result: str) -> int:
    """asyncpg trả kết quả DELETE dạng chuỗi 'DELETE <n>'."""
    try:
        return int(result.rsplit(" ", 1)[-1])
    except ValueError:
        return 0


EVENT_LEASE_SECONDS = 360


async def claim_event(channel: Channel, event_id: str) -> str | None:
    db = await pool()
    lease_token = uuid.uuid4()
    row = await db.fetchrow(
        """
        INSERT INTO processed_events(
          channel, event_id, status, attempts, locked_until, lease_token
        )
        VALUES($1, $2, 'processing', 1, NOW() + make_interval(secs => $4), $3)
        ON CONFLICT (channel, event_id) DO UPDATE
        SET status = 'processing', attempts = processed_events.attempts + 1,
            locked_until = NOW() + make_interval(secs => $4), lease_token = $3, last_error = NULL
        WHERE processed_events.status <> 'completed'
          AND (processed_events.locked_until IS NULL OR processed_events.locked_until < NOW())
        RETURNING lease_token
        """,
        channel.value,
        event_id,
        lease_token,
        EVENT_LEASE_SECONDS,
    )
    return str(row["lease_token"]) if row else None


async def complete_event(channel: Channel, event_id: str, lease_token: str) -> None:
    db = await pool()
    await db.execute(
        """UPDATE processed_events
        SET status = 'completed', completed_at = NOW(), locked_until = NULL, lease_token = NULL,
            last_error = NULL
        WHERE channel = $1 AND event_id = $2 AND lease_token = $3::uuid""",
        channel.value,
        event_id,
        lease_token,
    )


async def fail_event(channel: Channel, event_id: str, lease_token: str, error: str) -> None:
    db = await pool()
    await db.execute(
        """UPDATE processed_events
        SET status = 'failed', locked_until = NULL, lease_token = NULL, last_error = LEFT($4, 2000)
        WHERE channel = $1 AND event_id = $2 AND lease_token = $3::uuid""",
        channel.value,
        event_id,
        lease_token,
        error,
    )


async def _zalo_ensure_user(conn: asyncpg.Connection, external_id: str) -> dict[str, object]:
    return await conn.fetchrow(
        """
        INSERT INTO users(channel, external_id, role)
        VALUES('zalo', $1, 'user')
        ON CONFLICT(channel, external_id) DO UPDATE SET channel = users.channel
        RETURNING id::text, channel, external_id
        """,
        external_id,
    )


def _zalo_user_from_rows(user_row: object, zalo_row: object) -> User:
    return User(
        user_row["id"],
        Channel(user_row["channel"]),
        user_row["external_id"],
        Role.ZALO_ADMIN if zalo_row["role"] == "admin" else Role.USER,
        zalo_row["status"] == "active",
    )


async def zalo_pair(external_id: str, display_name: str = "") -> tuple[User, str]:
    """Owner Telegram pair một Zalo user mới (hoặc pair lại người đã bị xóa/khóa).
    Trả kèm display_name thực sự được lưu (giữ tên cũ nếu gọi lại mà không nhập tên mới)."""
    db = await pool()
    async with db.acquire() as conn, conn.transaction():
        user_row = await _zalo_ensure_user(conn, external_id)
        zalo_row = await conn.fetchrow(
            """
            INSERT INTO zalo_users(user_id, display_name, status, paired_at, last_active_at)
            VALUES($1::uuid, $2, 'active', NOW(), NOW())
            ON CONFLICT(user_id) DO UPDATE
              SET status = 'active',
                  display_name = CASE WHEN EXCLUDED.display_name = ''
                                       THEN zalo_users.display_name ELSE EXCLUDED.display_name END
            RETURNING role, status, display_name
            """,
            user_row["id"],
            display_name,
        )
    return _zalo_user_from_rows(user_row, zalo_row), zalo_row["display_name"]


async def zalo_set_admin(external_id: str, display_name: str = "") -> User:
    """Chỉ định A admin duy nhất; admin cũ (nếu có) tự động hạ về user."""
    db = await pool()
    async with db.acquire() as conn, conn.transaction():
        user_row = await _zalo_ensure_user(conn, external_id)
        await conn.execute("UPDATE zalo_users SET role = 'user' WHERE role = 'admin'")
        zalo_row = await conn.fetchrow(
            """
            INSERT INTO zalo_users(user_id, display_name, role, status, paired_at, last_active_at)
            VALUES($1::uuid, $2, 'admin', 'active', NOW(), NOW())
            ON CONFLICT(user_id) DO UPDATE
              SET role = 'admin', status = 'active',
                  display_name = CASE WHEN EXCLUDED.display_name = ''
                                       THEN zalo_users.display_name ELSE EXCLUDED.display_name END
            RETURNING role, status
            """,
            user_row["id"],
            display_name,
        )
    return _zalo_user_from_rows(user_row, zalo_row)


async def zalo_set_status(external_id: str, status: str) -> bool:
    """Khóa (`suspended`) hoặc mở khóa (`active`) một Zalo user đã pair."""
    db = await pool()
    result = await db.execute(
        """
        UPDATE zalo_users SET status = $2
        FROM users
        WHERE users.id = zalo_users.user_id
          AND users.channel = 'zalo' AND users.external_id = $1
        """,
        external_id,
        status,
    )
    return result.endswith("1")


async def zalo_delete(external_id: str) -> bool:
    """Xóa pairing (thu hồi quyền truy cập); lịch sử chat/danh mục của user được giữ nguyên."""
    db = await pool()
    result = await db.execute(
        """
        DELETE FROM zalo_users USING users
        WHERE users.id = zalo_users.user_id
          AND users.channel = 'zalo' AND users.external_id = $1
        """,
        external_id,
    )
    return result.endswith("1")


async def zalo_list_users() -> list[dict[str, object]]:
    db = await pool()
    rows = await db.fetch(
        """
        SELECT u.external_id, z.display_name, z.role, z.status, z.paired_at, z.last_active_at
        FROM zalo_users z
        JOIN users u ON u.id = z.user_id
        ORDER BY z.role DESC, z.paired_at ASC
        """
    )
    return [dict(row) for row in rows]


async def zalo_lookup(external_id: str) -> User | None:
    """Tra cứu user Zalo đã pair; trả None nếu chưa từng được owner pair."""
    db = await pool()
    row = await db.fetchrow(
        """
        SELECT u.id::text AS id, u.channel, u.external_id, z.role, z.status
        FROM users u
        JOIN zalo_users z ON z.user_id = u.id
        WHERE u.channel = 'zalo' AND u.external_id = $1
        """,
        external_id,
    )
    if row is None:
        return None
    await db.execute(
        "UPDATE zalo_users SET last_active_at = NOW() WHERE user_id = $1::uuid", row["id"]
    )
    return _zalo_user_from_rows(row, row)


async def zalo_register_group(group_id: str) -> None:
    """Ghi nhận một group_id mà B vừa thấy tin nhắn, không bật allowlist tự động."""
    db = await pool()
    await db.execute(
        "INSERT INTO zalo_groups(group_id) VALUES($1) ON CONFLICT DO NOTHING", group_id
    )


async def zalo_list_groups() -> list[dict[str, object]]:
    db = await pool()
    rows = await db.fetch(
        "SELECT group_id, alias, enabled, created_at FROM zalo_groups ORDER BY created_at ASC"
    )
    return [dict(row) for row in rows]


async def zalo_enable_group(group_id: str, alias: str | None) -> None:
    """Thêm/bật nhóm vào allowlist. Có thể gọi trước cả khi B từng thấy tin nhắn từ nhóm."""
    db = await pool()
    await db.execute(
        """
        INSERT INTO zalo_groups(group_id, alias, enabled)
        VALUES($1, $2, TRUE)
        ON CONFLICT(group_id) DO UPDATE
          SET enabled = TRUE, alias = COALESCE(EXCLUDED.alias, zalo_groups.alias)
        """,
        group_id,
        alias,
    )


async def zalo_disable_group(identifier: str) -> bool:
    """Gỡ khỏi allowlist theo group_id hoặc alias; giữ lại tin nhắn đã thu thập trước đó."""
    db = await pool()
    result = await db.execute(
        "UPDATE zalo_groups SET enabled = FALSE WHERE group_id = $1 OR alias = $1", identifier
    )
    return result.endswith("1")


async def zalo_group_id_for(identifier: str) -> str | None:
    """Chuẩn hóa alias hoặc group_id thành group_id thật, chỉ khi nhóm đang bật allowlist."""
    db = await pool()
    return await db.fetchval(
        "SELECT group_id FROM zalo_groups WHERE (group_id = $1 OR alias = $1) AND enabled",
        identifier,
    )


async def zalo_enabled_groups() -> list[dict[str, object]]:
    db = await pool()
    rows = await db.fetch("SELECT group_id, alias FROM zalo_groups WHERE enabled")
    return [dict(row) for row in rows]


async def _zoom_ensure_user(conn: asyncpg.Connection, external_id: str) -> dict[str, object]:
    return await conn.fetchrow(
        """
        INSERT INTO users(channel, external_id, role)
        VALUES('zoom', $1, 'user')
        ON CONFLICT(channel, external_id) DO UPDATE SET channel = users.channel
        RETURNING id::text, channel, external_id
        """,
        external_id,
    )


def _zoom_user_from_rows(user_row: object, zoom_row: object) -> User:
    # Không có phân biệt admin/user như Zalo (chưa có tính năng nào cần riêng quyền
    # admin cho Zoom) — mọi Zoom user đã pair đều là Role.USER.
    return User(
        user_row["id"],
        Channel(user_row["channel"]),
        user_row["external_id"],
        Role.USER,
        zoom_row["status"] == "active",
    )


async def zoom_pair(external_id: str, display_name: str = "") -> tuple[User, str]:
    """Owner Telegram pair một Zoom user mới (hoặc pair lại người đã bị xóa/khóa).
    Trả kèm display_name thực sự được lưu (giữ tên cũ nếu gọi lại mà không nhập tên mới)."""
    db = await pool()
    async with db.acquire() as conn, conn.transaction():
        user_row = await _zoom_ensure_user(conn, external_id)
        zoom_row = await conn.fetchrow(
            """
            INSERT INTO zoom_users(user_id, display_name, status, paired_at, last_active_at)
            VALUES($1::uuid, $2, 'active', NOW(), NOW())
            ON CONFLICT(user_id) DO UPDATE
              SET status = 'active',
                  display_name = CASE WHEN EXCLUDED.display_name = ''
                                       THEN zoom_users.display_name ELSE EXCLUDED.display_name END
            RETURNING status, display_name
            """,
            user_row["id"],
            display_name,
        )
    return _zoom_user_from_rows(user_row, zoom_row), zoom_row["display_name"]


async def zoom_set_status(external_id: str, status: str) -> bool:
    """Khóa (`suspended`) hoặc mở khóa (`active`) một Zoom user đã pair."""
    db = await pool()
    result = await db.execute(
        """
        UPDATE zoom_users SET status = $2
        FROM users
        WHERE users.id = zoom_users.user_id
          AND users.channel = 'zoom' AND users.external_id = $1
        """,
        external_id,
        status,
    )
    return result.endswith("1")


async def zoom_delete(external_id: str) -> bool:
    """Xóa pairing (thu hồi quyền truy cập); lịch sử chat/danh mục của user được giữ nguyên."""
    db = await pool()
    result = await db.execute(
        """
        DELETE FROM zoom_users USING users
        WHERE users.id = zoom_users.user_id
          AND users.channel = 'zoom' AND users.external_id = $1
        """,
        external_id,
    )
    return result.endswith("1")


async def zoom_list_users() -> list[dict[str, object]]:
    db = await pool()
    rows = await db.fetch(
        """
        SELECT u.external_id, z.display_name, z.status, z.paired_at, z.last_active_at
        FROM zoom_users z
        JOIN users u ON u.id = z.user_id
        ORDER BY z.paired_at ASC
        """
    )
    return [dict(row) for row in rows]


async def zoom_lookup(external_id: str) -> User | None:
    """Tra cứu user Zoom đã pair; trả None nếu chưa từng được owner pair."""
    db = await pool()
    row = await db.fetchrow(
        """
        SELECT u.id::text AS id, u.channel, u.external_id, z.status
        FROM users u
        JOIN zoom_users z ON z.user_id = u.id
        WHERE u.channel = 'zoom' AND u.external_id = $1
        """,
        external_id,
    )
    if row is None:
        return None
    await db.execute(
        "UPDATE zoom_users SET last_active_at = NOW() WHERE user_id = $1::uuid", row["id"]
    )
    return _zoom_user_from_rows(row, row)


async def zalo_save_summary(
    group_id: str,
    requested_by: str,
    period_start: object,
    period_end: object,
    content: str,
) -> None:
    db = await pool()
    await db.execute(
        """
        INSERT INTO zalo_group_summaries(group_id, requested_by, period_start, period_end, content)
        VALUES($1, $2::uuid, $3, $4, $5)
        """,
        group_id,
        requested_by,
        period_start,
        period_end,
        content,
    )


async def zalo_admin_user() -> User | None:
    """Trả A admin đang active (nếu có) - dùng cho daily digest tự động."""
    db = await pool()
    row = await db.fetchrow(
        """
        SELECT u.id::text AS id, u.channel, u.external_id, z.role, z.status
        FROM zalo_users z
        JOIN users u ON u.id = z.user_id
        WHERE z.role = 'admin' AND z.status = 'active'
        """
    )
    return None if row is None else _zalo_user_from_rows(row, row)


async def add_note(user_id: str, content: str) -> int:
    db = await pool()
    return await db.fetchval(
        "INSERT INTO notes(user_id, content) VALUES($1::uuid, $2) RETURNING id", user_id, content
    )


async def list_notes(user_id: str) -> list[dict[str, object]]:
    db = await pool()
    rows = await db.fetch(
        """SELECT id, content, created_at FROM notes
        WHERE user_id = $1::uuid ORDER BY created_at DESC LIMIT 50""",
        user_id,
    )
    return [dict(row) for row in rows]


async def delete_note(user_id: str, note_id: int) -> bool:
    db = await pool()
    result = await db.execute(
        "DELETE FROM notes WHERE id = $1 AND user_id = $2::uuid", note_id, user_id
    )
    return result.endswith("1")


async def add_reminder(user_id: str, content: str, remind_at: object) -> int:
    db = await pool()
    return await db.fetchval(
        """INSERT INTO reminders(user_id, content, remind_at)
        VALUES($1::uuid, $2, $3) RETURNING id""",
        user_id,
        content,
        remind_at,
    )


async def list_reminders(user_id: str) -> list[dict[str, object]]:
    db = await pool()
    rows = await db.fetch(
        """SELECT id, content, remind_at FROM reminders
        WHERE user_id = $1::uuid AND status IN ('pending','failed') AND NOT sent ORDER BY remind_at ASC LIMIT 50""",
        user_id,
    )
    return [dict(row) for row in rows]


async def delete_reminder(user_id: str, reminder_id: int) -> bool:
    db = await pool()
    result = await db.execute(
        "DELETE FROM reminders WHERE id = $1 AND user_id = $2::uuid AND NOT sent",
        reminder_id,
        user_id,
    )
    return result.endswith("1")


REMINDER_MAX_ATTEMPTS = 5


async def claim_due_reminders() -> list[dict[str, object]]:
    """Lấy các reminder đến hạn và tăng `attempts` trong cùng transaction (FOR UPDATE SKIP LOCKED
    tránh lấy trùng nếu nhiều tiến trình chạy song song). KHÔNG đánh dấu `sent` ở đây — việc đó
    chỉ xảy ra sau khi gửi thành công (xem `mark_reminder_sent`), để một lần gửi lỗi không làm
    mất nhắc nhở vĩnh viễn. Reminder đã thử quá `REMINDER_MAX_ATTEMPTS` lần sẽ không được lấy lại
    nữa (coi như gửi thất bại hẳn) để tránh vòng lặp thử lại vô hạn."""
    db = await pool()
    async with db.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            """
            WITH due AS (
                SELECT r.id
                FROM reminders r
                WHERE NOT r.sent AND r.remind_at <= NOW() AND r.attempts < $1
                  AND (r.status = 'pending' OR (r.status = 'sending' AND r.lease_until < NOW()))
                ORDER BY r.remind_at ASC
                LIMIT 100
                FOR UPDATE OF r SKIP LOCKED
            )
            UPDATE reminders r
            SET attempts = r.attempts + 1, status = 'sending', lease_until = NOW() + INTERVAL '5 minutes', last_error = NULL
            FROM due, users u
            WHERE r.id = due.id AND u.id = r.user_id
            RETURNING r.id, r.content, r.attempts, u.channel, u.external_id
            """,
            REMINDER_MAX_ATTEMPTS,
        )
    return [dict(row) for row in rows]


async def mark_reminder_sent(reminder_id: int) -> None:
    db = await pool()
    await db.execute(
        "UPDATE reminders SET sent = TRUE, status = 'sent', lease_until = NULL WHERE id = $1",
        reminder_id,
    )


async def mark_reminder_failed(reminder_id: int, error: str) -> None:
    db = await pool()
    await db.execute(
        """UPDATE reminders
        SET status = 'failed', lease_until = NULL, last_error = LEFT($2, 1000)
        WHERE id = $1 AND status = 'sending'""",
        reminder_id,
        error,
    )


async def release_reminder(reminder_id: int, error: str = "") -> None:
    db = await pool()
    await db.execute(
        """UPDATE reminders
        SET status = 'pending', lease_until = NULL, last_error = LEFT($2, 1000)
        WHERE id = $1 AND status = 'sending'""",
        reminder_id,
        error,
    )


async def upsert_holding(
    user_id: str, symbol: str, quantity: Decimal, price: Decimal
) -> tuple[Decimal, Decimal]:
    """Cộng dồn vị thế, tính lại giá vốn bình quân theo khối lượng. Trả (tổng KL, giá vốn mới).

    Dùng Decimal xuyên suốt (khớp kiểu NUMERIC của Postgres) thay vì float: giá vốn bình
    quân được tính lại từ đầu ở mỗi lần mua, nên sai số nhị phân của float sẽ cộng dồn qua
    nhiều giao dịch — với Decimal, phép tính khớp chính xác với những gì lưu trong DB."""
    db = await pool()
    async with db.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """SELECT quantity, average_price FROM stock_holdings
            WHERE user_id = $1::uuid AND symbol = $2 FOR UPDATE""",
            user_id,
            symbol,
        )
        if row is None:
            new_qty, new_avg = quantity, price
        else:
            old_qty, old_avg = Decimal(row["quantity"]), Decimal(row["average_price"])
            new_qty = old_qty + quantity
            new_avg = (old_qty * old_avg + quantity * price) / new_qty
        await conn.execute(
            """
            INSERT INTO stock_holdings(user_id, symbol, quantity, average_price, updated_at)
            VALUES($1::uuid, $2, $3, $4, NOW())
            ON CONFLICT(user_id, symbol) DO UPDATE
              SET quantity = EXCLUDED.quantity, average_price = EXCLUDED.average_price,
                  updated_at = NOW()
            """,
            user_id,
            symbol,
            new_qty,
            new_avg,
        )
    return new_qty, new_avg


async def reduce_holding(user_id: str, symbol: str, quantity: Decimal) -> Decimal | None:
    """Giảm khối lượng đang giữ (bán ra). Trả khối lượng còn lại, hoặc None nếu không đủ/không có."""
    db = await pool()
    async with db.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT quantity FROM stock_holdings WHERE user_id = $1::uuid AND symbol = $2 FOR UPDATE",
            user_id,
            symbol,
        )
        if row is None or Decimal(row["quantity"]) < quantity:
            return None
        remaining = Decimal(row["quantity"]) - quantity
        if remaining <= 0:
            await conn.execute(
                "DELETE FROM stock_holdings WHERE user_id = $1::uuid AND symbol = $2",
                user_id,
                symbol,
            )
        else:
            await conn.execute(
                """UPDATE stock_holdings SET quantity = $3, updated_at = NOW()
                WHERE user_id = $1::uuid AND symbol = $2""",
                user_id,
                symbol,
                remaining,
            )
    return remaining


async def delete_holding(user_id: str, symbol: str) -> bool:
    db = await pool()
    result = await db.execute(
        "DELETE FROM stock_holdings WHERE user_id = $1::uuid AND symbol = $2", user_id, symbol
    )
    return result.endswith("1")


async def list_holdings(user_id: str) -> list[dict[str, object]]:
    db = await pool()
    rows = await db.fetch(
        """SELECT symbol, quantity, average_price, stop_price, target_price FROM stock_holdings
        WHERE user_id = $1::uuid ORDER BY symbol""",
        user_id,
    )
    return [dict(row) for row in rows]


async def set_holding_alerts(
    user_id: str, symbol: str, stop_price: Decimal | None, target_price: Decimal | None
) -> bool:
    """Đặt/xoá mức cảnh báo stop/target THAM KHẢO cho 1 mã đang giữ (không tự động bắn
    thông báo khi giá chạm mức — chỉ lưu để hiển thị lại trong /danhmuc, xem README).
    Trả False nếu mã đó chưa có trong danh mục."""
    db = await pool()
    result = await db.execute(
        """UPDATE stock_holdings SET stop_price = $3, target_price = $4, updated_at = NOW()
        WHERE user_id = $1::uuid AND symbol = $2""",
        user_id,
        symbol,
        stop_price,
        target_price,
    )
    return result.endswith("1")


async def is_holding(user_id: str, symbol: str) -> bool:
    db = await pool()
    value = await db.fetchval(
        "SELECT 1 FROM stock_holdings WHERE user_id = $1::uuid AND symbol = $2", user_id, symbol
    )
    return value is not None


async def add_message(user_id: str, role: str, content: str) -> None:
    db = await pool()
    await db.execute(
        "INSERT INTO chat_messages(user_id,role,content) VALUES($1::uuid,$2,$3)",
        user_id,
        role,
        content,
    )


async def history(user_id: str, turns: int) -> list[dict[str, str]]:
    db = await pool()
    rows = await db.fetch(
        """SELECT role,content FROM chat_messages WHERE user_id=$1::uuid
        ORDER BY created_at DESC LIMIT $2""",
        user_id,
        turns * 2,
    )
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


async def memory(user_id: str) -> Sequence[str]:
    db = await pool()
    value = await db.fetchval("SELECT facts FROM user_memory WHERE user_id=$1::uuid", user_id)
    return json.loads(value) if isinstance(value, str) else (value or [])


async def save_memory(user_id: str, facts: Sequence[str]) -> None:
    """Ghi đè toàn bộ danh sách fact trí nhớ dài hạn của user (xem services/memory.py —
    luôn gọi với bản đã HỢP NHẤT facts cũ + facts mới, không phải chỉ facts mới)."""
    db = await pool()
    await db.execute(
        """INSERT INTO user_memory(user_id, facts, updated_at) VALUES($1::uuid, $2::jsonb, NOW())
        ON CONFLICT(user_id) DO UPDATE SET facts = EXCLUDED.facts, updated_at = NOW()""",
        user_id,
        json.dumps(list(facts), ensure_ascii=False),
    )


async def get_zalo_session() -> dict[str, str] | None:
    """Session Zalo B đã lưu (cookie+imei), dùng để tự đăng nhập lại khi container restart
    mà không cần quét QR lại. None nếu chưa từng đăng nhập thành công.

    `cookie_json` được lưu mã hoá (xem core.crypto) vì đây thực chất là bearer token của
    tài khoản Zalo B — ai đọc được nó là chiếm được tài khoản mà không cần quét QR."""
    db = await pool()
    row = await db.fetchrow("SELECT cookie_json, imei, user_agent FROM zalo_session WHERE id = 1")
    if row is None:
        return None
    result = dict(row)
    result["cookie_json"] = crypto.decrypt(result["cookie_json"])
    return result


async def save_zalo_session(cookie_json: str, imei: str, user_agent: str) -> None:
    db = await pool()
    await db.execute(
        """
        INSERT INTO zalo_session(id, cookie_json, imei, user_agent, updated_at)
        VALUES(1, $1, $2, $3, NOW())
        ON CONFLICT(id) DO UPDATE
          SET cookie_json = EXCLUDED.cookie_json, imei = EXCLUDED.imei,
              user_agent = EXCLUDED.user_agent, updated_at = NOW()
        """,
        crypto.encrypt(cookie_json),
        imei,
        user_agent,
    )


async def clear_zalo_session() -> None:
    db = await pool()
    await db.execute("DELETE FROM zalo_session WHERE id = 1")


def _vector_literal(values: Sequence[float]) -> str:
    """asyncpg không có codec sẵn cho kiểu `vector` của pgvector — truyền dạng text theo
    đúng cú pháp literal của pgvector rồi cast `::vector` phía SQL."""
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


async def get_knowledge_source_hash(source: str) -> str | None:
    db = await pool()
    return await db.fetchval("SELECT content_hash FROM knowledge_sources WHERE source = $1", source)


async def list_knowledge_sources() -> list[str]:
    db = await pool()
    rows = await db.fetch("SELECT source FROM knowledge_sources")
    return [row["source"] for row in rows]


async def has_knowledge_content() -> bool:
    """Kiểm tra rẻ (EXISTS, không SELECT dữ liệu) xem đã có chunk nào được index chưa.
    Dùng để tránh gọi API embedding (chậm + tốn quota) cho mỗi tin nhắn chat khi
    knowledge base hiện đang rỗng."""
    db = await pool()
    return bool(await db.fetchval("SELECT EXISTS (SELECT 1 FROM knowledge_chunks)"))


async def replace_knowledge_source(
    source: str, content_hash: str, chunks: list[tuple[str, str, list[float]]]
) -> None:
    """Thay toàn bộ chunk của 1 file nguồn bằng bộ chunk mới trong 1 transaction (xoá cũ rồi
    chèn mới) — tránh trạng thái nửa cũ nửa mới nếu app bị dừng giữa chừng lúc reindex."""
    db = await pool()
    async with db.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM knowledge_chunks WHERE source = $1", source)
        for index, (heading, content, embedding) in enumerate(chunks):
            await conn.execute(
                """INSERT INTO knowledge_chunks(source, chunk_index, heading, content, embedding)
                VALUES($1, $2, $3, $4, $5::vector)""",
                source,
                index,
                heading,
                content,
                _vector_literal(embedding),
            )
        await conn.execute(
            """
            INSERT INTO knowledge_sources(source, content_hash, updated_at)
            VALUES($1, $2, NOW())
            ON CONFLICT(source) DO UPDATE SET content_hash = EXCLUDED.content_hash, updated_at = NOW()
            """,
            source,
            content_hash,
        )


async def delete_knowledge_source(source: str) -> None:
    db = await pool()
    async with db.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM knowledge_chunks WHERE source = $1", source)
        await conn.execute("DELETE FROM knowledge_sources WHERE source = $1", source)


async def search_knowledge(embedding: Sequence[float], limit: int = 5) -> list[dict[str, object]]:
    db = await pool()
    literal = _vector_literal(embedding)
    rows = await db.fetch(
        """
        SELECT source, heading, content, 1 - (embedding <=> $1::vector) AS score
        FROM knowledge_chunks
        ORDER BY embedding <=> $1::vector
        LIMIT $2
        """,
        literal,
        limit,
    )
    return [dict(row) for row in rows]

import os
import aiosqlite
from config import (
    WHOP_API_KEY, WHOP_API_KEY_GLOBAL,
    ALLOWED_PLAN_IDS, ALLOWED_PRODUCT_IDS,
    SHEET_ID, SHEET_NAME, GLOBAL_JOIN_LINK,
)

# Railway Volume มอนต์ที่ /data — ถ้าไม่มีก็ใช้ local path (dev)
_DATA_DIR = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
DB_PATH = os.path.join(_DATA_DIR, "guild_config.db")

VALID_KEYS = {
    "whop_api_key",
    "whop_api_key_global",
    "allowed_plan_ids",
    "allowed_product_ids",
    "sheet_id",
    "sheet_name",
    "join_link",
    "checkout_links",
    "trial_role_id",
    "guild_name",
}


# ──────────────────────────────────────────
# Init
# ──────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Guild config table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_configs (
                guild_id             TEXT PRIMARY KEY,
                guild_name           TEXT,
                whop_api_key         TEXT,
                whop_api_key_global  TEXT,
                allowed_plan_ids     TEXT,
                allowed_product_ids  TEXT,
                sheet_id             TEXT,
                sheet_name           TEXT,
                join_link            TEXT,
                trial_role_id        TEXT,
                checkout_links       TEXT,
                created_at           TEXT DEFAULT (datetime('now')),
                updated_at           TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migration: add columns if upgrading from older schema
        for col in ("trial_role_id TEXT", "checkout_links TEXT", "guild_name TEXT"):
            try:
                await db.execute(f"ALTER TABLE guild_configs ADD COLUMN {col}")
            except Exception:
                pass

        await db.commit()

    await init_trials_tables()
    await init_features_table()
    await init_licensing_tables()
    await init_activity_tables()
    await init_course_links_table()
    print("✅ Database initialised")


async def init_trials_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trial_members (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      TEXT NOT NULL,
                discord_id    TEXT NOT NULL,
                role_id       TEXT NOT NULL,
                days          INTEGER NOT NULL,
                source        TEXT DEFAULT 'command',
                code          TEXT,
                granted_at    TEXT NOT NULL,
                expires_at    TEXT NOT NULL,
                notified_3d   INTEGER DEFAULT 0,
                notified_1d   INTEGER DEFAULT 0,
                revoked       INTEGER DEFAULT 0,
                revoked_at    TEXT,
                UNIQUE(guild_id, discord_id, role_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trial_codes (
                code            TEXT PRIMARY KEY,
                guild_id        TEXT NOT NULL,
                role_id         TEXT NOT NULL,
                days            INTEGER NOT NULL,
                max_uses        INTEGER DEFAULT 1,
                uses            INTEGER DEFAULT 0,
                created_by      TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                expires_code_at TEXT,
                active          INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trial_invites (
                invite_code  TEXT PRIMARY KEY,
                guild_id     TEXT NOT NULL,
                channel_id   TEXT NOT NULL,
                role_id      TEXT NOT NULL,
                days         INTEGER NOT NULL,
                max_uses     INTEGER DEFAULT 1,
                uses         INTEGER DEFAULT 0,
                created_by   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                active       INTEGER DEFAULT 1
            )
        """)
        # Indexes สำหรับ query ที่ใช้บ่อย
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trial_members_guild ON trial_members(guild_id, revoked)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trial_members_expires ON trial_members(expires_at, revoked)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trial_codes_guild ON trial_codes(guild_id, active)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trial_invites_guild ON trial_invites(guild_id, active)")
        await db.commit()


# ──────────────────────────────────────────
# Guild Config
# ──────────────────────────────────────────

def _split(val: str | None) -> list[str]:
    if not val:
        return []
    return [s.strip() for s in val.split(",") if s.strip()]


async def get_guild_config(guild_id: str | None) -> dict:
    """Return merged config: guild DB values override global .env."""
    row = None
    if guild_id:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM guild_configs WHERE guild_id = ?", (str(guild_id),)
            ) as cursor:
                row = await cursor.fetchone()

    if row is None:
        return {
            "guild_name":          None,
            "whop_api_key":        WHOP_API_KEY,
            "whop_api_key_global": WHOP_API_KEY_GLOBAL,
            "allowed_plan_ids":    ALLOWED_PLAN_IDS,
            "allowed_product_ids": ALLOWED_PRODUCT_IDS,
            "sheet_id":            SHEET_ID,
            "sheet_name":          SHEET_NAME,
            "join_link":           GLOBAL_JOIN_LINK,
            "checkout_links":      None,
            "trial_role_id":       None,
        }

    return {
        "guild_name":          row["guild_name"]      or None,
        "whop_api_key":        row["whop_api_key"]        or WHOP_API_KEY,
        "whop_api_key_global": row["whop_api_key_global"] or WHOP_API_KEY_GLOBAL,
        "allowed_plan_ids":    _split(row["allowed_plan_ids"])    or ALLOWED_PLAN_IDS,
        "allowed_product_ids": _split(row["allowed_product_ids"]) or ALLOWED_PRODUCT_IDS,
        "sheet_id":            row["sheet_id"]       or SHEET_ID,
        "sheet_name":          row["sheet_name"]     or SHEET_NAME,
        "join_link":           row["join_link"]      or GLOBAL_JOIN_LINK,
        "checkout_links":      row["checkout_links"] or None,
        "trial_role_id":       row["trial_role_id"]  or None,
    }


async def set_guild_config_field(guild_id: str, key: str, value: str):
    if key not in VALID_KEYS:
        raise ValueError(f"Unknown config key: {key}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO guild_configs (guild_id) VALUES (?)", (guild_id,)
        )
        await db.execute(
            f"UPDATE guild_configs SET {key} = ?, updated_at = datetime('now') WHERE guild_id = ?",
            (value, guild_id),
        )
        await db.commit()


async def set_guild_name(guild_id: str, name: str):
    """Auto-called by bot on_ready/on_guild_join to sync Discord server name."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO guild_configs (guild_id) VALUES (?)", (guild_id,)
        )
        await db.execute(
            "UPDATE guild_configs SET guild_name=?, updated_at=datetime('now') WHERE guild_id=?",
            (name, guild_id),
        )
        await db.commit()


async def reset_guild_config(guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM guild_configs WHERE guild_id = ?", (guild_id,))
        await db.commit()


async def get_raw_guild_row(guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_configs WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


# ──────────────────────────────────────────
# Trial Members
# ──────────────────────────────────────────

async def add_trial(
    guild_id: str, discord_id: str, role_id: str,
    days: int, source: str = "command", code: str | None = None,
    granted_at: str = None, expires_at: str = None,
):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    if granted_at is None:
        granted_at = now.isoformat()
    if expires_at is None:
        expires_at = (now + timedelta(days=days)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO trial_members
                (guild_id, discord_id, role_id, days, source, code, granted_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, discord_id, role_id) DO UPDATE SET
                days=excluded.days, source=excluded.source, code=excluded.code,
                granted_at=excluded.granted_at, expires_at=excluded.expires_at,
                notified_3d=0, notified_1d=0, revoked=0, revoked_at=NULL
        """, (guild_id, discord_id, role_id, days, source, code, granted_at, expires_at))
        await db.commit()


async def get_active_trials(guild_id: str | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if guild_id:
            async with db.execute(
                "SELECT * FROM trial_members WHERE guild_id=? AND revoked=0 ORDER BY expires_at",
                (guild_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM trial_members WHERE revoked=0 ORDER BY expires_at"
            ) as cursor:
                rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_user_trials(guild_id: str, discord_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trial_members WHERE guild_id=? AND discord_id=? AND revoked=0",
            (guild_id, discord_id),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def mark_notified_3d(trial_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE trial_members SET notified_3d=1 WHERE id=?", (trial_id,))
        await db.commit()


async def mark_notified_1d(trial_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE trial_members SET notified_1d=1 WHERE id=?", (trial_id,))
        await db.commit()


async def mark_trial_revoked(trial_id: int):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE trial_members SET revoked=1, revoked_at=? WHERE id=?",
            (now, trial_id),
        )
        await db.commit()


# ──────────────────────────────────────────
# Trial Codes
# ──────────────────────────────────────────

async def add_code(
    code: str, guild_id: str, role_id: str, days: int,
    max_uses: int, created_by: str, expires_code_at: str | None = None,
):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO trial_codes
                (code, guild_id, role_id, days, max_uses, created_by, created_at, expires_code_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, guild_id, role_id, days, max_uses, created_by, now, expires_code_at))
        await db.commit()


async def get_code(code: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trial_codes WHERE code=?", (code.upper(),)
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def use_code(code: str):
    """เพิ่ม uses +1 และปิด code ถ้าครบ max_uses"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT uses, max_uses FROM trial_codes WHERE code=?", (code.upper(),)
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            new_uses = row[0] + 1
            new_active = 0 if new_uses >= row[1] else 1
            await db.execute(
                "UPDATE trial_codes SET uses=?, active=? WHERE code=?",
                (new_uses, new_active, code.upper()),
            )
            await db.commit()


async def get_active_codes(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trial_codes WHERE guild_id=? AND active=1 ORDER BY created_at DESC",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def deactivate_code(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE trial_codes SET active=0 WHERE code=?", (code.upper(),)
        )
        await db.commit()


# ──────────────────────────────────────────
# Trial Invites
# ──────────────────────────────────────────

async def add_invite(
    invite_code: str, guild_id: str, channel_id: str,
    role_id: str, days: int, max_uses: int, created_by: str,
):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO trial_invites
                (invite_code, guild_id, channel_id, role_id, days, max_uses, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (invite_code, guild_id, channel_id, role_id, days, max_uses, created_by, now))
        await db.commit()


async def get_invite(invite_code: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trial_invites WHERE invite_code=?", (invite_code,)
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def use_invite(invite_code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT uses, max_uses FROM trial_invites WHERE invite_code=?", (invite_code,)
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            new_uses = row[0] + 1
            new_active = 0 if new_uses >= row[1] else 1
            await db.execute(
                "UPDATE trial_invites SET uses=?, active=? WHERE invite_code=?",
                (new_uses, new_active, invite_code),
            )
            await db.commit()


async def get_active_invites(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trial_invites WHERE guild_id=? AND active=1 ORDER BY created_at DESC",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def deactivate_invite(invite_code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE trial_invites SET active=0 WHERE invite_code=?", (invite_code,)
        )
        await db.commit()


# ──────────────────────────────────────────
# Guild Licenses
# ──────────────────────────────────────────

async def init_features_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_features (
                guild_id          TEXT PRIMARY KEY,
                welcome_msg       INTEGER DEFAULT 1,
                translation       INTEGER DEFAULT 1,
                sync_whop         INTEGER DEFAULT 1,
                auto_kick         INTEGER DEFAULT 0,
                activity_tracking INTEGER DEFAULT 0
            )
        """)
        for col in ("auto_kick INTEGER DEFAULT 0", "activity_tracking INTEGER DEFAULT 0"):
            try:
                await db.execute(f"ALTER TABLE guild_features ADD COLUMN {col}")
            except Exception:
                pass
        await db.commit()


async def get_guild_features(guild_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_features WHERE guild_id=?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if row:
        return dict(row)
    return {"guild_id": guild_id, "welcome_msg": 1, "translation": 1, "sync_whop": 1}


async def set_guild_feature(guild_id: str, feature: str, enabled: int):
    _valid = {"welcome_msg", "translation", "sync_whop", "auto_kick", "activity_tracking"}
    if feature not in _valid:
        raise ValueError(f"Unknown feature: {feature}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO guild_features (guild_id) VALUES (?)", (guild_id,)
        )
        await db.execute(
            f"UPDATE guild_features SET {feature}=? WHERE guild_id=?", (enabled, guild_id)
        )
        await db.commit()


async def is_feature_enabled(guild_id: str, feature: str) -> bool:
    features = await get_guild_features(guild_id)
    return bool(features.get(feature, 1))


# ──────────────────────────────────────────
# User Activity Tracking
# ──────────────────────────────────────────

async def init_activity_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_activity (
                guild_id    TEXT NOT NULL,
                discord_id  TEXT NOT NULL,
                last_seen   TEXT NOT NULL,
                PRIMARY KEY (guild_id, discord_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_last_seen ON user_activity(guild_id, last_seen)"
        )
        # Track which DM reminders have been sent to avoid spam
        await db.execute("""
            CREATE TABLE IF NOT EXISTS activity_reminders (
                guild_id    TEXT NOT NULL,
                discord_id  TEXT NOT NULL,
                reminded_14d  INTEGER DEFAULT 0,
                reminded_30d  INTEGER DEFAULT 0,
                reminded_at   TEXT,
                PRIMARY KEY (guild_id, discord_id)
            )
        """)
        await db.commit()


async def upsert_user_activity(guild_id: str, discord_id: str):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_activity (guild_id, discord_id, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, discord_id) DO UPDATE SET last_seen=excluded.last_seen
        """, (guild_id, discord_id, now))
        # Reset reminder flags when user becomes active again
        await db.execute("""
            INSERT INTO activity_reminders (guild_id, discord_id, reminded_14d, reminded_30d)
            VALUES (?, ?, 0, 0)
            ON CONFLICT(guild_id, discord_id) DO UPDATE SET
                reminded_14d=0, reminded_30d=0, reminded_at=NULL
        """, (guild_id, discord_id))
        await db.commit()


async def get_inactive_users(guild_id: str, days: int) -> list[dict]:
    """Return users who haven't been seen in `days` days and haven't been reminded yet."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    col = "reminded_14d" if days <= 14 else "reminded_30d"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"""
            SELECT ua.guild_id, ua.discord_id, ua.last_seen,
                   COALESCE(ar.{col}, 0) as already_reminded
            FROM user_activity ua
            LEFT JOIN activity_reminders ar
                ON ua.guild_id = ar.guild_id AND ua.discord_id = ar.discord_id
            WHERE ua.guild_id = ?
              AND ua.last_seen < ?
              AND COALESCE(ar.{col}, 0) = 0
        """, (guild_id, cutoff)) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_all_inactive_users(days: int) -> list[dict]:
    """Return inactive users across all guilds."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    col = "reminded_14d" if days <= 14 else "reminded_30d"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"""
            SELECT ua.guild_id, ua.discord_id, ua.last_seen,
                   COALESCE(ar.{col}, 0) as already_reminded
            FROM user_activity ua
            LEFT JOIN activity_reminders ar
                ON ua.guild_id = ar.guild_id AND ua.discord_id = ar.discord_id
            WHERE ua.last_seen < ?
              AND COALESCE(ar.{col}, 0) = 0
        """, (cutoff,)) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def mark_activity_reminded(guild_id: str, discord_id: str, days: int):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    col = "reminded_14d" if days <= 14 else "reminded_30d"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""
            INSERT INTO activity_reminders (guild_id, discord_id, {col}, reminded_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(guild_id, discord_id) DO UPDATE SET {col}=1, reminded_at=?
        """, (guild_id, discord_id, now, now))
        await db.commit()


async def get_user_last_seen(guild_id: str, discord_id: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT last_seen FROM user_activity WHERE guild_id=? AND discord_id=?",
            (guild_id, discord_id),
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None


# ──────────────────────────────────────────
# Course Links
# ──────────────────────────────────────────

async def init_course_links_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS course_links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL,
                title       TEXT NOT NULL,
                url         TEXT NOT NULL,
                description TEXT,
                sort_order  INTEGER DEFAULT 0,
                active      INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_course_category ON course_links(category, active, sort_order)"
        )
        await db.commit()


async def get_course_links(category: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM course_links WHERE category=? AND active=1 ORDER BY sort_order, id",
            (category,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_all_course_links() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM course_links WHERE active=1 ORDER BY category, sort_order, id"
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def add_course_link(
    category: str, title: str, url: str,
    description: str | None = None, sort_order: int = 0,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO course_links (category, title, url, description, sort_order)
            VALUES (?, ?, ?, ?, ?)
        """, (category, title, url, description, sort_order))
        await db.commit()
        return cursor.lastrowid


async def remove_course_link(link_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE course_links SET active=0 WHERE id=?", (link_id,))
        await db.commit()


async def edit_course_link(link_id: int, title: str | None = None, url: str | None = None,
                           description: str | None = None, sort_order: int | None = None):
    updates = []
    params = []
    if title is not None:
        updates.append("title=?"); params.append(title)
    if url is not None:
        updates.append("url=?"); params.append(url)
    if description is not None:
        updates.append("description=?"); params.append(description)
    if sort_order is not None:
        updates.append("sort_order=?"); params.append(sort_order)
    if not updates:
        return
    params.append(link_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE course_links SET {', '.join(updates)} WHERE id=?", params)
        await db.commit()


# ──────────────────────────────────────────
# API helper queries (ใช้ใน api.py แทน raw aiosqlite)
# ──────────────────────────────────────────

async def count_configs_and_codes() -> tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM guild_configs") as c:
            guild_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM trial_codes WHERE active=1") as c:
            active_codes = (await c.fetchone())[0]
    return guild_count, active_codes


async def get_all_codes() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trial_codes WHERE active=1 ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_all_invites() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trial_invites WHERE active=1 ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_guilds_overview() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT guild_id, guild_name, updated_at FROM guild_configs ORDER BY updated_at DESC"
        ) as c:
            config_rows = {r["guild_id"]: dict(r) for r in await c.fetchall()}
        async with db.execute(
            "SELECT guild_id, COUNT(*) as cnt FROM trial_members WHERE revoked=0 GROUP BY guild_id"
        ) as c:
            trial_counts = {r["guild_id"]: r["cnt"] for r in await c.fetchall()}

    result = []
    for gid in config_rows:
        result.append({
            "guild_id": gid,
            "guild_name": config_rows.get(gid, {}).get("guild_name") or None,
            "has_config": True,
            "active_trials": trial_counts.get(gid, 0),
        })
    return sorted(result, key=lambda x: (x["guild_name"] or x["guild_id"]))


# ──────────────────────────────────────────
# Supabase auto-switch (ต้องอยู่ท้ายสุดเสมอ)
# ──────────────────────────────────────────
# ถ้าตั้ง env var SUPABASE_DB_URL → override ทุก function ด้วย PostgreSQL version
# ถ้าไม่มี → ใช้ SQLite ตามปกติ (local dev / Railway Volume)
if os.getenv("SUPABASE_DB_URL"):
    from db_pg import *  # noqa: F401, F403

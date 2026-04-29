"""
PostgreSQL / Supabase implementation of db.py
ฟังก์ชันทุกตัวมี signature เดียวกับ db.py แต่ใช้ asyncpg แทน aiosqlite

ใช้งาน: ตั้ง env var SUPABASE_DB_URL แล้ว db.py จะ import * จากไฟล์นี้
"""
import os
import asyncpg
from datetime import datetime, timezone, timedelta
from config import (
    WHOP_API_KEY, WHOP_API_KEY_GLOBAL,
    ALLOWED_PLAN_IDS, ALLOWED_PRODUCT_IDS,
    SHEET_ID, SHEET_NAME, GLOBAL_JOIN_LINK,
)

DB_PATH = None  # ไม่ใช้กับ PostgreSQL

VALID_KEYS = {
    "whop_api_key", "whop_api_key_global",
    "allowed_plan_ids", "allowed_product_ids",
    "sheet_id", "sheet_name", "join_link", "checkout_links", "trial_role_id",
    "guild_name",
}

_SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL", "")
_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            _SUPABASE_DB_URL,
            min_size=2,
            max_size=10,
            statement_cache_size=0,  # required for Supabase PgBouncer
        )
    return _pool


def _row(record) -> dict | None:
    return dict(record) if record else None


def _rows(records) -> list[dict]:
    return [dict(r) for r in records]


def _split(val: str | None) -> list[str]:
    if not val:
        return []
    return [s.strip() for s in val.split(",") if s.strip()]


# ──────────────────────────────────────────
# Init (tables สร้างผ่าน supabase_schema.sql แล้ว)
# ──────────────────────────────────────────

async def init_db():
    await _get_pool()
    print("✅ Supabase pool ready")


async def init_trials_tables(): pass
async def init_features_table(): pass
async def init_licensing_tables(): pass
async def init_activity_tables(): pass
async def init_course_links_table(): pass


# ──────────────────────────────────────────
# Guild Config
# ──────────────────────────────────────────

async def get_guild_config(guild_id: str | None) -> dict:
    row = None
    if guild_id:
        pool = await _get_pool()
        row = _row(await pool.fetchrow(
            "SELECT * FROM guild_configs WHERE guild_id = $1", str(guild_id)
        ))
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
        "guild_name":          row.get("guild_name")      or None,
        "whop_api_key":        row.get("whop_api_key")        or WHOP_API_KEY,
        "whop_api_key_global": row.get("whop_api_key_global") or WHOP_API_KEY_GLOBAL,
        "allowed_plan_ids":    _split(row.get("allowed_plan_ids"))    or ALLOWED_PLAN_IDS,
        "allowed_product_ids": _split(row.get("allowed_product_ids")) or ALLOWED_PRODUCT_IDS,
        "sheet_id":            row.get("sheet_id")       or SHEET_ID,
        "sheet_name":          row.get("sheet_name")     or SHEET_NAME,
        "join_link":           row.get("join_link")      or GLOBAL_JOIN_LINK,
        "checkout_links":      row.get("checkout_links") or None,
        "trial_role_id":       row.get("trial_role_id")  or None,
    }


async def set_guild_config_field(guild_id: str, key: str, value: str):
    if key not in VALID_KEYS:
        raise ValueError(f"Unknown config key: {key}")
    pool = await _get_pool()
    await pool.execute(
        f"""INSERT INTO guild_configs (guild_id, {key}, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT(guild_id) DO UPDATE SET {key}=$2, updated_at=NOW()""",
        guild_id, value,
    )


async def set_guild_name(guild_id: str, name: str):
    pool = await _get_pool()
    await pool.execute(
        """INSERT INTO guild_configs (guild_id, guild_name, updated_at)
           VALUES ($1, $2, NOW())
           ON CONFLICT(guild_id) DO UPDATE SET guild_name=$2, updated_at=NOW()""",
        guild_id, name,
    )


async def reset_guild_config(guild_id: str):
    pool = await _get_pool()
    await pool.execute("DELETE FROM guild_configs WHERE guild_id=$1", guild_id)


async def get_raw_guild_row(guild_id: str) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM guild_configs WHERE guild_id=$1", guild_id)
    return _row(row)


# ──────────────────────────────────────────
# Trial Members
# ──────────────────────────────────────────

async def add_trial(
    guild_id: str, discord_id: str, role_id: str,
    days: int, source: str = "command", code: str | None = None,
    granted_at: str = None, expires_at: str = None,
):
    now = datetime.now(timezone.utc)
    if granted_at is None:
        granted_at = now.isoformat()
    if expires_at is None:
        expires_at = (now + timedelta(days=days)).isoformat()
    pool = await _get_pool()
    await pool.execute("""
        INSERT INTO trial_members
            (guild_id, discord_id, role_id, days, source, code, granted_at, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT(guild_id, discord_id, role_id) DO UPDATE SET
            days=EXCLUDED.days, source=EXCLUDED.source, code=EXCLUDED.code,
            granted_at=EXCLUDED.granted_at, expires_at=EXCLUDED.expires_at,
            notified_3d=0, notified_1d=0, revoked=0, revoked_at=NULL
    """, guild_id, discord_id, role_id, days, source, code, granted_at, expires_at)


async def get_active_trials(guild_id: str | None = None) -> list[dict]:
    pool = await _get_pool()
    if guild_id:
        rows = await pool.fetch(
            "SELECT * FROM trial_members WHERE guild_id=$1 AND revoked=0 ORDER BY expires_at",
            guild_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM trial_members WHERE revoked=0 ORDER BY expires_at"
        )
    return _rows(rows)


async def get_user_trials(guild_id: str, discord_id: str) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT * FROM trial_members WHERE guild_id=$1 AND discord_id=$2 AND revoked=0",
        guild_id, discord_id,
    )
    return _rows(rows)


async def mark_notified_3d(trial_id: int):
    pool = await _get_pool()
    await pool.execute("UPDATE trial_members SET notified_3d=1 WHERE id=$1", trial_id)


async def mark_notified_1d(trial_id: int):
    pool = await _get_pool()
    await pool.execute("UPDATE trial_members SET notified_1d=1 WHERE id=$1", trial_id)


async def mark_trial_revoked(trial_id: int):
    now = datetime.now(timezone.utc).isoformat()
    pool = await _get_pool()
    await pool.execute(
        "UPDATE trial_members SET revoked=1, revoked_at=$1 WHERE id=$2", now, trial_id
    )


# ──────────────────────────────────────────
# Trial Codes
# ──────────────────────────────────────────

async def add_code(
    code: str, guild_id: str, role_id: str, days: int,
    max_uses: int, created_by: str, expires_code_at: str | None = None,
):
    now = datetime.now(timezone.utc).isoformat()
    pool = await _get_pool()
    await pool.execute("""
        INSERT INTO trial_codes
            (code, guild_id, role_id, days, max_uses, created_by, created_at, expires_code_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """, code, guild_id, role_id, days, max_uses, created_by, now, expires_code_at)


async def get_code(code: str) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM trial_codes WHERE code=$1", code.upper())
    return _row(row)


async def use_code(code: str):
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT uses, max_uses FROM trial_codes WHERE code=$1", code.upper()
        )
        if row:
            new_uses = row["uses"] + 1
            new_active = 0 if new_uses >= row["max_uses"] else 1
            await conn.execute(
                "UPDATE trial_codes SET uses=$1, active=$2 WHERE code=$3",
                new_uses, new_active, code.upper(),
            )


async def get_active_codes(guild_id: str) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT * FROM trial_codes WHERE guild_id=$1 AND active=1 ORDER BY created_at DESC",
        guild_id,
    )
    return _rows(rows)


async def deactivate_code(code: str):
    pool = await _get_pool()
    await pool.execute("UPDATE trial_codes SET active=0 WHERE code=$1", code.upper())


# ──────────────────────────────────────────
# Trial Invites
# ──────────────────────────────────────────

async def add_invite(
    invite_code: str, guild_id: str, channel_id: str,
    role_id: str, days: int, max_uses: int, created_by: str,
):
    now = datetime.now(timezone.utc).isoformat()
    pool = await _get_pool()
    await pool.execute("""
        INSERT INTO trial_invites
            (invite_code, guild_id, channel_id, role_id, days, max_uses, created_by, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT(invite_code) DO UPDATE SET
            guild_id=$2, channel_id=$3, role_id=$4, days=$5, max_uses=$6, created_by=$7, created_at=$8
    """, invite_code, guild_id, channel_id, role_id, days, max_uses, created_by, now)


async def get_invite(invite_code: str) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM trial_invites WHERE invite_code=$1", invite_code
    )
    return _row(row)


async def use_invite(invite_code: str):
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT uses, max_uses FROM trial_invites WHERE invite_code=$1", invite_code
        )
        if row:
            new_uses = row["uses"] + 1
            new_active = 0 if new_uses >= row["max_uses"] else 1
            await conn.execute(
                "UPDATE trial_invites SET uses=$1, active=$2 WHERE invite_code=$3",
                new_uses, new_active, invite_code,
            )


async def get_active_invites(guild_id: str) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT * FROM trial_invites WHERE guild_id=$1 AND active=1 ORDER BY created_at DESC",
        guild_id,
    )
    return _rows(rows)


async def deactivate_invite(invite_code: str):
    pool = await _get_pool()
    await pool.execute("UPDATE trial_invites SET active=0 WHERE invite_code=$1", invite_code)


# ──────────────────────────────────────────
# Guild Features
# ──────────────────────────────────────────

async def get_guild_features(guild_id: str) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM guild_features WHERE guild_id=$1", guild_id)
    if row:
        return dict(row)
    return {"guild_id": guild_id, "welcome_msg": 1, "translation": 1, "sync_whop": 1, "auto_kick": 0, "activity_tracking": 0}


async def set_guild_feature(guild_id: str, feature: str, enabled: int):
    _valid = {"welcome_msg", "translation", "sync_whop", "auto_kick", "activity_tracking"}
    if feature not in _valid:
        raise ValueError(f"Unknown feature: {feature}")
    pool = await _get_pool()
    await pool.execute(
        f"""INSERT INTO guild_features (guild_id, {feature}) VALUES ($1, $2)
            ON CONFLICT(guild_id) DO UPDATE SET {feature}=$2""",
        guild_id, enabled,
    )


async def is_feature_enabled(guild_id: str, feature: str) -> bool:
    features = await get_guild_features(guild_id)
    return bool(features.get(feature, 1))


# ──────────────────────────────────────────
# Guild Licenses
# ──────────────────────────────────────────

async def get_guild_license(guild_id: str) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM guild_licenses WHERE guild_id=$1", str(guild_id))
    return _row(row)


async def upsert_guild_license(guild_id: str, days: int, notes: str = None):
    now = datetime.now(timezone.utc)
    activated_at = now.isoformat()
    expires_at = (now + timedelta(days=days)).isoformat()
    pool = await _get_pool()
    await pool.execute("""
        INSERT INTO guild_licenses
            (guild_id, status, days, activated_at, expires_at, notified_3d, notified_1d, notes)
        VALUES ($1, 'active', $2, $3, $4, 0, 0, $5)
        ON CONFLICT(guild_id) DO UPDATE SET
            status='active', days=$2, activated_at=$3, expires_at=$4,
            notified_3d=0, notified_1d=0, notes=$5
    """, guild_id, days, activated_at, expires_at, notes)


async def extend_guild_license(guild_id: str, days: int):
    now = datetime.now(timezone.utc)
    existing = await get_guild_license(guild_id)
    if not existing or not existing.get("expires_at"):
        await upsert_guild_license(guild_id, days)
        return
    try:
        current = datetime.fromisoformat(existing["expires_at"])
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        base = max(current, now)
    except Exception:
        base = now
    new_expires = (base + timedelta(days=days)).isoformat()
    pool = await _get_pool()
    await pool.execute(
        "UPDATE guild_licenses SET expires_at=$1, status='active', notified_3d=0, notified_1d=0 WHERE guild_id=$2",
        new_expires, guild_id,
    )


async def revoke_guild_license(guild_id: str):
    pool = await _get_pool()
    await pool.execute("UPDATE guild_licenses SET status='revoked' WHERE guild_id=$1", guild_id)


async def mark_license_notified_3d(guild_id: str):
    pool = await _get_pool()
    await pool.execute("UPDATE guild_licenses SET notified_3d=1 WHERE guild_id=$1", guild_id)


async def mark_license_notified_1d(guild_id: str):
    pool = await _get_pool()
    await pool.execute("UPDATE guild_licenses SET notified_1d=1 WHERE guild_id=$1", guild_id)


async def mark_license_expired(guild_id: str):
    pool = await _get_pool()
    await pool.execute("UPDATE guild_licenses SET status='expired' WHERE guild_id=$1", guild_id)


async def get_all_licenses() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch("SELECT * FROM guild_licenses ORDER BY expires_at")
    return _rows(rows)


async def is_guild_licensed(guild_id: str) -> bool:
    lic = await get_guild_license(guild_id)
    if not lic or lic["status"] != "active":
        return False
    if not lic["expires_at"]:
        return True
    now = datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(lic["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > now
    except Exception:
        return False


# ──────────────────────────────────────────
# License Tokens
# ──────────────────────────────────────────

async def add_license_token(token: str, days: int, created_by: str, max_uses: int = 1, notes: str = None):
    now = datetime.now(timezone.utc).isoformat()
    pool = await _get_pool()
    await pool.execute("""
        INSERT INTO license_tokens (token, days, max_uses, created_by, created_at, notes)
        VALUES ($1, $2, $3, $4, $5, $6)
    """, token, days, max_uses, created_by, now, notes)


async def get_license_token(token: str) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM license_tokens WHERE token=$1", token.upper())
    return _row(row)


async def consume_license_token(token: str):
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT uses, max_uses FROM license_tokens WHERE token=$1", token.upper()
        )
        if row:
            new_uses = row["uses"] + 1
            new_active = 0 if new_uses >= row["max_uses"] else 1
            await conn.execute(
                "UPDATE license_tokens SET uses=$1, active=$2 WHERE token=$3",
                new_uses, new_active, token.upper(),
            )


async def get_active_license_tokens() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT * FROM license_tokens WHERE active=1 ORDER BY created_at DESC"
    )
    return _rows(rows)


async def deactivate_license_token(token: str):
    pool = await _get_pool()
    await pool.execute("UPDATE license_tokens SET active=0 WHERE token=$1", token.upper())


# ──────────────────────────────────────────
# User Activity
# ──────────────────────────────────────────

async def upsert_user_activity(guild_id: str, discord_id: str):
    now = datetime.now(timezone.utc).isoformat()
    pool = await _get_pool()
    await pool.execute("""
        INSERT INTO user_activity (guild_id, discord_id, last_seen)
        VALUES ($1, $2, $3)
        ON CONFLICT(guild_id, discord_id) DO UPDATE SET last_seen=$3
    """, guild_id, discord_id, now)
    await pool.execute("""
        INSERT INTO activity_reminders (guild_id, discord_id, reminded_14d, reminded_30d)
        VALUES ($1, $2, 0, 0)
        ON CONFLICT(guild_id, discord_id) DO UPDATE SET reminded_14d=0, reminded_30d=0, reminded_at=NULL
    """, guild_id, discord_id)


async def get_inactive_users(guild_id: str, days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    col = "reminded_14d" if days <= 14 else "reminded_30d"
    pool = await _get_pool()
    rows = await pool.fetch(f"""
        SELECT ua.guild_id, ua.discord_id, ua.last_seen,
               COALESCE(ar.{col}, 0) as already_reminded
        FROM user_activity ua
        LEFT JOIN activity_reminders ar
            ON ua.guild_id = ar.guild_id AND ua.discord_id = ar.discord_id
        WHERE ua.guild_id = $1 AND ua.last_seen < $2 AND COALESCE(ar.{col}, 0) = 0
    """, guild_id, cutoff)
    return _rows(rows)


async def get_all_inactive_users(days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    col = "reminded_14d" if days <= 14 else "reminded_30d"
    pool = await _get_pool()
    rows = await pool.fetch(f"""
        SELECT ua.guild_id, ua.discord_id, ua.last_seen,
               COALESCE(ar.{col}, 0) as already_reminded
        FROM user_activity ua
        LEFT JOIN activity_reminders ar
            ON ua.guild_id = ar.guild_id AND ua.discord_id = ar.discord_id
        WHERE ua.last_seen < $1 AND COALESCE(ar.{col}, 0) = 0
    """, cutoff)
    return _rows(rows)


async def mark_activity_reminded(guild_id: str, discord_id: str, days: int):
    now = datetime.now(timezone.utc).isoformat()
    col = "reminded_14d" if days <= 14 else "reminded_30d"
    pool = await _get_pool()
    await pool.execute(f"""
        INSERT INTO activity_reminders (guild_id, discord_id, {col}, reminded_at)
        VALUES ($1, $2, 1, $3)
        ON CONFLICT(guild_id, discord_id) DO UPDATE SET {col}=1, reminded_at=$3
    """, guild_id, discord_id, now)


async def get_user_last_seen(guild_id: str, discord_id: str) -> str | None:
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT last_seen FROM user_activity WHERE guild_id=$1 AND discord_id=$2",
        guild_id, discord_id,
    )
    return row["last_seen"] if row else None


# ──────────────────────────────────────────
# Course Links
# ──────────────────────────────────────────

async def get_course_links(category: str) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT * FROM course_links WHERE category=$1 AND active=1 ORDER BY sort_order, id",
        category,
    )
    return _rows(rows)


async def get_all_course_links() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT * FROM course_links WHERE active=1 ORDER BY category, sort_order, id"
    )
    return _rows(rows)


async def add_course_link(
    category: str, title: str, url: str,
    description: str | None = None, sort_order: int = 0,
) -> int:
    pool = await _get_pool()
    row = await pool.fetchrow("""
        INSERT INTO course_links (category, title, url, description, sort_order)
        VALUES ($1, $2, $3, $4, $5) RETURNING id
    """, category, title, url, description, sort_order)
    return row["id"]


async def remove_course_link(link_id: int):
    pool = await _get_pool()
    await pool.execute("UPDATE course_links SET active=0 WHERE id=$1", link_id)


# ──────────────────────────────────────────
# API helper queries
# ──────────────────────────────────────────

async def count_configs_and_codes() -> tuple[int, int]:
    pool = await _get_pool()
    guild_count = await pool.fetchval("SELECT COUNT(*) FROM guild_configs")
    active_codes = await pool.fetchval("SELECT COUNT(*) FROM trial_codes WHERE active=1")
    return guild_count, active_codes


async def get_all_codes() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch("SELECT * FROM trial_codes WHERE active=1 ORDER BY created_at DESC")
    return _rows(rows)


async def get_all_invites() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch("SELECT * FROM trial_invites WHERE active=1 ORDER BY created_at DESC")
    return _rows(rows)


async def get_guilds_overview() -> list[dict]:
    pool = await _get_pool()
    config_rows = {r["guild_id"]: dict(r) for r in await pool.fetch(
        "SELECT guild_id, guild_name, updated_at FROM guild_configs ORDER BY updated_at DESC"
    )}
    license_rows = {r["guild_id"]: dict(r) for r in await pool.fetch(
        "SELECT guild_id, status, expires_at FROM guild_licenses"
    )}
    trial_counts = {r["guild_id"]: r["cnt"] for r in await pool.fetch(
        "SELECT guild_id, COUNT(*) as cnt FROM trial_members WHERE revoked=0 GROUP BY guild_id"
    )}
    all_guild_ids = set(config_rows) | set(license_rows)
    result = []
    for gid in all_guild_ids:
        result.append({
            "guild_id": gid,
            "guild_name": config_rows.get(gid, {}).get("guild_name") or None,
            "has_config": gid in config_rows,
            "license_status": license_rows.get(gid, {}).get("status", "none"),
            "license_expires_at": license_rows.get(gid, {}).get("expires_at"),
            "active_trials": trial_counts.get(gid, 0),
        })
    return sorted(result, key=lambda x: (x["guild_name"] or x["guild_id"]))


async def edit_course_link(link_id: int, title: str | None = None, url: str | None = None,
                           description: str | None = None, sort_order: int | None = None):
    updates = []
    params = []
    idx = 1
    if title is not None:
        updates.append(f"title=${idx}"); params.append(title); idx += 1
    if url is not None:
        updates.append(f"url=${idx}"); params.append(url); idx += 1
    if description is not None:
        updates.append(f"description=${idx}"); params.append(description); idx += 1
    if sort_order is not None:
        updates.append(f"sort_order=${idx}"); params.append(sort_order); idx += 1
    if not updates:
        return
    params.append(link_id)
    pool = await _get_pool()
    await pool.execute(
        f"UPDATE course_links SET {', '.join(updates)} WHERE id=${idx}", *params
    )

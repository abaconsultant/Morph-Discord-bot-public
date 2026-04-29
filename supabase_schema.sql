-- ============================================================
-- ABA100X Bot — Supabase Schema
-- รัน SQL นี้ใน Supabase SQL Editor เพื่อสร้าง tables ทั้งหมด
-- ============================================================

-- Guild configs
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
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- Guild features
CREATE TABLE IF NOT EXISTS guild_features (
    guild_id          TEXT PRIMARY KEY,
    welcome_msg       INTEGER DEFAULT 1,
    translation       INTEGER DEFAULT 1,
    sync_whop         INTEGER DEFAULT 1,
    auto_kick         INTEGER DEFAULT 0,
    activity_tracking INTEGER DEFAULT 0
);

-- Trial members
CREATE TABLE IF NOT EXISTS trial_members (
    id            SERIAL PRIMARY KEY,
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
);

CREATE INDEX IF NOT EXISTS idx_trial_members_guild ON trial_members(guild_id, revoked);
CREATE INDEX IF NOT EXISTS idx_trial_members_expires ON trial_members(expires_at, revoked);

-- Trial codes
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
);

CREATE INDEX IF NOT EXISTS idx_trial_codes_guild ON trial_codes(guild_id, active);

-- Trial invites
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
);

CREATE INDEX IF NOT EXISTS idx_trial_invites_guild ON trial_invites(guild_id, active);

-- Guild licenses
CREATE TABLE IF NOT EXISTS guild_licenses (
    guild_id      TEXT PRIMARY KEY,
    status        TEXT DEFAULT 'active',
    days          INTEGER,
    activated_at  TEXT,
    expires_at    TEXT,
    notified_3d   INTEGER DEFAULT 0,
    notified_1d   INTEGER DEFAULT 0,
    notes         TEXT
);

-- License tokens
CREATE TABLE IF NOT EXISTS license_tokens (
    token         TEXT PRIMARY KEY,
    days          INTEGER NOT NULL,
    max_uses      INTEGER DEFAULT 1,
    uses          INTEGER DEFAULT 0,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    active        INTEGER DEFAULT 1,
    notes         TEXT
);

-- User activity tracking
CREATE TABLE IF NOT EXISTS user_activity (
    guild_id    TEXT NOT NULL,
    discord_id  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (guild_id, discord_id)
);

CREATE INDEX IF NOT EXISTS idx_activity_last_seen ON user_activity(guild_id, last_seen);

-- Activity reminders
CREATE TABLE IF NOT EXISTS activity_reminders (
    guild_id      TEXT NOT NULL,
    discord_id    TEXT NOT NULL,
    reminded_14d  INTEGER DEFAULT 0,
    reminded_30d  INTEGER DEFAULT 0,
    reminded_at   TEXT,
    PRIMARY KEY (guild_id, discord_id)
);

-- Course links
CREATE TABLE IF NOT EXISTS course_links (
    id          SERIAL PRIMARY KEY,
    category    TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER DEFAULT 0,
    active      INTEGER DEFAULT 1,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_course_category ON course_links(category, active, sort_order);

-- ============================================================
-- Row Level Security (RLS) — เปิดสำหรับ service_role เท่านั้น
-- ============================================================
-- Bot ใช้ service_role key → bypass RLS ได้
-- Web dashboard ใช้ anon key → ต้องกำหนด policy เพิ่มเติมถ้าต้องการ
ALTER TABLE guild_configs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE guild_features    ENABLE ROW LEVEL SECURITY;
ALTER TABLE trial_members     ENABLE ROW LEVEL SECURITY;
ALTER TABLE trial_codes       ENABLE ROW LEVEL SECURITY;
ALTER TABLE trial_invites     ENABLE ROW LEVEL SECURITY;
ALTER TABLE guild_licenses    ENABLE ROW LEVEL SECURITY;
ALTER TABLE license_tokens    ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_activity     ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_reminders ENABLE ROW LEVEL SECURITY;
ALTER TABLE course_links      ENABLE ROW LEVEL SECURITY;

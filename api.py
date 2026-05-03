"""
FastAPI REST API — Bot Dashboard
รันร่วมกับ bot.py ใน asyncio event loop เดียวกัน
"""
import os
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import (
    get_active_trials, mark_trial_revoked,
    get_active_codes, deactivate_code,
    get_active_invites, deactivate_invite,
    get_guild_config, set_guild_config_field, reset_guild_config,
    get_guild_features, set_guild_feature,
    set_guild_name,
    count_configs_and_codes, get_all_codes, get_all_invites, get_guilds_overview,
)

app = FastAPI(title="ABA100X Bot API", docs_url="/api/docs", redoc_url=None)

# CORS — เว็บ ABA100X เรียกได้เลย
_ALLOWED_ORIGINS = os.getenv("API_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_API_SECRET = os.getenv("API_SECRET", "")


# ──────────────────────────────────────────
# Auth dependency
# ──────────────────────────────────────────

def require_auth(x_api_token: str = Header(...)):
    if not _API_SECRET:
        raise HTTPException(503, "API_SECRET not configured")
    if x_api_token != _API_SECRET:
        raise HTTPException(401, "Invalid API token")


# ──────────────────────────────────────────
# Status
# ──────────────────────────────────────────

@app.get("/api/status", dependencies=[Depends(require_auth)])
async def get_status():
    trials = await get_active_trials()
    guild_count, active_codes = await count_configs_and_codes()

    now = datetime.now(timezone.utc)
    expiring_soon = 0
    for t in trials:
        try:
            exp = datetime.fromisoformat(t["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            diff = (exp - now).total_seconds()
            if 0 < diff <= 259200:  # 3 days
                expiring_soon += 1
        except Exception:
            pass

    return {
        "active_trials": len(trials),
        "expiring_soon": expiring_soon,
        "total_guilds": guild_count,
        "active_codes": active_codes,
        "timestamp": now.isoformat(),
    }


# ──────────────────────────────────────────
# Trials
# ──────────────────────────────────────────

@app.get("/api/trials", dependencies=[Depends(require_auth)])
async def list_trials(guild_id: str | None = None):
    trials = await get_active_trials(guild_id)
    now = datetime.now(timezone.utc)
    result = []
    for t in trials:
        try:
            exp = datetime.fromisoformat(t["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            days_left = max(0, int((exp - now).total_seconds() / 86400))
        except Exception:
            days_left = None
        result.append({**t, "days_left": days_left})
    return result


@app.delete("/api/trials/{trial_id}", dependencies=[Depends(require_auth)])
async def revoke_trial(trial_id: int):
    await mark_trial_revoked(trial_id)
    return {"ok": True, "revoked_id": trial_id}


# ──────────────────────────────────────────
# Codes
# ──────────────────────────────────────────

@app.get("/api/codes", dependencies=[Depends(require_auth)])
async def list_codes(guild_id: str | None = None):
    if guild_id:
        return await get_active_codes(guild_id)
    return await get_all_codes()


@app.delete("/api/codes/{code}", dependencies=[Depends(require_auth)])
async def disable_code(code: str):
    await deactivate_code(code)
    return {"ok": True, "code": code.upper()}


# ──────────────────────────────────────────
# Invites
# ──────────────────────────────────────────

@app.get("/api/invites", dependencies=[Depends(require_auth)])
async def list_invites(guild_id: str | None = None):
    if guild_id:
        return await get_active_invites(guild_id)
    return await get_all_invites()


@app.delete("/api/invites/{invite_code}", dependencies=[Depends(require_auth)])
async def disable_invite(invite_code: str):
    await deactivate_invite(invite_code)
    return {"ok": True, "invite_code": invite_code}


# ──────────────────────────────────────────
# Guild Config
# ──────────────────────────────────────────

# Keys ที่ซ่อน (ไม่ส่งออก)
_SENSITIVE_KEYS = {"whop_api_key", "whop_api_key_global"}


def _sanitize_config(cfg: dict) -> dict:
    return {k: ("***" if k in _SENSITIVE_KEYS and v else v) for k, v in cfg.items()}


@app.get("/api/config/{guild_id}", dependencies=[Depends(require_auth)])
async def get_config(guild_id: str):
    cfg = await get_guild_config(guild_id)
    return _sanitize_config(cfg)


class ConfigUpdate(BaseModel):
    key: str
    value: str


@app.patch("/api/config/{guild_id}", dependencies=[Depends(require_auth)])
async def update_config(guild_id: str, body: ConfigUpdate):
    try:
        await set_guild_config_field(guild_id, body.key, body.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "key": body.key}


@app.delete("/api/config/{guild_id}", dependencies=[Depends(require_auth)])
async def delete_config(guild_id: str):
    await reset_guild_config(guild_id)
    return {"ok": True}


# ──────────────────────────────────────────
# Features
# ──────────────────────────────────────────

@app.get("/api/features/{guild_id}", dependencies=[Depends(require_auth)])
async def get_features(guild_id: str):
    return await get_guild_features(guild_id)


class FeatureUpdate(BaseModel):
    feature: str
    enabled: bool


@app.patch("/api/features/{guild_id}", dependencies=[Depends(require_auth)])
async def update_feature(guild_id: str, body: FeatureUpdate):
    try:
        await set_guild_feature(guild_id, body.feature, int(body.enabled))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "feature": body.feature, "enabled": body.enabled}


# ──────────────────────────────────────────
# Guilds overview
# ──────────────────────────────────────────

@app.get("/api/guilds", dependencies=[Depends(require_auth)])
async def list_guilds():
    """List all guilds that have either a config or a license."""
    return await get_guilds_overview()


class GuildRenameBody(BaseModel):
    name: str


@app.patch("/api/guilds/{guild_id}/name", dependencies=[Depends(require_auth)])
async def rename_guild(guild_id: str, body: GuildRenameBody):
    """ตั้งชื่อ guild ที่แสดงใน Dashboard."""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    await set_guild_name(guild_id, name)
    return {"ok": True, "guild_id": guild_id, "guild_name": name}

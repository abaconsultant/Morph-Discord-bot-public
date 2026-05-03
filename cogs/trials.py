import secrets
import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks

from db import (
    get_guild_config, is_feature_enabled,
    add_trial, get_active_trials, get_user_trials,
    mark_notified_3d, mark_notified_1d, mark_trial_revoked,
    add_code, get_code, use_code, get_active_codes, deactivate_code,
    add_invite, get_invite, use_invite, get_active_invites, deactivate_invite,
)


async def _notify_admin(guild: discord.Guild, message: str):
    channel = guild.system_channel
    if channel and channel.permissions_for(guild.me).send_messages:
        await channel.send(message)
        return
    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).send_messages:
            await ch.send(message)
            return


def _gen_code() -> str:
    """สร้าง code 8 ตัวอักษร เช่น ABCD1234"""
    return secrets.token_urlsafe(6).upper()[:8]


def _fmt_dt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
        return dt.strftime("%d/%m/%Y %H:%M UTC")
    except Exception:
        return iso


class TrialsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # invite_cache: guild_id (int) → {invite_code: uses_count}
        self.invite_cache: dict[int, dict[str, int]] = {}
        # lock ต่อ guild — ป้องกัน race condition เมื่อ 2 คน join พร้อมกัน
        self._join_locks: dict[int, asyncio.Lock] = {}
        # pending members (Membership Screening) — guild_id → {member_id: invite_code}
        # เก็บไว้รอจนกว่าสมาชิกจะกด Agree Rules แล้วค่อยให้ Role
        self._pending_members: dict[int, dict[int, str]] = {}
        self.trial_expiry_check.start()

    def cog_unload(self):
        self.trial_expiry_check.cancel()

    # ──────────────────────────────────────────
    # Background Task
    # ──────────────────────────────────────────

    @tasks.loop(hours=1)
    async def trial_expiry_check(self):
        now = datetime.now(timezone.utc)
        trials = await get_active_trials()

        for trial in trials:
            guild = self.bot.get_guild(int(trial["guild_id"]))
            if not guild:
                continue

            try:
                expires_at = datetime.fromisoformat(trial["expires_at"]).replace(tzinfo=timezone.utc)
            except Exception:
                continue

            time_left = expires_at - now
            member = guild.get_member(int(trial["discord_id"]))
            role = guild.get_role(int(trial["role_id"]))

            # 3 วันก่อนหมด
            if time_left.days <= 3 and not trial["notified_3d"] and time_left.total_seconds() > 0:
                name = member.mention if member else f"<@{trial['discord_id']}>"
                await _notify_admin(
                    guild,
                    f"⏰ **Trial ใกล้หมด:** {name} จะหมดอายุใน **{time_left.days} วัน** ({_fmt_dt(trial['expires_at'])})"
                )
                await mark_notified_3d(trial["id"])

            # 1 วันก่อนหมด
            if time_left.total_seconds() <= 86400 and not trial["notified_1d"] and time_left.total_seconds() > 0:
                name = member.mention if member else f"<@{trial['discord_id']}>"
                await _notify_admin(
                    guild,
                    f"⚠️ **Trial หมดพรุ่งนี้:** {name} จะหมดภายใน 24 ชั่วโมง ({_fmt_dt(trial['expires_at'])})"
                )
                await mark_notified_1d(trial["id"])

            # หมดแล้ว → ถอน Role (+ kick ถ้าเปิด auto_kick)
            if time_left.total_seconds() <= 0:
                auto_kick = await is_feature_enabled(trial["guild_id"], "auto_kick")
                name = member.mention if member else f"<@{trial['discord_id']}>"
                if auto_kick and member:
                    try:
                        await member.kick(reason="สิ้นสุดระยะทดลอง (auto-kick)")
                        await _notify_admin(guild, f"🦵 **Auto-kicked:** {name} — Trial หมดอายุแล้ว")
                        print(f"🦵 Kicked {trial['discord_id']} from {trial['guild_id']} (trial expired)")
                    except Exception as e:
                        print(f"⚠️ Kick ไม่สำเร็จ {trial['discord_id']}: {e}")
                        if role and role in member.roles:
                            try:
                                await member.remove_roles(role, reason="สิ้นสุดระยะทดลอง")
                            except Exception:
                                pass
                elif member and role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="สิ้นสุดระยะทดลอง")
                        await _notify_admin(guild, f"❌ **Trial หมดอายุ:** {name} — ถอน Role แล้ว")
                    except Exception as e:
                        print(f"⚠️ ถอน Role ไม่สำเร็จ {trial['discord_id']}: {e}")
                elif not member:
                    await _notify_admin(guild, f"❌ **Trial หมดอายุ:** {name} — ออกจาก Server ไปแล้ว")
                await mark_trial_revoked(trial["id"])
                print(f"✅ Revoked trial for {trial['discord_id']} in guild {trial['guild_id']}")

    @trial_expiry_check.before_loop
    async def before_trial_check(self):
        await self.bot.wait_until_ready()

    # ──────────────────────────────────────────
    # Admin: gen_code
    # ──────────────────────────────────────────

    @commands.command(name="gen_code")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def gen_code_cmd(self, ctx: commands.Context, role: discord.Role = None, days: int = 30, max_uses: int = 1):
        """
        สร้าง code ให้ลูกค้า redeem
        ตัวอย่าง: \\gen_code @Trial 30   หรือ  \\gen_code @Trial 30 5 (ใช้ได้ 5 ครั้ง)
        """
        # ใช้ role จาก guild config ถ้าไม่ได้ระบุ
        if role is None:
            gc = await get_guild_config(str(ctx.guild.id))
            default_role_id = gc.get("trial_role_id")
            if default_role_id:
                role = ctx.guild.get_role(int(default_role_id))
            if role is None:
                await ctx.reply("❌ ต้องระบุ @Role ครับ หรือตั้งค่า default ด้วย `\\set_config trial_role_id <role_id>`")
                return

        if days < 1 or days > 365:
            await ctx.reply("❌ days ต้องอยู่ระหว่าง 1-365 ครับ")
            return

        code = _gen_code()
        await add_code(
            code=code,
            guild_id=str(ctx.guild.id),
            role_id=str(role.id),
            days=days,
            max_uses=max_uses,
            created_by=str(ctx.author.id),
        )

        try:
            await ctx.message.delete()
        except Exception:
            pass

        uses_text = f"{max_uses} ครั้ง" if max_uses > 1 else "1 ครั้ง (Single-use)"
        await ctx.author.send(
            f"🎟️ **Code สำเร็จ!**\n\n"
            f"```\n{code}\n```\n"
            f"- Role: **{role.name}**\n"
            f"- ระยะเวลา: **{days} วัน**\n"
            f"- ใช้ได้: **{uses_text}**\n\n"
            f"**วิธีแชร์ให้ลูกค้า:**\n"
            f"> พิมพ์ `\\redeem {code}` ใน Discord server ครับ"
        )
        await ctx.reply("✅ ส่ง Code ทาง DM ให้คุณแล้วครับ", delete_after=5)

    # ──────────────────────────────────────────
    # Admin: grant_trial
    # ──────────────────────────────────────────

    @commands.command(name="grant_trial")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def grant_trial_cmd(self, ctx: commands.Context, member: discord.Member, days: int, role: discord.Role = None):
        """
        ให้สิทธิ์ทดลองตรงๆ โดยไม่ต้องมี code
        ตัวอย่าง: \\grant_trial @user 30 @Trial
        """
        if role is None:
            gc = await get_guild_config(str(ctx.guild.id))
            default_role_id = gc.get("trial_role_id")
            if default_role_id:
                role = ctx.guild.get_role(int(default_role_id))
            if role is None:
                await ctx.reply("❌ ต้องระบุ @Role ครับ หรือตั้งค่า default ด้วย `\\set_config trial_role_id <role_id>`")
                return

        if days < 1 or days > 365:
            await ctx.reply("❌ days ต้องอยู่ระหว่าง 1-365 ครับ")
            return

        await add_trial(
            guild_id=str(ctx.guild.id),
            discord_id=str(member.id),
            role_id=str(role.id),
            days=days,
            source="command",
        )

        if role not in member.roles:
            try:
                await member.add_roles(role, reason=f"Trial {days} วัน โดย {ctx.author}")
            except Exception as e:
                await ctx.reply(f"❌ บันทึกแล้วแต่ add Role ไม่สำเร็จ: {e}")
                return

        from datetime import timezone, timedelta
        expires = datetime.now(timezone.utc) + timedelta(days=days)
        await ctx.reply(
            f"✅ ให้สิทธิ์ **{member.mention}** Role **{role.name}** เป็นเวลา **{days} วัน**\n"
            f"📅 หมดอายุ: {expires.strftime('%d/%m/%Y %H:%M UTC')}"
        )

        try:
            await member.send(
                f"🎉 คุณได้รับสิทธิ์ทดลองใน **{ctx.guild.name}**!\n"
                f"- Role: **{role.name}**\n"
                f"- ระยะเวลา: **{days} วัน**\n"
                f"📅 หมดอายุ: {expires.strftime('%d/%m/%Y %H:%M UTC')}"
            )
        except discord.Forbidden:
            pass

    # ──────────────────────────────────────────
    # Admin: revoke_trial
    # ──────────────────────────────────────────

    @commands.command(name="revoke_trial")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def revoke_trial_cmd(self, ctx: commands.Context, member: discord.Member):
        """ถอนสิทธิ์ทดลองทั้งหมดของ user"""
        trials = await get_user_trials(str(ctx.guild.id), str(member.id))

        if not trials:
            await ctx.reply(f"❌ ไม่พบสิทธิ์ทดลองของ {member.mention} ในระบบครับ")
            return

        removed_roles = []
        for trial in trials:
            role = ctx.guild.get_role(int(trial["role_id"]))
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason=f"Revoked by {ctx.author}")
                    removed_roles.append(role.name)
                except Exception:
                    pass
            await mark_trial_revoked(trial["id"])

        roles_text = ", ".join(removed_roles) if removed_roles else "(ไม่มี Role ที่ต้องถอน)"
        await ctx.reply(
            f"✅ ถอนสิทธิ์ทดลองของ {member.mention} แล้วครับ\n"
            f"- Role ที่ถอน: {roles_text}"
        )

        try:
            await member.send(
                f"ℹ️ สิทธิ์ทดลองของคุณใน **{ctx.guild.name}** ถูกยกเลิกโดย Admin ครับ"
            )
        except discord.Forbidden:
            pass

    # ──────────────────────────────────────────
    # Admin: list_trials
    # ──────────────────────────────────────────

    @commands.command(name="list_trials")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def list_trials_cmd(self, ctx: commands.Context):
        """แสดงรายชื่อสมาชิกทดลองทั้งหมดใน Server นี้"""
        guild_trials = await get_active_trials(str(ctx.guild.id))

        if not guild_trials:
            await ctx.reply("📋 ไม่มีสมาชิกทดลองที่ active ในขณะนี้ครับ")
            return

        now = datetime.now(timezone.utc)
        lines = [f"**📋 สมาชิกทดลอง ({len(guild_trials)} คน)**\n"]
        for t in guild_trials:
            member = ctx.guild.get_member(int(t["discord_id"]))
            name = member.display_name if member else f"ID:{t['discord_id']}"
            role = ctx.guild.get_role(int(t["role_id"]))
            role_name = role.name if role else f"ID:{t['role_id']}"
            try:
                exp = datetime.fromisoformat(t["expires_at"]).replace(tzinfo=timezone.utc)
                remaining = (exp - now).days
                exp_text = f"{exp.strftime('%d/%m/%Y')} (เหลือ {remaining} วัน)" if remaining > 0 else f"⚠️ หมดแล้ว!"
            except Exception:
                exp_text = t["expires_at"]
            lines.append(f"- **{name}** | {role_name} | หมด: {exp_text}")

        msg = "\n".join(lines)
        if len(msg) > 1900:
            chunks = [msg[i:i+1900] for i in range(0, len(msg), 1900)]
            for chunk in chunks:
                await ctx.reply(chunk)
        else:
            await ctx.reply(msg)

    # ──────────────────────────────────────────
    # Admin: list_codes
    # ──────────────────────────────────────────

    @commands.command(name="list_codes")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def list_codes_cmd(self, ctx: commands.Context):
        """แสดง code ที่ active ทั้งหมด"""
        codes = await get_active_codes(str(ctx.guild.id))

        if not codes:
            await ctx.reply("📋 ไม่มี code ที่ active ขณะนี้ครับ")
            return

        lines = [f"**🎟️ Codes ที่ active ({len(codes)} รายการ)**\n"]
        for c in codes:
            role = ctx.guild.get_role(int(c["role_id"]))
            role_name = role.name if role else f"ID:{c['role_id']}"
            lines.append(
                f"- `{c['code']}` | {role_name} | {c['days']} วัน | "
                f"ใช้ไป {c['uses']}/{c['max_uses']}"
            )

        await ctx.reply("\n".join(lines))

    # ──────────────────────────────────────────
    # Admin: disable_code
    # ──────────────────────────────────────────

    @commands.command(name="disable_code")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def disable_code_cmd(self, ctx: commands.Context, code: str):
        """ปิดการใช้งาน code"""
        row = await get_code(code)
        if not row or row["guild_id"] != str(ctx.guild.id):
            await ctx.reply(f"❌ ไม่พบ code `{code.upper()}` ในระบบครับ")
            return
        await deactivate_code(code)
        await ctx.reply(f"✅ ปิดการใช้งาน code `{code.upper()}` แล้วครับ")

    # ──────────────────────────────────────────
    # Admin: import_trials (from Google Sheet)
    # ──────────────────────────────────────────

    @commands.command(name="import_trials")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def import_trials_cmd(self, ctx: commands.Context, *, sheet_tab: str):
        """
        Import สมาชิกทดลองจาก Google Sheet
        Sheet ต้องมีคอลัมน์: discord_id + (days หรือ expiry_date) + optional: role_id หรือ role_name
        ตัวอย่าง: \\import_trials Trial List
        """
        from config import SHEET_ID
        gc = await get_guild_config(str(ctx.guild.id))
        sheet_id = gc.get("sheet_id") or SHEET_ID

        if not sheet_id:
            await ctx.reply("❌ ยังไม่ได้ตั้งค่า Sheet ID ครับ ใช้ `\\set_config sheet_id <id>`")
            return

        await ctx.reply(f"⏳ กำลัง import จาก tab `{sheet_tab}` ...")

        try:
            result = sheets_service.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range=f"'{sheet_tab}'!A:Z",
            ).execute()
            rows = result.get("values", [])
        except Exception as e:
            await ctx.reply(f"❌ อ่าน Sheet ไม่สำเร็จ: {e}")
            return

        if not rows or len(rows) < 2:
            await ctx.reply("❌ ไม่พบข้อมูลใน Sheet หรือมีแค่ header ครับ")
            return

        header = [h.strip().lower() for h in rows[0]]

        # หาคอลัมน์ที่จำเป็น
        def col(name): return header.index(name) if name in header else None

        col_did = col("discord_id")
        col_days = col("days")
        col_exp = col("expiry_date")
        col_role_id = col("role_id")
        col_role_name = col("role_name")

        if col_did is None:
            await ctx.reply("❌ ไม่พบคอลัมน์ `discord_id` ใน Sheet ครับ")
            return
        if col_days is None and col_exp is None:
            await ctx.reply("❌ ต้องมีคอลัมน์ `days` หรือ `expiry_date` อย่างน้อย 1 คอลัมน์ครับ")
            return

        default_role_id = gc.get("trial_role_id")
        success, skipped, errors = 0, 0, 0

        for i, row in enumerate(rows[1:], start=2):
            if not row or len(row) <= col_did:
                skipped += 1
                continue

            discord_id = str(row[col_did]).strip()
            if not discord_id:
                skipped += 1
                continue

            # หา role_id
            role_id = None
            if col_role_id is not None and len(row) > col_role_id:
                role_id = str(row[col_role_id]).strip() or None
            if role_id is None and col_role_name is not None and len(row) > col_role_name:
                rname = str(row[col_role_name]).strip()
                found = discord.utils.get(ctx.guild.roles, name=rname)
                if found:
                    role_id = str(found.id)
            if role_id is None:
                role_id = default_role_id
            if role_id is None:
                errors += 1
                print(f"⚠️ Row {i}: ไม่มี role สำหรับ {discord_id}")
                continue

            # หา days หรือ expiry_date
            granted_at = None
            expires_at = None
            days_val = 30

            if col_days is not None and len(row) > col_days:
                try:
                    days_val = int(str(row[col_days]).strip())
                except Exception:
                    pass

            if col_exp is not None and len(row) > col_exp:
                raw_exp = str(row[col_exp]).strip()
                if raw_exp:
                    try:
                        # รองรับ YYYY-MM-DD หรือ DD/MM/YYYY
                        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                            try:
                                dt = datetime.strptime(raw_exp, fmt).replace(tzinfo=timezone.utc)
                                expires_at = dt.isoformat()
                                days_val = max(1, (dt - datetime.now(timezone.utc)).days)
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass

            try:
                await add_trial(
                    guild_id=str(ctx.guild.id),
                    discord_id=discord_id,
                    role_id=role_id,
                    days=days_val,
                    source="import",
                    expires_at=expires_at,
                )
                # Add role ถ้าสมาชิกยังอยู่ใน server
                member = ctx.guild.get_member(int(discord_id))
                if member:
                    role_obj = ctx.guild.get_role(int(role_id))
                    if role_obj and role_obj not in member.roles:
                        await member.add_roles(role_obj, reason="Trial import")
                success += 1
            except Exception as e:
                errors += 1
                print(f"⚠️ Row {i} error: {e}")

        await ctx.reply(
            f"✅ Import เสร็จสิ้นครับ\n"
            f"- สำเร็จ: **{success}** คน\n"
            f"- ข้าม (ข้อมูลไม่ครบ): **{skipped}** แถว\n"
            f"- Error: **{errors}** แถว"
        )

    # ──────────────────────────────────────────
    # User: redeem
    # ──────────────────────────────────────────

    @commands.command(name="redeem")
    @commands.guild_only()
    async def redeem_cmd(self, ctx: commands.Context, code: str):
        """แลก code เพื่อรับสิทธิ์ทดลอง  ตัวอย่าง: \\redeem ABCD1234"""
        row = await get_code(code)

        if not row:
            await ctx.reply("❌ ไม่พบ code นี้ในระบบครับ กรุณาตรวจสอบอีกครั้ง", delete_after=10)
            return

        if row["guild_id"] != str(ctx.guild.id):
            await ctx.reply("❌ code นี้ไม่ถูกต้องสำหรับ Server นี้ครับ", delete_after=10)
            return

        if not row["active"]:
            await ctx.reply("❌ code นี้หมดอายุหรือถูกใช้ครบแล้วครับ", delete_after=10)
            return

        # เช็ก code expiry (ถ้ามี)
        if row["expires_code_at"]:
            try:
                code_exp = datetime.fromisoformat(row["expires_code_at"]).replace(tzinfo=timezone.utc)
                if code_exp < datetime.now(timezone.utc):
                    await ctx.reply("❌ code นี้หมดอายุแล้วครับ", delete_after=10)
                    await deactivate_code(code)
                    return
            except Exception:
                pass

        role = ctx.guild.get_role(int(row["role_id"]))
        if role is None:
            await ctx.reply("❌ Role ที่ผูกกับ code นี้ไม่พบในระบบแล้วครับ โปรดแจ้ง Admin", delete_after=10)
            return

        # เพิ่ม trial ลงระบบ
        await add_trial(
            guild_id=str(ctx.guild.id),
            discord_id=str(ctx.author.id),
            role_id=str(role.id),
            days=row["days"],
            source="redeem",
            code=code.upper(),
        )
        await use_code(code)

        # Add role
        if role not in ctx.author.roles:
            try:
                await ctx.author.add_roles(role, reason=f"Trial redeem code {code.upper()}")
            except Exception as e:
                await ctx.reply(f"❌ บันทึกแล้วแต่ add Role ไม่สำเร็จ กรุณาแจ้ง Admin: {e}")
                return

        from datetime import timedelta
        expires = datetime.now(timezone.utc) + timedelta(days=row["days"])

        try:
            await ctx.message.delete()
        except Exception:
            pass

        await ctx.author.send(
            f"🎉 **Redeem สำเร็จ!** ยินดีต้อนรับสู่ **{ctx.guild.name}**\n\n"
            f"- Role: **{role.name}**\n"
            f"- ระยะเวลา: **{row['days']} วัน**\n"
            f"📅 หมดอายุ: {expires.strftime('%d/%m/%Y %H:%M UTC')}\n\n"
            "บอทจะแจ้งเตือนคุณ 3 วันและ 1 วันก่อนหมดอายุครับ 👍"
        )
        await ctx.reply(
            f"✅ {ctx.author.mention} Redeem สำเร็จ! ได้รับ Role **{role.name}** แล้วครับ\n"
            f"📅 หมดอายุ: {expires.strftime('%d/%m/%Y %H:%M UTC')}",
            delete_after=15,
        )


    # ──────────────────────────────────────────
    # Invite Cache — Events
    # ──────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        """Cache invite counts ของทุก guild ตอนบอทเริ่ม
        ใช้ค่าจาก DB เป็น baseline สำหรับ trial invites
        เพื่อให้ detect offline joins ได้เมื่อ Discord replay missed events
        """
        for guild in self.bot.guilds:
            try:
                invites = await guild.invites()
                db_rows = await get_active_invites(str(guild.id))
                db_uses = {row["invite_code"]: row["uses"] for row in db_rows}
                # Trial invites: ใช้ count จาก DB (ไม่รวม joins ที่เกิดระหว่าง offline)
                # Other invites: ใช้ count จาก Discord ปกติ
                self.invite_cache[guild.id] = {
                    inv.code: db_uses.get(inv.code, inv.uses)
                    for inv in invites
                }
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Cache invites เมื่อบอทถูกเพิ่มใน Server ใหม่"""
        try:
            invites = await guild.invites()
            self.invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        """อัปเดต cache เมื่อมี invite ใหม่"""
        guild_cache = self.invite_cache.setdefault(invite.guild.id, {})
        guild_cache[invite.code] = invite.uses or 0
        print(f"📨 on_invite_create: {invite.code} (guild {invite.guild.id}) — cache now has {len(guild_cache)} invites")

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        """ลบออกจาก cache เมื่อ invite ถูกลบ"""
        if invite.guild.id in self.invite_cache:
            self.invite_cache[invite.guild.id].pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """ตรวจจับว่าสมาชิกใหม่ใช้ invite ไหน แล้วให้ Trial role อัตโนมัติ"""
        guild = member.guild
        print(f"📥 Member joined: {member} (guild: {guild.id})")

        # Lock ต่อ guild — ป้องกัน 2 คน join พร้อมกันแล้ว handler หลังเห็น cache ที่ handler แรก update ไปแล้ว
        if guild.id not in self._join_locks:
            self._join_locks[guild.id] = asyncio.Lock()
        async with self._join_locks[guild.id]:
            await self._process_member_join(member)

    async def _process_member_join(self, member: discord.Member):
        guild = member.guild

        # ต้อง snapshot cache ก่อน await ใดๆ
        # เพราะ on_invite_delete อาจทำงานระหว่าง await guild.invites() และลบ invite ออกจาก cache
        old_cache = dict(self.invite_cache.get(guild.id, {}))

        try:
            current_invites = await guild.invites()
        except discord.Forbidden:
            print(f"⚠️ No permission to fetch invites for guild {guild.id}")
            return

        used_code = None
        print(f"🔎 Cache has {len(old_cache)} invites: {list(old_cache.keys())}")
        print(f"🔎 Discord reports {len(current_invites)} invites: {[inv.code for inv in current_invites]}")

        # กรณีที่ 1: invite uses เพิ่มขึ้น (multi-use invite)
        for inv in current_invites:
            old_uses = old_cache.get(inv.code, 0)
            if inv.uses > old_uses:
                used_code = inv.code
                print(f"🔍 Detected invite used: {used_code} (was {old_uses}, now {inv.uses})")
                break

        # กรณีที่ 2: invite หายไปจาก list เพราะถูกใช้ครบ (single-use invite)
        if used_code is None:
            current_codes = {inv.code for inv in current_invites}
            for code in old_cache:
                if code not in current_codes:
                    # invite หายไป — เช็คว่าเป็น trial invite ไหม
                    try:
                        row = await get_invite(code)
                        if row and row["active"] and row["guild_id"] == str(guild.id):
                            used_code = code
                            print(f"🔍 Detected vanished invite (single-use): {used_code}")
                            break
                    except Exception:
                        pass

        # อัปเดต cache
        self.invite_cache[guild.id] = {inv.code: inv.uses for inv in current_invites}

        if used_code is None:
            print(f"ℹ️ No invite change detected for {member} — not a trial invite or cache miss")
            return

        # เช็คว่า invite นี้ผูกกับ trial หรือเปล่า
        try:
            row = await get_invite(used_code)
        except Exception as e:
            print(f"❌ get_invite failed for {used_code}: {e}")
            return

        if not row:
            print(f"ℹ️ Invite {used_code} not in trial_invites DB — skipping")
            return
        if not row["active"]:
            print(f"ℹ️ Invite {used_code} is inactive — skipping")
            return
        if row["guild_id"] != str(guild.id):
            print(f"ℹ️ Invite {used_code} belongs to different guild — skipping")
            return

        role = guild.get_role(int(row["role_id"]))
        if role is None:
            print(f"❌ Role {row['role_id']} not found in guild {guild.id}")
            return

        # ถ้า Membership Screening เปิดอยู่ → member.pending=True → รอให้กด Agree ก่อน
        if member.pending:
            self._pending_members.setdefault(guild.id, {})[member.id] = used_code
            print(f"⏳ Member {member} is pending (Membership Screening) — waiting for Agree")
            return

        print(f"🎯 Granting role {role.name} to {member} via invite {used_code}")

        # ให้ Role
        try:
            await member.add_roles(role, reason=f"Trial invite {used_code} ({row['days']} วัน)")
        except Exception as e:
            print(f"❌ add_roles failed for {member}: {e}")
            await _notify_admin(guild, f"❌ **ให้ Role ไม่สำเร็จ:** {member.mention} — {e}\n"
                                       f"ตรวจสอบว่า Role ของบอทอยู่ **เหนือ** Trial Role ครับ")
            return

        # บันทึก trial ลง DB
        try:
            await add_trial(
                guild_id=str(guild.id),
                discord_id=str(member.id),
                role_id=str(role.id),
                days=row["days"],
                source="invite",
                code=used_code,
            )
            await use_invite(used_code)
        except Exception as e:
            print(f"❌ Failed to save trial record for {member}: {e}")
            await _notify_admin(guild, f"⚠️ **บันทึก Trial ไม่สำเร็จ:** {member.mention} — ได้รับ Role แล้วแต่บันทึก DB ล้มเหลว: {e}")
            return

        expires = datetime.now(timezone.utc) + timedelta(days=row["days"])
        try:
            await member.send(
                f"🎉 ยินดีต้อนรับสู่ **{guild.name}**!\n\n"
                f"คุณได้รับสิทธิ์ทดลอง Role **{role.name}** เป็นเวลา **{row['days']} วัน**\n"
                f"📅 หมดอายุ: {expires.strftime('%d/%m/%Y %H:%M UTC')}\n\n"
                "บอทจะแจ้งเตือนคุณ 3 วันและ 1 วันก่อนหมดอายุครับ 👍"
            )
        except discord.Forbidden:
            pass

        await _notify_admin(guild, f"✅ **Trial ใหม่:** {member.mention} — Role **{role.name}** | {row['days']} วัน (invite `{used_code}`)")
        print(f"✅ Trial granted to {member} via invite {used_code} ({row['days']} วัน)")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """จัดการ Membership Screening — ให้ Role เมื่อสมาชิกกด Agree Rules"""
        if not (before.pending and not after.pending):
            return

        guild = after.guild
        pending = self._pending_members.get(guild.id, {})
        used_code = pending.pop(after.id, None)
        if used_code is None:
            return

        try:
            row = await get_invite(used_code)
        except Exception as e:
            print(f"❌ get_invite failed for pending member {after}: {e}")
            return

        if not row or not row["active"] or row["guild_id"] != str(guild.id):
            return

        role = guild.get_role(int(row["role_id"]))
        if role is None:
            return

        print(f"🎯 Membership Screening passed — granting {role.name} to {after}")
        try:
            await after.add_roles(role, reason=f"Trial invite {used_code} (ผ่าน Rules Screening)")
        except Exception as e:
            print(f"❌ add_roles failed (post-screening) for {after}: {e}")
            await _notify_admin(guild, f"❌ **ให้ Role ไม่สำเร็จ:** {after.mention} — {e}")
            return

        try:
            await add_trial(
                guild_id=str(guild.id),
                discord_id=str(after.id),
                role_id=str(role.id),
                days=row["days"],
                source="invite",
                code=used_code,
            )
            await use_invite(used_code)
        except Exception as e:
            print(f"❌ Failed to save trial record (post-screening) for {after}: {e}")

        expires = datetime.now(timezone.utc) + timedelta(days=row["days"])
        try:
            await after.send(
                f"🎉 ยินดีต้อนรับสู่ **{guild.name}**!\n\n"
                f"คุณได้รับสิทธิ์ทดลอง Role **{role.name}** เป็นเวลา **{row['days']} วัน**\n"
                f"📅 หมดอายุ: {expires.strftime('%d/%m/%Y %H:%M UTC')}\n\n"
                "บอทจะแจ้งเตือนคุณ 3 วันและ 1 วันก่อนหมดอายุครับ 👍"
            )
        except discord.Forbidden:
            pass

        await _notify_admin(guild, f"✅ **Trial ใหม่:** {after.mention} — Role **{role.name}** | {row['days']} วัน (invite `{used_code}`)")

    # ──────────────────────────────────────────
    # Admin: gen_invite
    # ──────────────────────────────────────────

    @commands.command(name="gen_invite")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def gen_invite_cmd(
        self,
        ctx: commands.Context,
        role: discord.Role = None,
        days: int = 30,
        max_uses: int = 1,
        channel: discord.TextChannel = None,
    ):
        """
        สร้าง Discord invite link สำหรับ Trial
        ตัวอย่าง: \\gen_invite @Trial 30        (ใช้ได้ 1 ครั้ง)
                  \\gen_invite @Trial 30 5       (ใช้ได้ 5 ครั้ง)
                  \\gen_invite @Trial 30 1 #welcome  (สร้างใน channel นั้น)
        """
        # หา role default จาก guild config ถ้าไม่ระบุ
        if role is None:
            gc = await get_guild_config(str(ctx.guild.id))
            default_role_id = gc.get("trial_role_id")
            if default_role_id:
                role = ctx.guild.get_role(int(default_role_id))
            if role is None:
                await ctx.reply(
                    "❌ ต้องระบุ @Role ครับ หรือตั้งค่า default ด้วย `\\set_config trial_role_id <role_id>`"
                )
                return

        if days < 1 or days > 365:
            await ctx.reply("❌ days ต้องอยู่ระหว่าง 1-365 ครับ")
            return

        if max_uses < 1 or max_uses > 100:
            await ctx.reply("❌ max_uses ต้องอยู่ระหว่าง 1-100 ครับ")
            return

        # ใช้ channel ปัจจุบันถ้าไม่ได้ระบุ
        target_channel = channel or ctx.channel

        try:
            invite = await target_channel.create_invite(
                max_uses=max_uses,
                unique=True,
                reason=f"Trial invite by {ctx.author} — {role.name} {days}d",
            )
        except discord.Forbidden:
            await ctx.reply("❌ บอทไม่มีสิทธิ์สร้าง Invite ใน channel นั้นครับ")
            return
        except Exception as e:
            await ctx.reply(f"❌ สร้าง Invite ไม่สำเร็จ: {e}")
            return

        # บันทึกลง DB
        await add_invite(
            invite_code=invite.code,
            guild_id=str(ctx.guild.id),
            channel_id=str(target_channel.id),
            role_id=str(role.id),
            days=days,
            max_uses=max_uses,
            created_by=str(ctx.author.id),
        )

        # อัปเดต cache ทันที
        self.invite_cache.setdefault(ctx.guild.id, {})[invite.code] = 0

        try:
            await ctx.message.delete()
        except Exception:
            pass

        uses_text = f"{max_uses} ครั้ง" if max_uses > 1 else "1 ครั้ง (Single-use)"
        await ctx.author.send(
            f"🔗 **Invite Link พร้อมแล้ว!**\n\n"
            f"```\n{invite.url}\n```\n"
            f"- Role: **{role.name}**\n"
            f"- ระยะเวลา: **{days} วัน**\n"
            f"- ใช้ได้: **{uses_text}**\n\n"
            f"**วิธีใช้:** ส่งลิงก์นี้ให้ลูกค้า — เมื่อกดเข้า Server จะได้รับ Role อัตโนมัติเลยครับ"
        )
        await ctx.reply("✅ ส่ง Invite Link ทาง DM ให้คุณแล้วครับ", delete_after=5)

    # ──────────────────────────────────────────
    # Admin: list_invites
    # ──────────────────────────────────────────

    @commands.command(name="list_invites")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def list_invites_cmd(self, ctx: commands.Context):
        """แสดง invite links ที่ active ทั้งหมด"""
        invites = await get_active_invites(str(ctx.guild.id))

        if not invites:
            await ctx.reply("📋 ไม่มี invite link ที่ active ขณะนี้ครับ")
            return

        lines = [f"**🔗 Invite Links ที่ active ({len(invites)} รายการ)**\n"]
        for inv in invites:
            role = ctx.guild.get_role(int(inv["role_id"]))
            role_name = role.name if role else f"ID:{inv['role_id']}"
            lines.append(
                f"- `discord.gg/{inv['invite_code']}` | {role_name} | "
                f"{inv['days']} วัน | ใช้ไป {inv['uses']}/{inv['max_uses']}"
            )

        await ctx.reply("\n".join(lines))

    # ──────────────────────────────────────────
    # Admin: disable_invite
    # ──────────────────────────────────────────

    @commands.command(name="disable_invite")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def disable_invite_cmd(self, ctx: commands.Context, invite_code: str):
        """ปิดการใช้งาน invite link (ใส่แค่ code ไม่ต้องใส่ discord.gg/)"""
        invite_code = invite_code.replace("discord.gg/", "").strip()
        row = await get_invite(invite_code)
        if not row or row["guild_id"] != str(ctx.guild.id):
            await ctx.reply(f"❌ ไม่พบ invite `{invite_code}` ในระบบครับ")
            return

        await deactivate_invite(invite_code)

        # ลบ invite จาก Discord ด้วย
        try:
            guild_invites = await ctx.guild.invites()
            disc_inv = discord.utils.get(guild_invites, code=invite_code)
            if disc_inv:
                await disc_inv.delete(reason=f"Disabled by {ctx.author}")
        except Exception:
            pass

        # ลบออกจาก cache
        if ctx.guild.id in self.invite_cache:
            self.invite_cache[ctx.guild.id].pop(invite_code, None)

        await ctx.reply(f"✅ ปิด invite `discord.gg/{invite_code}` แล้วครับ")

    # ──────────────────────────────────────────
    # Admin: force_trial_check (ทดสอบ)
    # ──────────────────────────────────────────

    @commands.command(name="force_trial_check")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def force_trial_check_cmd(self, ctx: commands.Context):
        """รัน trial expiry check ทันที (สำหรับทดสอบ)"""
        msg = await ctx.reply("⏳ กำลังรัน trial check...")
        await self.trial_expiry_check()
        await msg.edit(content="✅ Trial check เสร็จแล้วครับ — ดู log สำหรับรายละเอียด")


async def setup(bot):
    await bot.add_cog(TrialsCog(bot))

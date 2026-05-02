import re
import csv
import io
import aiohttp
import discord
from discord.ext import commands
from discord import ui
from datetime import datetime, timezone, timedelta

from db import (
    set_guild_config_field,
    reset_guild_config,
    get_raw_guild_row,
    get_guild_config,
    get_guild_features,
    set_guild_feature,
    get_active_trials,
    add_trial,
    VALID_KEYS,
)


# ──────────────────────────────────────────
# Modals
# ──────────────────────────────────────────

class TrialRoleModal(ui.Modal, title="🎭 ตั้งค่า Default Trial Role"):
    role_id = ui.TextInput(
        label="Role ID (เลข ID ของ Role เท่านั้น)",
        placeholder="123456789012345678",
        required=True,
        style=discord.TextStyle.short,
        max_length=25,
    )

    async def on_submit(self, interaction: discord.Interaction):
        val = self.role_id.value.strip()
        if not val.isdigit():
            await interaction.response.send_message(
                "❌ Role ID ต้องเป็นตัวเลขเท่านั้นครับ (คลิกขวาที่ Role → Copy ID)",
                ephemeral=True,
            )
            return
        await set_guild_config_field(str(interaction.guild_id), "trial_role_id", val)
        role = interaction.guild.get_role(int(val))
        role_name = role.name if role else f"ID:{val}"
        await interaction.response.send_message(
            f"✅ ตั้ง Default Trial Role เป็น **{role_name}** แล้วครับ",
            ephemeral=True,
        )


class GenInviteModal(ui.Modal, title="🔗 สร้าง Trial Invite Link"):
    role_id_input = ui.TextInput(
        label="Role ID (เว้นว่างเพื่อใช้ Default Trial Role)",
        placeholder="123456789012345678",
        required=False,
        style=discord.TextStyle.short,
        max_length=25,
    )
    days_input = ui.TextInput(
        label="จำนวนวัน",
        placeholder="30",
        required=True,
        style=discord.TextStyle.short,
        max_length=5,
    )
    max_uses_input = ui.TextInput(
        label="จำนวนครั้งที่ใช้ได้ (default: 1)",
        placeholder="1",
        required=False,
        style=discord.TextStyle.short,
        max_length=5,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)

        days_str = self.days_input.value.strip()
        if not days_str.isdigit() or int(days_str) <= 0:
            await interaction.response.send_message("❌ จำนวนวันต้องเป็นตัวเลขบวกครับ", ephemeral=True)
            return
        days = int(days_str)

        max_uses_str = self.max_uses_input.value.strip() or "1"
        if not max_uses_str.isdigit() or int(max_uses_str) <= 0:
            await interaction.response.send_message("❌ จำนวนครั้งต้องเป็นตัวเลขบวกครับ", ephemeral=True)
            return
        max_uses = int(max_uses_str)

        role_str = self.role_id_input.value.strip()
        if role_str:
            if not role_str.isdigit():
                await interaction.response.send_message("❌ Role ID ต้องเป็นตัวเลขเท่านั้นครับ", ephemeral=True)
                return
            role_id = role_str
        else:
            cfg = await get_guild_config(guild_id)
            role_id = cfg.get("trial_role_id")
            if not role_id:
                await interaction.response.send_message(
                    "❌ ยังไม่ได้ตั้ง Default Trial Role ครับ\n"
                    "กรุณากรอก Role ID หรือกด **🎭 Trial Role** เพื่อตั้งค่าก่อนครับ",
                    ephemeral=True,
                )
                return

        role = interaction.guild.get_role(int(role_id))
        if role is None:
            await interaction.response.send_message("❌ ไม่พบ Role นี้ใน Server ครับ", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            invite = await interaction.channel.create_invite(
                max_uses=max_uses,
                unique=True,
                reason=f"Trial invite by {interaction.user} — {role.name} {days}d",
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ บอทไม่มีสิทธิ์สร้าง Invite ในห้องนี้ครับ", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ สร้าง Invite ไม่สำเร็จ: {e}", ephemeral=True)
            return

        from db import add_invite as db_add_invite
        await db_add_invite(
            invite_code=invite.code,
            guild_id=guild_id,
            channel_id=str(interaction.channel_id),
            role_id=role_id,
            days=days,
            max_uses=max_uses,
            created_by=str(interaction.user.id),
        )

        uses_text = f"{max_uses} ครั้ง" if max_uses > 1 else "1 ครั้ง (Single-use)"
        await interaction.followup.send(
            f"🔗 **Invite Link พร้อมแล้ว!**\n\n"
            f"```\n{invite.url}\n```\n"
            f"Role: **{role.name}** | {days} วัน | ใช้ได้ {uses_text}\n\n"
            f"ส่งลิงก์นี้ให้ลูกค้า — เมื่อกดเข้า Server จะได้รับ Role อัตโนมัติเลยครับ",
            ephemeral=True,
        )


# ──────────────────────────────────────────
# Import Registration Helpers
# ──────────────────────────────────────────

async def _fetch_rows_from_url(url: str, tab: str, guild_config: dict) -> list | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text()
        reader = csv.reader(io.StringIO(text))
        return list(reader)
    except Exception as e:
        print(f"⚠️ CSV fetch error: {e}")
        return None


async def _run_import(rows: list, guild: discord.Guild, guild_config: dict, default_days: int) -> tuple[int, int, int]:
    if not rows or len(rows) < 2:
        return 0, 0, 0

    header = [h.strip().lower() for h in rows[0]]

    def col(name):
        return header.index(name) if name in header else None

    col_did = col("discord_id")
    col_days = col("days")
    col_exp = col("expiry_date")
    col_role_id = col("role_id")
    col_role_name = col("role_name")

    if col_did is None:
        return 0, 0, -1

    default_role_id = guild_config.get("trial_role_id")
    success, skipped, errors = 0, 0, 0

    for i, row in enumerate(rows[1:], start=2):
        if not row or len(row) <= col_did:
            skipped += 1
            continue

        discord_id = str(row[col_did]).strip()
        if not discord_id or not discord_id.isdigit():
            skipped += 1
            continue

        role_id = None
        if col_role_id is not None and len(row) > col_role_id:
            role_id = str(row[col_role_id]).strip() or None
        if role_id is None and col_role_name is not None and len(row) > col_role_name:
            rname = str(row[col_role_name]).strip()
            found = discord.utils.get(guild.roles, name=rname)
            if found:
                role_id = str(found.id)
        if role_id is None:
            role_id = default_role_id
        if role_id is None:
            errors += 1
            continue

        expires_at = None
        days_val = default_days

        if col_days is not None and len(row) > col_days:
            try:
                days_val = int(str(row[col_days]).strip())
            except Exception:
                pass

        if col_exp is not None and len(row) > col_exp:
            raw_exp = str(row[col_exp]).strip()
            if raw_exp:
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                    try:
                        dt = datetime.strptime(raw_exp, fmt).replace(tzinfo=timezone.utc)
                        expires_at = dt.isoformat()
                        days_val = max(1, (dt - datetime.now(timezone.utc)).days)
                        break
                    except ValueError:
                        continue

        try:
            await add_trial(
                guild_id=str(guild.id),
                discord_id=discord_id,
                role_id=role_id,
                days=days_val,
                source="import",
                expires_at=expires_at,
            )
            member = guild.get_member(int(discord_id))
            if member:
                role_obj = guild.get_role(int(role_id))
                if role_obj and role_obj not in member.roles:
                    await member.add_roles(role_obj, reason="Trial import from panel")
            success += 1
        except Exception as e:
            errors += 1
            print(f"⚠️ Import row {i} error: {e}")

    return success, skipped, errors


class ImportRegistrationModal(ui.Modal, title="📥 Import ไฟล์ลงทะเบียน"):
    url_input = ui.TextInput(
        label="URL ไฟล์ CSV (public URL)",
        placeholder="https://example.com/members.csv",
        required=True,
        style=discord.TextStyle.short,
        max_length=500,
    )
    days_input = ui.TextInput(
        label="จำนวนวัน default (ถ้าไฟล์ไม่มีคอลัมน์ days)",
        placeholder="30",
        required=False,
        style=discord.TextStyle.short,
        max_length=5,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        days_str = self.days_input.value.strip()
        default_days = int(days_str) if days_str.isdigit() and int(days_str) > 0 else 30

        guild_id = str(interaction.guild_id)
        gc = await get_guild_config(guild_id)

        rows = await _fetch_rows_from_url(self.url_input.value.strip(), "", gc)

        if rows is None:
            await interaction.followup.send(
                "❌ ไม่สามารถอ่านไฟล์จาก URL นี้ได้ครับ\n"
                "ตรวจสอบว่า CSV URL เป็น public และ download ได้ตรงๆ",
                ephemeral=True,
            )
            return

        success, skipped, errors = await _run_import(rows, interaction.guild, gc, default_days)

        if errors == -1:
            await interaction.followup.send(
                "❌ ไม่พบคอลัมน์ `discord_id` ใน Header ของไฟล์ครับ\n\n"
                "**รูปแบบที่รองรับ:**\n"
                "```\ndiscord_id, days, role_id\n123456789012345678, 30,\n```",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ **Import เสร็จสิ้นครับ**\n\n"
            f"สำเร็จ: **{success}** คน\n"
            f"ข้าม: **{skipped}** แถว\n"
            f"Error: **{errors}** แถว",
            ephemeral=True,
        )


# ──────────────────────────────────────────
# Feature Toggle View
# ──────────────────────────────────────────

_FEATURE_LABELS = {
    "welcome_msg": "👋 Welcome Message",
    "translation": "🌐 แปลภาษา",
    "auto_kick":   "🦵 Auto Kick เมื่อ Trial หมด",
}


class FeatureToggleView(ui.View):
    def __init__(self, features: dict, guild_id: str):
        super().__init__(timeout=60)
        self.features = features
        self.guild_id = guild_id
        self._build()

    def _build(self):
        self.clear_items()
        for key, label in _FEATURE_LABELS.items():
            enabled = bool(self.features.get(key, 1))
            btn = discord.ui.Button(
                label=f"{label}  {'✅ เปิด' if enabled else '❌ ปิด'}",
                style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.danger,
                custom_id=f"feat_{key}_{self.guild_id}",
                row=list(_FEATURE_LABELS).index(key),
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    def _make_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ เฉพาะ Admin เท่านั้นครับ", ephemeral=True)
                return
            current = bool(self.features.get(key, 1))
            new_val = 0 if current else 1
            await set_guild_feature(self.guild_id, key, new_val)
            self.features[key] = new_val
            self._build()
            status = "✅ เปิด" if new_val else "❌ ปิด"
            await interaction.response.edit_message(
                content=f"**🔧 Feature Toggles** — {_FEATURE_LABELS[key]}: {status}\n\nกดปุ่มเพื่อเปิด/ปิดแต่ละฟีเจอร์ครับ",
                view=self,
            )
        return callback


# ──────────────────────────────────────────
# Confirm Reset View
# ──────────────────────────────────────────

class ResetConfirmView(ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    @ui.button(label="✅ ยืนยัน Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        await reset_guild_config(str(interaction.guild_id))
        await interaction.response.edit_message(
            content="✅ รีเซ็ต Config ของ Server นี้แล้วครับ",
            view=None,
        )

    @ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="↩️ ยกเลิกการ Reset แล้วครับ", view=None)


# ──────────────────────────────────────────
# Main Setup Panel View (persistent)
# ──────────────────────────────────────────

class SetupPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ── Row 0: Trial System ──

    @ui.button(label="🎭 Trial Role", style=discord.ButtonStyle.success,
               custom_id="setup_trial_role", row=0)
    async def btn_trial_role(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ เฉพาะ Admin เท่านั้นครับ", ephemeral=True)
            return
        await interaction.response.send_modal(TrialRoleModal())

    @ui.button(label="🔗 สร้าง Trial Invite", style=discord.ButtonStyle.success,
               custom_id="setup_gen_invite", row=0)
    async def btn_gen_invite(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ เฉพาะ Admin เท่านั้นครับ", ephemeral=True)
            return
        await interaction.response.send_modal(GenInviteModal())

    # ── Row 1: Info ──

    @ui.button(label="📋 Active Trials", style=discord.ButtonStyle.secondary,
               custom_id="setup_list_trials", row=1)
    async def btn_list_trials(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ เฉพาะ Admin เท่านั้นครับ", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        guild_trials = await get_active_trials(guild_id)
        if not guild_trials:
            await interaction.followup.send("📋 ยังไม่มี Trial Member ที่กำลังใช้งานอยู่ครับ", ephemeral=True)
            return
        now = datetime.now(timezone.utc)
        lines = []
        for t in guild_trials[:15]:
            member = interaction.guild.get_member(int(t["discord_id"]))
            name = member.display_name if member else f"ID:{t['discord_id']}"
            try:
                exp = datetime.fromisoformat(t["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                secs = (exp - now).total_seconds()
                d = int(secs // 86400)
                time_str = f"{d}d" if secs > 0 else "EXPIRED"
            except Exception:
                time_str = "?"
            lines.append(f"• **{name}** — {time_str} remaining")
        total = len(guild_trials)
        header = f"**📋 Active Trials ({total} คน)**\n"
        if total > 15:
            header += f"*(แสดง 15 คนแรก)*\n"
        await interaction.followup.send(header + "\n" + "\n".join(lines), ephemeral=True)

    @ui.button(label="📥 Import ลงทะเบียน", style=discord.ButtonStyle.secondary,
               custom_id="setup_import_reg", row=1)
    async def btn_import_reg(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ เฉพาะ Admin เท่านั้นครับ", ephemeral=True)
            return
        await interaction.response.send_modal(ImportRegistrationModal())

    # ── Row 2: Management ──

    @ui.button(label="🔧 เปิด/ปิดฟีเจอร์", style=discord.ButtonStyle.secondary,
               custom_id="setup_features", row=2)
    async def btn_features(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ เฉพาะ Admin เท่านั้นครับ", ephemeral=True)
            return
        guild_id = str(interaction.guild_id)
        features = await get_guild_features(guild_id)
        view = FeatureToggleView(features, guild_id)
        await interaction.response.send_message(
            "**🔧 Feature Toggles**\n\nกดปุ่มเพื่อเปิด/ปิดแต่ละฟีเจอร์ครับ",
            view=view,
            ephemeral=True,
        )

    @ui.button(label="🔄 Reset Config", style=discord.ButtonStyle.danger,
               custom_id="setup_reset_config", row=2)
    async def btn_reset_config(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ เฉพาะ Admin เท่านั้นครับ", ephemeral=True)
            return
        await interaction.response.send_message(
            "⚠️ **ยืนยันการ Reset Config?**\nจะลบการตั้งค่าทั้งหมดของ Server นี้ออก",
            view=ResetConfirmView(),
            ephemeral=True,
        )

    @ui.button(label="❓ วิธีใช้งาน", style=discord.ButtonStyle.secondary,
               custom_id="setup_help", row=2)
    async def btn_help(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "**📖 วิธีใช้งาน Setup Panel**\n\n"
            "**Row 1 — Trial System (เขียว)**\n"
            "• **🎭 Trial Role** — ตั้ง Role default สำหรับสมาชิกทดลอง\n"
            "• **🔗 Trial Invite** — สร้าง invite link ที่ให้ Role อัตโนมัติเมื่อเข้า Server\n\n"
            "**Row 2 — ข้อมูล (เทา)**\n"
            "• **📋 Active Trials** — ดูรายชื่อสมาชิกทดลองที่ใช้งานอยู่\n"
            "• **📥 Import** — Import จาก CSV URL\n\n"
            "**Row 3 — จัดการ (เทา)**\n"
            "• **🔧 เปิด/ปิดฟีเจอร์** — Toggle Welcome, Translation, Auto Kick\n"
            "• **🔄 Reset Config** — รีเซ็ตกลับค่า default\n\n"
            "💡 **ลำดับแนะนำ:**\n"
            "1️⃣ 🎭 Trial Role → 2️⃣ 🔗 Trial Invite → 3️⃣ 🔧 เปิด Auto Kick",
            ephemeral=True,
        )


# ──────────────────────────────────────────
# Cog
# ──────────────────────────────────────────

class SetupPanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        channel = guild.system_channel
        if not channel or not channel.permissions_for(guild.me).send_messages:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break
        if not channel:
            return

        embed = discord.Embed(
            title="👋 สวัสดีครับ! ขอบคุณที่เชิญบอทเข้ามา",
            description=(
                "บอทพร้อมใช้งานแล้วครับ! กดปุ่มด้านล่างเพื่อเริ่มตั้งค่าได้เลย\n\n"
                "**📌 เริ่มต้นง่ายๆ 3 ขั้น:**\n"
                "1️⃣ 🎭 **Trial Role** — เลือก Role สำหรับสมาชิกทดลอง\n"
                "2️⃣ 🔗 **Trial Invite** — ได้ลิงก์ที่ให้ Role อัตโนมัติเมื่อเข้า Server\n"
                "3️⃣ 🔧 **เปิด Auto Kick** — เตะอัตโนมัติเมื่อหมดอายุ"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="เฉพาะ Admin เท่านั้นที่ใช้ปุ่มเหล่านี้ได้ครับ")
        try:
            await channel.send(embed=embed, view=SetupPanelView())
        except Exception as e:
            print(f"⚠️ on_guild_join: ส่ง setup panel ไม่ได้: {e}")

    @commands.command(name="setup_panel")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def setup_panel_cmd(self, ctx: commands.Context):
        """ส่ง Setup Panel ในห้องนี้  \\setup_panel"""
        embed = discord.Embed(
            title="⚙️ Bot Setup Panel",
            description=(
                "กดปุ่มด้านล่างเพื่อตั้งค่าบอทสำหรับ Server นี้ครับ\n\n"
                "**📌 ลำดับแนะนำ:**\n"
                "1️⃣ 🎭 **Trial Role** → 2️⃣ 🔗 **Trial Invite** → 3️⃣ 🔧 **เปิด Auto Kick**"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="เฉพาะ Admin เท่านั้นที่ใช้ปุ่มเหล่านี้ได้ครับ")
        await ctx.send(embed=embed, view=SetupPanelView())
        try:
            await ctx.message.delete()
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(SetupPanelCog(bot))

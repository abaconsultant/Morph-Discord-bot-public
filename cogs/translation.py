import discord
from discord.ext import commands
from discord import app_commands
from deep_translator import GoogleTranslator
from langdetect import detect
from db import is_feature_enabled


class TranslationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.translation_enabled = True

        # Register context menus to the tree
        self.ctx_menu_th = app_commands.ContextMenu(
            name="🌐 แปลเป็นภาษาไทย",
            callback=self._translate_to_thai,
        )
        self.ctx_menu_en = app_commands.ContextMenu(
            name="🌐 Translate to English",
            callback=self._translate_to_english,
        )
        self.bot.tree.add_command(self.ctx_menu_th)
        self.bot.tree.add_command(self.ctx_menu_en)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.ctx_menu_th.name, type=self.ctx_menu_th.type)
        self.bot.tree.remove_command(self.ctx_menu_en.name, type=self.ctx_menu_en.type)

    # ──────────────────────────────────────────
    # Core translation logic
    # ──────────────────────────────────────────

    async def _do_translate(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
        target_lang: str,          # "th" or "en"
        target_label: str,         # "ไทย" or "English"
    ):
        guild_id = str(interaction.guild_id) if interaction.guild_id else None
        if guild_id and not await is_feature_enabled(guild_id, "translation"):
            await interaction.response.send_message(
                "🔴 ระบบแปลภาษาถูกปิดอยู่ใน Server นี้ครับ", ephemeral=True
            )
            return

        text = message.content.strip()
        if not text:
            await interaction.response.send_message(
                "❌ ข้อความนี้ไม่มีตัวอักษรให้แปลครับ", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            detected = detect(text[:200])
        except Exception:
            detected = "?"

        # ถ้าภาษาต้นทางเหมือนกับเป้าหมายก็ไม่ต้องแปล
        if detected.lower() == target_lang.lower():
            await interaction.followup.send(
                f"ℹ️ ข้อความนี้เป็นภาษา{target_label}อยู่แล้วครับ", ephemeral=True
            )
            return

        try:
            translated = GoogleTranslator(source="auto", target=target_lang).translate(text[:4900])
        except Exception as e:
            await interaction.followup.send(f"❌ แปลไม่สำเร็จครับ: {e}", ephemeral=True)
            return

        if not translated or translated.lower() == text.lower():
            await interaction.followup.send("❌ ไม่สามารถแปลข้อความนี้ได้ครับ", ephemeral=True)
            return

        embed = discord.Embed(
            description=translated,
            color=discord.Color.blue(),
        )
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
        )
        embed.set_footer(text=f"แปลเป็น{target_label} • เห็นเฉพาะคุณ")
        embed.add_field(name="ต้นฉบับ", value=f"[คลิกดูที่นี่]({message.jump_url})", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ──────────────────────────────────────────
    # Context Menu callbacks
    # ──────────────────────────────────────────

    async def _translate_to_thai(self, interaction: discord.Interaction, message: discord.Message):
        await self._do_translate(interaction, message, "th", "ไทย")

    async def _translate_to_english(self, interaction: discord.Interaction, message: discord.Message):
        await self._do_translate(interaction, message, "en", "English")

    # ──────────────────────────────────────────
    # Admin toggle command
    # ──────────────────────────────────────────

    @commands.command(name="toggle_trans")
    @commands.has_permissions(administrator=True)
    async def toggle_translation_cmd(self, ctx: commands.Context):
        """เปิด/ปิด ระบบแปลภาษา (เฉพาะ Admin)"""
        self.translation_enabled = not self.translation_enabled
        status = "🟢 เปิด (ON)" if self.translation_enabled else "🔴 ปิด (OFF)"
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.send(f"ระบบแปลภาษา: {status}", delete_after=5)


async def setup(bot: commands.Bot):
    await bot.add_cog(TranslationCog(bot))

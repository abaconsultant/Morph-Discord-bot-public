import discord
from discord.ext import commands
from discord import ui, ButtonStyle, Interaction
from db import is_feature_enabled


class AssistantView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🗺️ แผนผัง Server", style=ButtonStyle.blurple, custom_id="ast_map")
    async def btn_map(self, interaction: Interaction, button: ui.Button):
        msg = (
            "🗺️ **แผนผังการใช้งาน Server**\n\n"
            "💬 **โซนพูดคุย:**\n"
            "- `#general` : พูดคุยแลกเปลี่ยนได้ทุกเรื่อง\n\n"
            "❓ **โซนช่วยเหลือ:**\n"
            "- `#ticket` (ถ้ามี) : กดเปิด Ticket พูดคุยกับแอดมินโดยตรง\n\n"
            "💡 *Tip: ถ้าติดปัญหาอะไรให้กดปุ่ม ❓ ติดต่อ Admin ได้เลยครับ!*"
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @ui.button(label="❓ ติดต่อ Admin", style=ButtonStyle.red, custom_id="ast_contact")
    async def btn_contact(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message(
            "📞 หากมีปัญหา สามารถพิมพ์แจ้งในห้อง Ticket ให้แอดมินดูแลเป็นการส่วนตัวได้เลยครับ!",
            ephemeral=True
        )


class OnboardingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="setup_assistant")
    @commands.has_permissions(administrator=True)
    async def setup_assistant_cmd(self, ctx: commands.Context):
        """สร้าง Dashboard The Assistant ให้สมาชิกกดใช้งาน"""
        view = AssistantView()
        embed = discord.Embed(
            title="🤖 The Assistant (ผู้ช่วยส่วนตัว)",
            description="สวัสดีครับ! ยินดีต้อนรับสู่ Server\nหากคุณเพิ่งเข้ามาใหม่ หรือไม่แน่ใจว่าต้องเริ่มตรงไหน ให้ผมช่วยนำทางให้ครับ\n\nกดปุ่มด้านล่างเพื่อเริ่มต้นใช้งานได้เลย 👇",
            color=0x2b2d31
        )
        await ctx.send(embed=embed, view=view)
        await ctx.message.delete()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if not hasattr(member, 'guild'):
            return
        if not await is_feature_enabled(str(member.guild.id), "welcome_msg"):
            return

        view = AssistantView()
        embed = discord.Embed(
            title=f"🤖 ยินดีต้อนรับคุณ {member.name} เข้าสู่ Server!",
            description="สวัสดีครับ! กดปุ่มด้านล่างเพื่อเริ่มต้นการใช้งานได้เลยครับ\n*(แชทจะเป็นส่วนตัวเฉพาะคุณเท่านั้น)*",
            color=0x2b2d31
        )

        channel = discord.utils.get(member.guild.text_channels, name="the-assistant")
        if not channel:
            channel = discord.utils.get(member.guild.text_channels, name="welcome")
        if not channel:
            channel = discord.utils.get(member.guild.text_channels, name="general")

        if channel:
            try:
                await channel.send(
                    content=f"🎉 ยินดีต้อนรับ {member.mention}!",
                    embed=embed,
                    view=view
                )
            except Exception as e:
                print(f"Error sending welcome message: {e}")


async def setup(bot):
    await bot.add_cog(OnboardingCog(bot))

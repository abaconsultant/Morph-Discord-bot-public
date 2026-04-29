import asyncio
import discord
from discord.ext import commands
from discord import ui, ButtonStyle, Interaction

from db import get_guild_config


async def start_auto_close_ticket(channel: discord.TextChannel):
    try:
        countdown_msg = await channel.send("⏳ ห้องนี้จะถูกปิดอัตโนมัติใน **2 นาที** ครับ")
        await asyncio.sleep(60)
        try:
            await countdown_msg.edit(content="⏳ ห้องนี้จะถูกปิดอัตโนมัติใน **1 นาที** ครับ")
        except discord.HTTPException:
            pass
        await asyncio.sleep(60)
        await channel.delete(reason="ปิด Ticket อัตโนมัติหลังตรวจสอบสิทธิ์เรียบร้อย")
    except discord.Forbidden:
        print(f"❌ ไม่มีสิทธิ์ลบห้อง {channel.name}")
    except discord.HTTPException as e:
        print(f"⚠️ ลบห้อง {channel.name} ไม่สำเร็จ: {e}")


class WhopTicketView(ui.View):
    def __init__(self, timeout: float | None = None):
        super().__init__(timeout=timeout)

    @ui.button(label="✅ ตรวจสอบสิทธิ์ Whop", style=ButtonStyle.green, custom_id="check_whop_status")
    async def check_whop_status_btn(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        channel = interaction.channel

        gc = await get_guild_config(str(interaction.guild_id))
        is_owner = await interaction.client.is_owner(user)
        if not gc.get("whop_api_key") and not is_owner:
            await interaction.followup.send(
                "⚠️ Server นี้ยังไม่ได้ตั้งค่า Whop API ครับ\nติดต่อ Admin เพื่อตั้งค่าก่อนใช้งาน",
                ephemeral=True,
            )
            return
        try:
            from cogs.whop import check_whop_membership
        except ImportError:
            await interaction.followup.send("❌ ฟีเจอร์นี้ไม่ได้เปิดใช้งานในบอทนี้ครับ", ephemeral=True)
            return
        result = await check_whop_membership(str(user.id), guild_config=gc)

        if not result["has_access"]:
            reason = result.get("reason", "unknown")
            join_link = gc.get("join_link", "")

            _reason_map = {
                "no_membership": (
                    "**ไม่พบ Membership ของคุณในระบบ Whop**\n\n"
                    "อาจเกิดจาก:\n"
                    "• คุณยังไม่ได้ซื้อ Membership\n"
                    "• Discord account ที่ใช้ไม่ตรงกับที่ผูกไว้ใน Whop\n\n"
                    "**วิธีแก้ไข:**\n"
                    "1. เข้า Whop แล้วไปที่ Settings → Connections → เชื่อม Discord ใหม่\n"
                    "2. ตรวจสอบว่า login Discord ถูก account"
                ),
                "expired": (
                    "**Membership ของคุณหมดอายุแล้ว**\n\n"
                    "**วิธีแก้ไข:**\n"
                    "• ต่ออายุ Membership ผ่าน Whop แล้วกลับมาตรวจสอบอีกครั้ง"
                ),
                "invalid_status": (
                    "**สถานะ Membership ไม่ผ่านเงื่อนไข**\n\n"
                    "สถานะปัจจุบันของคุณยังไม่ได้รับอนุญาตให้เข้าถึงครับ\n\n"
                    "**วิธีแก้ไข:**\n"
                    "• ตรวจสอบสถานะใน Whop Dashboard\n"
                    "• ติดต่อ Admin ถ้าคิดว่าเป็นความผิดพลาด"
                ),
                "wrong_plan": (
                    "**แผน Membership ของคุณไม่ตรงกับแผนที่อนุญาต**\n\n"
                    "**วิธีแก้ไข:**\n"
                    "• ตรวจสอบว่าซื้อแผนที่ถูกต้อง\n"
                    "• ติดต่อ Admin เพื่อขอตรวจสอบ"
                ),
                "api_error": (
                    "**ระบบไม่สามารถเชื่อมต่อ Whop ได้ในขณะนี้**\n\n"
                    "**วิธีแก้ไข:**\n"
                    "• รอสักครู่แล้วลองใหม่\n"
                    "• ถ้ายังไม่ได้ แจ้ง Admin ครับ"
                ),
            }

            detail = _reason_map.get(reason, "**ไม่ผ่านการตรวจสอบสิทธิ์**\n\nติดต่อ Admin เพื่อขอความช่วยเหลือครับ")

            # แสดงลิงก์ซื้อถ้ามี (checkout_links คั่นด้วย newline หรือ comma)
            buy_line = ""
            if reason in ("no_membership", "expired", "wrong_plan"):
                checkout_raw = gc.get("checkout_links", "")
                if checkout_raw:
                    links = [l.strip() for l in checkout_raw.replace(",", "\n").splitlines() if l.strip()]
                    buy_line = "\n\n🛒 **ซื้อ Membership:**\n" + "\n".join(f"• {l}" for l in links)
                elif join_link:
                    buy_line = f"\n\n🛒 **ซื้อ Membership:** {join_link}"

            await interaction.followup.send(
                f"❌ {detail}{buy_line}",
                ephemeral=True,
            )
            return

        msg = (
            f"✅ พบสิทธิ์ของ {user.mention}\n"
            f"- แผน: `{result['plan_name']}`\n"
            f"- สถานะ: `{result['status']}`\n"
            f"- หมดอายุ: {result['countdown_text']}"
        )
        await interaction.followup.send(msg, ephemeral=True)

        if isinstance(channel, discord.TextChannel):
            await channel.send(msg)
            try:
                from cogs.sheets import log_to_sheet
                asyncio.create_task(asyncio.to_thread(
                    log_to_sheet,
                    str(user.id), str(user), "-",
                    result["plan_name"], result["status"], result["countdown_text"],
                    str(channel.guild.id), result["membership_id"], result["product_id"],
                    gc,
                ))
            except ImportError:
                pass

            join_link = gc.get("join_link", "")
            checkout_raw = gc.get("checkout_links", "")
            if join_link or checkout_raw:
                lines = [f"🎉 {user.mention} ยืนยันสิทธิ์สำเร็จแล้วครับ! นี่คือลิงก์สำหรับคุณ:"]
                if join_link:
                    lines.append(f"\n🔗 **Join Link:** {join_link}")
                if checkout_raw:
                    links = [l.strip() for l in checkout_raw.replace(",", "\n").splitlines() if l.strip()]
                    lines.append("\n🛒 **ลิงก์อื่นๆ:**\n" + "\n".join(f"• {l}" for l in links))
                await channel.send("\n".join(lines))
            else:
                await channel.send("⚠️ ยังไม่ได้ตั้งค่า join_link ในระบบครับ")

            await start_auto_close_ticket(channel)

    @ui.button(label="ℹ️ ระบบนี้ทำงานยังไง?", style=ButtonStyle.gray, custom_id="explain_system")
    async def explain_system_btn(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message(
            content=(
                "### ℹ️ วิธีการทำงาน\n"
                "1. กดปุ่ม '✅ ตรวจสอบสิทธิ์ Whop'\n"
                "2. ระบบจะเช็กสิทธิ์จาก Whop TH ด้วย Discord ID ของคุณ\n"
                "3. ถ้าผ่าน → ส่งลิงก์ ABA100X Global ให้ + บันทึกลงระบบ\n"
                "4. ห้อง Ticket นี้จะปิด/ลบตัวเองภายใน 2 นาทีหลังตรวจสอบเสร็จครับ"
            ),
            ephemeral=True,
        )


class CreateTicketView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🎫 เปิด Ticket เพื่อยืนยันสิทธิ์", style=ButtonStyle.primary, custom_id="create_ticket_btn")
    async def create_ticket_btn(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        # ค้นหาด้วย lowercase ให้ตรงกับชื่อที่จะสร้าง
        ticket_name = f"ticket-{user.name.lower()}"
        existing_channel = discord.utils.get(guild.text_channels, name=ticket_name)
        if existing_channel:
            await interaction.followup.send(
                f"❌ คุณมีห้อง Ticket เปิดอยู่แล้วที่ {existing_channel.mention} ครับ",
                ephemeral=True,
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        category = discord.utils.get(guild.categories, name="Tickets")

        try:
            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites,
                reason=f"ผู้ใช้ {user.name} เปิด Ticket ตรวจสอบสิทธิ์ Whop",
            )
            await interaction.followup.send(
                f"✅ เปิดห้อง Ticket สำเร็จแล้วครับ เชิญที่ {ticket_channel.mention}",
                ephemeral=True,
            )

            view = WhopTicketView()
            await ticket_channel.send(
                content=(
                    f"สวัสดีครับ {user.mention}! 👋\n"
                    "ระบบตรวจสอบสิทธิ์ **ABA100X TH → Global**\n\n"
                    "กดปุ่มด้านล่างเพื่อตรวจสอบสิทธิ์ Whop ของคุณในห้องนี้ได้เลยครับ\n"
                    "*(⏳ ห้องนี้จะถูกยุบอัตโนมัติหากไม่มีการทำรายการภายใน 3 นาที)*"
                ),
                view=view,
            )

            async def auto_close_inactive():
                await asyncio.sleep(180)
                try:
                    if guild.get_channel(ticket_channel.id):
                        await ticket_channel.delete(reason="หมดเวลา 3 นาที (ผู้ใช้เปิดทิ้งไว้)")
                except discord.HTTPException:
                    pass

            asyncio.create_task(auto_close_inactive())

        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาดในการสร้างห้อง: {e}", ephemeral=True)


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="setup_ticket")
    @commands.has_permissions(administrator=True)
    async def setup_ticket_cmd(self, ctx: commands.Context):
        """คำสั่งสร้างแผงให้สมาชิกกดเปิดห้อง Ticket ด้วยตัวเอง"""
        view = CreateTicketView()
        embed = discord.Embed(
            title="🎫 ศูนย์ช่วยเหลือ & ตรวจสอบสิทธิ์ (Global)",
            description="หากคุณต้องการตรวจสอบสิทธิ์ หรือติดปัญหาสามารถกดปุ่มด้านล่าง\nเพื่อสร้างห้องเตือนพูดคุยส่วนตัว (Ticket) กับบอทและแอดมินได้เลยครับ👇",
            color=0x3498db,
        )
        await ctx.send(embed=embed, view=view)
        await ctx.message.delete()


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))

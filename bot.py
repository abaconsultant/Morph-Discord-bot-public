import asyncio
import os
import discord
from discord.ext import commands
from config import DISCORD_TOKEN
from db import init_db, set_guild_name


_OWNER_ID = os.getenv("OWNER_ID")


class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        owner_id = int(_OWNER_ID) if _OWNER_ID and _OWNER_ID.isdigit() else None
        super().__init__(command_prefix='\\', intents=intents, owner_id=owner_id)

    async def setup_hook(self):
        await init_db()

        from cogs.onboarding import AssistantView
        from cogs.tickets import WhopTicketView, CreateTicketView
        from cogs.setup_panel import SetupPanelView

        self.add_view(AssistantView())
        self.add_view(WhopTicketView())
        self.add_view(CreateTicketView())
        self.add_view(SetupPanelView())

        initial_extensions = [
            "cogs.trials",
            "cogs.tickets",
            "cogs.onboarding",
            "cogs.translation",
            "cogs.setup_panel",
        ]

        for ext in initial_extensions:
            try:
                await self.load_extension(ext)
                print(f"✅ Loaded: {ext}")
            except Exception as e:
                print(f"❌ Failed to load {ext}: {e}")

    async def on_ready(self):
        await self.tree.sync()
        print(f"✅ Bot ready: {self.user}")
        for guild in self.guilds:
            try:
                await set_guild_name(str(guild.id), guild.name)
            except Exception:
                pass

    async def on_guild_join(self, guild):
        try:
            await set_guild_name(str(guild.id), guild.name)
        except Exception:
            pass

    async def on_guild_update(self, before, after):
        if before.name != after.name:
            try:
                await set_guild_name(str(after.id), after.name)
            except Exception:
                pass


bot = MyBot()


async def _run_api():
    import uvicorn
    from api import app as api_app
    port = int(os.getenv("API_PORT", "8000"))
    config = uvicorn.Config(api_app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    async with bot:
        asyncio.create_task(_run_api())
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

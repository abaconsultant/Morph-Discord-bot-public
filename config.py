import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")

WHOP_API_KEY = None
WHOP_API_KEY_GLOBAL = None
ALLOWED_PLAN_IDS = []
ALLOWED_PRODUCT_IDS = []
SHEET_ID = None
SHEET_NAME = "Sheet1"
GLOBAL_JOIN_LINK = ""

TRANSLATION_ENABLED = True

"""Configuration constants for the Discord bot."""

import os
from dotenv import load_dotenv

# Load local settings before any module imports configuration constants.
# Existing process variables (including Fly.io settings) take precedence.
load_dotenv()

BOT_ENV = os.getenv("BOT_ENV", "dev").strip().lower()
# Public announcements require an explicit opt-in, independently of Notion.
REMINDERS_ENABLED = BOT_ENV == "prod" and os.getenv("EVENT_REMINDERS_ENABLED", "false").strip().lower() == "true"

# File paths
DATA_DIR = "data"
MAJOR_YEAR_SELECT_SAVE_FILE = "data/major_year_select_message.json"
VERIFY_SAVE_FILE = "data/verify_message.json"
RULES_SAVE_FILE = "data/rules_message.json"

# Role names
UNVERIFIED_ROLE_NAME = "Unverified"
MEMBER_ROLE_NAME = "Member"

# Channel IDs
VERIFY_CHANNEL_ID = 1456120433190637729
ROLES_CHANNEL_ID = 1454883474166124606
ANNOUNCEMENTS_CHANNEL_ID = 1376293904227237982
RULES_CHANNEL_ID = 1456118818266152960

# Verification settings
VERIFICATION_URL_BASE = "https://www.ufembs.com/discord-verify"
TOKEN_EXPIRY_MINUTES = 15

REMINDER_INTERVALS = [
    {"days": 2, "message": "2 days"},
    {"hours": 2, "message": "2 hours"}
]

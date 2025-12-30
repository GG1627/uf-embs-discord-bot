"""Configuration constants for the Discord bot."""

# File paths
DATA_DIR = "data"
MAJOR_YEAR_SELECT_SAVE_FILE = "data/major_year_select_message.json"
VERIFY_SAVE_FILE = "data/verify_message.json"
RULES_SAVE_FILE = "data/rules_message.json"

# Role names
UNVERIFIED_ROLE_NAME = "Unverified"
MEMBER_ROLE_NAME = "Member"

# Channel IDs
VERIFY_CHANNEL_ID = 1453158292707868722
ROLES_CHANNEL_ID = 1454883474166124606
ANNOUNCEMENTS_CHANNEL_ID = 1455300214410842276
RULES_CHANNEL_ID = 1454939917926010961

# Verification settings
VERIFICATION_URL_BASE = "https://www.ufembs.com/discord-verify"
TOKEN_EXPIRY_MINUTES = 15

REMINDER_INTERVALS = [
    {"days": 5, "message": "5 days"},
    {"days": 1, "message": "1 day"},
    {"hours": 2, "message": "2 hours"}
]
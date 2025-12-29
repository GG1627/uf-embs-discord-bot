"""Main entry point for the Discord bot."""

import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
import os
from supabase import create_client, Client
from bot.events import setup_events
from bot.commands import setup_commands
from bot.config import DATA_DIR
from threading import Thread
from keep_alive import app

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Initialize Supabase client (only if credentials are provided)
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
else:
    print("⚠️ Warning: Supabase credentials not found. Verification feature will be disabled.")

# Set up bot intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# Create bot instance
bot = commands.Bot(command_prefix='!', intents=intents)

# Store supabase client on bot instance for access in commands/views
bot.supabase = supabase

# Set up events and commands
setup_events(bot, supabase)
setup_commands(bot)

# Run the bot
if __name__ == "__main__":
    # Start Flask app in a thread to keep the bot alive
    def run_flask():
        port = int(os.environ.get('PORT', 8080))
        app.run(host='0.0.0.0', port=port)
    
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run the Discord bot
    handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
    bot.run(DISCORD_TOKEN, log_handler=handler, log_level=logging.INFO)

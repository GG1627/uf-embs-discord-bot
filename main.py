"""Main entry point for the Discord bot."""

import discord
from discord.ext import commands
import logging
import asyncio
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

async def safe_bot_close():
    """Close the bot cleanly to avoid unclosed aiohttp sessions. Safe to call even if not fully started."""
    try:
        await bot.close()
    except Exception:
        pass


async def start_bot_with_retry():
    """Start the bot with exponential backoff on rate limits."""
    max_retries = 5
    base_delay = 120  # Start with 120 seconds for rate limits (was 60)

    for attempt in range(max_retries):
        try:
            print(f"🚀 Starting Discord bot (attempt {attempt + 1}/{max_retries})...")
            await bot.start(DISCORD_TOKEN)
            break  # If successful, exit the loop

        except discord.LoginFailure as e:
            print(f"❌ CRITICAL ERROR: Discord login failed! Check your DISCORD_TOKEN.")
            print(f"   Error details: {e}")
            await safe_bot_close()
            return  # Don't retry on auth failures

        except discord.PrivilegedIntentsRequired as e:
            print(f"❌ CRITICAL ERROR: Privileged intents required but not enabled!")
            print(f"   Error details: {e}")
            print("   Enable 'Server Members Intent' and 'Message Content Intent' in your bot settings.")
            await safe_bot_close()
            return  # Don't retry on intent failures

        except discord.HTTPException as e:
            if e.status == 429:  # Rate limited
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    print(f"⏳ Rate limited! Waiting {wait_time} seconds before retry...")
                    print(f"   (Attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"❌ Maximum retry attempts reached. Rate limit persists.")
                    print(f"   Please wait several hours before redeploying.")
                    await safe_bot_close()
                    return
            else:
                print(f"❌ HTTP Error: {e.status} - {e.text}")
                if attempt < max_retries - 1:
                    wait_time = 30
                    print(f"⏳ Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    await safe_bot_close()
                    return

        except Exception as e:
            print(f"❌ Unexpected error starting Discord bot!")
            print(f"   Error details: {e}")
            if attempt < max_retries - 1:
                wait_time = 30
                print(f"⏳ Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)
            else:
                print(f"   Maximum retry attempts reached.")
                await safe_bot_close()
                return

# Run the bot
if __name__ == "__main__":
    # Defensive check for Discord token
    if not DISCORD_TOKEN:
        print("❌ CRITICAL ERROR: DISCORD_TOKEN environment variable is missing!")
        print("   Please set DISCORD_TOKEN in your environment variables or .env file.")
        exit(1)

    # Start Flask app in a thread to keep the bot alive
    def run_flask():
        port = int(os.environ.get('PORT', 10000))  # Changed default to 10000 for Render
        app.run(host='0.0.0.0', port=port)

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Set up logging
    handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
    
    # Run the Discord bot with retry logic
    try:
        asyncio.run(start_bot_with_retry())
    except KeyboardInterrupt:
        print("👋 Bot stopped by user")
    except Exception as e:
        print(f"❌ Fatal error in main loop: {e}")
        exit(1)
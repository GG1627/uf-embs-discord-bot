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
import time
import traceback

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Don't create Supabase client yet - will create after bot starts
supabase: Client | None = None

# Set up bot intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# Create bot instance
bot = commands.Bot(command_prefix='!', intents=intents)

# Store supabase credentials on bot for later initialization
bot.supabase_url = SUPABASE_URL
bot.supabase_key = SUPABASE_SERVICE_ROLE_KEY
bot.supabase = None  # Will be set in on_ready

# Set up events and commands
setup_events(bot)
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
    base_delay = 120  # Start with 120 seconds for rate limits

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
            print(f"   Full traceback:")
            traceback.print_exc()
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
    print("=" * 50)
    print("🔧 MAIN.PY STARTED")
    print("=" * 50)
    
    # Defensive check for Discord token
    if not DISCORD_TOKEN:
        print("❌ CRITICAL ERROR: DISCORD_TOKEN environment variable is missing!")
        print("   Please set DISCORD_TOKEN in your environment variables or .env file.")
        exit(1)
    
    print("✅ Discord token found")

    # Start Flask app in a thread to keep the bot alive
    import threading
    flask_started = threading.Event()
    
    def run_flask():
        port = int(os.environ.get('PORT', 10000))
        # Disable Flask logging to reduce noise
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        
        # Signal that we're about to start
        print("🌐 Flask thread: Starting server...")
        flask_started.set()
        
        app.run(host='0.0.0.0', port=port, use_reloader=False)

    print("🌐 Creating Flask thread...")
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("⏱️ Waiting for Flask to signal ready...")
    flask_started.wait(timeout=5)
    print("✅ Flask signaled ready!")
    
    print("⏱️ Additional 1 second buffer...")
    time.sleep(1)
    print("✅ Sleep completed!")

    # Set up logging
    print("📝 Setting up logging...")
    try:
        handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
        print("✅ Logging setup complete!")
    except Exception as e:
        print(f"⚠️ Logging setup failed: {e}")
    
    print("🤖 Starting Discord bot initialization...")
    print(f"🔍 About to call asyncio.run()...")
    
    # Run the Discord bot with retry logic
    try:
        print("🔍 Inside try block, calling asyncio.run()...")
        asyncio.run(start_bot_with_retry())
        print("✅ asyncio.run() completed")
    except KeyboardInterrupt:
        print("👋 Bot stopped by user")
    except Exception as e:
        print(f"❌ Fatal error in main loop: {e}")
        traceback.print_exc()
        exit(1)
    
    print("🏁 Main.py execution complete")
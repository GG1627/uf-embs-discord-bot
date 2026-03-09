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
            _log(f"🚀 Starting Discord bot (attempt {attempt + 1}/{max_retries})...")
            await bot.start(DISCORD_TOKEN)
            break  # If successful, exit the loop

        except discord.LoginFailure as e:
            _log(f"❌ CRITICAL ERROR: Discord login failed! Check your DISCORD_TOKEN.")
            _log(f"   Error details: {e}")
            await safe_bot_close()
            return  # Don't retry on auth failures

        except discord.PrivilegedIntentsRequired as e:
            _log(f"❌ CRITICAL ERROR: Privileged intents required but not enabled!")
            _log(f"   Error details: {e}")
            _log("   Enable 'Server Members Intent' and 'Message Content Intent' in your bot settings.")
            await safe_bot_close()
            return  # Don't retry on intent failures

        except discord.HTTPException as e:
            _log(f"❌ Discord HTTPException: status={e.status} response={e.text!r}")
            if e.status == 429:  # Rate limited
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    _log(f"⏳ Rate limited (429)! Waiting {wait_time} seconds before retry...")
                    _log(f"   (Attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    _log(f"❌ Maximum retry attempts reached. Rate limit persists.")
                    _log(f"   Please wait several hours before redeploying.")
                    await safe_bot_close()
                    return
            else:
                if attempt < max_retries - 1:
                    wait_time = 30
                    _log(f"⏳ Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    await safe_bot_close()
                    return

        except Exception as e:
            _log(f"❌ Unexpected error starting Discord bot: {type(e).__name__}: {e}")
            _log("   Full traceback:")
            traceback.print_exc()
            import sys
            sys.stdout.flush()
            sys.stderr.flush()
            if attempt < max_retries - 1:
                wait_time = 30
                _log(f"⏳ Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)
            else:
                _log("   Maximum retry attempts reached.")
                await safe_bot_close()
                return

def _log(msg: str, flush: bool = True) -> None:
    """Print and flush so Render logs show output immediately."""
    print(msg, flush=flush)


# Run the bot
if __name__ == "__main__":
    import sys
    # Force line-buffered stdout so deploy logs show every line immediately
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    print("=" * 50, flush=True)
    print("🔧 MAIN.PY STARTED", flush=True)
    print("=" * 50, flush=True)
    
    # Defensive check for Discord token
    if not DISCORD_TOKEN:
        print("❌ CRITICAL ERROR: DISCORD_TOKEN environment variable is missing!", flush=True)
        print("   Please set DISCORD_TOKEN in your environment variables or .env file.", flush=True)
        exit(1)
    
    print("✅ Discord token found", flush=True)

    # Set up logging
    print("📝 Setting up logging...", flush=True)
    try:
        handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
        print("✅ Logging setup complete!", flush=True)
    except Exception as e:
        print(f"⚠️ Logging setup failed: {e}", flush=True)
    
    print("🤖 Starting Discord bot...", flush=True)
    
    # Run the Discord bot with retry logic
    try:
        asyncio.run(start_bot_with_retry())
        print("✅ asyncio.run() completed", flush=True)
    except KeyboardInterrupt:
        print("👋 Bot stopped by user", flush=True)
    except Exception as e:
        print(f"❌ Fatal error in main loop: {e}", flush=True)
        traceback.print_exc()
        exit(1)
    
    print("🏁 Main.py execution complete", flush=True)
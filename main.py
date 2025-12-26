import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
import os
import secrets
import datetime
import re
import asyncio
from supabase import create_client, Client
from words.BANNED_WORDS import bad_words
from words.ALLOWED_WORDS import chill_profane_words

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

# init the supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# ______________________PROFANITY FILTER FUNCTIONS______________________
def contains_allowed_words(text: str) -> bool:
    """Check if message contains any allowed profane words."""
    text_lower = text.lower()
    # Check each allowed word (with word boundaries to avoid false positives)
    for word in chill_profane_words:
        # Use word boundaries to match whole words only
        pattern = r'\b' + re.escape(word.lower()) + r'\b'
        if re.search(pattern, text_lower):
            return True
    return False

def contains_banned_words(text: str) -> bool:
    """
    Check if message contains any banned slurs/hate speech.
    Allows longer words that contain banned words (e.g., "class" containing "ass").
    """
    text_lower = text.lower()
    
    for banned_word in bad_words:
        banned_word_lower = banned_word.lower()
        
        # Find all occurrences of the banned word
        for match in re.finditer(re.escape(banned_word_lower), text_lower):
            start_pos = match.start()
            end_pos = match.end()
            
            # Check if it's at a word boundary (standalone word)
            # Word boundary means: start of string OR non-alphanumeric before, AND end of string OR non-alphanumeric after
            is_word_start = (start_pos == 0 or not text_lower[start_pos - 1].isalnum())
            is_word_end = (end_pos == len(text_lower) or not text_lower[end_pos].isalnum())
            
            # Only ban if it's a standalone word (at word boundaries)
            # If it's part of a longer word (has alphanumeric chars before/after), allow it
            if is_word_start and is_word_end:
                return True
    
    return False

def check_profanity(text: str) -> tuple[bool, str]:
    """
    Check if message contains profanity.
    Returns: (is_banned, reason)
    - If contains allowed words, return (False, "allowed")
    - If contains banned words, return (True, "banned_word")
    """
    # First check: if message contains allowed words, it's fine
    if contains_allowed_words(text):
        return False, "allowed"
    
    # Second check: if message contains banned words from our list
    if contains_banned_words(text):
        return True, "banned_word"
    
    return False, "clean"

# ______________________CONFIG______________________
UNVERIFIED_ROLE_NAME = "Unverified"
MEMBER_ROLE_NAME = "Member"
VERIFY_CHANNEL_ID = 1453158292707868722
VERIFICATION_URL_BASE = "https://www.ufembs.com/discord-verify"
TOKEN_EXPIRY_MINUTES = 15

def get_roles(guild: discord.Guild):
    """"Helper function to get Unverified and Member roles"""
    unverified = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE_NAME)
    member = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)
    return unverified, member

# ______________________EVENTS______________________
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # make the bot post the verify message with button in the verify channel
    channel = bot.get_channel(VERIFY_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="Server Verification",
            description="Welcome! Click the **Verify** button below to get access to the server.\n\n"
                       "You'll receive a verification link to complete a CAPTCHA.",
            color=discord.Color.blurple()
        )
        embed.set_footer(text="If you experience any issues, message an officer.")

        await channel.send(embed=embed, view=VerifyView())
        print("✅ Posted verify message with button!")
    else:
        print(f"❌ Verify channel not found! Check VERIFY_CHANNEL_ID: {VERIFY_CHANNEL_ID}")

@bot.event
async def on_member_join(member: discord.Member):
    """Giver Unverified role to new members"""
    unverified, _ = get_roles(member.guild)
    if unverified:
        try:
            await member.add_roles(unverified, reason="New member joined the server")
        except discord.Forbidden:
            print("Missing permissions to add Unverified role")

# censor out any slurs/hate speech in messages
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    # Check for profanity
    is_banned, reason = check_profanity(message.content)
    
    if is_banned:
        try:
            # Delete the message
            await message.delete()
            
            # Send a warning message
            warning_embed = discord.Embed(
                title="⚠️ Message Removed",
                description=f"{message.author.mention}, please refrain from using inappropriate language in this server.",
                color=discord.Color.red()
            )
            warning_embed.set_footer(text="This message was automatically removed by the moderation system.")
            
            # Try to send warning in the same channel, fallback to DM if no permissions
            try:
                warning_msg = await message.channel.send(embed=warning_embed)
                # Delete warning after 10 seconds
                async def delete_warning():
                    await asyncio.sleep(10.0)
                    try:
                        await warning_msg.delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass
                asyncio.create_task(delete_warning())
            except discord.Forbidden:
                # If we can't send in channel, try DM
                try:
                    await message.author.send(embed=warning_embed)
                except discord.Forbidden:
                    # User has DMs disabled, just log it
                    print(f"Could not send warning to {message.author} - message deleted for: {reason}")
        except discord.Forbidden:
            print(f"Missing permissions to delete message from {message.author} in {message.channel}")
        except discord.NotFound:
            # Message was already deleted
            pass
        except Exception as e:
            print(f"Error handling profanity filter: {e}")
    
    # Process bot commands after checking profanity
    await bot.process_commands(message)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.blurple, custom_id="embs_verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Create a verification token row in Supabase and DM/reply a URL."""
        guild = interaction.guild
        user = interaction.user

        if guild is None:
            await interaction.response.send_message(
                "Use this button in the server.", ephemeral=True
            )
            return

        # optional: if already Member, do nothing
        _, member_role = get_roles(guild)
        if member_role and member_role in user.roles:
            await interaction.response.send_message(
                "You are already verified.", ephemeral=True
            )
            return

        # 1) generate a random token
        token = secrets.token_urlsafe(32)
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=TOKEN_EXPIRY_MINUTES)

        # 2) insert into Supabase
        try:
            supabase.table("discord_verification_tokens").insert({
                "discord_user_id": str(user.id),
                "guild_id": str(guild.id),
                "token": token,
                "expires_at": expires_at.isoformat() + "Z",
            }).execute()
        except Exception as e:
            print("Supabase insert error:", e)
            await interaction.response.send_message(
                "Could not start verification right now. Please try again later.",
                ephemeral=True,
            )
            return

        # 3) build URL and send to user
        url = f"{VERIFICATION_URL_BASE}?token={token}"
        await interaction.response.send_message(
            f"Click this link to complete CAPTCHA verification:\n{url}",
            ephemeral=True,
        )

# ______________________RUN BOT______________________
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
bot.run(DISCORD_TOKEN, log_handler=handler, log_level=logging.INFO)
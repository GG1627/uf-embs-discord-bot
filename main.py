import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
import os
import secrets
import datetime
from supabase import create_client, Client

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
            color=discord.Color.light_embed()
        )
        embed.set_footer(text="If you have issues, message an officer.")

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
"""Discord UI components (views, selects, buttons)."""

import discord
import secrets
import datetime
from bot.helpers import get_roles
from bot.config import VERIFY_CHANNEL_ID, VERIFICATION_URL_BASE, TOKEN_EXPIRY_MINUTES

class YearSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Freshman"),
            discord.SelectOption(label="Sophomore"),
            discord.SelectOption(label="Junior"),
            discord.SelectOption(label="Senior"),
            discord.SelectOption(label="Grad"),
            discord.SelectOption(label="Alumni"),
        ]

        super().__init__(
            placeholder="Select your year...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="year_select_menu"  # must stay same for persistence
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]

        role = discord.utils.get(interaction.guild.roles, name=chosen)

        if role is None:
            return await interaction.response.send_message(
                "That role does not exist — please tell an officer.",
                ephemeral=True
            )

        year_names = ["Freshman", "Sophomore", "Junior", "Senior", "Grad", "Alumni"]
        for r in interaction.user.roles:
            if r.name in year_names:
                await interaction.user.remove_roles(r)

        await interaction.user.add_roles(role)

        await interaction.response.send_message(
            f"You have been assigned the **{role.name}** role.",
            ephemeral=True
        )

class YearView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(YearSelect())

class MajorSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Biology"),
            discord.SelectOption(label="Biomedical Engineering"),
            discord.SelectOption(label="Chemistry"),
            discord.SelectOption(label="Computer Engineering"),
            discord.SelectOption(label="Computer Science"),
            discord.SelectOption(label="Electrical Engineering"),
            discord.SelectOption(label="Mechanical Engineering"),
        ]

        super().__init__(
            placeholder="Select your major...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="major_select_menu"  # must stay same for persistence
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]

        role = discord.utils.get(interaction.guild.roles, name=chosen)

        if role is None:
            return await interaction.response.send_message(
                "That role does not exist — please tell an officer.",
                ephemeral=True
            )

        major_names = ["Biology", "Biomedical Engineering", "Chemistry", "Computer Engineering", "Computer Science", "Electrical Engineering", "Mechanical Engineering"]
        for r in interaction.user.roles:
            if r.name in major_names:
                await interaction.user.remove_roles(r)

        await interaction.user.add_roles(role)

        await interaction.response.send_message(
            f"You have been assigned the **{role.name}** role.",
            ephemeral=True
        )


class MajorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MajorSelect())


class VerifyView(discord.ui.View):
    def __init__(self, supabase_client=None):
        super().__init__(timeout=None)
        self.supabase = supabase_client

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
        if self.supabase is None:
            await interaction.response.send_message(
                "Verification is not available. Supabase is not configured.",
                ephemeral=True,
            )
            return
        
        try:
            self.supabase.table("discord_verification_tokens").insert({
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


"""Discord bot commands."""

import os
import json
import discord
from discord.ext import commands
from bot.views import MajorView, VerifyView, YearView
from bot.config import MAJOR_YEAR_SELECT_SAVE_FILE, VERIFY_SAVE_FILE, VERIFY_CHANNEL_ID


def setup_commands(bot: commands.Bot):
    """Register all bot commands."""
    
    @bot.command()
    @commands.has_permissions(manage_guild=True)
    async def setuproles(ctx):
        # if we already have message saved dont create a new one
        if os.path.exists(MAJOR_YEAR_SELECT_SAVE_FILE):
            return await ctx.send("Roles setup already exists.")

        major_view = MajorView()
        year_view = YearView()
        major_msg = await ctx.send(
            "Select your major from the menu below:",
            view=major_view
        )
        year_msg = await ctx.send(
            "Select your year from the menu below:",
            view=year_view
        )



        # save the message + channel so we know it exists
        data = {
            "year_message_id": year_msg.id,
            "major_message_id": major_msg.id,
            "channel_id": ctx.channel.id,
        }
        with open(MAJOR_YEAR_SELECT_SAVE_FILE, "w") as f:
            json.dump(data, f)

        await ctx.send("If your major is not listed, please message an officer.")

    @bot.command()
    @commands.has_permissions(manage_guild=True)
    async def setupverify(ctx):
        # if we already have message saved dont create a new one
        if os.path.exists(VERIFY_SAVE_FILE):
            return await ctx.send("Verification setup already exists.")

        verify_channel = bot.get_channel(VERIFY_CHANNEL_ID)
        if not verify_channel:
            return await ctx.send(f"Verify channel not found! Check VERIFY_CHANNEL_ID: {VERIFY_CHANNEL_ID}")

        embed = discord.Embed(
            title="Server Verification",
            description="Welcome! Click the **Verify** button below to get access to the server.\n\n"
                       "You'll receive a verification link to complete a CAPTCHA.",
            color=discord.Color.blurple()
        )
        embed.set_footer(text="If you experience any issues, message an officer.")

        # Get supabase client from bot's attributes
        supabase = getattr(bot, 'supabase', None)
        view = VerifyView(supabase_client=supabase)
        msg = await verify_channel.send(embed=embed, view=view)

        # save the message + channel so we know it exists
        data = {
            "message_id": msg.id,
            "channel_id": verify_channel.id,
        }
        with open(VERIFY_SAVE_FILE, "w") as f:
            json.dump(data, f)

        # commented out for testing
        # await ctx.send("Verification button created successfully 🎉")


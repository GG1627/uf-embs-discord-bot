"""Discord bot event handlers."""

import os
import asyncio
import discord
from discord.ext import commands
from bot.helpers import check_spam, check_profanity, get_roles
from bot.views import MajorView, VerifyView
from bot.config import SAVE_FILE, VERIFY_SAVE_FILE


def setup_events(bot: commands.Bot, supabase_client=None):
    """Register all event handlers with the bot."""
    
    @bot.event
    async def on_ready():
        print(f"Logged in as {bot.user} (ID: {bot.user.id})")

        # Check if verification is already set up, if not, remind admin
        if not os.path.exists(VERIFY_SAVE_FILE):
            print("⚠️ Verification not set up! Use !setupverify command to set it up.")
        else:
            print("Persistent VerifyView loaded from saved message")

        # Check if roles are already set up, if not, remind admin
        if not os.path.exists(SAVE_FILE):
            print("⚠️ Roles not set up! Use !setuproles command to set it up.")
        else:
            print("Persistent MajorView loaded from saved message")

        bot.add_view(MajorView())
        bot.add_view(VerifyView(supabase_client))
        print("Persistent views added")

    @bot.event
    async def on_member_join(member: discord.Member):
        """Give Unverified role to new members"""
        unverified, _ = get_roles(member.guild)
        if unverified:
            try:
                await member.add_roles(unverified, reason="New member joined the server")
            except discord.Forbidden:
                print("Missing permissions to add Unverified role")

    @bot.event
    async def on_message(message: discord.Message):
        """Censor out any slurs/hate speech in messages and detect spam from all users"""
        # Skip messages from the bot itself
        if message.author == bot.user:
            await bot.process_commands(message)
            return
        
        # Check for spam messages from all users (bots and regular users)
        if check_spam(message.content):
            try:
                # Delete the message
                await message.delete()
                user_type = "bot" if message.author.bot else "user"
                print(f"Deleted spam message from {user_type} {message.author.name} (ID: {message.author.id})")
                
                # Send a warning message (only for regular users, not bots)
                if not message.author.bot:
                    warning_embed = discord.Embed(
                        title="⚠️ Spam Message Removed",
                        description=f"{message.author.mention}, please refrain from posting spam messages in this server.",
                        color=discord.Color.orange()
                    )
                    warning_embed.set_footer(text="This message was automatically removed by the spam filter.")
                    
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
                            print(f"Could not send spam warning to {message.author}")
            except discord.Forbidden:
                user_type = "bot" if message.author.bot else "user"
                print(f"Missing permissions to delete spam message from {user_type} {message.author.name} in {message.channel}")
            except discord.NotFound:
                # Message was already deleted
                pass
            except Exception as e:
                print(f"Error handling spam filter: {e}")
            # Don't process commands if message was spam
            return
        
        # Skip profanity check for bots (they've already been checked for spam above)
        if message.author.bot:
            await bot.process_commands(message)
            return
        
        # Check for profanity (regular users only)
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


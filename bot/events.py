"""Discord bot event handlers."""

import os
import asyncio
import discord
from discord.ext import commands
from bot.helpers import check_spam, check_profanity, get_roles
from bot.views import MajorView, VerifyView, YearView
from bot.config import MAJOR_YEAR_SELECT_SAVE_FILE, VERIFY_SAVE_FILE, ANNOUNCEMENTS_CHANNEL_ID, REMINDER_INTERVALS


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
        if not os.path.exists(MAJOR_YEAR_SELECT_SAVE_FILE):
            print("⚠️ Roles not set up! Use !setuproles command to set it up.")
        else:
            print("Persistent YearView and MajorView loaded from saved message")

        bot.add_view(YearView())
        bot.add_view(MajorView())
        bot.add_view(VerifyView(supabase_client))
        print("Persistent views added")
        
        # Start the event reminder checker if Supabase is available
        if supabase_client:
            asyncio.create_task(check_event_reminders(bot, supabase_client))
            print("Event reminder system started")
        else:
            print("Event reminder system disabled - Supabase not available")

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

    async def check_event_reminders(bot, supabase):
        """Check for events that need reminders and send them"""
        if not supabase:
            print("Warning: Supabase not available for event reminders")
            return
            
        while True:
            try:
                from datetime import datetime, timedelta, timezone
                current_time = datetime.now(timezone.utc)
                
                # Get upcoming events (next 30 days)
                thirty_days_from_now = current_time + timedelta(days=30)
                
                response = supabase.table('events').select('*').gte('start_time', current_time.isoformat()).lte('start_time', thirty_days_from_now.isoformat()).execute()
                events = response.data if response.data else []
                
                announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
                
                if not announcements_channel:
                    print(f"Warning: Announcements channel {ANNOUNCEMENTS_CHANNEL_ID} not found")
                    await asyncio.sleep(600)
                    continue
                
                for event in events:
                    # Parse event start time
                    start_time_str = event['start_time'].replace('Z', '+00:00')
                    event_datetime = datetime.fromisoformat(start_time_str)
                    if event_datetime.tzinfo is None:
                        event_datetime = event_datetime.replace(tzinfo=timezone.utc)
                    
                    time_until = event_datetime - current_time
                    
                    # Skip events that have already passed
                    if time_until.total_seconds() <= 0:
                        continue
                    
                    # Check each reminder interval
                    for interval in REMINDER_INTERVALS:
                        # Build reminder type code (e.g., "5d", "1d", "2h")
                        reminder_type_code = ""
                        if 'days' in interval:
                            reminder_type_code = f"{interval['days']}d"
                        elif 'hours' in interval:
                            reminder_type_code = f"{interval['hours']}h"
                        
                        # Check if reminder already sent
                        reminder_response = supabase.table('event_reminders').select('*').eq('event_id', event['id']).eq('reminder_type', reminder_type_code).execute()
                        already_sent = len(reminder_response.data) > 0 if reminder_response.data else False
                        
                        if already_sent:
                            continue
                        
                        # Calculate the target time for this reminder
                        reminder_time = event_datetime
                        if 'days' in interval:
                            reminder_time = reminder_time - timedelta(days=interval['days'])
                        if 'hours' in interval:
                            reminder_time = reminder_time - timedelta(hours=interval['hours'])
                        
                        # Check if it's time to send this reminder (within 5 minutes)
                        time_to_reminder = reminder_time - current_time
                        if abs(time_to_reminder.total_seconds()) <= 300:  # Within 5 minutes
                            
                            # Create rich embed with event details
                            embed = discord.Embed(
                                title="📢 Event Reminder",
                                description=f"@everyone\n\n**{event['name']}** is happening in **{interval['message']}**!",
                                color=discord.Color.teal()
                            )
                            
                            # Add flyer image if available
                            if event.get('flyer_url'):
                                embed.set_image(url=event['flyer_url'])
                            
                            # Format date/time - convert UTC to Eastern Time
                            try:
                                from zoneinfo import ZoneInfo
                                eastern_time = event_datetime.astimezone(ZoneInfo('America/New_York'))
                            except ImportError:
                                # Fallback for Python < 3.9
                                eastern_offset = timezone(timedelta(hours=-5))
                                eastern_time = event_datetime.astimezone(eastern_offset)
                            date_str = eastern_time.strftime('%B %d, %Y at %I:%M %p %Z')
                            embed.add_field(
                                name="📅 Date & Time",
                                value=date_str,
                                inline=True
                            )
                            
                            days = time_until.days
                            hours = time_until.seconds // 3600
                            embed.add_field(
                                name="⏰ Time Until",
                                value=f"{days} days, {hours} hours",
                                inline=True
                            )
                            
                            if event.get('location'):
                                embed.add_field(
                                    name="📍 Location",
                                    value=event['location'],
                                    inline=True
                                )
                            
                            if event.get('description'):
                                # Discord embed field value limit is 1024 characters
                                desc = event['description'][:1024]
                                embed.add_field(
                                    name="📝 Description",
                                    value=desc,
                                    inline=False
                                )
                            
                            embed.set_footer(text=f"Event ID: {event['id']}")
                            
                            await announcements_channel.send(embed=embed)
                            
                            # Record that we sent this reminder
                            try:
                                supabase.table('event_reminders').insert({
                                    'event_id': event['id'],
                                    'reminder_type': reminder_type_code
                                }).execute()
                            except Exception as e:
                                print(f"Error recording reminder: {e}")
                            
                            print(f"Sent {interval['message']} reminder for event: {event['name']}")
                
                # Check every 10 minutes
                await asyncio.sleep(600)
                
            except Exception as e:
                print(f"Error in event reminder checker: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(600)


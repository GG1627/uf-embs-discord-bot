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
        
        # Initialize Supabase client NOW (after bot is connected)
        from supabase import create_client
        supabase_client = None
        if bot.supabase_url and bot.supabase_key:
            try:
                print("🔧 Initializing Supabase client...")
                supabase_client = create_client(bot.supabase_url, bot.supabase_key)
                bot.supabase = supabase_client
                print("✅ Supabase client initialized")
            except Exception as e:
                print(f"⚠️ Failed to initialize Supabase: {e}")
        else:
            print("⚠️ Supabase credentials not found. Verification feature will be disabled.")

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
        
        # Start background tasks if Supabase is available (guard against duplicate on_ready)
        if supabase_client and not getattr(bot, '_reminder_task_started', False):
            asyncio.create_task(check_event_reminders(bot, supabase_client))
            asyncio.create_task(sync_discord_scheduled_events(bot, supabase_client))
            bot._reminder_task_started = True
            print("Event reminder system started")
            print("Discord scheduled event sync started")
        elif not supabase_client:
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
        """Check for events that need reminders and send them.
        
        Uses a 'past-due' timing model: if a reminder's target time has arrived
        (or passed) and the event itself hasn't happened yet, the reminder is sent.
        This naturally catches up on missed reminders after bot downtime.
        """
        from datetime import datetime, timedelta, timezone
        try:
            from zoneinfo import ZoneInfo
            eastern_tz = ZoneInfo('America/New_York')
        except ImportError:
            eastern_tz = timezone(timedelta(hours=-5))

        if not supabase:
            print("Warning: Supabase not available for event reminders")
            return
            
        while True:
            try:
                current_time = datetime.now(timezone.utc)
                
                # Get upcoming events (next 30 days)
                thirty_days_from_now = current_time + timedelta(days=30)
                
                response = supabase.table('events').select('*').gte('start_time', current_time.isoformat()).lte('start_time', thirty_days_from_now.isoformat()).execute()
                events = response.data if response.data else []
                
                announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
                
                if not announcements_channel:
                    print(f"Warning: Announcements channel {ANNOUNCEMENTS_CHANNEL_ID} not found")
                    await asyncio.sleep(300)
                    continue
                
                # Batch-fetch all sent reminders in one query instead of per-event
                sent_reminders_set = set()
                if events:
                    event_ids = [e['id'] for e in events if e.get('id')]
                    if event_ids:
                        try:
                            rem_response = supabase.table('event_reminders').select('event_id, reminder_type').in_('event_id', event_ids).execute()
                            for r in (rem_response.data or []):
                                sent_reminders_set.add((r['event_id'], r['reminder_type']))
                        except Exception as e:
                            print(f"Warning: Failed to batch-fetch sent reminders, will skip this cycle: {e}")
                            await asyncio.sleep(300)
                            continue
                
                for event in events:
                    try:
                        # Guard required fields
                        if not event.get('start_time') or not event.get('name'):
                            print(f"Skipping event with missing start_time or name: {event.get('id', 'unknown')}")
                            continue
                        
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
                            if 'days' in interval:
                                reminder_type_code = f"{interval['days']}d"
                            elif 'hours' in interval:
                                reminder_type_code = f"{interval['hours']}h"
                            else:
                                continue
                            
                            # Check if reminder already sent (O(1) set lookup)
                            if (event['id'], reminder_type_code) in sent_reminders_set:
                                continue
                            
                            # Calculate the target time for this reminder
                            reminder_time = event_datetime
                            if 'days' in interval:
                                reminder_time = reminder_time - timedelta(days=interval['days'])
                            if 'hours' in interval:
                                reminder_time = reminder_time - timedelta(hours=interval['hours'])
                            
                            # Past-due check: send if reminder time has arrived and event is still upcoming
                            if reminder_time <= current_time:
                                
                                # Create rich embed with event details
                                event_name = event['name']
                                embed = discord.Embed(
                                    title="📢 Event Reminder",
                                    description=f"**{event_name}** is happening in **{interval['message']}**!\n\u200b",
                                    color=discord.Color.teal()
                                )
                                
                                # Add flyer image if available
                                if event.get('flyer_url'):
                                    embed.set_image(url=event['flyer_url'])
                                
                                # Format date/time - convert UTC to Eastern Time
                                eastern_time = event_datetime.astimezone(eastern_tz)
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
                                
                                # Spacer between info block and description
                                embed.add_field(name="\u200b", value="\u200b", inline=False)
                                
                                if event.get('description'):
                                    # Discord embed field value limit is 1024 characters
                                    desc = event['description'][:1024]
                                    embed.add_field(
                                        name="📝 Description",
                                        value=desc,
                                        inline=False
                                    )
                                
                                embed.set_footer(text=f"Event ID: {event['id']}")
                                
                                # Send with @everyone as content so it actually pings
                                await announcements_channel.send(content="@everyone", embed=embed)
                                
                                # Record that we sent this reminder
                                try:
                                    supabase.table('event_reminders').insert({
                                        'event_id': event['id'],
                                        'reminder_type': reminder_type_code
                                    }).execute()
                                    sent_reminders_set.add((event['id'], reminder_type_code))
                                except Exception as e:
                                    print(f"Error recording reminder: {e}")
                                
                                print(f"Sent {interval['message']} reminder for event: {event_name}")
                    
                    except Exception as e:
                        print(f"Error processing reminder for event {event.get('id', 'unknown')}: {e}")
                        import traceback
                        traceback.print_exc()
                        continue
                
                # Check every 5 minutes
                await asyncio.sleep(300)
                
            except Exception as e:
                print(f"Error in event reminder checker: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(300)

    async def sync_discord_scheduled_events(bot, supabase):
        """Periodically sync Supabase events to Discord Scheduled Events."""
        if not supabase:
            print("Warning: Supabase not available for scheduled event sync")
            return

        await bot.wait_until_ready()

        while True:
            try:
                await sync_discord_scheduled_events_once(bot, supabase)
                await asyncio.sleep(900)
            except Exception as e:
                print(f"Error in scheduled event sync: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(900)


def _sync_tag(supabase_id: str) -> str:
    """Return a hidden tag embedded in Discord event descriptions to track the Supabase source ID."""
    return f"\n\n[sync:{supabase_id}]"


def _extract_sync_id(description: str | None) -> str | None:
    """Extract the Supabase event ID from a Discord event description tag."""
    if not description:
        return None
    import re
    m = re.search(r'\[sync:([a-f0-9\-]+)\]', description)
    return m.group(1) if m else None


def _build_description(event: dict) -> str:
    """Build the Discord event description with the sync tag appended."""
    desc = (event.get('description') or '')[:950]
    return desc + _sync_tag(event['id'])


async def _fetch_flyer(url: str) -> bytes | None:
    """Download a flyer image, returning bytes or None on failure."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        pass
    return None


async def sync_discord_scheduled_events_once(bot, supabase):
    """One-shot sync of Supabase events to Discord Scheduled Events.
    
    Creates new events, updates existing ones when data has changed.
    Returns a tuple (created_count, updated_count).
    """
    from datetime import datetime, timedelta, timezone

    current_time = datetime.now(timezone.utc)
    thirty_days_from_now = current_time + timedelta(days=30)

    response = supabase.table('events').select('*').gte(
        'start_time', current_time.isoformat()
    ).lte(
        'start_time', thirty_days_from_now.isoformat()
    ).execute()
    supabase_events = response.data if response.data else []

    created_count = 0
    updated_count = 0

    for guild in bot.guilds:
        try:
            discord_events = await guild.fetch_scheduled_events()

            # Map Supabase ID → Discord event via the sync tag in description
            existing_by_sync_id = {}
            # Fallback map for events created before sync tags were added
            existing_by_name_time = {}
            for de in discord_events:
                sync_id = _extract_sync_id(de.description)
                if sync_id:
                    existing_by_sync_id[sync_id] = de
                key = (de.name, de.start_time.isoformat() if de.start_time else None)
                existing_by_name_time[key] = de

            for event in supabase_events:
                try:
                    if not event.get('start_time') or not event.get('name'):
                        continue

                    start_time_str = event['start_time'].replace('Z', '+00:00')
                    event_datetime = datetime.fromisoformat(start_time_str)
                    if event_datetime.tzinfo is None:
                        event_datetime = event_datetime.replace(tzinfo=timezone.utc)

                    if event_datetime <= current_time:
                        continue

                    event_name = event['name']
                    location = event.get('location') or 'TBA'
                    end_datetime = event_datetime + timedelta(hours=1)
                    description = _build_description(event)
                    supabase_id = event['id']

                    discord_event = existing_by_sync_id.get(supabase_id)
                    # Fallback: match by name + start_time for events created before sync tags
                    if not discord_event:
                        fallback_key = (event_name, event_datetime.isoformat())
                        discord_event = existing_by_name_time.get(fallback_key)

                    if discord_event:
                        # Check if anything changed
                        needs_update = False
                        edit_kwargs = {}

                        if discord_event.name != event_name:
                            edit_kwargs['name'] = event_name
                            needs_update = True
                        if discord_event.start_time and discord_event.start_time.isoformat() != event_datetime.isoformat():
                            edit_kwargs['start_time'] = event_datetime
                            edit_kwargs['end_time'] = end_datetime
                            needs_update = True
                        if (discord_event.location or '') != location:
                            edit_kwargs['location'] = location
                            needs_update = True
                        if (discord_event.description or '').strip() != description.strip():
                            edit_kwargs['description'] = description
                            needs_update = True

                        if needs_update:
                            if event.get('flyer_url'):
                                image_data = await _fetch_flyer(event['flyer_url'])
                                if image_data:
                                    edit_kwargs['image'] = image_data

                            await discord_event.edit(**edit_kwargs)
                            print(f"Updated Discord scheduled event: {event_name}")
                            updated_count += 1
                        continue

                    # Create new event
                    kwargs = {
                        'name': event_name,
                        'start_time': event_datetime,
                        'end_time': end_datetime,
                        'entity_type': discord.EntityType.external,
                        'location': location,
                        'privacy_level': discord.PrivacyLevel.guild_only,
                        'description': description,
                        'reason': 'Auto-synced from EMBS events',
                    }

                    if event.get('flyer_url'):
                        image_data = await _fetch_flyer(event['flyer_url'])
                        if image_data:
                            kwargs['image'] = image_data

                    await guild.create_scheduled_event(**kwargs)
                    print(f"Created Discord scheduled event: {event_name}")
                    created_count += 1

                except discord.Forbidden:
                    print(f"Missing permissions to create/update scheduled event in {guild.name}")
                    break
                except Exception as e:
                    print(f"Error syncing scheduled event '{event.get('name', '?')}': {e}")
                    continue

        except discord.Forbidden:
            print(f"Missing permissions to fetch scheduled events in {guild.name}")
        except Exception as e:
            print(f"Error syncing scheduled events for guild {guild.name}: {e}")

    return created_count, updated_count

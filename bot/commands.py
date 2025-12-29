"""Discord bot commands."""

import os
import json
import discord
from discord.ext import commands
from bot.views import MajorView, VerifyView, YearView
from bot.config import MAJOR_YEAR_SELECT_SAVE_FILE, VERIFY_SAVE_FILE, VERIFY_CHANNEL_ID, ANNOUNCEMENTS_CHANNEL_ID


def setup_commands(bot: commands.Bot):
    """Register all bot commands."""
    
    # not needed anymore because of discord onboarding
    
    # @bot.command()
    # @commands.has_permissions(manage_guild=True)
    # async def setuproles(ctx):
    #     # if we already have message saved dont create a new one
    #     if os.path.exists(MAJOR_YEAR_SELECT_SAVE_FILE):
    #         return await ctx.send("Roles setup already exists.")

    #     major_view = MajorView()
    #     year_view = YearView()
    #     major_msg = await ctx.send(
    #         "Select your major from the menu below:",
    #         view=major_view
    #     )
    #     year_msg = await ctx.send(
    #         "Select your year from the menu below:",
    #         view=year_view
    #     )



    #     # save the message + channel so we know it exists
    #     data = {
    #         "year_message_id": year_msg.id,
    #         "major_message_id": major_msg.id,
    #         "channel_id": ctx.channel.id,
    #     }
    #     with open(MAJOR_YEAR_SELECT_SAVE_FILE, "w") as f:
    #         json.dump(data, f)

    #     await ctx.send("If your major is not listed, please message an officer.")

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

    @bot.command()
    @commands.has_permissions(manage_guild=True)
    async def checkevents(ctx):
        """Check upcoming events and reminder status"""
        supabase = getattr(bot, 'supabase', None)
        if not supabase:
            return await ctx.send("❌ Supabase not configured!")
        
        try:
            from datetime import datetime, timedelta, timezone
            # Get upcoming events (next 30 days)
            current_time = datetime.now(timezone.utc)
            thirty_days_from_now = current_time + timedelta(days=30)
            
            response = supabase.table('events').select('*').gte('start_time', current_time.isoformat()).lte('start_time', thirty_days_from_now.isoformat()).execute()
            events = response.data if response.data else []
            
            if not events:
                return await ctx.send("📅 No upcoming events found.")
            
            embed = discord.Embed(
                title="📅 Upcoming Events",
                description=f"Found {len(events)} upcoming events. Monitoring for reminders.",
                color=discord.Color.blue()
            )
            
            for event in events:
                start_time_str = event['start_time'].replace('Z', '+00:00')
                event_datetime = datetime.fromisoformat(start_time_str)
                if event_datetime.tzinfo is None:
                    event_datetime = event_datetime.replace(tzinfo=timezone.utc)
                
                time_until = event_datetime - current_time
                
                days = time_until.days
                hours = time_until.seconds // 3600
                
                event_info = f"📅 {event_datetime.strftime('%B %d, %Y at %I:%M %p')}\n⏰ {days} days, {hours} hours from now"
                
                if event.get('location'):
                    event_info += f"\n📍 {event['location']}"
                
                embed.add_field(
                    name=f"{event['name']} (ID: {event['id'][:8]}...)",
                    value=event_info,
                    inline=False
                )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error checking events: {str(e)}")
            import traceback
            traceback.print_exc()

    @bot.command()
    @commands.has_permissions(manage_guild=True)
    async def eventinfo(ctx, event_uuid: str):
        """Get detailed information about a specific event"""
        supabase = getattr(bot, 'supabase', None)
        if not supabase:
            return await ctx.send("❌ Supabase not configured!")
        
        try:
            from datetime import datetime, timezone
            response = supabase.table('events').select('*').eq('id', event_uuid).execute()
            event = response.data[0] if response.data else None
            
            if not event:
                return await ctx.send(f"❌ Event with ID {event_uuid} not found!")
            
            start_time_str = event['start_time'].replace('Z', '+00:00')
            event_datetime = datetime.fromisoformat(start_time_str)
            current_time = datetime.now(timezone.utc)
            if event_datetime.tzinfo is None:
                event_datetime = event_datetime.replace(tzinfo=timezone.utc)
            
            time_until = event_datetime - current_time
            
            embed = discord.Embed(
                title=f"📅 {event['name']}",
                color=discord.Color.blue()
            )
            
            if event.get('flyer_url'):
                embed.set_image(url=event['flyer_url'])
            
            embed.add_field(
                name="📅 Date & Time",
                value=event_datetime.strftime('%B %d, %Y at %I:%M %p'),
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
                embed.add_field(
                    name="📝 Description",
                    value=event['description'],
                    inline=False
                )
            
            # Check sent reminders
            reminders_response = supabase.table('event_reminders').select('*').eq('event_id', event['id']).execute()
            sent_reminders = [r['reminder_type'] for r in reminders_response.data] if reminders_response.data else []
            
            reminder_status = []
            for interval in [('5d', '5 days'), ('2d', '2 days'), ('1d', '1 day'), ('10h', '10 hours'), ('2h', '2 hours')]:
                status = "✅ Sent" if interval[0] in sent_reminders else "⏳ Pending"
                reminder_status.append(f"{interval[1]}: {status}")
            
            embed.add_field(
                name="🔔 Reminder Status",
                value="\n".join(reminder_status),
                inline=False
            )
            
            embed.set_footer(text=f"Event ID: {event['id']}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error getting event details: {str(e)}")
            import traceback
            traceback.print_exc()


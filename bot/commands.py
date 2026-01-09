"""Discord bot commands."""

import os
import json
import discord
from discord.ext import commands
import requests
from bot.views import MajorView, VerifyView, YearView
from bot.config import MAJOR_YEAR_SELECT_SAVE_FILE, VERIFY_SAVE_FILE, VERIFY_CHANNEL_ID, ANNOUNCEMENTS_CHANNEL_ID, RULES_SAVE_FILE, RULES_CHANNEL_ID


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
                color=discord.Color.blurple()
            )
            
            for event in events:
                start_time_str = event['start_time'].replace('Z', '+00:00')
                event_datetime = datetime.fromisoformat(start_time_str)
                if event_datetime.tzinfo is None:
                    event_datetime = event_datetime.replace(tzinfo=timezone.utc)
                
                time_until = event_datetime - current_time
                
                days = time_until.days
                hours = time_until.seconds // 3600
                
                # Convert UTC to Eastern Time for display
                try:
                    from zoneinfo import ZoneInfo
                    eastern_time = event_datetime.astimezone(ZoneInfo('America/New_York'))
                except ImportError:
                    # Fallback for Python < 3.9
                    eastern_offset = timezone(timedelta(hours=-5))
                    eastern_time = event_datetime.astimezone(eastern_offset)
                
                event_info = f"📅 {eastern_time.strftime('%B %d, %Y at %I:%M %p %Z')}\n⏰ {days} days, {hours} hours from now"
                
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
                color=discord.Color.blurple()
            )
            
            if event.get('flyer_url'):
                embed.set_image(url=event['flyer_url'])
            
            # Convert UTC to Eastern Time for display
            try:
                from zoneinfo import ZoneInfo
                eastern_time = event_datetime.astimezone(ZoneInfo('America/New_York'))
            except ImportError:
                # Fallback for Python < 3.9
                from datetime import timedelta
                eastern_offset = timezone(timedelta(hours=-5))
                eastern_time = event_datetime.astimezone(eastern_offset)
            
            embed.add_field(
                name="📅 Date & Time",
                value=eastern_time.strftime('%B %d, %Y at %I:%M %p %Z'),
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

    @bot.command()
    async def dadjoke(ctx):
        """Get a random dad joke."""
        try:
            headers = {'Accept': 'application/json'}
            response = requests.get('https://icanhazdadjoke.com/', headers=headers)

            if response.status_code == 200:
                joke_data = response.json()
                joke = joke_data.get('joke', 'Sorry, couldn\'t fetch a joke right now!')
                await ctx.send(f"{joke}")
            else:
                await ctx.send("❌ Sorry, I couldn't fetch a dad joke right now!")

        except Exception as e:
            await ctx.send("❌ Sorry, something went wrong while fetching the joke!")
            print(f"Error fetching dad joke: {e}")

    @bot.command()
    async def meme(ctx):
        """Get a random meme."""
        try:
            response = requests.get('https://meme-api.com/gimme/1')

            if response.status_code == 200:
                meme_data = response.json()
                if meme_data.get('memes') and len(meme_data['memes']) > 0:
                    meme = meme_data['memes'][0]
                    embed = discord.Embed(
                        title=meme.get('title', 'Random Meme'),
                        url=meme.get('postLink', ''),
                        color=discord.Color.blue()
                    )
                    embed.set_image(url=meme['url'])
                    embed.set_footer(text=f"From r/{meme.get('subreddit', 'unknown')} • {meme.get('ups', 0)} upvotes")

                    await ctx.send(embed=embed)
                else:
                    await ctx.send("❌ Sorry, couldn't fetch a meme right now!")
            else:
                await ctx.send("❌ Sorry, I couldn't fetch a meme right now!")

        except Exception as e:
            await ctx.send("❌ Sorry, something went wrong while fetching the meme!")
            print(f"Error fetching meme: {e}")

    @bot.command()
    async def quote(ctx):
        """Get a random inspirational quote."""
        try:
            response = requests.get('https://zenquotes.io/api/random')

            if response.status_code == 200:
                quote_data = response.json()
                if quote_data and len(quote_data) > 0:
                    quote = quote_data[0]
                    embed = discord.Embed(
                        description=f'"{quote.get("q", "No quote found")}"',
                        color=discord.Color.blue()
                    )
                    embed.set_footer(text=f"— {quote.get('a', 'Unknown Author')}")
                    await ctx.send(embed=embed)
                else:
                    await ctx.send("❌ Sorry, couldn't fetch a quote right now!")
            else:
                await ctx.send("❌ Sorry, I couldn't fetch a quote right now!")

        except Exception as e:
            await ctx.send("❌ Sorry, something went wrong while fetching the quote!")
            print(f"Error fetching quote: {e}")

    @bot.command()
    @commands.has_permissions(manage_guild=True)
    async def fun1(ctx):
        """Display a fun fact about UF EMBS."""
        await ctx.send("Did you know that UF EMBS was founded in 2025?")

    @bot.command()
    @commands.has_permissions(manage_guild=True)
    async def fun2(ctx):
        """Display a fun fact about biomedical engineering."""
        await ctx.send("Biomedical engineers are now 3D printing functional mini organs from a patient's own cells, like heart tissue that beats in a dish, which can be used to test personalized drugs without ever entering the body.")

    @bot.command()
    @commands.has_permissions(manage_guild=True)
    async def setuprules(ctx):
        """Set up the rules message in the rules channel."""
        # if we already have message saved dont create a new one
        if os.path.exists(RULES_SAVE_FILE):
            return await ctx.send("Rules message already exists.")

        # Use configured channel or the channel where command is run
        if RULES_CHANNEL_ID:
            rules_channel = bot.get_channel(RULES_CHANNEL_ID)
            if not rules_channel:
                return await ctx.send(f"Rules channel not found! Check RULES_CHANNEL_ID: {RULES_CHANNEL_ID}")
        else:
            rules_channel = ctx.channel

        embed = discord.Embed(
            title="📋 Server Rules & Commands",
            description="Welcome to our Discord server! Please follow these rules and enjoy using our bot commands.",
            color=discord.Color.green()
        )

        # Rules section
        rules_text = """
1. Be respectful. No harassment, hate speech, discrimination, or personal attacks.

2. Keep it friendly & welcoming. Treat others the way you want to be treated.

3. Use channels appropriately. Keep topics in the correct channels.

4. No spam or excessive self-promotion. Ask a moderator if unsure.

5. Keep content appropriate. No NSFW, illegal, or harmful content.

6. Protect privacy. Don’t share personal info without permission.

7. Follow Discord’s Terms of Service & Community Guidelines.

8. Listen to moderators. Staff decisions are final — if you have concerns, message them privately.
        """.strip()

        embed.add_field(
            name="📜 Server Rules",
            value=rules_text,
            inline=False
        )

        # Commands section
        commands_text = """
`!meme` - Get a random meme
`!dadjoke` - Get a random dad joke
`!quote` - Get an inspirational quote
        """.strip()

        embed.add_field(
            name="🎮 Available Commands",
            value=commands_text,
            inline=False
        )

        embed.set_footer(text="These rules help keep our community friendly and fun!")

        msg = await rules_channel.send(embed=embed)

        # save the message + channel so we know it exists
        data = {
            "message_id": msg.id,
            "channel_id": rules_channel.id,
        }
        with open(RULES_SAVE_FILE, "w") as f:
            json.dump(data, f)

        await ctx.send("Rules message created successfully 🎉")


"""Officer-only Notion commands and a signed, persistently queued webhook."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

import aiohttp
from aiohttp import web
import discord
from discord.ext import commands

from bot.config import BOT_ENV
from bot.notion_client import IntegrationError, NotionClient, notion_id, plan_question, week_range
from bot.notion_state import NotionState
from bot.notion_workflows import NotionWorkflows
from bot.notion_style import ART_FILES, ASSET_DIR, card, status_label

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotionSettings:
    enabled: bool = False
    environment: str = 'dev'
    guild_id: int = 0
    channel_id: int = 0
    officer_role_ids: tuple[int, ...] = ()
    token: str = field(default='', repr=False)
    database_id: str = ''
    data_source_id: str = ''
    path_secret: str = field(default='', repr=False)
    webhook_secret: str = field(default='', repr=False)
    supabase_url: str = ''
    supabase_key: str = field(default='', repr=False)
    port: int = 8080
    title_property: str = 'Name'
    status_property: str = 'Status'
    date_property: str = 'Date'
    location_property: str = 'Location'
    description_property: str = 'Description'
    priority_property: str = 'Priority'
    event_type_property: str = 'Event Type'
    post_deadline_property: str = 'Post Deadline'
    digest_enabled: bool = True
    completion_sync_enabled: bool = False
    groq_key: str = field(default='', repr=False)
    groq_model: str = 'openai/gpt-oss-120b'

    @classmethod
    def from_env(cls):
        def number(name, default='0'):
            try:
                return int(os.getenv(name, default).strip() or default)
            except ValueError:
                raise IntegrationError(f'{name} must be a numeric ID or port.') from None

        prefix = f'NOTION_{BOT_ENV.upper()}_'
        raw_roles = os.getenv(prefix + 'OFFICER_ROLE_IDS', '').strip()
        # Accept the previous single-role setting while moving to a role list.
        if not raw_roles:
            raw_roles = os.getenv(prefix + 'OFFICER_ROLE_ID', '').strip()
        try:
            role_ids = tuple(dict.fromkeys(int(value.strip()) for value in raw_roles.split(',') if value.strip()))
            if any(role_id <= 0 for role_id in role_ids):
                raise ValueError
        except ValueError:
            raise IntegrationError(f'{prefix}OFFICER_ROLE_IDS must contain comma-separated numeric role IDs.') from None
        settings = cls(
            enabled=os.getenv('NOTION_ENABLED', '').lower() == 'true', environment=BOT_ENV,
            guild_id=number(prefix + 'GUILD_ID'), channel_id=number(prefix + 'CHANNEL_ID'),
            officer_role_ids=role_ids, token=os.getenv('NOTION_API_TOKEN', '').strip(),
            database_id=os.getenv('NOTION_DATABASE_ID', '').strip(),
            data_source_id=os.getenv('NOTION_DATA_SOURCE_ID', '').strip(),
            path_secret=os.getenv('NOTION_WEBHOOK_PATH_SECRET', '').strip(),
            webhook_secret=os.getenv('NOTION_WEBHOOK_SECRET', '').strip(),
            supabase_url=os.getenv('SUPABASE_URL', '').strip(),
            supabase_key=os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip(),
            port=number('NOTION_HTTP_PORT', '8080'),
            title_property=os.getenv('NOTION_TITLE_PROPERTY', 'Name'),
            status_property=os.getenv('NOTION_STATUS_PROPERTY', 'Status'),
            date_property=os.getenv('NOTION_DATE_PROPERTY', 'Date'),
            location_property=os.getenv('NOTION_LOCATION_PROPERTY', 'Location'),
            description_property=os.getenv('NOTION_DESCRIPTION_PROPERTY', 'Description'),
            priority_property=os.getenv('NOTION_PRIORITY_PROPERTY', 'Priority'),
            event_type_property=os.getenv('NOTION_EVENT_TYPE_PROPERTY', 'Event Type'),
            post_deadline_property=os.getenv('NOTION_POST_DEADLINE_PROPERTY', 'Post Deadline'),
            digest_enabled=os.getenv('NOTION_DIGEST_ENABLED', 'true').lower() == 'true',
            completion_sync_enabled=os.getenv('NOTION_COMPLETION_SYNC_ENABLED', 'false').lower() == 'true',
            groq_key=os.getenv('GROQ_API_KEY', '').strip(),
            groq_model=os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b').strip())
        if settings.enabled:
            if BOT_ENV not in ('dev', 'prod'):
                raise IntegrationError('Notion requires BOT_ENV=dev or prod.')
            if min(settings.guild_id, settings.channel_id) <= 0:
                raise IntegrationError(f'Fill in {prefix}GUILD_ID and CHANNEL_ID before enabling Notion.')
            if BOT_ENV == 'prod' and not settings.officer_role_ids:
                raise IntegrationError(f'Fill in {prefix}OFFICER_ROLE_IDS before enabling Notion in prod.')
            if not settings.token or not (settings.data_source_id or settings.database_id):
                raise IntegrationError('Notion requires NOTION_API_TOKEN and NOTION_DATABASE_ID (or NOTION_DATA_SOURCE_ID).')
            if not settings.supabase_url.startswith('https://') or not settings.supabase_key:
                raise IntegrationError('Notion tracking requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.')
            if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', settings.path_secret):
                raise IntegrationError('Set NOTION_WEBHOOK_PATH_SECRET to a random URL-safe secret of at least 32 characters.')
            if not 1 <= settings.port <= 65535:
                raise IntegrationError('NOTION_HTTP_PORT must be a valid port.')
            try:
                settings = replace(settings,
                                   database_id=notion_id(settings.database_id) if settings.database_id else '',
                                   data_source_id=notion_id(settings.data_source_id) if settings.data_source_id else '')
            except ValueError:
                raise IntegrationError('Notion database/data source IDs must be UUIDs, not URLs.') from None
        return settings


def clean(value, limit=200):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


def friendly_date(value):
    return f'{value.strftime("%B")} {value.day}, {value.year}'


def event_date(value):
    if not value:
        return 'Date not set'
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return f'{day.strftime("%A")}, {friendly_date(day)}'
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo('America/New_York'))
        moment = moment.astimezone(ZoneInfo('America/New_York'))
        clock = f'{moment.hour % 12 or 12}:{moment.minute:02d} {moment.strftime("%p %Z")}'
        return f'{moment.strftime("%A")}, {friendly_date(moment)} • {clock}'
    except (ValueError, TypeError):
        return 'Date unavailable'


def event_embeds(events, label):
    """Bound output; do not ping users even when Notion text contains mentions."""
    embeds = []
    for offset in range(0, min(len(events), 30), 10):
        title, _, subtitle = label.partition(' · ')
        embed = card('📅 ' + title, subtitle or None)
        for event in events[offset:offset + 10]:
            detail = f'**{status_label(clean(event.status, 80))}**\n'
            detail += f'📅 {event_date(event.date)}\n📍 {clean(event.location or "Location not set", 100)}\n'
            detail += f'[Open event in Notion ↗]({event.url})'
            embed.add_field(name=clean(event.title, 120), value=detail, inline=False)
        count = len(events)
        summary = f'{count} event{"s" if count != 1 else ""}'
        if count > 30:
            summary += ' • Showing the first 30'
        embed.set_footer(text=summary + ' • Eastern Time')
        embeds.append(embed)
    return embeds


class NotionCog(NotionWorkflows, commands.Cog):
    def __init__(self, bot, settings):
        self.bot, self.settings = bot, settings
        self.session = None
        self.client = None
        self.state = None
        self.runner = None
        self.worker = None
        self.initialized = False
        self.wake = asyncio.Event()
        self.edit_lock = asyncio.Lock()
        self._digest_queued = None
        self._next_completion_check = 0

    async def cog_load(self):
        if not self.settings.enabled:
            return
        self.session = aiohttp.ClientSession()
        try:
            self.client = NotionClient(self.session, self.settings)
            if not self.settings.data_source_id:
                database = await self.client.request('GET', 'databases/' + self.settings.database_id)
                sources = database.get('data_sources', [])
                if len(sources) != 1:
                    raise IntegrationError('Set NOTION_DATA_SOURCE_ID explicitly: the database does not have exactly one data source.')
                self.settings = replace(self.settings, data_source_id=notion_id(sources[0]['id']))
                self.client.settings = self.settings
            scope = f'{self.settings.environment}:{self.settings.guild_id}:{self.settings.channel_id}:{self.settings.data_source_id}'
            self.state = NotionState(self.session, self.settings.supabase_url, self.settings.supabase_key, scope)
            await self.state.setting('initialized')
            app = web.Application(client_max_size=128 * 1024)
            app.router.add_get('/health', self.health)
            app.router.add_get('/notion/assets/{name}', self.artwork)
            app.router.add_post('/notion/webhook/' + self.settings.path_secret, self.webhook)
            # Do not log request URLs: the webhook path contains a setup secret.
            self.runner = web.AppRunner(app, access_log=None)
            await self.runner.setup()
            await web.TCPSite(self.runner, '0.0.0.0', self.settings.port).start()
            self.worker = asyncio.create_task(self.work(), name='notion-webhooks')
            log.info('Notion integration started for %s on port %s', self.settings.environment, self.settings.port)
        except BaseException:
            await self.cog_unload()
            raise

    async def cog_unload(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        if self.state:
            try:
                await self.state.close()
            except Exception:
                pass
            self.state = None
        if self.session:
            await self.session.close()
            self.session = None

    def allowed(self, ctx):
        has_role = any(role.id in self.settings.officer_role_ids for role in getattr(ctx.author, 'roles', []))
        dev_owner = (self.settings.environment == 'dev' and not self.settings.officer_role_ids
                     and ctx.guild is not None and ctx.author.id == ctx.guild.owner_id)
        return (self.settings.enabled and ctx.guild is not None
                and ctx.guild.id == self.settings.guild_id
                and ctx.channel.id == self.settings.channel_id
                and not ctx.author.bot
                and (has_role or dev_owner))

    def private_channel(self):
        channel = self.bot.get_channel(self.settings.channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.settings.guild_id:
            raise IntegrationError('The configured Notion channel must be a text channel in the configured server.')
        if self.settings.environment == 'prod' and channel.permissions_for(channel.guild.default_role).view_channel:
            raise IntegrationError('Hide the Notion channel from @everyone before enabling Notion.')
        if self.settings.environment == 'prod' and not self.settings.officer_role_ids:
            raise IntegrationError('Configure officer roles before enabling Notion in prod.')
        if any(channel.guild.get_role(role_id) is None for role_id in self.settings.officer_role_ids):
            raise IntegrationError('A configured officer role was not found in this server.')
        return channel

    async def cog_check(self, ctx):
        if not self.allowed(ctx):
            return False
        self.private_channel()
        return True

    async def cog_command_error(self, ctx, error):
        # Never disclose results/errors outside the configured access boundary.
        if not self.allowed(ctx):
            return
        try:
            self.private_channel()
        except IntegrationError:
            return
        original = getattr(error, 'original', error)
        if isinstance(original, IntegrationError):
            message = str(original)
        elif isinstance(error, commands.CommandOnCooldown):
            message = f'Please wait {error.retry_after:.0f} seconds before trying again.'
        elif isinstance(error, commands.MissingRequiredArgument):
            message = 'Try !ask YOUR QUESTION, !notion week, or !help.'
        elif isinstance(error, commands.MaxConcurrencyReached):
            message = 'An event query is already running. Please try again shortly.'
        else:
            log.warning('Notion command failed: %s', type(original).__name__)
            message = 'The event query failed. Check integration access and try again.'
        await ctx.send(discord.utils.escape_mentions(message), allowed_mentions=discord.AllowedMentions.none())

    async def show_events(self, ctx, start, end, status=None):
        if not self.initialized:
            raise IntegrationError('Notion is still initializing. Check the server logs if this persists.')
        events = await self.client.list_events(start, end, status)
        # Re-check permissions after network waits, before exposing records.
        if not self.allowed(ctx):
            return
        self.private_channel()
        if not events:
            embed = card('Nothing on this calendar 🐾',
                         f'No matching events from {friendly_date(start)} to {friendly_date(end - timedelta(days=1))}.\n'
                         'Try another week or `!notion month`.', tone='mint')
            return await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        label = f'Events · {friendly_date(start)} – {friendly_date(end - timedelta(days=1))}'
        # Discord's 6,000-character limit applies across all embeds in a message.
        for embed in event_embeds(events, label):
            if not self.allowed(ctx):
                return
            self.private_channel()
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name='notion', aliases=['notionevents'])
    @commands.cooldown(5, 60, commands.BucketType.user)
    @commands.max_concurrency(1, per=commands.BucketType.guild, wait=False)
    async def notionevents(self, ctx, period: str = 'upcoming', *, status: str = None):
        """See events: today, week, nextweek, upcoming, or month."""
        routes = {'missingrooms': self.notionmissingrooms, 'deadlines': self.notiondeadlines,
                  'reimbursements': self.notionreimbursements, 'digest': self.notiondigest}
        if period.lower() in routes:
            return await ctx.invoke(routes[period.lower()])
        if period.lower() == 'event':
            return await ctx.invoke(self.notionevent, query=status or '')
        if period.lower() == 'edit':
            return await ctx.invoke(self.notionedit, query=status or '')
        start, end = week_range(period.lower())
        await self.show_events(ctx, start, end, status)

    @commands.command(name='ask', aliases=['notionask'])
    @commands.cooldown(3, 60, commands.BucketType.user)
    @commands.max_concurrency(1, per=commands.BucketType.guild, wait=False)
    async def notionask(self, ctx, *, question: str):
        """Ask about event dates/statuses. Read-only; requires GROQ_API_KEY."""
        if not self.initialized:
            raise IntegrationError('Notion is still initializing.')
        async with ctx.typing():
            start, end, status = await plan_question(self.session, self.settings, question)
            await self.show_events(ctx, start, end, status)

    async def send_notion_help(self, ctx):
        """Send the guide through !help, within the officer access boundary."""
        from bot.notion_help import NOTION_HELP
        if not self.allowed(ctx):
            return
        self.private_channel()
        await ctx.send(NOTION_HELP, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)

    @commands.command()
    @commands.cooldown(1, 60, commands.BucketType.channel)
    async def intro(self, ctx):
        """Post the channel welcome with cat1 as a regular attachment."""
        from bot.notion_help import NOTION_INTRO
        path = ASSET_DIR / 'cat1.jpg'
        if not path.is_file():
            raise IntegrationError('The welcome image is missing: assets/notion/cat1.jpg.')
        image = discord.File(path, filename='cat1.jpg')
        try:
            await ctx.send(NOTION_INTRO, file=image, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)
        finally:
            image.close()

    @commands.command()
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def notionstatuses(self, ctx):
        """List the status names from the live Notion database."""
        source = await self.client.request('GET', 'data_sources/' + self.settings.data_source_id)
        prop = source.get('properties', {}).get(self.settings.status_property, {})
        options = prop.get('status', {}).get('options', [])
        if not self.allowed(ctx):
            return
        self.private_channel()
        text = '\n'.join('• ' + clean(option['name'], 100) for option in options[:30])
        embed = card('🗂️ Event statuses', text or 'No statuses configured.')
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command()
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def notionstatus(self, ctx):
        """Show connection readiness without exposing credentials."""
        connected = bool(self.settings.webhook_secret or await self.state.setting('webhook_secret'))
        if not self.allowed(ctx):
            return
        self.private_channel()
        embed = card('🔌 Notion connection')
        embed.add_field(name='Event queries', value='Ready' if self.initialized else 'Starting up')
        embed.add_field(name='Ask', value='Configured' if self.settings.groq_key else 'Not configured')
        embed.add_field(name='Status notifications', value='Verification token received — confirm subscription in Notion' if connected else 'Waiting for webhook setup', inline=False)
        embed.set_footer(text='A received token does not confirm that the subscription is enabled in Notion.')
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def health(self, request):
        return web.json_response({'ready': self.initialized}, status=200 if self.initialized else 503)

    async def artwork(self, request):
        name = request.match_info['name']
        if name not in ART_FILES:
            raise web.HTTPNotFound()
        path = ASSET_DIR / name
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Cache-Control': 'public, max-age=86400', 'X-Content-Type-Options': 'nosniff'})

    async def webhook(self, request):
        raw = await request.read()
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return web.Response(status=400)
        if not isinstance(event, dict):
            return web.Response(status=400)
        try:
            secret = self.settings.webhook_secret or await self.state.setting('webhook_secret')
        except IntegrationError:
            return web.Response(status=503)
        # The unguessable URL protects the one-time Notion verification POST.
        # Store its token privately; never print it or include it in responses.
        token = event.get('verification_token')
        if token is not None:
            if not isinstance(token, str) or not 16 <= len(token) <= 256:
                return web.Response(status=400)
            if secret and not hmac.compare_digest(secret, token):
                return web.Response(status=403)
            await self.state.set_setting('webhook_secret', token)
            return web.Response(status=200)
        if not secret:
            return web.Response(status=503)
        signature = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get('X-Notion-Signature', ''), signature):
            return web.Response(status=401)
        if event.get('type') not in ('page.properties_updated', 'page.created'):
            return web.Response(status=200)
        try:
            event['id'] = notion_id(event['id'])
            entity = event['entity']
            if entity.get('type') != 'page':
                return web.Response(status=400)
            entity['id'] = notion_id(entity['id'])
        except (ValueError, KeyError, TypeError, AttributeError):
            return web.Response(status=400)
        # Acknowledge only after the job is committed. Work survives a restart.
        try:
            await self.state.enqueue(event)
        except (IntegrationError, aiohttp.ClientError, asyncio.TimeoutError):
            return web.Response(status=503)
        self.wake.set()
        return web.Response(status=200)

    async def initialize(self):
        self.private_channel()
        events = await self.client.list_events()
        if await self.state.setting('initialized'):
            statuses = await self.state.all_statuses([event.id for event in events])
            # Reconcile downtime changes without re-announcing unchanged pages.
            for event in events:
                previous = statuses.get(event.id)
                if previous is not None and previous != event.status:
                    await self.state.enqueue({'id': str(uuid4()), 'entity': {'type': 'page', 'id': event.id}})
        await self.state.baseline(events)
        self.initialized = True

    async def deliver(self, job):
        if job['payload'].get('kind') == 'digest':
            return await self.deliver_digest(job)
        event = await self.client.get_event(job['payload']['entity']['id'])
        if event is None:
            await self.state.finish(job['id'])
            return
        previous = await self.state.status(event.id)
        if previous is None or previous == event.status:
            await self.state.finish(job['id'], event)
            return
        channel = self.private_channel()
        marker = 'Notion update ' + job['id']
        if job['state'] == 'sending':
            # Recover a send whose following database commit was interrupted.
            # No send if history is unavailable: retry instead of risking a ping.
            async for message in channel.history(limit=100):
                if message.author.id == self.bot.user.id and any(embed.footer.text == marker for embed in message.embeds):
                    delivered_status = job['payload'].get('_delivery_status', event.status)
                    await self.state.finish(job['id'], replace(event, status=delivered_status))
                    if delivered_status != event.status:
                        await self.state.enqueue({'id': str(uuid4()), 'entity': {'type': 'page', 'id': event.id}})
                    return
        embed = card('🔔 Event plan updated', tone='mint' if event.status.casefold() == 'event complete' else 'blue', art='status')
        embed.url = event.url
        embed.description = f'**[{clean(event.title)}]({event.url})**\n{status_label(clean(previous))} → **{status_label(clean(event.status))}**'
        embed.add_field(name='Date', value=event_date(event.date))
        embed.add_field(name='Location', value=clean(event.location or 'Not set'))
        embed.set_footer(text=marker)
        await self.state.sending(job, event.status)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await self.state.finish(job['id'], event)

    async def work(self):
        await self.bot.wait_until_ready()
        while True:
            job = None
            try:
                self.wake.clear()
                if not await self.state.lease():
                    await asyncio.sleep(30)
                    continue
                if not self.initialized:
                    await self.initialize()
                await self.queue_digest()
                await self.sync_completions()
                job = await self.state.pending()
                if job:
                    await self.deliver(job)
            except Exception as exc:
                # Avoid dumping API payloads, private event details, or keys.
                log.warning('Notion background processing failed: %s', str(exc) if isinstance(exc, IntegrationError) else type(exc).__name__)
                if job:
                    try:
                        await self.state.retry(job)
                    except Exception:
                        pass
                await asyncio.sleep(30)
                continue
            if not job:
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=self.schedule_wait())
                except asyncio.TimeoutError:
                    pass


async def install_notion(bot):
    try:
        settings = NotionSettings.from_env()
    except IntegrationError as exc:
        log.warning('Notion disabled: %s', exc)
        return
    if not settings.enabled:
        log.info('Notion integration disabled (NOTION_ENABLED is not true)')
        return
    try:
        await bot.add_cog(NotionCog(bot, settings))
    except Exception as exc:
        log.warning('Notion could not start: %s. Existing bot features remain available.',
                    str(exc) if isinstance(exc, IntegrationError) else type(exc).__name__)

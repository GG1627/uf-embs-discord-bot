import copy
import hashlib
import hmac
import json
import unittest
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import discord

from bot.notion import NotionCog, NotionSettings, event_embeds, event_date
from discord.ext import commands
from bot.notion_client import Event, IntegrationError, NotionClient, plan_question, validate_plan, week_range
from bot.notion_state import NotionState


PAGE = '00000000-0000-4000-8000-000000000001'
SOURCE = '00000000-0000-4000-8000-000000000002'
JOB = '00000000-0000-4000-8000-000000000003'
EVENT = Event(PAGE, 'Workshop @everyone', 'Planning', '2026-11-02', 'Room 101')


class MemoryState:
    def __init__(self):
        self.settings = {}
        self.statuses = {}
        self.jobs = {}
        self.fail_finish = False

    async def setting(self, key): return self.settings.get(key)
    async def set_setting(self, key, value): self.settings[key] = value
    async def status(self, page): return self.statuses.get(page)
    async def all_statuses(self, page_ids): return dict(self.statuses)
    async def baseline(self, events):
        for event in events:
            self.statuses.setdefault(event.id, event.status)
        self.settings['initialized'] = 'yes'
    async def enqueue(self, event):
        self.jobs.setdefault(event['id'], {'id': event['id'], 'key': 'job:' + event['id'],
                                          'payload': copy.deepcopy(event), 'state': 'queued', 'attempts': 0})
    async def sending(self, job, status):
        self.jobs[job['id']]['state'] = 'sending'
        self.jobs[job['id']]['payload']['_delivery_status'] = status
    async def finish(self, job_id, event=None):
        if self.fail_finish:
            raise RuntimeError('storage unavailable')
        if event:
            self.statuses[event.id] = event.status
        self.jobs[job_id]['state'] = 'done'


def cog():
    settings = NotionSettings(enabled=True, environment='prod', guild_id=10, channel_id=20,
                              officer_role_ids=(30, 31), data_source_id=SOURCE)
    bot = SimpleNamespace(user=SimpleNamespace(id=99))
    result = NotionCog(bot, settings)
    result.state = MemoryState()
    result.client = SimpleNamespace(get_event=AsyncMock(return_value=EVENT), list_events=AsyncMock(return_value=[EVENT]))
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 20
    channel.guild = SimpleNamespace(id=10, default_role=object(), get_role=lambda _: object())
    channel.permissions_for.return_value = SimpleNamespace(view_channel=False)
    channel.send = AsyncMock()
    bot.get_channel = lambda _: channel
    return result, channel


class MappingTests(unittest.TestCase):
    def test_readable_dates_handle_date_only_and_eastern_daylight_saving(self):
        self.assertEqual(event_date('2026-10-28T18:00:00.000-04:00'),
                         'Wednesday, October 28, 2026 • 6:00 PM EDT')
        self.assertEqual(event_date('2026-11-03T23:30:00Z'),
                         'Tuesday, November 3, 2026 • 6:30 PM EST')
        self.assertEqual(event_date('2026-10-28'), 'Wednesday, October 28, 2026')
        self.assertEqual(event_date(''), 'Date not set')
        self.assertEqual(event_date('broken'), 'Date unavailable')

    def test_simple_periods_and_old_alias(self):
        today = date(2026, 10, 28)
        self.assertEqual(week_range('today', today), (today, date(2026, 10, 29)))
        self.assertEqual(week_range('upcoming', today), week_range('fortnight', today))
        self.assertEqual(week_range('month', today), (today, date(2026, 11, 1)))
        self.assertEqual(week_range('month', date(2026, 12, 31)),
                         (date(2026, 12, 31), date(2027, 1, 1)))

    def test_week_ranges_are_calendar_weeks_in_eastern_time(self):
        today = date(2026, 10, 28)
        self.assertEqual(week_range('week', today), (today, date(2026, 11, 2)))
        self.assertEqual(week_range('nextweek', today), (date(2026, 11, 2), date(2026, 11, 9)))
        self.assertEqual(week_range('fortnight', today), (today, date(2026, 11, 9)))

    def test_model_arguments_are_bounded_and_allowlisted(self):
        today = date(2026, 10, 28)
        good = {'start_date': '2026-10-28', 'end_date': '2026-11-09'}
        self.assertEqual(validate_plan(json.dumps(good), today), (today, date(2026, 11, 9), None))
        for bad in [{**good, 'command': 'delete'}, {**good, 'end_date': '2026-10-28'},
                    {**good, 'end_date': '2027-06-01'}, {**good, 'status': ['Planning']},
                    {**good, 'status': ''}, [], None]:
            with self.subTest(bad=bad), self.assertRaises(IntegrationError):
                validate_plan(json.dumps(bad), today)

    def test_page_mapping_only_accepts_configured_source(self):
        client = NotionClient(None, NotionSettings(data_source_id=SOURCE))
        page = {'id': PAGE, 'parent': {'type': 'data_source_id', 'data_source_id': SOURCE}, 'properties': {
            'Name': {'type': 'title', 'title': [{'plain_text': 'Workshop'}]},
            'Status': {'type': 'status', 'status': {'name': 'Planning'}},
            'Date': {'type': 'date', 'date': None}}}
        self.assertEqual(client.event(page).date, '')
        page['parent']['data_source_id'] = PAGE
        self.assertIsNone(client.event(page))
        page['parent']['data_source_id'] = SOURCE
        page['properties']['Status'] = {'type': 'select', 'select': {'name': 'Planning'}}
        with self.assertRaises(IntegrationError):
            client.event(page)

    def test_production_requires_roles(self):
        env = {'NOTION_ENABLED': 'true', 'NOTION_PROD_GUILD_ID': '10', 'NOTION_PROD_CHANNEL_ID': '20',
               'NOTION_PROD_OFFICER_ROLE_IDS': ''}
        with patch.dict('os.environ', env, clear=True), patch('bot.notion.BOT_ENV', 'prod'):
            with self.assertRaises(IntegrationError):
                NotionSettings.from_env()

    def test_role_lists_are_scoped_and_single_role_setting_is_supported(self):
        env = {'NOTION_PROD_OFFICER_ROLE_IDS': '30,31,30', 'NOTION_DEV_OFFICER_ROLE_ID': '40'}
        with patch.dict('os.environ', env, clear=True):
            with patch('bot.notion.BOT_ENV', 'prod'):
                self.assertEqual(NotionSettings.from_env().officer_role_ids, (30, 31))
            with patch('bot.notion.BOT_ENV', 'dev'):
                self.assertEqual(NotionSettings.from_env().officer_role_ids, (40,))

    def test_invalid_role_list_fails_closed(self):
        for value in ('30,not-a-role', '-1', '0'):
            with self.subTest(value=value), patch.dict('os.environ', {'NOTION_DEV_OFFICER_ROLE_IDS': value}, clear=True):
                with patch('bot.notion.BOT_ENV', 'dev'), self.assertRaises(IntegrationError):
                    NotionSettings.from_env()

    def test_discord_output_escapes_mentions_and_bounds_result_count(self):
        embeds = event_embeds([EVENT] * 80, 'Upcoming events')
        self.assertEqual(len(embeds), 3)
        self.assertEqual(sum(len(e.fields) for e in embeds), 30)
        self.assertNotIn('@everyone', embeds[0].fields[0].name)
        huge = replace(EVENT, title='x' * 2000, status='x' * 2000, location='x' * 2000)
        self.assertTrue(all(len(embed) < 6000 for embed in event_embeds([huge] * 30, 'Upcoming events')))


class AccessAndDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_friendly_commands_and_legacy_aliases_register_together(self):
        bot = commands.Bot(command_prefix='!', intents=discord.Intents.none())
        try:
            await bot.add_cog(NotionCog(bot, NotionSettings()))
            self.assertIs(bot.get_command('notion'), bot.get_command('notionevents'))
            self.assertIs(bot.get_command('ask'), bot.get_command('notionask'))
            self.assertIsNone(bot.get_command('notionhelp'))
            for name in ('intro', 'notionstatuses', 'notionstatus'):
                self.assertIsNotNone(bot.get_command(name))
                self.assertIsInstance(bot.get_command(name).cog, NotionCog)
        finally:
            await bot.close()

    async def test_help_is_specific_to_notion_and_has_simple_examples(self):
        integration, _ = cog()
        integration.allowed = MagicMock(return_value=True)
        ctx = SimpleNamespace(send=AsyncMock())
        await integration.send_notion_help(ctx)
        text = ctx.send.call_args.args[0]
        self.assertIn('!notion week', text)
        self.assertIn('!ask ', text)
        self.assertNotIn('fortnight', text)
        self.assertLessEqual(len(text), 2000)
        self.assertNotIn('embed', ctx.send.call_args.kwargs)
        self.assertNotIn('embeds', ctx.send.call_args.kwargs)
        self.assertNotIn('!notionhelp', text)
        self.assertNotIn('SUPABASE_EVENT_UUID', text)
        self.assertNotIn('!notionstatus`', text)

    async def test_server_channel_and_officer_role_are_all_required(self):
        integration, channel = cog()
        ctx = SimpleNamespace(guild=SimpleNamespace(id=10), channel=channel,
                              author=SimpleNamespace(bot=False, roles=[SimpleNamespace(id=30)]))
        self.assertTrue(await integration.cog_check(ctx))
        second_role = SimpleNamespace(guild=ctx.guild, channel=channel,
                                      author=SimpleNamespace(bot=False, roles=[SimpleNamespace(id=31)]))
        self.assertTrue(await integration.cog_check(second_role))
        for overrides in [{'guild': None}, {'guild': SimpleNamespace(id=11)},
                          {'channel': SimpleNamespace(id=21)},
                          {'author': SimpleNamespace(bot=False, roles=[])},
                          {'author': SimpleNamespace(bot=True, roles=[SimpleNamespace(id=30)])}]:
            altered = SimpleNamespace(**{**vars(ctx), **overrides})
            self.assertFalse(await integration.cog_check(altered))
        channel.permissions_for.return_value.view_channel = True
        with self.assertRaises(IntegrationError):
            await integration.cog_check(ctx)
        integration.client.list_events.assert_not_awaited()

    async def test_dev_general_channel_without_roles_is_owner_only(self):
        integration, channel = cog()
        integration.settings = replace(integration.settings, environment='dev', officer_role_ids=())
        channel.permissions_for.return_value.view_channel = True
        ctx = SimpleNamespace(guild=SimpleNamespace(id=10, owner_id=42), channel=channel,
                              author=SimpleNamespace(id=42, bot=False, roles=[]))
        self.assertTrue(await integration.cog_check(ctx))
        ctx.author.id = 43
        self.assertFalse(await integration.cog_check(ctx))
        ctx.author.id = 42
        ctx.guild.id = 11
        self.assertFalse(await integration.cog_check(ctx))

    async def test_production_owner_and_administrator_do_not_bypass_roles(self):
        integration, channel = cog()
        ctx = SimpleNamespace(guild=SimpleNamespace(id=10, owner_id=42), channel=channel,
                              author=SimpleNamespace(id=42, bot=False, roles=[],
                                                     guild_permissions=SimpleNamespace(administrator=True)))
        self.assertFalse(await integration.cog_check(ctx))

    async def test_production_blocks_public_channels_even_for_allowed_roles(self):
        integration, channel = cog()
        channel.permissions_for.return_value.view_channel = True
        ctx = SimpleNamespace(guild=SimpleNamespace(id=10), channel=channel,
                              author=SimpleNamespace(bot=False, roles=[SimpleNamespace(id=30)]))
        with self.assertRaises(IntegrationError):
            await integration.cog_check(ctx)

    async def test_first_baseline_is_silent_and_restart_preserves_status(self):
        integration, channel = cog()
        await integration.initialize()
        self.assertEqual(integration.state.statuses[PAGE], 'Planning')
        self.assertEqual(integration.state.jobs, {})
        integration.client.list_events.return_value = [replace(EVENT, status='Confirmed')]
        await integration.initialize()
        self.assertEqual(integration.state.statuses[PAGE], 'Planning')
        self.assertEqual(len(integration.state.jobs), 1)
        channel.send.assert_not_awaited()

    async def test_only_status_changes_send_and_repeat_webhooks_do_not_replay(self):
        integration, channel = cog()
        integration.state.statuses[PAGE] = 'Draft'
        event = {'id': JOB, 'entity': {'id': PAGE}}
        await integration.state.enqueue(event)
        await integration.deliver(copy.deepcopy(integration.state.jobs[JOB]))
        await integration.state.enqueue(event)
        await integration.deliver(copy.deepcopy(integration.state.jobs[JOB]))
        channel.send.assert_awaited_once()
        self.assertEqual(integration.state.statuses[PAGE], 'Planning')
        self.assertFalse(channel.send.call_args.kwargs['allowed_mentions'].everyone)

    async def test_unknown_page_is_baselined_without_notification(self):
        integration, channel = cog()
        await integration.state.enqueue({'id': JOB, 'entity': {'id': PAGE}})
        await integration.deliver(copy.deepcopy(integration.state.jobs[JOB]))
        channel.send.assert_not_awaited()
        self.assertEqual(integration.state.statuses[PAGE], 'Planning')

    async def test_recovery_after_send_does_not_repeat_or_consume_a_newer_status(self):
        integration, channel = cog()
        integration.state.statuses[PAGE] = 'Draft'
        await integration.state.enqueue({'id': JOB, 'entity': {'id': PAGE}})
        integration.state.fail_finish = True
        with self.assertRaises(RuntimeError):
            await integration.deliver(copy.deepcopy(integration.state.jobs[JOB]))
        sent_embed = channel.send.call_args.kwargs['embed']
        async def history(**kwargs):
            yield SimpleNamespace(author=integration.bot.user, embeds=[sent_embed])
        channel.history = history
        integration.client.get_event.return_value = replace(EVENT, status='Complete')
        integration.state.fail_finish = False
        await integration.deliver(copy.deepcopy(integration.state.jobs[JOB]))
        channel.send.assert_awaited_once()
        self.assertEqual(integration.state.statuses[PAGE], 'Planning')
        self.assertEqual(len(integration.state.jobs), 2)

    async def test_storage_failure_before_sending_never_posts(self):
        integration, channel = cog()
        integration.state.statuses[PAGE] = 'Draft'
        await integration.state.enqueue({'id': JOB, 'entity': {'id': PAGE}})
        integration.state.sending = AsyncMock(side_effect=RuntimeError('down'))
        with self.assertRaises(RuntimeError):
            await integration.deliver(copy.deepcopy(integration.state.jobs[JOB]))
        channel.send.assert_not_awaited()


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.integration, self.channel = cog()
        self.integration.settings = replace(self.integration.settings, webhook_secret='test-signing-secret')
        app = web.Application()
        app.router.add_post('/hook', self.integration.webhook)
        self.http = TestClient(TestServer(app))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)

    async def post(self, event, valid=True):
        raw = json.dumps(event).encode()
        signature = 'sha256=' + hmac.new(b'test-signing-secret', raw, hashlib.sha256).hexdigest()
        return await self.http.post('/hook', data=raw, headers={'X-Notion-Signature': signature if valid else 'bad'})

    async def test_signed_webhook_is_deduplicated_and_never_sends_in_request(self):
        event = {'id': JOB, 'type': 'page.properties_updated', 'entity': {'id': PAGE, 'type': 'page'}}
        self.assertEqual((await self.post(event)).status, 200)
        self.assertEqual((await self.post(event)).status, 200)
        self.assertEqual(len(self.integration.state.jobs), 1)
        self.channel.send.assert_not_awaited()

    async def test_bad_signature_is_rejected_before_enqueue(self):
        self.assertEqual((await self.post({'id': JOB}, valid=False)).status, 401)
        self.assertEqual(self.integration.state.jobs, {})

    async def test_storage_failure_is_not_acknowledged(self):
        self.integration.state.enqueue = AsyncMock(side_effect=IntegrationError('database down'))
        response = await self.post({'id': JOB, 'type': 'page.created', 'entity': {'id': PAGE, 'type': 'page'}})
        self.assertEqual(response.status, 503)

    async def test_verification_token_is_stored_but_never_returned(self):
        self.integration.settings = replace(self.integration.settings, webhook_secret='')
        token = 'secret-verification-token'
        response = await self.http.post('/hook', json={'verification_token': token})
        self.assertEqual(response.status, 200)
        self.assertNotIn(token, await response.text())
        self.assertEqual(await self.integration.state.setting('webhook_secret'), token)
        response = await self.http.post('/hook', json={'verification_token': 'another-token-1234567890'})
        self.assertEqual(response.status, 403)


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoping_dedup_retry_and_lease_filters(self):
        # Inspect requests at an actual HTTP boundary; no production credentials.
        calls = []
        async def endpoint(request):
            body = await request.json() if request.can_read_body else None
            calls.append((request.method, dict(request.query), body, request.headers.get('Prefer', '')))
            return web.json_response([])
        app = web.Application()
        app.router.add_route('*', '/rest/v1/notion_bot_state', endpoint)
        async with TestServer(app) as server, aiohttp.ClientSession() as session:
            state = NotionState(session, str(server.make_url('/')).rstrip('/'), 'test-key', 'dev:10:20:source')
            await state.enqueue({'id': JOB})
            await state.retry({'key': 'job:' + JOB, 'attempts': 0, 'state': 'queued'})
            await state.lease()
        inserts = [call for call in calls if call[0] == 'POST']
        self.assertEqual(inserts[0][2]['scope'], 'dev:10:20:source')
        self.assertIn('ignore-duplicates', inserts[0][3])
        retry = next(call for call in calls if call[0] == 'PATCH' and call[1]['key'].startswith('eq.job:'))
        self.assertNotIn('state', retry[2])  # Preserve a possibly successful sending state.
        for method, params, body, prefer in calls:
            if method != 'POST':
                self.assertEqual(params['scope'], 'eq.dev:10:20:source')
        lease = calls[-1]
        self.assertIn('ready_at.lt.', lease[1]['or'])
        self.assertIn('payload->>owner.eq.', lease[1]['or'])

    async def test_model_is_forced_to_read_only_tool_and_receives_no_event_records(self):
        captured = []
        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self):
                return {'choices': [{'message': {'tool_calls': [{'function': {
                    'name': 'list_events', 'arguments': '{"start_date":"2026-10-28","end_date":"2026-11-09"}'}}]}}]}
        class Session:
            def post(self, url, **kwargs):
                captured.append(kwargs['json'])
                return Response()
        settings = NotionSettings(groq_key='test-key')
        plan = await plan_question(Session(), settings, 'events this week and next week', date(2026, 10, 28))
        self.assertEqual(plan[1], date(2026, 11, 9))
        self.assertEqual(captured[0]['tool_choice']['function']['name'], 'list_events')
        self.assertNotIn(EVENT.title, json.dumps(captured))

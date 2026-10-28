import copy
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ext import commands

from bot.notion import NotionCog, NotionSettings
from bot.notion_client import IntegrationError
from bot.notion_workflows import (ConfirmAction, EventPicker, FieldPicker, ValuePicker, EditModal,
                                  EASTERN, completed_event, digest_window, find_events,
                                  form_date, planning_events, update_property)
from test_notion import EVENT, PAGE, JOB, cog


SCHEMA = {'Status': {'type': 'status', 'status': {'options': [{'name': 'Planning'}, {'name': 'Event Complete'}]}},
          'Priority': {'type': 'select', 'select': {'options': [{'name': 'High'}, {'name': 'Low'}]}},
          'Location': {'type': 'rich_text'}, 'Date': {'type': 'date'}, 'Name': {'type': 'title'}}


class PlanningTests(unittest.TestCase):
    def test_date_form_accepts_familiar_dates_and_handles_eastern_offsets(self):
        self.assertEqual(form_date('November 3, 2026', '6:30 PM'), '2026-11-03T18:30:00-05:00')
        self.assertEqual(form_date('10/28/2026', '18:00'), '2026-10-28T18:00:00-04:00')
        self.assertEqual(form_date('Nov 3, 2026'), '2026-11-03')
        self.assertEqual(form_date(''), '')
        for day, clock in [('', '6 PM'), ('tomorrow', ''), ('November 1, 2026', '1:30 AM'),
                           ('March 8, 2026', '2:30 AM')]:
            with self.subTest(day=day, clock=clock), self.assertRaises(IntegrationError):
                form_date(day, clock)

    def test_digest_is_sunday_ten_eastern_across_dst(self):
        self.assertIsNone(digest_window(datetime(2026, 10, 25, 9, 59, tzinfo=EASTERN)))
        self.assertEqual(digest_window(datetime(2026, 10, 25, 14, tzinfo=timezone.utc)),
                         (date(2026, 10, 26), date(2026, 11, 2)))
        self.assertEqual(digest_window(datetime(2026, 11, 1, 15, tzinfo=timezone.utc)),
                         (date(2026, 11, 2), date(2026, 11, 9)))
        self.assertIsNone(digest_window(datetime(2026, 11, 2, 15, tzinfo=timezone.utc)))

    def test_recurring_names_are_not_silently_resolved(self):
        other = replace(EVENT, id=JOB, date='2026-11-09')
        self.assertEqual(len(find_events([EVENT, other], EVENT.title)), 2)
        self.assertEqual(find_events([EVENT, other], EVENT.title, '2026-11-09'), [other])
        self.assertEqual(find_events([EVENT, other], PAGE), [EVENT])

    def test_status_and_priority_are_existing_options_only(self):
        settings = NotionSettings()
        name, canonical, body = update_property(settings, SCHEMA, 'status', 'planning')
        self.assertEqual(canonical, 'Planning')
        self.assertEqual(body, {'Status': {'status': {'name': 'Planning'}}})
        for field, value in [('status', 'Invented'), ('priority', 'Urgent'), ('arbitrary', 'x')]:
            with self.subTest(field=field), self.assertRaises(IntegrationError):
                update_property(settings, SCHEMA, field, value)

    def test_dates_require_offsets_and_clear_is_explicit(self):
        s = NotionSettings()
        self.assertEqual(update_property(s, SCHEMA, 'date', '')[2], {'Date': {'date': None}})
        self.assertEqual(update_property(s, SCHEMA, 'location', '')[2], {'Location': {'rich_text': []}})
        for value in ('next Tuesday', '2026-11-03T18:00:00', '2026-02-30'):
            with self.subTest(value=value), self.assertRaises(IntegrationError):
                update_property(s, SCHEMA, 'date', value)

    def test_checks_use_planning_columns_and_ignore_complete_events(self):
        missing = replace(EVENT, location='', post_deadline='2026-10-27')
        done = replace(missing, id=JOB, status='Event Complete')
        reimburse = replace(EVENT, id='reimburse', date='2026-01-01', status='Reimbursement Needed')
        events = [missing, done, reimburse]
        today = date(2026, 10, 28)
        self.assertEqual(planning_events(events, 'missingrooms', today), [missing])
        self.assertEqual(planning_events(events, 'deadlines', today), [missing])
        self.assertEqual(planning_events(events, 'reimbursements', today), [reimburse])

    def test_completion_requires_a_known_end_and_excludes_cancellations(self):
        now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        ended = {'end_time': '2026-10-28T11:00:00Z'}
        self.assertTrue(completed_event(ended, now))
        for event in ({}, {**ended, 'status': 'cancelled'}, {**ended, 'cancelled': True},
                      {'end_time': '2026-10-28T13:00:00Z'}, {'end_time': '2026-10-28T11:00:00'}):
            self.assertFalse(completed_event(event, now))


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixin_commands_register_and_have_the_same_access_boundary(self):
        bot = commands.Bot(command_prefix='!', intents=discord.Intents.none())
        try:
            await bot.add_cog(NotionCog(bot, NotionSettings()))
            for name in ('notionedit', 'notionupdate', 'notionlazy', 'notionmissingrooms', 'notiondeadlines',
                         'notionreimbursements', 'notionevent', 'notiondigest', 'notionlink'):
                self.assertIsInstance(bot.get_command(name).cog, NotionCog)
        finally:
            await bot.close()

    def setup_edit(self):
        integration, channel = cog()
        integration.client.schema = AsyncMock(return_value=SCHEMA)
        integration.client.update_event = AsyncMock(return_value=EVENT)
        integration.state.put = AsyncMock()
        ctx = SimpleNamespace(author=SimpleNamespace(id=7), send=AsyncMock())
        integration.allowed = Mock(return_value=True)
        click = SimpleNamespace(user=SimpleNamespace(id=7), response=SimpleNamespace(
            edit_message=AsyncMock(), send_message=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        return integration, ctx, click

    async def test_guided_editor_lists_actual_fields_and_current_values_without_ai(self):
        integration, ctx, _ = self.setup_edit()
        await integration.notionedit.callback(integration, ctx, query=EVENT.title)
        view = ctx.send.call_args.kwargs['view']
        self.assertIsInstance(view, FieldPicker)
        status = next(option for option in view.menu.options if option.value == 'status')
        self.assertIn('Planning', status.description)
        integration.client.update_event.assert_not_awaited()

    async def test_guided_editor_without_name_opens_event_selection(self):
        integration, ctx, _ = self.setup_edit()
        integration.client.list_events.return_value = [EVENT, replace(EVENT, id=JOB, date='2026-11-09')]
        await integration.notionedit.callback(integration, ctx)
        self.assertIsInstance(ctx.send.call_args.kwargs['view'], EventPicker)
        integration.client.update_event.assert_not_awaited()

    async def test_status_picker_marks_current_and_paginates_all_live_options(self):
        integration, ctx, _ = self.setup_edit()
        schema = copy.deepcopy(SCHEMA)
        schema['Status']['status']['options'] = [{'name': 'Planning'}] + [{'name': f'Option {i}'} for i in range(29)]
        view = ValuePicker(integration, ctx, EVENT, 'status', schema)
        self.assertEqual(len(view.menu.options), 25)
        self.assertTrue(view.menu.options[0].default)
        self.assertEqual(view.menu.options[0].description, 'Current value')
        self.assertTrue(view.previous.disabled)
        self.assertFalse(view.next.disabled)
        view.page = 1
        view.refresh()
        self.assertEqual(len(view.menu.options), 5)
        self.assertTrue(view.next.disabled)

    async def test_text_form_is_prefilled_and_shows_confirmation_before_any_write(self):
        integration, ctx, _ = self.setup_edit()
        owner = FieldPicker(integration, ctx, EVENT, SCHEMA)
        owner.interaction_check = AsyncMock(return_value=True)
        modal = EditModal(owner, ctx, EVENT, 'location', SCHEMA)
        self.assertEqual(modal.text.default, 'Room 101')
        modal.text._value = 'Room 201'
        click = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        await modal.on_submit(click)
        self.assertIsInstance(click.followup.send.call_args.kwargs['view'], ConfirmAction)
        integration.client.update_event.assert_not_awaited()

    async def test_date_form_prefills_a_readable_date(self):
        integration, ctx, _ = self.setup_edit()
        owner = FieldPicker(integration, ctx, EVENT, SCHEMA)
        modal = EditModal(owner, ctx, replace(EVENT, date='2026-11-03T18:30:00-05:00'), 'date', SCHEMA)
        self.assertEqual(modal.day.default, 'November 3, 2026')
        self.assertEqual(modal.clock.default, '6:30 PM')

    async def test_revoked_access_blocks_modal_submission(self):
        integration, ctx, _ = self.setup_edit()
        owner = FieldPicker(integration, ctx, EVENT, SCHEMA)
        owner.interaction_check = AsyncMock(return_value=False)
        modal = EditModal(owner, ctx, EVENT, 'location', SCHEMA)
        click = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        await modal.on_submit(click)
        click.followup.send.assert_not_awaited()
        integration.client.update_event.assert_not_awaited()

    async def test_update_preview_does_not_write_until_confirm_and_only_writes_once(self):
        integration, ctx, click = self.setup_edit()
        await integration.preview_edit(ctx, EVENT, 'status', 'Event Complete', SCHEMA)
        integration.client.update_event.assert_not_awaited()
        view = ctx.send.call_args.kwargs['view']
        view.interaction_check = AsyncMock(return_value=True)
        await view.confirm.callback(click)
        await view.confirm.callback(click)
        integration.client.update_event.assert_awaited_once_with(PAGE, {'Status': {'status': {'name': 'Event Complete'}}})
        self.assertEqual(integration.state.put.call_args.args[2]['outcome'], 'applied')

    async def test_cancel_never_writes(self):
        integration, ctx, click = self.setup_edit()
        await integration.preview_edit(ctx, EVENT, 'status', 'Event Complete', SCHEMA)
        view = ctx.send.call_args.kwargs['view']
        await view.cancel.callback(click)
        integration.client.update_event.assert_not_awaited()
        self.assertTrue(view.finished)

    async def test_audit_failure_before_write_blocks_update(self):
        integration, ctx, click = self.setup_edit()
        await integration.preview_edit(ctx, EVENT, 'status', 'Event Complete', SCHEMA)
        view = ctx.send.call_args.kwargs['view']
        view.interaction_check = AsyncMock(return_value=True)
        integration.state.put.side_effect = IntegrationError('tracking unavailable')
        with self.assertRaises(IntegrationError):
            await view.callback(click)
        integration.client.update_event.assert_not_awaited()

    async def test_proposed_text_is_not_truncated_out_of_confirmation(self):
        integration, ctx, click = self.setup_edit()
        schema = {**SCHEMA, 'Description': {'type': 'rich_text'}}
        await integration.preview_edit(ctx, EVENT, 'description', '*' * 1780 + 'END OF PROPOSAL', schema)
        embed = ctx.send.call_args.kwargs['embed']
        self.assertIn('END OF PROPOSAL', ''.join(f.value for f in embed.fields))
        self.assertLess(len(embed), 6000)

    async def test_stale_value_and_revoked_access_block_edits(self):
        integration, ctx, click = self.setup_edit()
        await integration.preview_edit(ctx, EVENT, 'status', 'Event Complete', SCHEMA)
        view = ctx.send.call_args.kwargs['view']
        integration.client.get_event.return_value = replace(EVENT, status='Changed by another officer')
        with self.assertRaises(IntegrationError):
            await view.callback(click)
        integration.client.update_event.assert_not_awaited()
        integration.allowed.return_value = False
        with self.assertRaises(IntegrationError):
            await view.callback(click)

    async def test_other_users_cannot_confirm(self):
        integration, channel = cog()
        ctx_guild = SimpleNamespace(id=10)
        click = SimpleNamespace(user=SimpleNamespace(id=8, bot=False, roles=[SimpleNamespace(id=30)]),
                                guild=ctx_guild, channel=channel,
                                response=SimpleNamespace(send_message=AsyncMock()))
        view = ConfirmAction(integration, 7, AsyncMock())
        self.assertFalse(await view.interaction_check(click))
        click.response.send_message.assert_awaited_once()

    async def test_digest_queue_is_stable_across_restarts(self):
        integration, _ = cog()
        integration.state.scope = 'prod:10:20:source'
        now = datetime(2026, 11, 1, 10, tzinfo=EASTERN)
        await integration.queue_digest(now)
        await integration.queue_digest(now)
        self.assertEqual(len(integration.state.jobs), 1)
        integration._digest_queued = None
        await integration.queue_digest(now)
        self.assertEqual(len(integration.state.jobs), 1)
        integration._digest_queued = None
        await integration.state.set_setting('digest:2026-11-02', 'sent')
        integration.state.jobs.clear()
        await integration.queue_digest(now)
        self.assertFalse(integration.state.jobs)

    async def test_prod_digest_mentions_only_allowed_roles_and_dev_mentions_none(self):
        for environment in ('prod', 'dev'):
            integration, channel = cog()
            integration.settings = replace(integration.settings, environment=environment)
            today = datetime.now(EASTERN).date()
            start = today + timedelta(days=7 - today.weekday())
            job = {'id': JOB, 'payload': {'id': JOB, 'kind': 'digest', 'start': start.isoformat(),
                                        'end': (start + timedelta(days=7)).isoformat()}, 'state': 'queued', 'attempts': 0}
            integration.state.jobs[JOB] = copy.deepcopy(job)
            await integration.deliver_digest(job)
            kwargs = channel.send.call_args.kwargs
            if environment == 'prod':
                self.assertEqual(kwargs['content'], '<@&30> <@&31>')
                self.assertEqual([r.id for r in kwargs['allowed_mentions'].roles], [30, 31])
            else:
                self.assertIsNone(kwargs['content'])
                self.assertEqual(kwargs['allowed_mentions'].roles, [])
            self.assertFalse(kwargs['allowed_mentions'].everyone)
            self.assertFalse(kwargs['allowed_mentions'].users)
            self.assertEqual(await integration.state.setting('digest:' + start.isoformat()), 'sent')

    async def test_digest_preview_never_pings_or_consumes_scheduled_delivery(self):
        integration, _ = cog()
        integration.allowed = Mock(return_value=True)
        ctx = SimpleNamespace(send=AsyncMock())
        await integration.notiondigest.callback(integration, ctx)
        kwargs = ctx.send.call_args.kwargs
        self.assertFalse(kwargs['allowed_mentions'].roles)
        self.assertFalse(integration.state.settings)

    async def test_digest_send_recovery_does_not_ping_roles_again(self):
        integration, channel = cog()
        today = datetime.now(EASTERN).date()
        start = today + timedelta(days=7 - today.weekday())
        job = {'id': JOB, 'payload': {'id': JOB, 'kind': 'digest', 'start': start.isoformat(),
                                    'end': (start + timedelta(days=7)).isoformat()}, 'state': 'queued', 'attempts': 0}
        integration.state.jobs[JOB] = copy.deepcopy(job)
        original = integration.state.set_setting
        integration.state.set_setting = AsyncMock(side_effect=IntegrationError('storage interrupted'))
        with self.assertRaises(IntegrationError):
            await integration.deliver_digest(job)
        integration.state.set_setting = original
        sent = channel.send.call_args.kwargs['embed']
        async def history(**kwargs):
            yield SimpleNamespace(author=integration.bot.user, embeds=[sent])
        channel.history = history
        await integration.deliver_digest(copy.deepcopy(integration.state.jobs[JOB]))
        channel.send.assert_awaited_once()
        self.assertEqual(await integration.state.setting('digest:' + start.isoformat()), 'sent')

    async def test_linked_prod_completion_updates_only_eligible_records(self):
        integration, _ = cog()
        integration.settings = replace(integration.settings, completion_sync_enabled=True)
        integration.state.request = AsyncMock(return_value=[{'payload': {'enabled': True, 'event_id': JOB, 'notion_id': PAGE}}])
        integration.state.lease = AsyncMock(return_value=True)
        integration.state.put = AsyncMock()
        integration.supabase_event = AsyncMock(return_value={'end_time': '2020-01-01T12:00:00Z'})
        integration.client.schema = AsyncMock(return_value=SCHEMA)
        integration.client.update_event = AsyncMock()
        await integration.sync_completions()
        integration.client.update_event.assert_awaited_once_with(PAGE, {'Status': {'status': {'name': 'Event Complete'}}})
        integration._next_completion_check = 0
        integration.client.get_event.return_value = replace(EVENT, status='Reimbursement Needed')
        integration.client.update_event.reset_mock()
        await integration.sync_completions()
        integration.client.update_event.assert_not_awaited()

    async def test_dev_and_disabled_completion_sync_never_query_or_write(self):
        integration, _ = cog()
        integration.supabase_event = AsyncMock()
        integration.client.update_event = AsyncMock()
        await integration.sync_completions()
        integration.settings = replace(integration.settings, environment='dev', completion_sync_enabled=True)
        await integration.sync_completions()
        integration.supabase_event.assert_not_awaited()
        integration.client.update_event.assert_not_awaited()

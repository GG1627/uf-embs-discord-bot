import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.reminders import ReminderRunner, build_reminder, reminder_due


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def event(hours=48):
    return {'id': 'test-event', 'name': 'EMBS Meeting',
            'start_time': (NOW + timedelta(hours=hours)).isoformat(),
            'description': 'Explore biomedical engineering with us.', 'location': 'Room 101'}


class Database:
    def __init__(self, events):
        self.events = events
        self.sent = []
        self.fail_insert = False
        self.fail_read = False

    def table(self, name):
        db = self

        class Query:
            record = None

            def select(self, *args): return self
            def gte(self, *args): return self
            def lte(self, *args): return self
            def in_(self, *args): return self
            def insert(self, record):
                self.record = record
                return self

            def execute(self):
                if self.record is not None:
                    if db.fail_insert:
                        raise RuntimeError('database unavailable')
                    db.sent.append(self.record)
                    return SimpleNamespace(data=[self.record])
                if db.fail_read:
                    raise RuntimeError('read unavailable')
                return SimpleNamespace(data=db.events if name == 'events' else db.sent)
        return Query()


class FormattingTests(unittest.TestCase):
    def test_requested_eastern_format_for_both_reminders(self):
        e = event()
        e.update(start_time='2026-10-07T21:00:00Z', location='Weimer Hall 1064')
        for code in ('2d', '2h'):
            text = build_reminder(e, NOW - timedelta(days=5), code)
            self.assertIn('📅 Wednesday, October 7th\n⏱️ 5:00 - 6:00 pm\n📍 Weimer Hall 1064', text)
            self.assertNotIn('<t:', text)

    def test_end_time_and_am_pm_transition(self):
        e = event()
        e.update(start_time='2026-01-21T16:30:00Z', end_time='2026-01-21T18:00:00Z')
        text = build_reminder(e, NOW, '2d')
        self.assertIn('January 21st', text)
        self.assertIn('⏱️ 11:30 am - 1:00 pm', text)

    def test_ordinal_teen_exception(self):
        e = event()
        e['start_time'] = '2026-10-11T21:00:00Z'
        self.assertIn('October 11th', build_reminder(e, NOW, '2h'))

    def test_intervals_and_catchup(self):
        for hours, expected in [(49, None), (48, '2d'), (3, '2d'), (2, '2h'), (1, '2h'), (0, None), (-1, None)]:
            with self.subTest(hours=hours):
                self.assertEqual(reminder_due(NOW + timedelta(hours=hours), NOW), expected)

    def test_description_and_short_reminder(self):
        e = event()
        full = build_reminder(e, NOW, '2d')
        self.assertIn(e['description'], full)
        self.assertNotIn('Join us for', full)
        self.assertNotIn('View event & RSVP', full)
        short = build_reminder(event(1), NOW, '2h')
        self.assertIn('about 1 hour', short)
        self.assertNotIn(e['description'], short)
        self.assertNotIn(e['id'], short)

    def test_length_and_mentions(self):
        e = event()
        e['description'] = '@everyone ' + 'x' * 5000
        text = build_reminder(e, NOW, '2d', ping=False)
        self.assertLessEqual(len(text), 2000)
        self.assertNotIn('@everyone', text)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'journal.json'
        self.runner = ReminderRunner(self.path)
        self.channel = SimpleNamespace(send=AsyncMock(), guild=SimpleNamespace(id=1))
        self.bot = SimpleNamespace(get_channel=lambda _: self.channel)

    async def test_normal_two_reminders_and_no_repeat(self):
        db = Database([event()])
        await self.runner.run_once(self.bot, db, NOW)
        await self.runner.run_once(self.bot, db, NOW)
        await self.runner.run_once(self.bot, db, NOW + timedelta(hours=46))
        await self.runner.run_once(self.bot, db, NOW + timedelta(hours=47))
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertEqual([r['reminder_type'] for r in db.sent], ['2d', '2h'])
        self.assertNotIn('embed', self.channel.send.call_args.kwargs)

    async def test_downtime_never_sends_obsolete_reminder(self):
        db = Database([event(1)])
        await self.runner.run_once(self.bot, db, NOW)
        await self.runner.run_once(self.bot, db, NOW + timedelta(minutes=5))
        self.channel.send.assert_awaited_once()
        self.assertEqual(db.sent[0]['reminder_type'], '2h')

    async def test_failed_record_survives_restart_without_repeat(self):
        db = Database([event(1)])
        db.fail_insert = True
        await self.runner.run_once(self.bot, db, NOW)
        restarted = ReminderRunner(self.path)
        await restarted.run_once(self.bot, db, NOW)
        db.fail_insert = False
        await restarted.run_once(self.bot, db, NOW)
        self.channel.send.assert_awaited_once()
        self.assertEqual(len(db.sent), 1)
        self.assertEqual(restarted.delivered, {})

    async def test_failed_send_can_retry(self):
        db = Database([event(1)])
        self.channel.send.side_effect = RuntimeError('Discord unavailable')
        await self.runner.run_once(self.bot, db, NOW)
        self.assertEqual(db.sent, [])
        self.channel.send.side_effect = None
        await self.runner.run_once(self.bot, db, NOW)
        self.assertEqual(len(db.sent), 1)

    async def test_read_failure_does_not_ping(self):
        db = Database([event(1)])
        db.fail_read = True
        with self.assertRaises(RuntimeError):
            await self.runner.run_once(self.bot, db, NOW)
        self.channel.send.assert_not_awaited()

    async def test_flyer_for_both_reminders(self):
        e = event()
        e['flyer_url'] = 'https://example.com/flyer.png'
        db = Database([e])
        with patch('bot.events._fetch_flyer', new=AsyncMock(return_value=b'image')) as fetch:
            await self.runner.run_once(self.bot, db, NOW)
            self.assertIn('file', self.channel.send.call_args.kwargs)
            await self.runner.run_once(self.bot, db, NOW + timedelta(hours=46))
            self.assertIn('file', self.channel.send.call_args.kwargs)
            self.assertEqual(fetch.await_count, 2)


if __name__ == '__main__':
    unittest.main()

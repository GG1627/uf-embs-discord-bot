import io
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot.events import setup_events


class EnvironmentTests(unittest.TestCase):
    def test_only_explicit_production_enables_reminders(self):
        config_path = Path(__file__).resolve().parents[1] / 'bot' / 'config.py'
        for value, enabled in [(None, False), ('development', False),
                               ('dev', False), ('', False), ('typo', False),
                               ('production', False), ('prod', True),
                               (' PROD ', True)]:
            with self.subTest(value=value):
                env = {'EVENT_REMINDERS_ENABLED': 'true'}
                if value is not None:
                    env['BOT_ENV'] = value
                with patch.dict('os.environ', env, clear=True), patch('dotenv.load_dotenv'):
                    config = runpy.run_path(str(config_path))
                self.assertEqual(config['REMINDERS_ENABLED'], enabled)
                if value is None:
                    self.assertEqual(config['BOT_ENV'], 'dev')

    def test_production_reminders_require_explicit_opt_in(self):
        config_path = Path(__file__).resolve().parents[1] / 'bot' / 'config.py'
        for value in (None, '', 'false', 'typo'):
            env = {'BOT_ENV': 'prod'}
            if value is not None:
                env['EVENT_REMINDERS_ENABLED'] = value
            with self.subTest(value=value), patch.dict('os.environ', env, clear=True), patch('dotenv.load_dotenv'):
                self.assertFalse(runpy.run_path(str(config_path))['REMINDERS_ENABLED'])


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def check_startup(self, enabled, database_available=True):
        bot = SimpleNamespace(
            user=SimpleNamespace(id=1),
            supabase_url='https://example.com' if database_available else None,
            supabase_key='test-key' if database_available else None,
            add_view=Mock(),
        )

        def register(handler):
            setattr(bot, handler.__name__, handler)
            return handler

        bot.event = register
        setup_events(bot)
        started = []

        def record_task(coroutine):
            started.append(coroutine.cr_code.co_name)
            coroutine.close()

        with patch('bot.events.REMINDERS_ENABLED', enabled), \
                patch('supabase.create_client', return_value=Mock()), \
                patch('bot.events.asyncio.create_task', side_effect=record_task), \
                patch('sys.stdout', new_callable=io.StringIO):
            await bot.on_ready()
            # Reconnecting must not start another copy of either task.
            await bot.on_ready()
        return started

    async def test_development_never_starts_reminders(self):
        self.assertEqual(await self.check_startup(False), ['sync_discord_scheduled_events'])

    async def test_production_starts_reminders_once(self):
        self.assertEqual(await self.check_startup(True),
                         ['check_event_reminders', 'sync_discord_scheduled_events'])

    async def test_missing_database_starts_no_background_tasks(self):
        self.assertEqual(await self.check_startup(True, database_available=False), [])


if __name__ == '__main__':
    unittest.main()

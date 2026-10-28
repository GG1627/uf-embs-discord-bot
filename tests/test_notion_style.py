import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.notion import event_embeds
from bot.notion_style import ART_FILES, STATUS_ART, ASSET_DIR, card
from bot.notion_workflows import EventPicker
from test_notion import EVENT, JOB, cog
from test_notion_workflows import SCHEMA


class StyleTests(unittest.TestCase):
    def test_cat_art_is_optional_and_uses_only_public_static_paths(self):
        with patch.dict('os.environ', {'NOTION_ASSET_BASE_URL': ''}):
            self.assertFalse(card('Status', art='status').thumbnail.url)
            self.assertFalse(card('Digest', art='digest').image.url)
        with patch.dict('os.environ', {'NOTION_ASSET_BASE_URL': 'https://example.com/'}):
            for name in STATUS_ART:
                with patch('bot.notion_style.random.choice', return_value=name) as choose:
                    self.assertEqual(card('Status', art='status').thumbnail.url,
                                     'https://example.com/notion/assets/' + name)
                    choose.assert_called_once_with(STATUS_ART)
            digest = card('Digest', art='digest')
            self.assertEqual(digest.image.url, 'https://example.com/notion/assets/cat4.jpg')
            self.assertFalse(digest.thumbnail.url)
            self.assertFalse(card('Planning', art='planning').thumbnail.url)
            self.assertFalse(card('Empty', art='empty').thumbnail.url)
            self.assertFalse(card('Review', tone='amber').thumbnail.url)
            self.assertNotIn('cat4.jpg', STATUS_ART)
            self.assertTrue(STATUS_ART)

    def test_selected_art_is_present_and_is_jpeg(self):
        for name in ART_FILES:
            with self.subTest(name=name), (ASSET_DIR / name).open('rb') as file:
                self.assertEqual(file.read(3), b'\xff\xd8\xff')

    def test_event_cards_omit_branding_and_keep_the_date_range_separate(self):
        embed = event_embeds([EVENT], 'Events · November 2, 2026 – November 8, 2026')[0]
        self.assertEqual(embed.title, '📅 Events')
        self.assertEqual(embed.description, 'November 2, 2026 – November 8, 2026')
        self.assertFalse(embed.author.name)
        self.assertNotIn('2026-11-02', embed.fields[0].value)
        self.assertFalse(embed.thumbnail.url)
        self.assertFalse(embed.image.url)


class StyleInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_picker_uses_human_dates(self):
        integration, _ = cog()
        view = EventPicker(integration, 7, [EVENT], AsyncMock())
        self.assertEqual(view.menu.options[0].description, 'Monday, November 2, 2026')

    async def test_date_confirmation_displays_readable_values(self):
        integration, _ = cog()
        integration.allowed = Mock(return_value=True)
        ctx = SimpleNamespace(author=SimpleNamespace(id=7), send=AsyncMock())
        await integration.preview_edit(ctx, EVENT, 'date', '2026-11-03T18:30:00-05:00', SCHEMA)
        embed = ctx.send.call_args.kwargs['embed']
        text = ' '.join(field.value for field in embed.fields)
        self.assertIn('November 2, 2026', text)
        self.assertIn('November 3, 2026', text)
        self.assertIn('6:30 PM EST', text)
        self.assertNotIn('2026-11-03T', text)
        self.assertFalse(embed.thumbnail.url)

    async def test_asset_endpoint_serves_images_but_not_repo_files(self):
        integration, _ = cog()
        app = web.Application()
        app.router.add_get('/notion/assets/{name}', integration.artwork)
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/notion/assets/cat1.jpg')
            self.assertEqual(response.status, 200)
            self.assertEqual(response.content_type, 'image/jpeg')
            self.assertIn('max-age=86400', response.headers['Cache-Control'])
            await response.read()
            for name in ART_FILES:
                self.assertEqual((await client.get('/notion/assets/' + name)).status, 200)
            for name in ('README.md', '.env', 'not-a-cat.png', 'help-cat.png'):
                self.assertEqual((await client.get('/notion/assets/' + name)).status, 404)

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from discord.ext import commands

from bot.help import EMBSHelpCommand
from bot.notion_client import IntegrationError


class ContextualHelpTests(unittest.IsolatedAsyncioTestCase):
    async def test_officer_help_routes_to_notion_guide(self):
        cog = SimpleNamespace(allowed=Mock(return_value=True), private_channel=Mock(), send_notion_help=AsyncMock())
        ctx = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda _: cog), invoke=AsyncMock(), send=AsyncMock())
        help_command = EMBSHelpCommand()
        help_command.context = ctx
        await help_command.send_bot_help({})
        cog.send_notion_help.assert_awaited_once_with(ctx)

    async def test_public_help_keeps_regular_bot_help(self):
        cog = SimpleNamespace(allowed=Mock(return_value=False), send_notion_help=AsyncMock())
        ctx = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda _: cog), invoke=AsyncMock())
        help_command = EMBSHelpCommand()
        help_command.context = ctx
        with patch.object(commands.DefaultHelpCommand, 'send_bot_help', new_callable=AsyncMock) as regular_help:
            await help_command.send_bot_help({})
        regular_help.assert_awaited_once()
        ctx.invoke.assert_not_awaited()

    async def test_unsafe_channel_does_not_show_officer_guide(self):
        cog = SimpleNamespace(allowed=Mock(return_value=True), private_channel=Mock(side_effect=IntegrationError('not private')),
                              send_notion_help=AsyncMock())
        ctx = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda _: cog), invoke=AsyncMock(), send=AsyncMock())
        help_command = EMBSHelpCommand()
        help_command.context = ctx
        await help_command.send_bot_help({})
        ctx.invoke.assert_not_awaited()
        ctx.send.assert_awaited_once()

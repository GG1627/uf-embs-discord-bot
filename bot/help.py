"""Contextual help: officer guide in its channel, regular bot help elsewhere."""

from discord.ext import commands

from bot.notion_client import IntegrationError


class EMBSHelpCommand(commands.DefaultHelpCommand):
    async def send_bot_help(self, mapping):
        cog = self.context.bot.get_cog('NotionCog')
        if cog is not None and cog.allowed(self.context):
            try:
                cog.private_channel()
            except IntegrationError:
                return await self.context.send('The Notion channel needs its officer permissions set up first.')
            return await cog.send_notion_help(self.context)
        return await super().send_bot_help(mapping)

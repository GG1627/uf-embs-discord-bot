"""Officer planning checks, confirmed edits, and weekly digests."""

import asyncio
import json
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands

from bot.notion_client import IntegrationError, notion_id, week_range
from bot.notion_style import card, status_label

EASTERN = ZoneInfo('America/New_York')
FIELDS = {'title': ('title_property', 'title'), 'status': ('status_property', 'status'),
          'location': ('location_property', 'rich_text'), 'description': ('description_property', 'rich_text'),
          'priority': ('priority_property', 'select'), 'event_type': ('event_type_property', 'select'),
          'date': ('date_property', 'date'), 'post_deadline': ('post_deadline_property', 'date')}


def local_date(value):
    if not value:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return moment.replace(tzinfo=EASTERN).date() if moment.tzinfo is None else moment.astimezone(EASTERN).date()
    except (ValueError, TypeError):
        return None


def digest_window(now):
    now = now.astimezone(EASTERN)
    if now.weekday() == 6 and now.hour >= 10:
        monday = now.date() + timedelta(days=1)
        return monday, monday + timedelta(days=7)
    return None


def planning_events(events, mode, today):
    active = [e for e in events if e.status.casefold() != 'event complete']
    if mode == 'missingrooms':
        return [e for e in active if local_date(e.date) is not None
                and today <= local_date(e.date) <= today + timedelta(days=30)
                and e.location.strip().casefold() in ('', 'tbd', 'not set', 'pending', 'tba')]
    if mode == 'deadlines':
        return sorted([e for e in active if local_date(e.post_deadline) is not None
                       and local_date(e.post_deadline) <= today + timedelta(days=7)
                       and (local_date(e.date) is None or local_date(e.date) >= today)],
                      key=lambda e: local_date(e.post_deadline))
    if mode == 'reimbursements':
        return [e for e in events if e.status.casefold() == 'reimbursement needed']
    raise IntegrationError('Unknown planning check.')


def find_events(events, query, on_date=None):
    query = query.strip().casefold()
    if not query:
        raise IntegrationError('Include an event name, for example !notion event Intro to Git.')
    matches = [e for e in events if query in e.title.casefold() or query == e.id.casefold()]
    if on_date:
        matches = [e for e in matches if local_date(e.date) == date.fromisoformat(on_date)]
    exact = [e for e in matches if e.title.casefold() == query or e.id.casefold() == query]
    return exact or matches


def update_property(settings, schema, field, value):
    if field not in FIELDS or not isinstance(value, str) or len(value) > 1800:
        raise IntegrationError('Choose one supported event field and a value under 1,800 characters.')
    attr, kind = FIELDS[field]
    name = getattr(settings, attr)
    if schema.get(name, {}).get('type') != kind:
        raise IntegrationError(f'The Notion field {name} must have type {kind}.')
    if kind in ('select', 'status'):
        options = schema[name][kind]['options']
        option = next((o['name'] for o in options if o['name'].casefold() == value.strip().casefold()), None)
        if option is None:
            raise IntegrationError(f'Choose an existing {name} option: ' + ', '.join(o['name'] for o in options))
        value = option
        prop = {kind: {'name': value}}
    elif kind == 'date':
        if value:
            try:
                if len(value) == 10:
                    date.fromisoformat(value)
                else:
                    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    if moment.tzinfo is None:
                        raise ValueError
            except ValueError:
                raise IntegrationError('Dates must be YYYY-MM-DD or a date/time with a timezone. Please clarify the date.') from None
        prop = {'date': {'start': value} if value else None}
    else:
        if kind == 'title' and not value.strip():
            raise IntegrationError('An event name cannot be empty.')
        prop = {kind: [{'type': 'text', 'text': {'content': value}}] if value else []}
    return name, value, {name: prop}


def form_date(day_text, time_text=''):
    day_text, time_text = day_text.strip(), time_text.strip()
    if not day_text:
        if time_text:
            raise IntegrationError('Enter a date as well as a time.')
        return ''
    day = None
    for pattern in ('%Y-%m-%d', '%B %d, %Y', '%b %d, %Y', '%m/%d/%Y'):
        try:
            day = datetime.strptime(day_text, pattern).date()
            break
        except ValueError:
            pass
    if day is None:
        raise IntegrationError('Enter a date like November 3, 2026 or 11/03/2026.')
    if not time_text:
        return day.isoformat()
    clock = None
    for pattern in ('%I:%M %p', '%I %p', '%H:%M'):
        try:
            clock = datetime.strptime(time_text.upper(), pattern).time()
            break
        except ValueError:
            pass
    if clock is None:
        raise IntegrationError('Enter a time like 6:30 PM, or leave it blank for a date only.')
    moment = datetime.combine(day, clock, EASTERN)
    if moment.replace(fold=0).utcoffset() != moment.replace(fold=1).utcoffset():
        raise IntegrationError('That time is affected by a daylight-saving clock change. Choose another time or edit it directly in Notion.')
    return moment.isoformat()


async def propose_update(session, settings, question, schema):
    if not settings.groq_key:
        raise IntegrationError('Set GROQ_API_KEY to use natural-language updates.')
    if not question.strip() or len(question) > 1000:
        raise IntegrationError('Describe one event change in under 1,000 characters.')
    options = {field: [o['name'] for o in schema.get(getattr(settings, attr), {}).get(kind, {}).get('options', [])]
               for field, (attr, kind) in FIELDS.items() if kind in ('status', 'select')}
    tool = {'type': 'function', 'function': {'name': 'propose_event_update',
            'description': 'Propose one property edit for user review. Does not execute a write.',
            'parameters': {'type': 'object', 'additionalProperties': False,
                'properties': {'event_name': {'type': 'string'},
                               'event_date': {'type': 'string', 'description': 'Existing event date YYYY-MM-DD, only when given by the user'},
                               'field': {'type': 'string', 'enum': list(FIELDS)},
                               'value': {'type': 'string', 'description': 'New value. Dates must be ISO; timed dates need explicit offset. Empty string clears a field.'}},
                'required': ['event_name', 'field', 'value']}}}
    payload = {'model': settings.groq_model, 'temperature': 0, 'max_completion_tokens': 1200,
               'tools': [tool], 'tool_choice': {'type': 'function', 'function': {'name': 'propose_event_update'}},
               'parallel_tool_calls': False, 'messages': [
                   {'role': 'system', 'content': f'Today is {datetime.now(EASTERN).date()}. Timezone is America/New_York. '
                    'Extract one explicitly requested edit; never combine changes or invent missing information. '
                    'event_date identifies the existing event, not its new date. Only use an existing dropdown value. '
                    'No record content is supplied. Options: ' + json.dumps(options)},
                   {'role': 'user', 'content': question}]}
    async with session.post('https://api.groq.com/openai/v1/chat/completions',
                            headers={'Authorization': 'Bearer ' + settings.groq_key}, json=payload,
                            timeout=aiohttp.ClientTimeout(total=30)) as response:
        if response.status != 200:
            raise IntegrationError(f'Could not interpret the update (Groq HTTP {response.status}).')
        data = await response.json()
    try:
        calls = data['choices'][0]['message']['tool_calls']
        if len(calls) != 1 or calls[0]['function']['name'] != 'propose_event_update':
            raise ValueError
        plan = json.loads(calls[0]['function']['arguments'])
        if not isinstance(plan, dict) or set(plan) - {'event_name', 'event_date', 'field', 'value'}:
            raise ValueError
        if not isinstance(plan['event_name'], str) or not plan['event_name'].strip() or len(plan['event_name']) > 200:
            raise ValueError
        if plan.get('event_date'):
            date.fromisoformat(plan['event_date'])
        update_property(settings, schema, plan['field'], plan['value'])
        return plan
    except (KeyError, TypeError, ValueError):
        raise IntegrationError('Please describe a single event, field, and new value.') from None


class OfficerView(discord.ui.View):
    def __init__(self, cog, requester, timeout=180):
        super().__init__(timeout=timeout)
        self.cog, self.requester = cog, requester
        self.lock = asyncio.Lock()
        self.finished = False
        self.message = None

    async def interaction_check(self, interaction):
        ctx = SimpleNamespace(guild=interaction.guild, channel=interaction.channel, author=interaction.user)
        allowed = interaction.user.id == self.requester and self.cog.allowed(ctx)
        try:
            self.cog.private_channel()
        except IntegrationError:
            allowed = False
        if not allowed:
            await interaction.response.send_message('Only the officer who requested this can use it, in the configured channel.', ephemeral=True)
        return allowed

    def disable(self):
        self.finished = True
        for item in self.children:
            item.disabled = True
        self.stop()

    async def on_timeout(self):
        self.disable()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        text = str(error) if isinstance(error, IntegrationError) else 'The request could not be completed. Check Notion before retrying.'
        if interaction.response.is_done():
            await interaction.followup.send(discord.utils.escape_mentions(text), ephemeral=True)
        else:
            await interaction.response.send_message(discord.utils.escape_mentions(text), ephemeral=True)


class EventPicker(OfficerView):
    def __init__(self, cog, requester, events, callback):
        from bot.notion import event_date
        super().__init__(cog, requester)
        self.events, self.callback = {e.id: e for e in events}, callback
        menu = discord.ui.Select(placeholder='Choose the event and date', options=[
            discord.SelectOption(label=e.title[:100], value=e.id,
                                 description=event_date(e.date)[:100]) for e in events])
        menu.callback = self.choose
        self.add_item(menu)
        self.menu = menu

    async def choose(self, interaction):
        async with self.lock:
            if self.finished:
                return await interaction.response.send_message('This selection expired. Run the command again.', ephemeral=True)
            self.disable()
            await interaction.response.edit_message(view=self)
            await self.callback(self.events[self.menu.values[0]], interaction)


class ConfirmAction(OfficerView):
    def __init__(self, cog, requester, callback):
        super().__init__(cog, requester)
        self.callback = callback

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, interaction, button):
        async with self.lock:
            if self.finished:
                return await interaction.response.send_message('This request expired. Run it again.', ephemeral=True)
            if not await self.interaction_check(interaction):
                return
            self.disable()
            await interaction.response.edit_message(view=self)
            await self.callback(interaction)

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        async with self.lock:
            if self.finished:
                return await interaction.response.send_message('This request was already handled.', ephemeral=True)
            self.disable()
            await interaction.response.edit_message(content='Cancelled. Nothing was changed.', embed=None, view=self)


class EditModal(discord.ui.Modal):
    def __init__(self, owner, ctx, event, field, schema):
        name = getattr(owner.cog.settings, FIELDS[field][0])
        super().__init__(title=('Edit ' + name)[:45], timeout=180)
        self.owner, self.ctx, self.event, self.field, self.schema = owner, ctx, event, field, schema
        value = getattr(event, field)
        if FIELDS[field][1] == 'date':
            day = local_date(value)
            default_day = f'{day.strftime("%B")} {day.day}, {day.year}' if day else ''
            default_time = ''
            if value and len(value) > 10:
                moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
                moment = moment.replace(tzinfo=EASTERN) if moment.tzinfo is None else moment.astimezone(EASTERN)
                default_time = f'{moment.hour % 12 or 12}:{moment.minute:02d} {moment.strftime("%p")}'
            self.day = discord.ui.TextInput(label='Date (blank clears this field)',
                placeholder='November 3, 2026', default=default_day, required=False, max_length=60)
            self.clock = discord.ui.TextInput(label='Time in Eastern (optional)',
                placeholder='6:30 PM — leave blank for date only', default=default_time, required=False, max_length=20)
            self.add_item(self.day)
            self.add_item(self.clock)
        else:
            if len(value) > 1800:
                raise IntegrationError('This field is too long for the bot editor. Please edit it in Notion.')
            self.text = discord.ui.TextInput(label=name[:45], default=value, required=field == 'title',
                placeholder='Enter the new value', max_length=1800,
                style=discord.TextStyle.paragraph if field == 'description' else discord.TextStyle.short)
            self.add_item(self.text)

    async def on_submit(self, interaction):
        if not await self.owner.interaction_check(interaction):
            return
        value = form_date(self.day.value, self.clock.value) if FIELDS[self.field][1] == 'date' else self.text.value
        await interaction.response.defer(ephemeral=True)
        await self.owner.cog.preview_edit(self.ctx, self.event, self.field, value, self.schema, interaction)

    async def on_error(self, interaction, error):
        await self.owner.on_error(interaction, error, None)


class ValuePicker(OfficerView):
    def __init__(self, cog, ctx, event, field, schema):
        super().__init__(cog, ctx.author.id)
        self.ctx, self.event, self.field, self.schema = ctx, event, field, schema
        attr, kind = FIELDS[field]
        self.options = schema[getattr(cog.settings, attr)][kind]['options']
        if not self.options:
            raise IntegrationError('No options are configured for this field in Notion.')
        self.page = 0
        self.menu = discord.ui.Select(placeholder='Choose a value to review', row=0)
        self.menu.callback = self.choose
        self.add_item(self.menu)
        self.previous = discord.ui.Button(label='Previous options', row=1)
        self.next = discord.ui.Button(label='More options', row=1)
        self.previous.callback = self.prev_page
        self.next.callback = self.next_page
        if len(self.options) > 25:
            self.add_item(self.previous)
            self.add_item(self.next)
        self.refresh()

    def refresh(self):
        start = self.page * 25
        self.menu.options = [discord.SelectOption(label=o['name'][:100], value=str(i),
                    description='Current value' if o['name'] == getattr(self.event, self.field) else None,
                    default=o['name'] == getattr(self.event, self.field))
                    for i, o in enumerate(self.options[start:start + 25], start)]
        self.previous.disabled = self.page == 0
        self.next.disabled = start + 25 >= len(self.options)

    async def prev_page(self, interaction):
        async with self.lock:
            if self.finished:
                return await interaction.response.send_message('This editor expired. Run !notionedit again.', ephemeral=True)
            self.page = max(0, self.page - 1)
            self.refresh()
            await interaction.response.edit_message(view=self)

    async def next_page(self, interaction):
        async with self.lock:
            if self.finished:
                return await interaction.response.send_message('This editor expired. Run !notionedit again.', ephemeral=True)
            self.page = min((len(self.options) - 1) // 25, self.page + 1)
            self.refresh()
            await interaction.response.edit_message(view=self)

    async def choose(self, interaction):
        async with self.lock:
            if self.finished:
                return await interaction.response.send_message('This editor expired. Run !notionedit again.', ephemeral=True)
            value = self.options[int(self.menu.values[0])]['name']
            self.disable()
            await interaction.response.edit_message(view=self)
            await self.cog.preview_edit(self.ctx, self.event, self.field, value, self.schema, interaction)


class FieldPicker(OfficerView):
    def __init__(self, cog, ctx, event, schema):
        from bot.notion import event_date
        super().__init__(cog, ctx.author.id)
        self.ctx, self.event, self.schema = ctx, event, schema
        options = []
        for field, (attr, kind) in FIELDS.items():
            name = getattr(cog.settings, attr)
            if schema.get(name, {}).get('type') == kind:
                current = getattr(event, field)
                current = event_date(current) if kind == 'date' else current or 'Not set'
                options.append(discord.SelectOption(label=name[:100], value=field,
                    description=('Current: ' + current)[:100]))
        if not options:
            raise IntegrationError('No supported fields are configured for this event.')
        self.menu = discord.ui.Select(placeholder='What would you like to update?', options=options)
        self.menu.callback = self.choose
        self.add_item(self.menu)

    async def choose(self, interaction):
        from bot.notion import clean
        async with self.lock:
            if self.finished:
                return await interaction.response.send_message('This editor expired. Run !notionedit again.', ephemeral=True)
            field = self.menu.values[0]
            name = getattr(self.cog.settings, FIELDS[field][0])
            if FIELDS[field][1] in ('status', 'select'):
                view = ValuePicker(self.cog, self.ctx, self.event, field, self.schema)
                embed = card(title=('✏️ Edit ' + name)[:200],
                    description=f'**{clean(self.event.title)}**\nCurrent value: **{clean(getattr(self.event, field) or "Not set")}**\nChoose an option below. You will review it before saving.',
                    tone='blue')
                self.disable()
                await interaction.response.edit_message(embed=embed, view=view)
                view.message = interaction.message
            else:
                modal = EditModal(self, self.ctx, self.event, field, self.schema)
                await interaction.response.send_modal(modal)
                self.disable()


class NotionWorkflows:
    async def choose_event(self, ctx, query, callback, on_date=None, allow_all=False):
        all_events = await self.client.list_events()
        events = all_events if allow_all and not query.strip() else find_events(all_events, query, on_date)
        if not events:
            raise IntegrationError('No matching event found. Check its name or use the Notion page ID.')
        if not self.allowed(ctx):
            return
        self.private_channel()
        if len(events) == 1:
            return await callback(events[0], None)
        if len(events) > 25:
            raise IntegrationError('Too many matches. Use a more specific event name or page ID.')
        view = EventPicker(self, ctx.author.id, events, callback)
        view.message = await ctx.send(embed=card('Pick the right event 📅', 'Choose an event and date below to continue.'), view=view,
                                      allowed_mentions=discord.AllowedMentions.none())

    async def send_planning(self, ctx, mode):
        from bot.notion import clean, event_date, event_embeds
        today = datetime.now(EASTERN).date()
        events = planning_events(await self.client.list_events(), mode, today)
        if not self.allowed(ctx):
            return
        self.private_channel()
        labels = {'missingrooms': 'Rooms to confirm · Next 30 days',
                  'deadlines': 'Marketing deadlines · Overdue & next 7 days',
                  'reimbursements': 'Reimbursements to complete'}
        if not events:
            descriptions = {'missingrooms': 'No missing rooms for events in the next 30 days.',
                            'deadlines': 'No overdue or approaching post deadlines in this list.',
                            'reimbursements': 'No events are marked Reimbursement Needed.'}
            return await ctx.send(embed=card('All caught up 🐾', descriptions[mode], tone='mint'),
                                  allowed_mentions=discord.AllowedMentions.none())
        if mode == 'deadlines':
            for e in events[:30]:
                self.private_channel()
                if not self.allowed(ctx):
                    return
                label = 'Overdue' if local_date(e.post_deadline) < today else 'Due soon'
                embed = card('⏰ ' + labels[mode], f'**[{clean(e.title)}]({e.url})**', tone='amber')
                embed.add_field(name=label, value=event_date(e.post_deadline), inline=False)
                embed.add_field(name='Status', value=clean(e.status), inline=False)
                if len(events) > 30:
                    embed.set_footer(text='Showing the first 30 deadlines. Complete or update stale events in Notion.')
                await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        else:
            for embed in event_embeds(events, labels[mode]):
                self.private_channel()
                if self.allowed(ctx):
                    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command()
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def notionmissingrooms(self, ctx):
        await self.send_planning(ctx, 'missingrooms')

    @commands.command()
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def notiondeadlines(self, ctx):
        await self.send_planning(ctx, 'deadlines')

    @commands.command()
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def notionreimbursements(self, ctx):
        await self.send_planning(ctx, 'reimbursements')

    @commands.command()
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def notionevent(self, ctx, *, query: str):
        from bot.notion import clean, event_date
        async def show(event, interaction):
            if not self.allowed(ctx):
                return
            self.private_channel()
            embed = card('📋 ' + event.title[:195], clean(event.description or 'No description yet.', 1500))
            embed.url = event.url
            for name, value in [('Date', event_date(event.date)), ('Location', event.location or 'Not set'),
                                ('Status', event.status), ('Priority', event.priority or 'Not set'),
                                ('Type', event.event_type or 'Not set'), ('Post deadline', event_date(event.post_deadline))]:
                embed.add_field(name=name, value=clean(value, 350), inline=False)
            if interaction:
                await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            else:
                await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await self.choose_event(ctx, query, show)

    @commands.command(aliases=['notionlazy'])
    @commands.cooldown(3, 60, commands.BucketType.user)
    @commands.max_concurrency(1, per=commands.BucketType.guild, wait=False)
    async def notionupdate(self, ctx, *, request: str):
        schema = await self.client.schema()
        plan = await propose_update(self.session, self.settings, request, schema)
        async def preview(event, interaction):
            await self.preview_edit(ctx, event, plan['field'], plan['value'], schema, interaction)
        await self.choose_event(ctx, plan['event_name'], preview, plan.get('event_date'))

    @commands.command()
    @commands.cooldown(5, 60, commands.BucketType.user)
    async def notionedit(self, ctx, *, query: str = ''):
        """Choose a field and value using Notion's actual options. No AI needed."""
        from bot.notion import clean, event_date
        schema = await self.client.schema()
        async def open_editor(event, interaction):
            if not self.allowed(ctx):
                return
            self.private_channel()
            view = FieldPicker(self, ctx, event, schema)
            embed = card('✏️ Edit event', f'**{clean(event.title)}**\n{event_date(event.date)}\nChoose a field below. Nothing changes until you confirm.')
            if interaction:
                view.message = await interaction.followup.send(embed=embed, view=view, wait=True, allowed_mentions=discord.AllowedMentions.none())
            else:
                view.message = await ctx.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        await self.choose_event(ctx, query, open_editor, allow_all=True)

    async def preview_edit(self, ctx, event, field, value, schema, interaction=None):
        from bot.notion import clean, event_date
        name, value, _ = update_property(self.settings, schema, field, value)
        before = getattr(event, field)
        if not self.allowed(ctx):
            return
        self.private_channel()
        embed = card('Review before saving ✏️', f'**[{clean(event.title)}]({event.url})**\n{event_date(event.date)}', tone='amber')
        before_display = event_date(before) if FIELDS[field][1] == 'date' else before or 'Not set'
        embed.add_field(name='Current · ' + name, value=clean(before_display, 900), inline=False)
        # Show the entire proposed value, including markdown escaping expansion.
        value_display = event_date(value) if value and FIELDS[field][1] == 'date' else value or 'Clear this field'
        proposed = discord.utils.escape_markdown(discord.utils.escape_mentions(value_display))
        for offset in range(0, len(proposed), 900):
            embed.add_field(name=name + (' · Proposed' if offset == 0 else ' · Continued'),
                            value=proposed[offset:offset + 900], inline=False)
        embed.set_footer(text='Expires in 3 minutes. Only the requester can confirm. Nothing changes until confirmed.')
        async def apply(click):
            async with self.edit_lock:
                if not self.allowed(ctx):
                    raise IntegrationError('Your officer access has changed. Run the command again.')
                self.private_channel()
                current = await self.client.get_event(event.id)
                if current is None or getattr(current, field) != before:
                    raise IntegrationError('This field changed since the preview. Run the command again to review the latest value.')
                fresh_schema = await self.client.schema()
                _, canonical, properties = update_property(self.settings, fresh_schema, field, value)
                if not await view.interaction_check(click):
                    return
                audit = {'actor_id': str(click.user.id), 'event_id': event.id, 'field': field,
                         'before': before, 'after': canonical, 'outcome': 'confirmed'}
                key = 'action:' + str(uuid4())
                await self.state.put(key, 'setting', audit)
                try:
                    await self.client.update_event(event.id, properties)
                except Exception as exc:
                    await self.state.put(key, 'setting', {**audit, 'outcome': 'unconfirmed'})
                    if isinstance(exc, IntegrationError) and 'Update content' in str(exc):
                        raise
                    raise IntegrationError('The change could not be confirmed. Check Notion before retrying; the request may have reached it.') from None
                await self.state.put(key, 'setting', {**audit, 'outcome': 'applied'})
                # Status notifications flow through the existing webhook.
                success = card('Saved to Notion ✅', f'**{clean(event.title)}**\n{name} has been updated.', tone='mint')
                await click.followup.send(embed=success, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        view = ConfirmAction(self, ctx.author.id, apply)
        if interaction:
            view.message = await interaction.followup.send(embed=embed, view=view, wait=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            view.message = await ctx.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())

    def schedule_wait(self, now=None):
        now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        if not self.settings.digest_enabled:
            return 300
        due = now.replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=(6 - now.weekday()) % 7)
        if due <= now:
            return 300
        return min(300, max(1, (due - now).total_seconds()))

    async def queue_digest(self, now=None):
        if not self.settings.digest_enabled:
            return
        window = digest_window(now or datetime.now(EASTERN))
        if not window:
            return
        start, end = window
        key = 'digest:' + start.isoformat()
        if self._digest_queued == key:
            return
        if not await self.state.setting(key):
            identity = str(uuid5(NAMESPACE_URL, self.state.scope + ':' + key))
            await self.state.enqueue({'id': identity, 'kind': 'digest', 'start': start.isoformat(), 'end': end.isoformat()})
        self._digest_queued = key

    def digest_embed(self, events, start, end):
        from bot.notion import clean, event_date, friendly_date
        weekly = [e for e in events if local_date(e.date) is not None and start <= local_date(e.date) < end]
        count = len(weekly)
        embed = card('☕ Your week, at a glance', f'{friendly_date(start)} – {friendly_date(end - timedelta(days=1))}\n**{count} event{"s" if count != 1 else ""} on the calendar**', art='digest')
        for e in weekly[:8]:
            embed.add_field(name=clean(e.title, 100), value=(f'{event_date(e.date)}\n'
                f'📍 {clean(e.location or "Room not set", 80)}\n'
                f'**{status_label(clean(e.status, 80))}** · [Open in Notion ↗]({e.url})'), inline=False)
        rooms = planning_events(weekly, 'missingrooms', start)
        deadlines = [e for e in planning_events(events, 'deadlines', start)
                     if local_date(e.post_deadline) < end]
        reimbursements = planning_events(events, 'reimbursements', start)
        summary = f'Rooms to confirm: **{len(rooms)}**\nMarketing deadlines (including overdue): **{len(deadlines)}**\nReimbursements waiting: **{len(reimbursements)}**'
        if len(weekly) > 8:
            summary += '\nUse !notion nextweek to see the full list.'
        embed.add_field(name='📌 Before the week begins', value=summary, inline=False)
        embed.set_footer(text='Eastern Time • !help for commands')
        return embed

    @commands.command()
    @commands.cooldown(2, 60, commands.BucketType.user)
    async def notiondigest(self, ctx):
        start, end = week_range('nextweek')
        events = await self.client.list_events()
        if not self.allowed(ctx):
            return
        self.private_channel()
        embed = self.digest_embed(events, start, end)
        embed.set_footer(text='Preview only • No pings • Does not mark the scheduled digest as sent')
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def deliver_digest(self, job):
        start, end = date.fromisoformat(job['payload']['start']), date.fromisoformat(job['payload']['end'])
        key = 'digest:' + start.isoformat()
        if await self.state.setting(key):
            return await self.state.finish(job['id'])
        # Avoid posting a stale digest for a week that has already started.
        if datetime.now(EASTERN).date() >= start:
            return await self.state.finish(job['id'])
        channel = self.private_channel()
        marker = 'Officer digest ' + job['id']
        if job['state'] == 'sending':
            async for message in channel.history(limit=100):
                if message.author.id == self.bot.user.id and any(e.footer.text == marker for e in message.embeds):
                    await self.state.set_setting(key, 'sent')
                    return await self.state.finish(job['id'])
        events = await self.client.list_events()
        embed = self.digest_embed(events, start, end)
        embed.set_footer(text=marker)
        self.private_channel()
        role_ids = self.settings.officer_role_ids if self.settings.environment == 'prod' else ()
        mentions = ' '.join(f'<@&{role_id}>' for role_id in role_ids)
        await self.state.sending(job, 'digest')
        await channel.send(content=mentions or None, embed=embed,
                           allowed_mentions=discord.AllowedMentions(everyone=False, users=False,
                               roles=[discord.Object(id=role_id) for role_id in role_ids], replied_user=False))
        await self.state.set_setting(key, 'sent')
        await self.state.finish(job['id'])

    @commands.command()
    @commands.cooldown(2, 60, commands.BucketType.user)
    async def notionlink(self, ctx, event_uuid: str, *, query: str):
        event_uuid = notion_id(event_uuid)
        record = await self.supabase_event(event_uuid)
        if not record:
            raise IntegrationError('That Supabase event UUID was not found.')
        from bot.notion import clean, event_date
        async def preview(event, interaction):
            embed = card('Review event link 🔗', f'Notion: **{clean(event.title)}**\nSupabase: **{clean(record.get("name", event_uuid))}**', tone='amber')
            embed.add_field(name='Notion date', value=event_date(event.date), inline=False)
            embed.add_field(name='Supabase date', value=event_date(record.get('start_time')), inline=False)
            async def link(click):
                if not await view.interaction_check(click):
                    return
                await self.state.put('link:' + event_uuid, 'setting', {'notion_id': event.id, 'event_id': event_uuid,
                                     'actor_id': str(click.user.id), 'enabled': True})
                await click.followup.send('Event link saved. Automatic completion requires NOTION_COMPLETION_SYNC_ENABLED=true in prod.', ephemeral=True)
            view = ConfirmAction(self, ctx.author.id, link)
            if interaction:
                view.message = await interaction.followup.send(embed=embed, view=view, wait=True, allowed_mentions=discord.AllowedMentions.none())
            else:
                view.message = await ctx.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        await self.choose_event(ctx, query, preview)

    async def supabase_event(self, event_uuid):
        async with self.session.get(self.settings.supabase_url.rstrip('/') + '/rest/v1/events',
                headers=self.state.headers, params={'id': 'eq.' + event_uuid, 'select': '*', 'limit': '1'},
                timeout=aiohttp.ClientTimeout(total=20)) as response:
            if response.status != 200:
                raise IntegrationError('Could not look up the Supabase event.')
            rows = await response.json()
            return rows[0] if rows else None

    async def sync_completions(self):
        if self.settings.environment != 'prod' or not self.settings.completion_sync_enabled:
            return
        if time.monotonic() < self._next_completion_check:
            return
        self._next_completion_check = time.monotonic() + 900
        links = await self.state.request('GET', {'kind': 'eq.setting', 'key': 'like.link:*', 'limit': '1000'})
        for row in links:
            link = row['payload']
            if not link.get('enabled'):
                continue
            record = await self.supabase_event(notion_id(link['event_id']))
            if not completed_event(record, datetime.now(EASTERN)):
                continue
            if not await self.state.lease():
                return
            async with self.edit_lock:
                event = await self.client.get_event(link['notion_id'])
                if event is None or event.status.casefold() in ('event complete', 'reimbursement needed'):
                    continue
                schema = await self.client.schema()
                _, _, properties = update_property(self.settings, schema, 'status', 'Event Complete')
                await self.state.put('action:' + str(uuid4()), 'setting', {'event_id': event.id,
                                     'supabase_id': link['event_id'], 'after': 'Event Complete', 'outcome': 'auto_completion'})
                await self.client.update_event(event.id, properties)


def completed_event(record, now):
    """Only linked events with an explicit, elapsed end time qualify."""
    if not record or record.get('cancelled') or record.get('canceled'):
        return False
    if str(record.get('status', '')).casefold() in ('cancelled', 'canceled', 'postponed', 'draft'):
        return False
    try:
        end = datetime.fromisoformat(record['end_time'].replace('Z', '+00:00'))
        return end.tzinfo is not None and end <= now
    except (KeyError, ValueError, TypeError, AttributeError):
        return False

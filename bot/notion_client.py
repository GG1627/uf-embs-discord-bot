"""Small asynchronous Notion client and bounded Groq event-query planner."""

import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import aiohttp


class IntegrationError(Exception):
    """An error whose message is safe to display in Discord."""


def notion_id(value):
    return str(UUID(value))


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    status: str
    date: str
    location: str
    description: str = ''
    priority: str = ''
    event_type: str = ''
    post_deadline: str = ''

    @property
    def url(self):
        return 'https://www.notion.so/' + self.id.replace('-', '')


def property_text(prop):
    kind = prop.get('type')
    value = prop.get(kind)
    if kind in ('title', 'rich_text'):
        return ''.join(part.get('plain_text', '') for part in value or [])
    if kind in ('status', 'select'):
        return (value or {}).get('name', '')
    if kind == 'date':
        return (value or {}).get('start', '')
    return ''


class NotionClient:
    def __init__(self, session, settings):
        self.session = session
        self.settings = settings
        # Serialize requests so even a burst of webhook deliveries stays small.
        self.lock = asyncio.Lock()

    async def request(self, method, path, body=None):
        headers = {'Authorization': f'Bearer {self.settings.token}',
                   'Notion-Version': '2025-09-03'}
        async with self.lock:
            for attempt in range(3):
                async with self.session.request(
                    method, 'https://api.notion.com/v1/' + path,
                    headers=headers, json=body,
                    timeout=aiohttp.ClientTimeout(total=25),
                ) as response:
                    if response.status in (429, 529) and attempt < 2:
                        try:
                            delay = min(60, max(1, float(response.headers.get('Retry-After', 2))))
                        except ValueError:
                            delay = 2
                        await asyncio.sleep(delay)
                        continue
                    if response.status != 200:
                        if method == 'PATCH' and response.status == 403:
                            raise IntegrationError('Enable Update content in the Notion connection capabilities before editing events.')
                        raise IntegrationError(f'Notion request failed (HTTP {response.status}). Check integration access and property settings.')
                    return await response.json()

    def event(self, page):
        parent = page.get('parent', {})
        if parent.get('type') != 'data_source_id' or notion_id(parent['data_source_id']) != self.settings.data_source_id:
            return None
        if page.get('archived') or page.get('in_trash'):
            return None
        props = page.get('properties', {})
        for name, kind in ((self.settings.title_property, 'title'),
                           (self.settings.status_property, 'status'),
                           (self.settings.date_property, 'date')):
            if props.get(name, {}).get('type') != kind:
                raise IntegrationError(f'Notion needs a {kind} property named {name}. Check the configured property names.')
        return Event(notion_id(page['id']),
                     property_text(props[self.settings.title_property]) or 'Untitled event',
                     property_text(props[self.settings.status_property]) or 'No status',
                     property_text(props[self.settings.date_property]),
                     property_text(props.get(self.settings.location_property, {})),
                     property_text(props.get(self.settings.description_property, {})),
                     property_text(props.get(self.settings.priority_property, {})),
                     property_text(props.get(self.settings.event_type_property, {})),
                     property_text(props.get(self.settings.post_deadline_property, {})))

    async def schema(self):
        return (await self.request('GET', 'data_sources/' + self.settings.data_source_id))['properties']

    async def update_event(self, page_id, properties):
        # Never accept an arbitrary page outside the configured events source.
        if await self.get_event(page_id) is None:
            raise IntegrationError('That event is no longer in the planning database.')
        return self.event(await self.request('PATCH', 'pages/' + notion_id(page_id), {'properties': properties}))

    async def get_event(self, page_id):
        return self.event(await self.request('GET', 'pages/' + notion_id(page_id)))

    async def list_events(self, start=None, end=None, status=None):
        conditions = []
        if start is not None:
            conditions.append({'property': self.settings.date_property, 'date': {'on_or_after': start.isoformat()}})
        if end is not None:
            conditions.append({'property': self.settings.date_property, 'date': {'before': end.isoformat()}})
        if status:
            conditions.append({'property': self.settings.status_property, 'status': {'equals': status}})
        body = {'page_size': 100, 'sorts': [{'property': self.settings.date_property, 'direction': 'ascending'}]}
        if conditions:
            body['filter'] = {'and': conditions}
        events = []
        # Refuse partial baselines/results rather than silently missing pages.
        for _ in range(10):
            result = await self.request('POST', f'data_sources/{self.settings.data_source_id}/query', body)
            events.extend(event for page in result.get('results', []) if (event := self.event(page)) is not None)
            if not result.get('has_more'):
                return events
            body['start_cursor'] = result['next_cursor']
        raise IntegrationError('More than 1,000 matching Notion events. Narrow the date range or archive old events.')


def week_range(period, today=None):
    today = today or datetime.now(ZoneInfo('America/New_York')).date()
    monday = today - timedelta(days=today.weekday())
    if period == 'today':
        return today, today + timedelta(days=1)
    if period == 'week':
        return today, monday + timedelta(days=7)
    if period == 'nextweek':
        return monday + timedelta(days=7), monday + timedelta(days=14)
    if period in ('upcoming', 'twoweeks', 'fortnight'):
        return today, monday + timedelta(days=14)
    if period == 'month':
        return today, (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    raise IntegrationError('Try !notion week, !notion nextweek, !notion month, or !help.')


def validate_plan(arguments, today):
    try:
        plan = json.loads(arguments)
        if not isinstance(plan, dict) or set(plan) - {'start_date', 'end_date', 'status'}:
            raise ValueError
        start, end = date.fromisoformat(plan['start_date']), date.fromisoformat(plan['end_date'])
        status = plan.get('status')
        if status is not None and (not isinstance(status, str) or not status.strip() or len(status) > 100):
            raise ValueError
        if not (0 < (end - start).days <= 62):
            raise ValueError
        if start < today - timedelta(days=366) or end > today + timedelta(days=366):
            raise ValueError
        return start, end, status
    except (TypeError, KeyError, ValueError):
        raise IntegrationError('Ask for an event date range of up to 62 days, within the next or previous year.') from None


async def plan_question(session, settings, question, today=None):
    """Send only the question and tool schema; Notion records stay out of Groq."""
    if not settings.groq_key:
        raise IntegrationError('Ask is not configured. Try !notion week or !notion upcoming.')
    if len(question) > 1000:
        raise IntegrationError('Please keep your question under 1,000 characters.')
    today = today or datetime.now(ZoneInfo('America/New_York')).date()
    tool = {'type': 'function', 'function': {
        'name': 'list_events',
        'description': 'Read events from the officers Notion events database. Cannot modify records.',
        'parameters': {'type': 'object', 'additionalProperties': False,
                       'properties': {'start_date': {'type': 'string', 'description': 'Inclusive YYYY-MM-DD'},
                                      'end_date': {'type': 'string', 'description': 'Exclusive YYYY-MM-DD'},
                                      'status': {'type': 'string', 'description': 'Exact Notion status, only if explicitly requested'}},
                       'required': ['start_date', 'end_date']}}}
    payload = {'model': settings.groq_model, 'temperature': 0,
               'max_completion_tokens': 1024,
               'messages': [{'role': 'system', 'content':
                   f'Today is {today.isoformat()} in America/New_York. Weeks run Monday through Sunday. '
                   'Translate the event question into list_events arguments. Use an exclusive end date. '
                   'This week starts today; this week and next week ends on the Monday after next. '
                   'When no range is given use the next 14 days. Never invent event details.'},
                   {'role': 'user', 'content': question}],
               'tools': [tool], 'tool_choice': {'type': 'function', 'function': {'name': 'list_events'}},
               'parallel_tool_calls': False}
    async with session.post('https://api.groq.com/openai/v1/chat/completions',
                            headers={'Authorization': f'Bearer {settings.groq_key}'},
                            json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
        if response.status != 200:
            raise IntegrationError(f'Groq request failed (HTTP {response.status}). Try !notion week.')
        result = await response.json()
    try:
        calls = result['choices'][0]['message']['tool_calls']
        if len(calls) != 1 or calls[0]['function']['name'] != 'list_events':
            raise ValueError
        arguments = calls[0]['function']['arguments']
    except (KeyError, IndexError, TypeError, ValueError):
        raise IntegrationError('Could not interpret that question. Try !notion week or !help.') from None
    return validate_plan(arguments, today)

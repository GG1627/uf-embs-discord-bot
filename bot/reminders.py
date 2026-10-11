"""Plain-message reminders; one bot instance should own announcement delivery."""

import asyncio
import io
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

from bot.config import ANNOUNCEMENTS_CHANNEL_ID, DATA_DIR, REMINDER_INTERVALS


def event_start(event):
    value = datetime.fromisoformat(event['start_time'].replace('Z', '+00:00'))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def reminder_due(start, now):
    """Choose the latest reached interval, never fall back to an obsolete one."""
    remaining = start - now
    if remaining <= timedelta(0):
        return None
    due = []
    for interval in REMINDER_INTERVALS:
        unit = 'days' if 'days' in interval else 'hours'
        delta = timedelta(**{unit: interval[unit]})
        if remaining <= delta:
            due.append((delta, f"{interval[unit]}{unit[0]}"))
    return min(due)[1] if due else None


def build_reminder(event, now, code, ping=True):
    start = event_start(event)
    minutes = max(1, math.ceil((start - now).total_seconds() / 60))
    if minutes > 24 * 60:
        timing = f"in about {math.ceil(minutes / 1440)} days"
    elif minutes >= 60:
        timing = f"in about {math.ceil(minutes / 60)} hour{'s' if minutes > 60 else ''}"
    else:
        timing = f"in {minutes} minute{'s' if minutes != 1 else ''}"
    name = discord.utils.escape_markdown(discord.utils.escape_mentions(event['name']))[:200]
    lines = (['@everyone'] if ping else []) + [f"**{name} starts {timing}!**"]
    if code == '2d' and event.get('description'):
        description = discord.utils.escape_mentions(event['description'].strip())
        if len(description) > 700:
            description = description[:697].rstrip() + '…'
        lines += ['', description]
    eastern = ZoneInfo('America/New_York')
    local_start = start.astimezone(eastern)
    # Match scheduled events' one-hour duration when no end time is provided.
    end = event_start({'start_time': event['end_time']}) if event.get('end_time') else start + timedelta(hours=1)
    local_end = end.astimezone(eastern)
    day = local_start.day
    suffix = 'th' if 11 <= day % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    date = f"{local_start.strftime('%A, %B')} {day}{suffix}"
    def clock(value):
        return f"{value.hour % 12 or 12}:{value.minute:02d}"
    start_period = local_start.strftime('%p').lower()
    end_period = local_end.strftime('%p').lower()
    first = clock(local_start)
    if start_period != end_period or local_start.date() != local_end.date():
        first += f' {start_period}'
    last = f'{clock(local_end)} {end_period}'
    if local_start.date() != local_end.date():
        last += f" ({local_end.strftime('%A, %B')} {local_end.day})"
    lines += ['', f"📅 {date}", f"⏱️ {first} - {last}"]
    location = discord.utils.escape_mentions(event.get('location') or 'TBA')
    lines += [f"📍 {location[:250]}"]
    return '\n'.join(lines)


class ReminderRunner:
    def __init__(self, journal=None):
        self.path = Path(journal or Path(DATA_DIR) / 'reminder_deliveries.json')
        # Fail closed on a corrupt journal rather than accidentally repeat pings.
        self.delivered = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.delivered), encoding='utf-8')
        temporary.replace(self.path)

    async def run_once(self, bot, supabase, now=None):
        now = now or datetime.now(timezone.utc)
        channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
        if channel is None:
            raise RuntimeError('Announcements channel not found')
        response = await asyncio.to_thread(lambda: supabase.table('events').select('*')
            .gte('start_time', now.isoformat())
            .lte('start_time', (now + timedelta(days=30)).isoformat()).execute())
        events = response.data or []
        ids = [e['id'] for e in events if e.get('id')]
        sent = set()
        if ids:
            response = await asyncio.to_thread(lambda: supabase.table('event_reminders')
                .select('event_id, reminder_type').in_('event_id', ids).execute())
            sent = {(r['event_id'], r['reminder_type']) for r in response.data or []}
        # Retry database bookkeeping without repeating Discord delivery.
        for key, record in list(self.delivered.items()):
            pair = (record['event_id'], record['reminder_type'])
            if pair in sent:
                del self.delivered[key]
                self.save()
                continue
            try:
                await asyncio.to_thread(lambda r=record: supabase.table('event_reminders').insert(r).execute())
                sent.add(pair)
                del self.delivered[key]
                self.save()
            except Exception as exc:
                print(f'Reminder bookkeeping pending: {exc}')
        for event in events:
            try:
                if not all(event.get(k) for k in ('id', 'name', 'start_time')):
                    continue
                code = reminder_due(event_start(event), now)
                pair = (event['id'], code)
                key = f"{event['id']}:{code}"
                if code is None or pair in sent or key in self.delivered:
                    continue
                kwargs = {'content': build_reminder(event, now, code),
                          'allowed_mentions': discord.AllowedMentions(everyone=True, users=False, roles=False)}
                if event.get('flyer_url'):
                    from bot.events import _fetch_flyer
                    flyer = await _fetch_flyer(event['flyer_url'])
                    if flyer:
                        kwargs['file'] = discord.File(io.BytesIO(flyer), filename='event-flyer.png')
                await channel.send(**kwargs)
                record = {'event_id': event['id'], 'reminder_type': code}
                self.delivered[key] = record
                self.save()
                try:
                    await asyncio.to_thread(lambda: supabase.table('event_reminders').insert(record).execute())
                    sent.add(pair)
                    del self.delivered[key]
                    self.save()
                except Exception as exc:
                    print(f'Reminder delivered; database recording pending: {exc}')
            except Exception as exc:
                print(f"Error processing reminder for {event.get('id')}: {exc}")


async def check_event_reminders(bot, supabase):
    await bot.wait_until_ready()
    runner = ReminderRunner()
    while True:
        try:
            await runner.run_once(bot, supabase)
        except Exception as exc:
            print(f'Error checking event reminders: {exc}')
        await asyncio.sleep(300)

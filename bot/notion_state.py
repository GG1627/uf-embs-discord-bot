"""Small service-role-only Supabase state store; no Notion workspace copy."""

import json
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import aiohttp

from bot.notion_client import IntegrationError


def timestamp(seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class NotionState:
    def __init__(self, session, url, key, scope):
        self.session, self.scope = session, scope
        self.url = url.rstrip('/') + '/rest/v1/notion_bot_state'
        self.headers = {'apikey': key, 'Authorization': f'Bearer {key}'}
        self.owner = str(uuid4())
        self.lease_valid_until = 0

    async def request(self, method, params=None, body=None, prefer=None):
        headers = dict(self.headers)
        if prefer:
            headers['Prefer'] = prefer
        params = {'scope': 'eq.' + self.scope, **(params or {})}
        if method == 'POST':
            params.pop('scope')
        async with self.session.request(method, self.url, params=params, json=body, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=20)) as response:
            if response.status not in (200, 201, 204):
                raise IntegrationError(f'Notion tracking storage failed (HTTP {response.status}). Apply the Notion state migration and check the service-role credentials.')
            raw = await response.read()
            return json.loads(raw) if raw else []

    async def get(self, key):
        rows = await self.request('GET', {'key': 'eq.' + key, 'limit': '1'})
        return rows[0] if rows else None

    async def put(self, key, kind, payload, ignore=False, **fields):
        await self.request('POST', {'on_conflict': 'scope,key'},
                           {'scope': self.scope, 'key': key, 'kind': kind, 'payload': payload,
                            'updated_at': timestamp(), **fields},
                           'resolution=' + ('ignore-duplicates' if ignore else 'merge-duplicates') + ',return=minimal')

    async def setting(self, key):
        row = await self.get('setting:' + key)
        return row['payload']['value'] if row else None

    async def set_setting(self, key, value):
        await self.put('setting:' + key, 'setting', {'value': value})

    async def status(self, page_id):
        row = await self.get('snapshot:' + page_id)
        return row['payload']['status'] if row else None

    async def all_statuses(self, page_ids):
        statuses = {}
        for offset in range(0, len(page_ids), 100):
            keys = ','.join('snapshot:' + page for page in page_ids[offset:offset + 100])
            rows = await self.request('GET', {'kind': 'eq.snapshot', 'key': f'in.({keys})',
                                             'select': 'key,payload', 'limit': '100'})
            statuses.update({row['key'][9:]: row['payload']['status'] for row in rows})
        return statuses

    async def baseline(self, events):
        for offset in range(0, len(events), 100):
            rows = [{'scope': self.scope, 'key': 'snapshot:' + e.id, 'kind': 'snapshot',
                     'payload': {'status': e.status}} for e in events[offset:offset + 100]]
            await self.request('POST', {'on_conflict': 'scope,key'}, rows,
                               'resolution=ignore-duplicates,return=minimal')
        await self.set_setting('initialized', 'yes')

    async def enqueue(self, event):
        await self.request('DELETE', {'kind': 'eq.job', 'state': 'eq.done', 'updated_at': 'lt.' + timestamp(-7 * 86400)})
        await self.put('job:' + event['id'], 'job', event, ignore=True, state='queued')

    async def pending(self):
        rows = await self.request('GET', {'kind': 'eq.job', 'state': 'in.(queued,sending)',
                                         'ready_at': 'lte.' + timestamp(), 'order': 'created_at.asc', 'limit': '1'})
        return {**rows[0], 'id': rows[0]['key'][4:]} if rows else None

    async def sending(self, job, target_status):
        payload = {**job['payload'], '_delivery_status': target_status}
        await self.request('PATCH', {'key': 'eq.' + job['key']},
                           {'state': 'sending', 'payload': payload, 'updated_at': timestamp()})

    async def finish(self, job_id, event=None):
        if event is not None:
            await self.put('snapshot:' + event.id, 'snapshot', {'status': event.status})
        await self.request('PATCH', {'key': 'eq.job:' + job_id}, {'state': 'done', 'updated_at': timestamp()})

    async def retry(self, job):
        attempts = job['attempts'] + 1
        fields = {'attempts': attempts, 'ready_at': timestamp(min(900, 2 ** attempts)), 'updated_at': timestamp()}
        if attempts >= 8:
            fields['state'] = 'failed'
        # Preserve sending state: delivery may already have succeeded.
        await self.request('PATCH', {'key': 'eq.' + job['key']}, fields)

    async def lease(self):
        """One notification worker per scope, including overlapping redeploys."""
        if time.monotonic() < self.lease_valid_until:
            return True
        await self.put('setting:worker_lease', 'setting', {'owner': self.owner}, ignore=True)
        rows = await self.request('PATCH',
                                  {'key': 'eq.setting:worker_lease',
                                   'or': f'(ready_at.lt.{timestamp()},payload->>owner.eq.{self.owner})'},
                                  {'payload': {'owner': self.owner}, 'ready_at': timestamp(300)},
                                  'return=representation')
        if rows:
            self.lease_valid_until = time.monotonic() + 120
            return True
        return False

    async def close(self):
        await self.request('PATCH', {'key': 'eq.setting:worker_lease', 'payload->>owner': 'eq.' + self.owner},
                           {'ready_at': timestamp(-1)})

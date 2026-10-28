-- Internal notification bookkeeping only. Existing event tables are untouched.
begin;

create table public.notion_bot_state (
    scope text not null check (length(scope) between 1 and 250),
    key text not null check (length(key) between 1 and 100),
    kind text not null check (kind in ('snapshot', 'setting', 'job')),
    payload jsonb not null check (jsonb_typeof(payload) = 'object'),
    state text not null default 'queued' check (state in ('queued', 'sending', 'done', 'failed')),
    attempts smallint not null default 0 check (attempts between 0 and 8),
    ready_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (scope, key)
);

create index notion_bot_state_pending_idx
    on public.notion_bot_state (scope, ready_at, created_at)
    where kind = 'job' and state in ('queued', 'sending');

create index notion_bot_state_cleanup_idx
    on public.notion_bot_state (scope, updated_at)
    where kind = 'job' and state = 'done';

alter table public.notion_bot_state enable row level security;
-- No public/client policies: only the bot's server-side service role can access
-- these rows, including the webhook verification token. Revoke default grants.
revoke all on table public.notion_bot_state from public, anon, authenticated;
grant select, insert, update, delete on table public.notion_bot_state to service_role;

comment on table public.notion_bot_state is
    'Private officer-bot notification state; isolated by environment, guild, channel, and Notion data source.';

commit;

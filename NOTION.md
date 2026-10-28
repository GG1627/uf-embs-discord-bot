# Officer Notion integration

This integration is opt-in. `NOTION_ENABLED=false` leaves existing bot behavior
alone. `BOT_ENV=dev` still disables public automatic event reminders. Notion
continues working in prod with public reminders paused using
`EVENT_REMINDERS_ENABLED=false` (the current deployment setting).
Notion uses separate `NOTION_DEV_*` / `NOTION_PROD_*` access settings; enabling public
reminders does not automatically grant access to Notion.

## What is implemented

- Signed Notion webhooks for page creation/property updates. The bot retrieves
  the current record and posts only when its **Status** changed.
- A silent first baseline, persistent delivery IDs, queued retries, and a
  worker lease to reduce duplicates during overlapping deployments.
- `!help` shows a plain-text guide for authorized officers in the Notion channel;
  regular bot help remains available elsewhere. The guide covers everyday
  commands and useful extras, with examples and expected results. Setup and
  connection details stay in this document, outside the officer-facing help.
- `!notion` / `!notion upcoming` shows this week and next week. `!notion today`,
  `!notion week`, `!notion nextweek`, and `!notion month` give common date ranges.
  Weeks start Monday; week/month start today. Displayed date ranges include
  both shown dates. Dates/times are rendered in Eastern Time, with EST/EDT.
- Optional exact status filter: `!notion week Waiting for Marketing`.
- `!notionstatuses` lists the actual status options in Notion.
- `!notionstatus` checks event readiness, Ask configuration, and whether a
  webhook verification token has arrived. It cannot confirm subscription
  activation in Notion; finish that step in Notion's UI.
- `!ask Show events this week and next week with their status` uses Groq
  to select a bounded, read-only date/status query. It cannot execute arbitrary
  commands or modify Notion. Only the user's question and tool schema are sent
  to Groq; event records are formatted by the bot without a second LLM call.
  Previous `!notionevents`, `!notionask`, and `fortnight` usages remain aliases.
- Commands require the configured guild and exact text channel. DMs and
  threads do not qualify. In prod, a member must have **either** configured
  officer role; administrator/owner permissions alone do not grant command
  access. The prod channel must hide `@everyone`. Set its Discord overwrites
  so only the allowed roles and the bot can view it; Discord administrators
  inherently retain visibility. Responses disable all mentions.
- Dev may use the configured general channel. If no dev officer roles are set,
  only the **test server owner** can run commands. Other members can see the
  bot's replies and notifications in that channel. Optional dev role IDs can
  restrict commands to matching test-server roles instead.

## Configuration

The local `.env` has entries for new secrets and separate dev/prod role lists.
Use `.env.example` as a reference; do not replace existing credentials with its
blank values. The supplied test server/channel IDs are already filled in.

1. Create an internal Notion connection with **Read content** access and add it
   to the **planning database**, using the database's Connections menu. Set
   `NOTION_API_TOKEN` to its token. The entire hub does not need to be shared.
2. Confirm `NOTION_DATABASE_ID` identifies the events database itself. A page
   containing a linked database is not necessarily the source database. The
   `v=` value in a Notion link is a view ID, not a data source ID. If there is
   exactly one data source, the bot resolves it automatically; otherwise set
   `NOTION_DATA_SOURCE_ID` explicitly.
3. Match property names in `.env`: the event title (`title` property), Status
   (`status` property), Date (`date` property), and optional Location (`rich_text`
   or `select`). This workspace uses `Event Name`, `Status`, `Event Date`, and
   `Room Booking/Location`, as configured in `.env` and `.env.example`. A select
   field named Status is not a status property; adjust the database or client
   mapping before enabling. Missing dates/locations display as “Not set.”
4. In prod, set `NOTION_PROD_OFFICER_ROLE_IDS` to comma-separated role IDs.
   Chair Director (`1376301509674795120`) and Eboard (`1376293904227237979`)
   are already configured; either grants command access. The prod server is
   `1376293904227237978` and its private Notion channel is `1565050975813763142`.
   Roles are specific to their server.
   In dev, leave `NOTION_DEV_OFFICER_ROLE_IDS` blank to test as the test server
   owner in general, or supply dev-server role IDs. The previous singular
   `OFFICER_ROLE_ID` setting is accepted only when the plural setting is blank.
   Copy role IDs from Server Settings → Roles with Developer Mode enabled.
5. Optional: fill `GROQ_API_KEY`. `GROQ_MODEL` defaults to
   `openai/gpt-oss-120b`. Commands have per-user cooldowns; each question makes
   one bounded model request. The plain events command does not use Groq.

The connection can read the Events Planning database. Its data source ID and
property names are configured in `.env` and `.env.example`. The status options
include `Waiting for Room Booking`, `Waiting for Marketing`, `Waiting for Food
Order`, `Marketing In Progress`, `Reimbursement Needed`, and `Event Complete`.

## Supabase tracking storage

Apply `supabase/migrations/20261028160928_notion_bot_state.sql` once, using your
existing Supabase SQL Editor (or your normal migration workflow). This creates
**one new table**, `notion_bot_state`; it does not alter `events`,
`event_reminders`, or verification tables. No Fly volume is needed.

Rows contain last-seen statuses, webhook metadata, delivery/retry state, the
worker lease, and the private webhook verification token. Full pages and page
contents are not copied. Scope includes environment, guild, channel, and data
source, so test activity cannot consume production notification history.
Successful jobs are cleaned up after seven days when new webhooks arrive.

RLS is enabled with **no public policies**. All public/anonymous/authenticated
grants are revoked. Only the existing server-side service-role credential can
read/write these rows. Never use an anon/publishable key for this integration.
The bot tests storage access before accepting webhooks and will not send
notifications if storage is unavailable.

Validate the migration in SQL Editor:

```sql
select relrowsecurity
from pg_class where oid = 'public.notion_bot_state'::regclass;

select grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public' and table_name = 'notion_bot_state';
```

Expect RLS enabled and no grants to `anon`, `authenticated`, or `PUBLIC`.
The migration is supplied but has not been applied to the live project.

## Webhook setup and Fly.io

Locally, use an HTTPS tunnel to the configured port; Notion cannot call
localhost directly. `fly.toml` is configured for production with the
actual database property mappings and this HTTP service on the existing VM:

```toml
[http_service]
  processes = ['app']
  internal_port = 8080
  force_https = true
  auto_stop_machines = 'off'
  min_machines_running = 1
```

The bot's persistent Discord connection needs the machine running. The Notion
endpoint shares that machine; no new machine or volume is required. Run one
bot instance; the lease is an additional guard during redeploys, not a reason
to scale the bot horizontally.

The app has a shared IPv4 for public HTTPS ingress (no dedicated IP was added).
New Fly apps need public ingress configured before Notion can reach their URL.

1. Generate a random `NOTION_WEBHOOK_PATH_SECRET` of at least 32 URL-safe
   characters (the local `.env` already contains one). Add all activation
   settings and secrets to the **Fly app's environment**; local `.env` is
   excluded from Docker. `fly.toml` sets `BOT_ENV=prod` and `NOTION_ENABLED=true`
   for the configured private prod channel;
   local `.env` remains disabled until you choose to run the bot locally.
   Configuration is read at startup. Public reminders remain off with `BOT_ENV=dev`.
   The existing verified webhook token has been copied into the prod scope;
   the Notion subscription URL and verification do not need to change.
2. In the Notion connection's Webhooks tab, create a subscription to
   the full `NOTION_WEBHOOK_URL` saved in local `.env` (equivalent to
   `https://YOUR-APP.fly.dev/notion/webhook/YOUR_PATH_SECRET`), selecting
   `page.properties_updated` and `page.created`. Do not share this URL.
3. Notion sends a one-time `verification_token`. The bot saves it privately in
   `notion_bot_state`. In your private SQL Editor, find that row:

   ```sql
   select scope, payload ->> 'value' as verification_token
   from public.notion_bot_state
   where key = 'setting:webhook_secret';
   ```

   Copy the token for the correct scope into Notion's **Verify subscription**
   dialog. Do not post it in Discord or commit it. Future events must have a
   valid `X-Notion-Signature`; unsigned events are rejected. Access logging is
   disabled for the webhook server so the secret path does not enter logs.
4. Check `/health` for readiness, try `!notion week` in the test
   channel as the test server owner (or a configured dev role), then change one test event's status. Confirm one
   notification arrives and redeploying does not replay it.

## Officer planning commands and confirmed updates

`!intro` posts a short channel welcome as a regular message with `cat1.jpg`
attached. It points officers to `!notion week`, `!notionedit`, and `!help`.
It uses the same officer/channel checks and can be posted once per minute.

The officer desk uses readable Eastern dates and compact event cards.
Cats appear only on status-change notifications (a random existing cat1?cat8,
excluding cat4, as a top-right thumbnail) and weekly digests/previews (cat4 as
a large bottom image). Event lists, planning checks, empty results, help,
editors, confirmations, and errors have no images. Missing cat files are
skipped. Files live in `assets/notion/`; no image API calls run during bot use.
`NOTION_ASSET_BASE_URL` points to the deployed bot origin; leaving it blank
hides artwork. The asset endpoint exposes only the approved cat JPG filenames
and caches them for a day.

- `!notion missingrooms`: incomplete events in the next 30 days with a blank,
  TBD, TBA, pending, or unset location.
- `!notion deadlines`: overdue and next-seven-day Post Deadlines for events
  that have not passed or been marked Event Complete. Deadlines are listed
  explicitly; the bot does not infer whether a marketing post was published.
- `!notion reimbursements`: all events whose Status is Reimbursement Needed,
  including past events.
- `!notion event Intro to Git`: details including Description, Priority, Event
  Type, and Post Deadline. Duplicate titles open an event/date picker.
- `!notionedit` opens an event picker. `!notionedit Intro to Git` (or
  `!notion edit Intro to Git`) goes straight to matching events. Choose a
  column to see its current value. Status, Priority, and Event Type show live
  dropdown options, with the current option marked; long lists have pagination.
  Text fields open a form prefilled with the existing text. Date forms accept
  familiar dates like November 3, 2026 and an optional Eastern time like
  6:30 PM. Blank optional fields can be cleared. Every edit still requires a
  final Confirm/Cancel. This path makes **no Groq calls**.
- `!notionupdate Set Intro to Git location to Room 201` (alias `!notionlazy`):
  optional natural-language shortcut. Groq proposes one change, then an officer
  reviews Confirm/Cancel buttons.
  Status, location, description, priority, event type, event date, post deadline,
  and title are supported. Dropdown values must already exist in Notion.

Enable **Update content** in the Notion internal connection before testing
edits; Read content alone cannot update pages. The model sees the question and
dropdown schema, not the event records. It cannot perform arbitrary API calls.
Only the requester can confirm; permissions are checked again on interaction.
The old value is fetched again before writing. A changed value aborts the edit
so another officer's work is not silently overwritten. Notion does not offer
an atomic compare-and-swap for page properties, so a very small race remains
between that check and the write. Confirmations expire after three minutes
and expire immediately if the bot redeploys. A timeout never applies a write.

Before an approved edit, an action row is written to the existing private
`notion_bot_state` table (no new migration). It records requester ID, target
event, field, before/after values, and result. If delivery of the Notion PATCH
cannot be confirmed, inspect the event before requesting another edit.

## Sunday officer digest

`NOTION_DIGEST_ENABLED=true` enables an automatic digest every **Sunday at
10 AM America/New_York**, covering the coming Monday through Sunday. This
means 10 AM EDT in summer and 10 AM EST in winter. It lists up to eight events,
their status/room/date, and counts of missing rooms, marketing deadlines, and
reimbursements. All matching events remain available through the commands.

In **prod only**, the automatic digest mentions the configured E-Board and
Chair Director role IDs. The bot needs permission to mention those roles, or
the roles must be mentionable. Allowed mentions are restricted to those role
IDs: event text cannot ping everyone, users, or other roles. Dev digests never
mention prod roles. `!notion digest` / `!notiondigest` previews next week's
digest without any mentions or changes to scheduled delivery records.

The existing persistent job queue and worker lease handle scheduled delivery.
The week has a stable job ID and a persistent sent marker, preventing normal
restart/redeploy replays. Sunday downtime catches up that Sunday after 10 AM;
a digest is not sent once its target Monday has begun. Discord/Supabase cannot
guarantee exactly-once delivery through every possible crash; recovery checks
recent message markers before retrying, just as status notifications do.

## Optional Supabase completion sync

`!notionlink SUPABASE_EVENT_UUID Event name` looks up the Supabase record,
lets you pick the exact Notion event if needed, and requires confirmation.
Links are scoped separately for dev/prod and store the mapping in the existing
private tracking table. Event names are never used for automatic matching.

Completion sync remains **off by default**, including in `fly.toml`. To opt
in after linking prod events, set `NOTION_COMPLETION_SYNC_ENABLED=true`.
Every 15 minutes, linked events with an explicit timezone-aware Supabase
`end_time` in the past become Event Complete. Missing/naive/future end times
and cancelled/canceled/postponed/draft records are skipped. Reimbursement
Needed is preserved, so outstanding reimbursement work is not erased.
No duration is guessed, and no existing Supabase event data is changed.

## Delivery limits

Notion can aggregate updates, so this reports observed status changes, not an
audit of every rapid intermediate edit. Unknown/new pages are baselined without
announcing their initial status. On restart, known pages are compared with
their saved status to reconcile downtime changes.

After a crash during a Discord send, the bot checks the last 100 channel
messages for the notification marker before retrying. This reduces duplicates;
exactly-once delivery across Discord and Supabase is not guaranteed. The bot
needs **Read Message History** as well as Send Messages and Embed Links.
After eight failed attempts, the job stays `failed` for inspection. A moderator
can fix the underlying issue and reset that row's `state` to `queued`,
`attempts` to `0`, and `ready_at` to `now()` in SQL Editor.

Possible future features include owner assignment (requires an owner column),
marketing-post tracking, and summaries of planning changes since the last digest.

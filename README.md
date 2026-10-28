# EMBS Discord Bot

A Discord bot built with Python for the UF EMBS community.

<div align="center">
  <img src="DiscordBotPFP.png" alt="Discord Bot Avatar" width="200" style="border-radius: 50%;">
</div>

## 🚀 Getting Started

### Prerequisites

- Python 3.x
- Discord Bot Token ([Get one here](https://discord.com/developers/applications/))
- Git

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/GG1627/embs-discord-bot.git
   ```

2. **Navigate to the project directory**
   ```bash
   cd embs-discord-bot
   ```

### Setup

1. **Create a virtual environment**
   ```bash
   python -m venv venv
   ```

2. **Activate the virtual environment**
   ```bash
   venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r .\requirements.txt
   ```

4. **Configure Git ignore**
   - Create a `.gitignore` file
   - Add the following entries:
     ```
     venv/
     .env
     ```

## Event reminders

Use `BOT_ENV=dev` or `BOT_ENV=prod`. Automatic reminders are **disabled by
default**. They require both `BOT_ENV=prod` and `EVENT_REMINDERS_ENABLED=true`.
Missing, false, or unrecognized values leave them disabled. The startup logs
show the environment and reminder status.

For local runs, set `BOT_ENV=dev` in `.env` and restart the bot.
For Fly.io, `fly.toml` keeps `BOT_ENV = 'prod'` for Notion access and sets
`EVENT_REMINDERS_ENABLED = 'false'`, so this deployment pauses public event
reminders. Change that flag to `'true'` and redeploy only when ready to resume.
Fly.io uses its deployed environment; changing your local `.env` does not
change the running Fly app. An existing Fly secret named `BOT_ENV` overrides
the value in `fly.toml`, so avoid defining a setting in both places.

This switch only controls automatic reminder messages. Commands (including
`!previewreminder`), verification, moderation, and scheduled-event sync continue
to work with their existing server/channel settings. It does not restrict the
bot to a dev server. Reminders paused during development are not marked as
sent; enabling production can immediately send a reminder that is still due.

In production, the bot checks every five minutes and posts regular messages in the configured
announcements channel at two days and two hours before an event. The early
reminder includes the event description as written (trimmed to 700 characters)
and both reminders attach the flyer when available. The final reminder omits
the description. Details appear on separate calendar, clock, and location
lines in Eastern Time (for example, `📅 Wednesday, October 7th`,
`⏱️ 5:00 - 6:00 pm`, and `📍 Weimer Hall 1064`). The event's `end_time`
is used when provided; otherwise the duration defaults to one hour, matching
the scheduled-event sync.

After downtime, only the latest applicable reminder is sent, using the actual
time remaining. Existing reminder records prevent repeat deliveries.

Admins can preview either format in the current channel without pings or
changing reminder records:

```text
!previewreminder EVENT_UUID 2d
!previewreminder EVENT_UUID 2h
```

Run local tests with `python -m unittest discover -s tests -v`.

Run only one bot instance that sends announcements. Keep
`data/reminder_deliveries.json` on persistent storage: it retains delivered
reminders whose Supabase recording failed, and the bot retries that recording
without sending another message. This reduces duplicate risk, but a process
crash between Discord delivery and saving the journal, or losing the journal
before database recovery, can still cause a duplicate.

## Officer Notion integration

Optional Notion status notifications, officer-only event lists, and Groq query
interpretation are available. They stay disabled until configured. See
[NOTION.md](NOTION.md) for setup, access controls, Supabase tracking storage,
and testing instructions. Local settings are documented in `.env.example`.
## 📚 Resources

- [Discord Developer Portal](https://discord.com/developers/applications/)

## 👥 Contributors

- **Gael Garcia**
- **Lincy Phipps**

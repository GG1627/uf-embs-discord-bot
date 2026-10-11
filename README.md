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

The bot checks every five minutes and posts regular messages in the configured
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

## 📚 Resources

- [Discord Developer Portal](https://discord.com/developers/applications/)

## 👥 Contributors

- **Gael Garcia**
- **Lincy Phipps**

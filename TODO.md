# EMBS Discord Bot - TODO List

Note for Lincy: Feel free to add any ideas of your own ^-^

## ✅ Completed

- [x] **CAPTCHA Verification System**
  - Token-based verification with Cloudflare Turnstile
  - Auto-assign Unverified role on join
  - Verify button in #verify channel
  - Supabase token storage

---
## In Progress

- [ ] **Message Filtering/Censoring**
  - Filter foul language and profanity
  - Custom word blacklist (configurable)
  - Auto-delete messages with banned words
  - Warn users on violations
  - ^^^ DONE

  - Detect a spam message from a bot and delete it
  - Maybe something like if said message contains at least 3/4 of the most command spam words, delete it 🤔
  - @Lincy u can work on this if u want, i didnt start this

---

## 📋 To Do

- [ ] **Role Management System**
  - **Majors**: Add roles for different majors (Computer Engineering, Electrical Engineering, etc.)
  - **School Year**: Add roles for Freshman, Sophomore, Junior, Senior, Graduate
  - Self-assignable role menu/buttons
  - Slash commands for role assignment (`/role major`, `/role year`)

- [ ] **Event Reminders**
  - Automatic reminders for scheduled events
  - Support for recurring events (weekly meetings, etc.)
  - Configurable reminder times (1 day before, 1 hour before, etc.)
  - Event creation command (`/event create`)
  - Event listing command (`/event list`)

- [ ] **Welcome Messages**
  - Custom welcome message when users join
  - DM with server rules and important info
  - Welcome embed with server info

- [ ] **Deploy Bot (Always Running)**
  - Deploy to hosting service (Heroku, Railway, DigitalOcean, etc.)
  - Set up environment variables on hosting platform
  - Configure auto-restart on crash
  - Set up monitoring/uptime checks
  - Ensure bot stays online 24/7

## Maybes

- [ ] **Moderation Commands**
  - `/warn` - Warn a user with reason
  - `/kick` - Kick a user from server
  - `/ban` - Ban a user with reason
  - `/timeout` - Temporarily mute a user

- [ ] **Announcement System**
  - `/announce` command for officers/admins
  - Ping specific roles with announcements
  - Scheduled announcements

- [ ] **Fun Commands**
  - `/meme` - Random meme generator
  - `/joke` - Random jokes
  - `/8ball` - Magic 8-ball responses
  - `/poll` - Create polls
  - `/quote` - Random inspirational quotes

---

**Last Updated:** December 25, 2025

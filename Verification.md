# Verification System - Production Reference Guide

> **Quick Reference:** This document outlines the verification system implementation. Use this as a checklist when setting up the production Discord server.

## 📋 What Was Implemented

The bot implements a **token-based verification system** with Cloudflare Turnstile:

1. ✅ Auto-assigns "Unverified" role when users join
2. ✅ Posts verify button in `#verify` channel
3. ✅ Generates secure tokens (32 chars, 15 min expiry)
4. ✅ Stores tokens in Supabase (`discord_verification_tokens` table)
5. ✅ Sends verification link to users
6. ✅ Website validates token + Turnstile → grants "Member" role

---

## 🚀 Production Setup Checklist

### Step 1: Create Roles in Discord

Create these roles in your production server:

| Role Name | Color | Purpose |
|-----------|-------|---------|
| `Unverified` | Red/Orange | Assigned on join, limited channel access |
| `Member` | Green/Blue | Granted after verification, full access |

**Role Hierarchy (top to bottom):**
```
1. Admin/Owner Roles
2. Bot Role (EMBS Bot) ← MUST be here
3. Member Role
4. Unverified Role
5. @everyone
```

### Step 2: Configure Channel Permissions

#### #verify Channel
- **@everyone**: ❌ View Channel, ❌ Send Messages
- **Unverified**: ✅ View Channel, ✅ Send Messages, ✅ Read History
- **Member**: ✅ View Channel, ✅ Send Messages, ✅ Read History
- **Bot Role**: ✅ View Channel, ✅ Send Messages, ✅ Embed Links, ✅ Read History

#### All Other Channels
- **@everyone**: ❌ View Channel, ❌ Send Messages
- **Unverified**: ❌ View Channel, ❌ Send Messages
- **Member**: ✅ View Channel, ✅ Send Messages, ✅ Read History
- **Bot Role**: ✅ View Channel, ✅ Send Messages, ✅ Read History

### Step 3: Bot Permissions

Ensure bot role has:
- ✅ **Manage Roles** (critical!)
- ✅ **Send Messages**
- ✅ **Embed Links**
- ✅ **Read Message History**

### Step 4: Update Configuration

Edit `main.py` and update these values:

```python
# Line 25-29 in main.py
UNVERIFIED_ROLE_NAME = "Unverified"
MEMBER_ROLE_NAME = "Member"
VERIFY_CHANNEL_ID = YOUR_PRODUCTION_CHANNEL_ID  # ← Update this!
VERIFICATION_URL_BASE = "https://www.ufembs.com/discord-verify"
TOKEN_EXPIRY_MINUTES = 15
```

**To get Channel ID:**
1. Enable Developer Mode (User Settings → Advanced)
2. Right-click `#verify` channel → Copy ID
3. Paste into `VERIFY_CHANNEL_ID`

### Step 5: Environment Variables

Ensure `.env` file has production values:

```env
DISCORD_TOKEN=your_production_bot_token
SUPABASE_URL=your_supabase_project_url
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
```

**⚠️ Important:** Use **Service Role Key**, not anon key!

### Step 6: Verify Supabase Table

Confirm `discord_verification_tokens` table exists with this schema:

```sql
CREATE TABLE discord_verification_tokens (
    id BIGSERIAL PRIMARY KEY,
    discord_user_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX idx_token ON discord_verification_tokens(token);
CREATE INDEX idx_discord_user_id ON discord_verification_tokens(discord_user_id);
CREATE INDEX idx_expires_at ON discord_verification_tokens(expires_at);
```

---

## 🔄 How It Works (Reference)

### Verification Flow

```
1. User joins server
   ↓
2. Bot assigns "Unverified" role automatically
   ↓
3. User sees only #verify channel
   ↓
4. User clicks "Verify" button
   ↓
5. Bot generates token → stores in Supabase → sends link
   ↓
6. User visits link → completes Turnstile → Edge Function validates
   ↓
7. Bot grants "Member" role → user sees all channels
```

### Bot Code Components

**`on_ready()` Event:**
- Posts verify message with button when bot starts
- Runs on every bot restart

**`on_member_join()` Event:**
- Auto-assigns "Unverified" role
- Handles permission errors

**`VerifyView` Class:**
- Persistent "Verify" button
- Generates token: `secrets.token_urlsafe(32)`
- Stores in Supabase with 15-minute expiry
- Sends verification link

---

## 🔧 Configuration Values

### Current Settings

| Setting | Value | Location |
|---------|-------|----------|
| Unverified Role | `"Unverified"` | `main.py` line 25 |
| Member Role | `"Member"` | `main.py` line 26 |
| Verify Channel ID | `1453158292707868722` | `main.py` line 27 |
| Verification URL | `https://www.ufembs.com/discord-verify` | `main.py` line 28 |
| Token Expiry | `15 minutes` | `main.py` line 29 |

### Token Generation

```python
token = secrets.token_urlsafe(32)  # ~43 characters, URL-safe
expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
```

---

## 🌐 Website Integration (Reference)

### What the Website Does

1. **Extracts token** from URL: `?token=...`
2. **Displays Cloudflare Turnstile** widget
3. **Sends to Edge Function**: token + Turnstile response
4. **Edge Function validates**:
   - Token exists in Supabase
   - Token not expired (`expires_at > NOW()`)
   - Token not used (`used = false`)
   - Turnstile response valid
5. **Grants Discord role** via API
6. **Marks token as used**: `used = true`

---

## 🐛 Quick Troubleshooting

| Problem | Solution |
|---------|----------|
| Users don't get "Unverified" role | Check bot has "Manage Roles" + role hierarchy |
| Verify button not appearing | Check `VERIFY_CHANNEL_ID` + bot permissions |
| Token generation fails | Verify Supabase connection + table exists |
| Users see all channels | Check channel permissions for "Unverified" role |
| Bot can't assign roles | Bot role must be above target roles in hierarchy |

### Check Logs

```bash
# View bot logs
cat discord.log

# Common errors:
# - "Missing Access" → Bot permissions issue
# - "Forbidden" → Role hierarchy problem
# - Supabase errors → Check connection/table
```

---

## 🧹 Maintenance Tasks

### Cleanup Expired Tokens

Run periodically (weekly/monthly):

```sql
DELETE FROM discord_verification_tokens 
WHERE expires_at < NOW() 
   OR (used = TRUE AND created_at < NOW() - INTERVAL '7 days');
```

### Monitor

- Check `discord.log` for errors
- Monitor Supabase table size
- Track verification success rate

---

## 📝 Quick Reference Commands

### Test Bot Connection
```bash
python main.py
# Should see: "Logged in as EMBS Bot#..."
# Should see: "✅ Posted verify message with button!"
```

### Check Channel ID
1. Right-click channel → Copy ID
2. Update `VERIFY_CHANNEL_ID` in `main.py`

### Verify Roles Exist
```python
# In Discord: Server Settings → Roles
# Look for: "Unverified" and "Member"
```

### Check Bot Permissions
```python
# In Discord: Server Settings → Roles → Bot Role
# Verify: Manage Roles ✅
```

---

## 🔗 Related Files

- **Bot Code**: `main.py` (lines 24-117)
- **Environment**: `.env`
- **Logs**: `discord.log`
- **Database**: Supabase `discord_verification_tokens` table

---

## 📚 External Dependencies

- **Discord.py**: Bot framework
- **Supabase**: Token storage
- **Cloudflare Turnstile**: CAPTCHA verification
- **Website**: `https://www.ufembs.com/discord-verify`

---

**Last Updated:** December 2024  
**Version:** 1.0

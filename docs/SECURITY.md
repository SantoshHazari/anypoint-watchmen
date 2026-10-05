# Security Guide

## ⚠️ Credentials Management

### Current Status
- `.env` file contains sensitive credentials (local only, NOT in git)
- `.env.example` template available for safe sharing
- All credentials must be changed regularly

### Credentials to Protect

1. **Anypoint Platform**
   - Connected App: `ANYPOINT_CLIENT_ID` + `ANYPOINT_CLIENT_SECRET`
   - Status: ✅ OAuth 2.0 Client Credentials (Connected App "Watchmen Dashboard")
   - Token auto-renews at 90% of lifetime (~54 min TTL, fully automatic)

2. **Email (SMTP)**
   - Email: `SMTP_SENDER_EMAIL`
   - Password: `SMTP_SENDER_PASSWORD`
   - Recommendation: Use Gmail App Passwords, not main account password
   - See: https://myaccount.google.com/apppasswords

### Data to Protect

1. **Database**
   - File: `data/watchmen.sqlite`
   - Contains: Historical usage data
   - Status: ✅ Protected in .gitignore

2. **Raw API Responses**
   - Folder: `data/raw/usage/`
   - Contains: JSON snapshots of Anypoint API responses
   - Status: ✅ Protected in .gitignore

3. **Configuration**
   - File: `config/settings.json`
   - File: `config/entitlements.json`
   - Status: ✅ Protected in .gitignore (contain customer-specific data)

## 🔐 Security Best Practices

### 1. Local Development
```bash
# Copy template
cp .env.example .env

# Fill in YOUR credentials
# NEVER commit .env to git
# .gitignore prevents accidental commits
```

### 2. Environment Variables
```bash
# On Windows (Command Prompt)
set ANYPOINT_CLIENT_ID=your_client_id
set ANYPOINT_CLIENT_SECRET=your_client_secret

# On Windows (PowerShell)
$env:ANYPOINT_CLIENT_ID="your_client_id"
$env:ANYPOINT_CLIENT_SECRET="your_client_secret"

# On macOS/Linux
export ANYPOINT_CLIENT_ID=your_client_id
export ANYPOINT_CLIENT_SECRET=your_client_secret
```

### 3. Gmail App Passwords (Recommended)
Instead of using your Gmail password:
1. Enable 2-Factor Authentication on your Google account
2. Generate App Password: https://myaccount.google.com/apppasswords
3. Use the 16-character password in `.env`

## ✅ Connected App (OAuth 2.0) — Active

Migration from personal credentials to OAuth 2.0 is **complete**. The app now uses a Connected App exclusively.

### Why Connected App?
- ✅ Safer than storing personal credentials
- ✅ Can rotate credentials without changing password
- ✅ Can be revoked independently
- ✅ Follows OAuth 2.0 Client Credentials standard
- ✅ Token auto-renews (no manual intervention needed)

### How Authentication Works

```
scripts/anypoint_auth.py  checks in order:
  1. ANYPOINT_TOKEN env var  → static bearer token (bypass)
  2. ANYPOINT_CLIENT_ID + ANYPOINT_CLIENT_SECRET  → OAuth 2.0 (active)
  3. ANYPOINT_USERNAME + ANYPOINT_PASSWORD  → legacy login (deprecated, removed from .env)
```

Token endpoint: `POST /accounts/api/v2/oauth2/token` (`client_secret_post` method)
TTL: ~54 minutes. Auto-refreshed at 90% of lifetime by `load_auth()`.

### Connected App Setup (already done — for reference)

1. **Anypoint Platform** → Access Management → Connected Apps
2. App name: **"Watchmen Dashboard"**, type: Confidential OAuth client
3. **Scopes granted:**
   - View organization usage
   - View entitlements
   - View environments
   - View applications
   - View APIs
   - View users
   - Manage organization settings
4. Credentials stored in `.env` (never committed):
   ```
   ANYPOINT_CLIENT_ID=your_client_id
   ANYPOINT_CLIENT_SECRET=your_client_secret
   ```

### Auth Tests

`tests/test_anypoint_auth.py` — 15 tests (unit + integration):
- Unit: `_can_use_oauth()`, `_can_login()`, `auth_status()`, priority enforcement
- Integration (skipped unless env vars set): token request, load_auth(), refresh_token(), JWT claims

## 🚨 Security Audit Checklist

- [x] `.env` file is in `.gitignore`
- [x] `.env.example` contains only placeholder values
- [x] `data/` folder is in `.gitignore`
- [x] `config/settings.json` is in `.gitignore`
- [x] `config/entitlements.json` is in `.gitignore`
- [x] No passwords in any `.py` files
- [x] No passwords in any `.json` files (except `.example` files)
- [x] All credentials loaded from environment variables
- [x] Connected App created and active (OAuth 2.0)
- [x] Personal credentials (ANYPOINT_USERNAME/PASSWORD) removed from `.env`
- [ ] SQLite database backed up regularly

## 🛡️ File Protection Matrix

| File/Folder | Git | Local | Protection |
|-------------|-----|-------|-----------|
| `.env` | ❌ Protected | ✅ Keep | Active secrets |
| `.env.example` | ✅ Allow | ✅ Keep | Templates only |
| `data/watchmen.sqlite` | ❌ Protected | ✅ Keep | Historical data |
| `data/raw/usage/` | ❌ Protected | ✅ Keep | API snapshots |
| `config/settings.json` | ❌ Protected | ✅ Keep | Org-specific config |
| `config/entitlements.json` | ❌ Protected | ✅ Keep | Entitlement limits |
| `scripts/` | ✅ Allow | ✅ Keep | Application code |
| `web/` | ✅ Allow | ✅ Keep | Web frontend |
| `docs/` | ✅ Allow | ✅ Keep | Documentation |

## 📋 Regular Maintenance

### Monthly
- [ ] Review recent git commits for accidental secrets
- [ ] Check that `.env` is NOT committed
- [ ] Verify `.gitignore` is protecting sensitive data

### Quarterly
- [ ] Rotate passwords (if using personal auth)
- [ ] Review API token scopes
- [ ] Audit access logs

### Annually
- [ ] Security review of all scripts
- [ ] Update dependencies (pip freeze)
- [ ] Plan migration if using old authentication methods

## 🔍 How to Check for Secrets in Git

```bash
# Search commit history for common patterns
git log -p -S "password\|secret\|token\|api_key" | head -100

# Check current branches for unprotected files
git ls-files | grep -E "\.env|credentials|secret"

# Verify gitignore is working
git check-ignore .env
git check-ignore data/watchmen.sqlite
```

## ⚡ Emergency Actions

### If credentials are accidentally committed:

1. **Immediately change passwords**
   - Anypoint password
   - Gmail password

2. **Rewrite git history** (only if NOT pushed to remote):
   ```bash
   git filter-branch --tree-filter 'rm -f .env' HEAD
   ```

3. **Create new Connected App** to invalidate old credentials

4. **Notify stakeholders** if shared with anyone

## 📞 Support

For security questions or concerns:
- Review this document
- Check `.gitignore` configuration
- Verify all credentials are in `.env` (not in code)
- Use environment variables for all secrets

## References

- [Anypoint Platform Security](https://docs.mulesoft.com/general/)
- [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [Git Secrets Best Practices](https://git-scm.com/book/en/v2/Git-Tools-Signing-Your-Work)

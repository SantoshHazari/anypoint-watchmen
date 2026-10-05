# Email Alerts Runbook

## Purpose

Validate and use the email alert channel without creating fake usage records.

## Configuration

Create a local `.env` file in the project root.

Do not commit `.env`.

Required variables:

```env
SMTP_SENDER_EMAIL=<gmail-account>
SMTP_SENDER_PASSWORD=<gmail-app-password>
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
```

Recipient is configured in:

```text
config/settings.json
```

Current recipient:

```text
alerts@example.com
```

## Test Email

Run:

```powershell
python scripts\usage_status.py --test-email
```

Expected output:

```json
{
  "test_email_sent": 1
}
```

This sends one synthetic alert named:

```text
Watchmen SMTP validation
```

It does not:

- query Anypoint
- insert usage records
- modify entitlement status
- fake threshold crossings

## Real Alert Execution

Run usage status with alert dispatch:

```powershell
python scripts\usage_status.py --write-alerts
```

Email sends only when a threshold event exists.

With current zero usage, expected result:

```json
{
  "alerts": {
    "file": 0,
    "email": 0
  }
}
```

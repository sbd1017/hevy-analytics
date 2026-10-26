# Running Hevy Analytics on a server

Two services and one SQLite file:

```
Hevy API ──> hevy-sync (worker: holds the key, polls Hevy) ──> /var/lib/hevy-analytics/hevy.sqlite3
                                                                         │
browser ──> nginx (your login) ──> hevy-web (web app: no keys, no outbound network) ──┘
```

The examples assume Debian or Ubuntu with systemd and nginx, the code in `/opt/hevy-analytics` and the data in
`/var/lib/hevy-analytics`. Change the paths, the site address and the time zone to suit.

## Install

```bash
# accounts: the web app and the worker run as different users that share one group
sudo groupadd --system hevy
sudo useradd --system --gid hevy --no-create-home --shell /usr/sbin/nologin hevy-web
sudo useradd --system --gid hevy --no-create-home --shell /usr/sbin/nologin hevy-sync

# code and Python environment
sudo git clone https://github.com/sbd1017/hevy-analytics /opt/hevy-analytics
cd /opt/hevy-analytics
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt

# data folder: shared by the group, never served by nginx
sudo install -d -m 2770 -o hevy-sync -g hevy /var/lib/hevy-analytics

# keys: root-only files, handed to the worker alone by systemd (typed at a hidden prompt, never on a command line)
sudo install -d -m 0700 /etc/hevy-analytics
sudo bash -c 'umask 077; IFS= read -rsp "Hevy API key: " k; echo; printf %s "$k" > /etc/hevy-analytics/api-key'
sudo install -m 0600 /dev/null /etc/hevy-analytics/openai-api-key      # empty: the OpenAI option is off

# services
sudo cp deploy/systemd/hevy-*.service deploy/systemd/hevy-*.timer /etc/systemd/system/
sudoedit /etc/systemd/system/hevy-web.service          # set HEVY_PUBLIC_ORIGINS and HEVY_TIMEZONE
sudo systemctl daemon-reload
sudo systemctl enable --now hevy-sync hevy-web hevy-backup.timer
```

The first sync downloads your whole history (a few minutes for a few hundred workouts). Watch it with
`journalctl -u hevy-sync -f`, or on the app's System page.

## The login

In production the app refuses to start without a login in front of it. With `HEVY_AUTH=proxy` (as in the example
unit), it trusts the `X-Remote-User` header from a reverse proxy on the same machine, and only from `127.0.0.1`. The
proxy must authenticate the visitor, set that header itself and never pass a client's own copy.

[deploy/nginx/hevy.conf](../deploy/nginx/hevy.conf) does this with an nginx password (HTTP basic auth over HTTPS).
Authelia, oauth2-proxy, Cloudflare Access or Tailscale Serve in front of nginx work the same way, as long as nginx sets
`X-Remote-User` from their result.

Every change the page makes (Sync now, settings) also needs the page's own CSRF token and an `Origin` that matches
`HEVY_PUBLIC_ORIGINS`, so other sites can't act in your name.

Optional: if other people have accounts on the same machine, set `SITE_PROXY_KEY_FILE` (or a systemd credential named
`site-proxy-key`) to a random secret and have the proxy send it as `X-Site-Proxy-Key`. Then a local process can't
pose as the proxy.

## Settings

All settings are environment variables in the units. The API keys are never settings: the worker reads them from
files.

| Variable | Default | Meaning |
|---|---|---|
| `HEVY_ENV` | `production` | `development` serves this computer only and allows key files from `HEVY_API_KEY_FILE` |
| `HEVY_DATA_DIR` | `./var` | Where the database and backups live |
| `HEVY_HOST`, `HEVY_PORT` | `127.0.0.1`, `8778` | Where the web app listens (keep it on localhost) |
| `HEVY_PUBLIC_ORIGINS` | `https://example.com` | Your site's address, for the same-origin checks (comma-separated) |
| `HEVY_TIMEZONE` | `Europe/London` | Days and weeks are counted in this zone |
| `HEVY_AUTH` | (none) | `proxy`: trust `X-Remote-User` from the local reverse proxy |
| `HEVY_SYNC_INTERVAL_S` | `600` | How often the worker checks Hevy for changes (at least 120) |
| `HEVY_COACH_DAILY_CALLS` | `8` | OpenAI API calls a day, at most |
| `HEVY_COACH_MODEL` | newest `gpt-N` | OpenAI model for suggestions |
| `HEVY_DEMO` | (none) | `1`: the public demo mode (made-up data, no login, nothing connects anywhere) |

The app always lives under `/hevy/`.

## How syncing works

Hevy has no webhooks, so the worker polls:

- every ~10 minutes for changed or deleted workouts, which is cheap and usually one request;
- daily, early in the morning, for exercises, routines and body measurements, plus a count check;
- weekly, a full comparison;
- straight away when you press Sync now on the System page.

Failures back off (1, 2, 4 … 60 minutes; 6 hours if Hevy refuses the key) and are shown on the System page. Nothing is
ever deleted from the raw copy: edits and deletions in Hevy are stored as new versions.

## Next workout from the OpenAI API

Pay-as-you-go suggestions made by the worker, after each new workout and when you press Recalculate:

```bash
sudo bash -c 'umask 077; IFS= read -rsp "OpenAI API key: " k; echo; printf %s "$k" > /etc/hevy-analytics/openai-api-key'
sudo systemctl restart hevy-sync
```

Each call sends your programme routines, the last 21 days of sessions, recent muscle work and the last performance of
each programme exercise, and nothing else. To turn it off, empty the file
(`sudo truncate -s 0 /etc/hevy-analytics/openai-api-key`) and restart the worker. For the free option that uses a
ChatGPT plan instead, see [CHATGPT.md](CHATGPT.md).

## Backups and updates

`hevy-backup.timer` keeps 30 daily copies in `/var/lib/hevy-analytics/backups/`. Copy them off the machine too (rclone,
restic, …). Check a backup with `python -m hevy.cli restore-check FILE`.

To update:

```bash
cd /opt/hevy-analytics && sudo git pull && sudo .venv/bin/pip install -r requirements.txt
sudo systemctl restart hevy-sync hevy-web
```

Database changes are applied automatically at start. The calculated tables are rebuilt when their rules change.

## Commands

Run them as the worker's user, with the same environment as the units:

```bash
sudo -u hevy-sync env HEVY_ENV=production HEVY_DATA_DIR=/var/lib/hevy-analytics .venv/bin/python -m hevy.cli status
```

| Command | Does |
|---|---|
| `status` | sync health, counts, recent runs |
| `request-sync` | ask the running worker to sync now |
| `verify` | integrity check, and "would a rebuild give the same result?" |
| `rebuild-normalised`, `rebuild-analytics` | recompute from the raw copy (no Hevy calls) |
| `backup [--keep N]`, `restore-check FILE` | consistent copy; check a copy without changing it |
| `gpt-key new\|revoke\|status` | the key for your GPT in ChatGPT (docs/CHATGPT.md) |
| `demo-seed` | fill an empty database with made-up history (development or `HEVY_DEMO=1` only) |

# Claude Code Remote Control (always-on)

**beefy** runs **Claude Code Remote Control** as a persistent background service, so the
host is always reachable from **[claude.ai/code](https://claude.ai/code)** and the
**Claude mobile app** without touching the terminal (no VS Code, no SSH).

`claude remote-control` runs a persistent server that registers the local machine with
your Claude account. Sessions started from the web/phone run **here, on beefy**, with
full access to the local filesystem, tools and MCP servers — only the UI is remote.
The session shows up in the session list named **`beefy`** (the `--name`).

> Mirrors the fastpi setup (`see the Pi's equivalent doc`). Main differences on beefy:
> it uses the **native standalone `claude`** build (`~/.local/bin/claude`, no fnm/node),
> and a one-time consent + trust had to be recorded so the service can run headless
> (see "First-time setup" below).

> Requires being logged in with a Claude account that has a subscription. Auth lives in
> `~/.claude/.credentials.json` and is refreshed by the CLI.

> **Hard limit:** beefy must be **powered on and online**. The web/phone cannot wake or
> reach a powered-off/asleep machine, nor start a session if this service isn't running.
> See `5-Sleep-and-WOL.md` to wake beefy first (`wakeonlan 74:56:3c:96:79:a3` from fastpi).

---

## How to use it

1. Open **[claude.ai/code](https://claude.ai/code)** (or the Claude mobile app) on any device.
2. Pick the session named **`beefy`** from the session list (under "Remote Control").
3. Type — it runs in beefy's home directory (`/home/buntu`). New sessions are created in
   the same directory (capacity 32). The service keeps one session pre-created so there's
   always somewhere to type.

Nothing to copy/paste per session — as long as the service is running, `beefy` is listed
automatically.

---

## The service

systemd **user** service (not system-wide), so it runs as `buntu` with the user's
environment and Claude credentials.

**Unit file:** `~/.config/systemd/user/claude-remote.service`

```ini
[Unit]
Description=Claude Code Remote Control (drive local sessions from claude.ai/code & the Claude mobile app)
# Never permanently give up: always keep retrying (no start-rate limit) so a
# transient crash-loop (auth/network blip) can't leave the service dead until reboot.
StartLimitIntervalSec=0

[Service]
Type=simple

# The directory Claude works in. Change to a specific project/repo if you prefer.
WorkingDirectory=%h

# Stable PATH. beefy uses the native standalone build at ~/.local/bin/claude
# (no fnm/node needed, unlike fastpi).
Environment=HOME=%h
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin

ExecStart=%h/.local/bin/claude remote-control --name beefy --remote-control-session-name-prefix beefy --permission-mode auto

# Stop cleanly: the server handles SIGINT (Ctrl+C) gracefully; plain SIGTERM is
# ignored and would force a slow 90s SIGKILL on every restart.
KillSignal=SIGINT
TimeoutStopSec=30

# Resilience: come back from crashes, network blips, transient auth hiccups
Restart=always
RestartSec=10

# Memory ceiling so a long-running/leaking session can't take the host down.
# MemoryHigh throttles first (soft), MemoryMax is the hard kill line.
MemoryHigh=1500M
MemoryMax=2G

[Install]
WantedBy=default.target
```

### What makes it "always available"

- **`enabled`** — symlinked into `default.target.wants`, so it starts at user login / boot.
- **Linger enabled for `buntu`** (`loginctl enable-linger buntu`) — the user manager starts
  at **boot without anyone logging in**, so the service is up after a reboot/power cycle.
  This is the key piece; a user service without linger only runs while you're logged in.
- **`Restart=always` / `RestartSec=10`** — auto-recovers from crashes, network blips or
  transient auth hiccups.
- **`StartLimitIntervalSec=0`** — no start-rate limit, so a transient crash-loop can't
  trip systemd's "give up" threshold and leave the service dead until the next reboot.
- **`KillSignal=SIGINT` / `TimeoutStopSec=30`** — the server only stops gracefully on
  SIGINT (Ctrl+C); without this, `stop`/`restart` hangs ~90s then SIGKILLs.
- **Memory ceiling (`MemoryHigh=1500M` / `MemoryMax=2G`)** — long-running Claude sessions
  accumulate memory; this throttles then hard-caps the cgroup so a leak can't take beefy down.
- **Nightly restart timer** (see below) — reclaims memory, refreshes auth, and picks up
  any `claude` update once a day.
- **Native standalone binary** — `~/.local/bin/claude` is a stable, self-updating path,
  unlike the VS Code extension's version-stamped binary (which moves on every update).

### Nightly restart timer

A oneshot service + timer restart Remote Control every night at 04:00. This is the
standard "just restart it" mitigation for the long-uptime risks above (memory growth,
silent OAuth-refresh stalls, running a stale binary after an update).

**`~/.config/systemd/user/claude-remote-restart.service`**

```ini
[Unit]
Description=Nightly restart of Claude Code Remote Control (reclaim memory, refresh auth, pick up updates)
Wants=claude-remote.service

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl --user restart claude-remote.service
```

**`~/.config/systemd/user/claude-remote-restart.timer`**

```ini
[Unit]
Description=Nightly restart timer for Claude Code Remote Control

[Timer]
# Every day at 04:00 local time; Persistent catches up if beefy was off at 04:00.
OnCalendar=*-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable + inspect:

```bash
systemctl --user daemon-reload
systemctl --user enable --now claude-remote-restart.timer
systemctl --user list-timers claude-remote-restart.timer
```

---

## First-time setup (beefy-specific)

These one-time steps had to be done so the headless service starts cleanly. They're
already done — kept here for rebuilds.

```bash
# 1. Install the native, self-updating CLI (shares ~/.claude config + login)
wget -qO- https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

# 2. Log in with a subscription account (creates ~/.claude/.credentials.json)
~/.local/bin/claude   # then /login   (skip if already logged in)
```

Two flags in `~/.claude.json` let the service run without an interactive TTY — without
them the headless server **silently blocks forever**:

- **`remoteDialogSeen: true`** — answers the one-time *"Enable Remote Control? (y/n)"*
  consent prompt. (Set automatically the first time you answer `y` in an interactive
  `claude remote-control`; we set it directly.)
- **`projects["/home/buntu"].hasTrustDialogAccepted: true`** (and `/home/buntu/Projects`) —
  accepts the workspace-trust dialog for the working directory.

If the consent prompt or a trust error ever comes back (e.g. the file got overwritten),
re-assert them:

```bash
python3 - <<'PY'
import json; p='/home/buntu/.claude.json'; d=json.load(open(p))
d['remoteDialogSeen']=True
for k in ('/home/buntu','/home/buntu/Projects'):
    e=d.setdefault('projects',{}).setdefault(k,{}); e['hasTrustDialogAccepted']=True
json.dump(d,open(p,'w'),indent=2); print('ok')
PY
systemctl --user restart claude-remote.service
```

---

## Managing it

> From a fresh, non-login shell, first: `export XDG_RUNTIME_DIR=/run/user/$(id -u)`

```bash
# Status / is it running?
systemctl --user status claude-remote.service

# Live logs (includes the session URL on (re)start)
journalctl --user -u claude-remote.service -f

# Restart / stop / start
systemctl --user restart claude-remote.service
systemctl --user stop claude-remote.service
systemctl --user start claude-remote.service

# Disable autostart (keep the file) / re-enable
systemctl --user disable claude-remote.service
systemctl --user enable claude-remote.service

# After editing the unit file
systemctl --user daemon-reload
systemctl --user restart claude-remote.service

# Update the CLI, then apply it
claude update && systemctl --user restart claude-remote.service
```

Health check (what was verified at setup):

```bash
systemctl --user is-active claude-remote.service                 # -> active
systemctl --user show claude-remote.service -p NRestarts --value # -> 0 (no crash loop)
journalctl --user -u claude-remote.service | grep -i Ready       # -> "Ready ... Capacity: 1/32"
ss -tnp | grep claude | grep ESTAB                               # -> established TLS to 160.79.x.x:443
```

Linger (only needed once; already set):

```bash
loginctl enable-linger buntu      # survive reboots without login
loginctl show-user buntu | grep Linger
```

---

## Security note — permission mode

The service runs with **`--permission-mode auto`** (matching fastpi). Other modes you can
set on the `ExecStart` line (then daemon-reload + restart):

```ini
# Safest — prompts before edits/commands (approve from web/phone)
ExecStart=%h/.local/bin/claude remote-control --name beefy --permission-mode default

# Auto-accept file edits, prompt for everything else
ExecStart=%h/.local/bin/claude remote-control --name beefy --permission-mode acceptEdits

# No prompts at all — full unattended control of beefy (convenient, higher risk)
ExecStart=%h/.local/bin/claude remote-control --name beefy --permission-mode bypassPermissions
```

```bash
systemctl --user daemon-reload && systemctl --user restart claude-remote.service
```

Because this grants control of the host, keep the Claude account locked down (strong
auth + MFA).

---

## Recreating from scratch

```bash
# CLI + login (see "First-time setup" above), then the consent/trust flags, then:
mkdir -p ~/.config/systemd/user
# write the three unit files above:
#   ~/.config/systemd/user/claude-remote.service
#   ~/.config/systemd/user/claude-remote-restart.service
#   ~/.config/systemd/user/claude-remote-restart.timer
loginctl enable-linger buntu
systemctl --user daemon-reload
systemctl --user enable --now claude-remote.service
systemctl --user enable --now claude-remote-restart.timer
systemctl --user status claude-remote.service
systemctl --user list-timers claude-remote-restart.timer
```

---

_Set up 2026-06-15. Verified: service `active`, 0 restarts, reached `Ready` (Capacity 1/32),
established TLS to Anthropic's API. Session name: `beefy`._

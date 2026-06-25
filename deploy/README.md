# deploy/ — Process Supervision for the AI S&P Trading Bot

This directory contains process-supervision artifacts for running the bot
unattended. The primary supervisor is macOS launchd (D-05); a portable shell
fallback (`run_forever.sh`) is provided for Linux, CI, or quick testing.

---

## macOS — launchd LaunchAgent (Recommended, D-05/D-06)

launchd is the macOS-native supervisor. It survives reboots, restart-limits,
and partial network loss without any Python dependency.

### One-time setup

1. **Edit the template** — fill in every `OPERATOR` placeholder in
   `com.bot.trading.plist`:
   - `ProgramArguments` — full absolute path to your virtualenv Python
     (e.g. `/Users/you/venv/bin/python`)
   - `WorkingDirectory` — absolute path to the project root
   - `StandardOutPath` / `StandardErrorPath` — absolute paths under `logs/`
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — your Telegram secrets

2. **Secure the plist** (required — it contains secrets):
   ```bash
   chmod 600 ~/Library/LaunchAgents/com.bot.trading.plist
   ```

   **IMPORTANT:** Do NOT commit a filled-in copy of this file. Keep secrets
   out of git history. The template ships with placeholder values only.

3. **Copy the plist** to the LaunchAgents directory:
   ```bash
   cp deploy/com.bot.trading.plist ~/Library/LaunchAgents/com.bot.trading.plist
   chmod 600 ~/Library/LaunchAgents/com.bot.trading.plist
   ```

### launchctl commands

```bash
# Load and start (runs immediately due to RunAtLoad=true):
launchctl load ~/Library/LaunchAgents/com.bot.trading.plist

# Check status (exit status 0 = running; non-zero = stopped/failed):
launchctl list | grep com.bot.trading

# Unload (stops the bot and disables auto-start on login):
launchctl unload ~/Library/LaunchAgents/com.bot.trading.plist
```

### Key plist settings

| Key | Value | Purpose |
|-----|-------|---------|
| `KeepAlive` | `true` | Restart the process if it exits for any reason (D-05) |
| `RunAtLoad` | `true` | Start immediately when the agent is loaded (and on login) |
| `ThrottleInterval` | `30` | Minimum seconds between crash-restarts; matches `service.launchd_throttle_interval_s` in `rules.json` (D-06) |
| `EnvironmentVariables.PAPER_TRADING` | `true` | Hardcoded paper-safety flag — never change to `false` here (T-05-04-SAFE) |
| `EnvironmentVariables.FUTU_TRD_ENV` | `SIMULATE` | Hardcoded to prevent accidental live trading |
| `EnvironmentVariables.PATH` | explicit | launchd does not source shell profiles; venv Python path relies on this |

### Crash-loop behavior (D-06)

If the bot crashes rapidly and repeatedly, launchd keeps trying but waits at
least `ThrottleInterval` seconds between restarts. The bot itself fires a
Telegram alert when detecting repeated restarts. To stop a crash-loop:

```bash
launchctl unload ~/Library/LaunchAgents/com.bot.trading.plist
# Diagnose from logs:
tail -100 logs/bot.stderr.log
```

---

## Shell fallback — run_forever.sh (Portable, D-05)

`run_forever.sh` is a simple `while true` supervisor loop. Use it on Linux,
in Docker, CI/CD pipelines, or for quick testing before launchd setup.

```bash
cd /path/to/ai-snp-trading-claude
./deploy/run_forever.sh
```

The script:
1. Sources `.env` for environment variables (D-13).
2. Runs `python3 -m bot` in a loop.
3. Waits `THROTTLE_INTERVAL` seconds (default 30) after each exit.

---

## Linux — systemd (Note Only)

For Linux servers, the equivalent of launchd's LaunchAgent is a systemd user
service. Create a file at `~/.config/systemd/user/bot-trading.service`:

```ini
[Unit]
Description=AI S&P Trading Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/ai-snp-trading-claude
ExecStart=/path/to/venv/bin/python -m bot
Restart=always
RestartSec=30
EnvironmentFile=/path/to/ai-snp-trading-claude/.env

[Install]
WantedBy=default.target
```

Enable and start:
```bash
systemctl --user enable bot-trading.service
systemctl --user start bot-trading.service
systemctl --user status bot-trading.service
journalctl --user -u bot-trading.service -f
```

**Security note for systemd:** Use `chmod 600 ~/.env` and place secrets in
`.env` (not committed). The `EnvironmentFile` directive reads them at start.

---

## Log files

Logs are written to the `logs/` directory (gitignored):

| File | Content |
|------|---------|
| `logs/bot.stdout.log` | launchd stdout redirect |
| `logs/bot.stderr.log` | launchd stderr redirect |
| `logs/bot.log` | Structured rotating JSON log (structlog SVC-03, 10 MB × 5) |

To tail the structured log in real time:
```bash
tail -f logs/bot.log | python3 -c "import sys,json; [print(json.dumps(json.loads(l),indent=2)) for l in sys.stdin]"
```

# PresenceGuard

PresenceGuard is a macOS user-session security daemon that locks the screen when your phone is no longer nearby, then alerts you if keyboard or mouse activity occurs while you are still absent.

It is intentionally privacy-preserving:

- It does not capture typed content.
- It does not store keystrokes.
- It only records the existence and timestamp of input activity.
- It uses native macOS screen locking, so running processes continue normally.

## Features

- Bluetooth presence detection with a preferred `blueutil` path and a `system_profiler` fallback.
- Debounced absence detection to tolerate short Bluetooth dropouts.
- Native screen locking with `CGSession -suspend`.
- AppleScript fallback lock path with `Ctrl+Cmd+Q`.
- Keyboard and mouse activity detection using `pynput`.
- Modular notifications: `ntfy`, Telegram, or log-only test mode.
- Cooldown logic to avoid duplicate alerts.
- `launchd` LaunchAgent support for background startup.

## File layout

- `main.py`: orchestrator and state machine.
- `bluetooth.py`: Bluetooth presence polling.
- `input_monitor.py`: keyboard and mouse activity timestamps.
- `locker.py`: macOS screen locking.
- `notifier.py`: notification providers.
- `settings.py`: YAML config loading and validation.
- `config.yaml`: sample configuration.
- `launchd/com.presenceguard.daemon.plist`: sample LaunchAgent.

## State machine

- `PRESENT`: phone has been seen recently.
- `AWAY`: phone has exceeded the configured absence timeout.
- `LOCKED`: screen lock has been triggered and intrusion monitoring is active.

Transitions:

1. `PRESENT -> AWAY` when the configured phone has not been detected for `bluetooth.away_timeout_seconds`.
2. `AWAY -> LOCKED` immediately after a lock command is issued.
3. `LOCKED -> PRESENT` when the configured phone becomes present again.
4. While in `LOCKED`, any new keyboard or mouse activity sends an alert if the phone is still absent.

## Requirements

- macOS user session.
- Python 3.9+.
- `PyYAML` and `pynput` from `requirements.txt`.
- Optional but recommended: `blueutil` for faster Bluetooth checks.

Install `blueutil` with Homebrew:

```bash
brew install blueutil
```

## Installation

1. Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Edit `config.yaml`:

- Set `bluetooth.device_mac` to your phone MAC address when possible.
- Optionally set `bluetooth.device_name`.
- Set `notify.ntfy.topic` or configure Telegram.

3. Run in test mode first:

```bash
python3 main.py --config config.yaml --test --debug
```

Test mode keeps all monitoring active but logs instead of locking the screen or sending real notifications.

4. Run for real once permissions are granted:

```bash
python3 main.py --config config.yaml
```

## macOS permissions

PresenceGuard needs user-session privacy permissions because it listens for input events and may use the AppleScript lock fallback.

Grant these in `System Settings -> Privacy & Security`:

- `Input Monitoring`: allow the Python interpreter you run with, or your terminal app during manual testing.
- `Accessibility`: allow the same interpreter or terminal app.

Notes:

- If you launch the daemon with `launchd`, macOS may attribute permissions to the Python interpreter path used in the plist, not just Terminal.
- `CGSession -suspend` usually does not need extra automation privileges.
- The AppleScript fallback may require Accessibility access for `osascript` / `System Events`.

## Bluetooth detection behavior

PresenceGuard checks Bluetooth in this order:

1. `blueutil --is-connected <MAC>` when a MAC address is configured.
2. `blueutil --connected`.
3. `system_profiler SPBluetoothDataType`.

Operational notes:

- `blueutil` is strongly recommended. It is lighter and more suitable for short polling intervals.
- `system_profiler` is much slower, so fallback results are cached via `bluetooth.fallback_cache_seconds`.
- The daemon does not rely on RSSI or continuous scanning. It uses connection visibility and known-device presence heuristics.
- Best reliability comes from using a paired iPhone with a configured MAC address.

## Locking behavior

Primary lock path:

```bash
/System/Library/CoreServices/Menu\ Extras/User.menu/Contents/Resources/CGSession -suspend
```

Fallback:

```bash
osascript -e 'tell application "System Events" to keystroke "q" using {control down, command down}'
```

This locks the session without terminating user-space processes. Background scripts, shells, and long-running jobs continue to run.

## Notification configuration

### ntfy

Example section in `config.yaml`:

```yaml
notify:
  provider: ntfy
  ntfy:
    server_url: "https://ntfy.sh"
    topic: "presenceguard-demo"
```

### Telegram

```yaml
notify:
  provider: telegram
  telegram:
    bot_token: "123456:ABCDEF"
    chat_id: "12345678"
```

## CLI flags

- `--config`: path to YAML config.
- `--test`: test mode; no real lock and no real outbound notifications.
- `--no-lock`: skip locking but still monitor absence and input.
- `--debug`: verbose logging.

## launchd integration

Use a per-user LaunchAgent, not a system LaunchDaemon. This daemon interacts with the logged-in GUI session, Bluetooth state, and TCC permissions.

1. Copy the sample plist:

```bash
cp launchd/com.presenceguard.daemon.plist ~/Library/LaunchAgents/com.presenceguard.daemon.plist
```

2. Edit the copied plist and replace every `YOUR_USER` path with real absolute paths.

3. Load it:

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.presenceguard.daemon.plist
launchctl kickstart -k "gui/$(id -u)/com.presenceguard.daemon"
```

Stop it:

```bash
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.presenceguard.daemon.plist
```

Check status:

```bash
launchctl print "gui/$(id -u)/com.presenceguard.daemon"
```

## Debugging

Manual debug run:

```bash
python3 main.py --config config.yaml --test --debug
```

Common checks:

- `blueutil --connected`
- `blueutil --is-connected AA-BB-CC-DD-EE-FF`
- `system_profiler SPBluetoothDataType`
- `tail -f ~/Library/Logs/PresenceGuard.stdout.log`
- `tail -f ~/Library/Logs/PresenceGuard.stderr.log`

## Safety notes

- Input activity is used only as a boolean event source.
- No keylogging is implemented.
- No input contents are written to disk.
- The design stays in user space and does not require root.
- If Bluetooth reliability is poor in your environment, increase `bluetooth.away_timeout_seconds` slightly before relying on it in production.

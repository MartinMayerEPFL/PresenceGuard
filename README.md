# PresenceGuard

PresenceGuard is a macOS user-session security daemon that locks the screen when your phone is no longer nearby, then alerts you if keyboard, mouse, or USB activity occurs while you are still absent.

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
- USB device attachment detection using `system_profiler SPUSBDataType`.
- Optional intrusion photo capture with `imagesnap` or `ffmpeg`.
- Modular notifications: `ntfy`, Telegram, or log-only test mode.
- Cooldown logic to avoid duplicate alerts.
- `launchd` LaunchAgent support for background startup.

## File layout

- `main.py`: orchestrator and state machine.
- `bluetooth.py`: Bluetooth presence polling.
- `input_monitor.py`: keyboard and mouse activity timestamps.
- `locker.py`: macOS screen locking.
- `notifier.py`: notification providers.
- `usb_monitor.py`: USB inventory polling and delta detection.
- `camera.py`: optional webcam capture.
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
5. While in `LOCKED`, any newly attached USB device also sends an alert.

## Requirements

- macOS user session.
- Python 3.9+.
- `PyYAML` and `pynput` from `requirements.txt`.
- Optional but recommended: `blueutil` for faster Bluetooth checks.
- Optional for photo capture: `imagesnap` or `ffmpeg`.

Install `blueutil` with Homebrew:

```bash
brew install blueutil
```

Optional camera tools:

```bash
brew install imagesnap
```

If you already have `ffmpeg` with `avfoundation` support, PresenceGuard can use that as a fallback.

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
- If you want photo delivery, set `notify.provider: telegram` and fill `telegram.bot_token` and `telegram.chat_id`.
- Enable `camera.enabled: true` only after Camera permission is granted.

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
- `Camera`: allow the capture backend you use (`imagesnap`, `ffmpeg`, or the host app that triggers the prompt) if you enable photo capture.

Notes:

- If you launch the daemon with `launchd`, macOS may attribute permissions to the Python interpreter path used in the plist, not just Terminal.
- `CGSession -suspend` usually does not need extra automation privileges.
- The AppleScript fallback may require Accessibility access for `osascript` / `System Events`.
- Camera access is only required if `camera.enabled: true`.

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

## USB intrusion detection

PresenceGuard maintains a baseline of USB devices while you are present. Once the machine is in `LOCKED`, any newly attached USB device is treated as an intrusion event.

This covers cases such as:

- a USB keyboard or mouse being plugged in
- a USB flash drive being inserted
- a USB Ethernet adapter or hub appearing

Operational notes:

- USB changes are detected by polling `system_profiler SPUSBDataType -json`.
- Built-in internal devices become part of the normal baseline and do not trigger alerts by themselves.
- If you connect a legitimate USB device while you are present, it becomes part of the baseline automatically.

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

When `provider=telegram`, PresenceGuard can attach the intrusion photo if camera capture is enabled and succeeds.

## Camera capture

Example configuration:

```yaml
camera:
  enabled: true
  method: auto
  save_directory: "/tmp/presenceguard-captures"
  ffmpeg_input: "0:none"
  retain_local_copy: false
```

Behavior:

- `auto` tries `imagesnap` first, then `ffmpeg`.
- If photo capture fails, the text alert is still sent.
- Photos are deleted after sending unless `retain_local_copy: true`.
- Photo attachment is only used by the Telegram notifier. `ntfy` remains text-only.

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
- `system_profiler SPUSBDataType -json -detailLevel mini`
- `ffmpeg -f avfoundation -list_devices true -i ""`
- `tail -f ~/Library/Logs/PresenceGuard.stdout.log`
- `tail -f ~/Library/Logs/PresenceGuard.stderr.log`

## Safety notes

- Input activity is used only as a boolean event source.
- No keylogging is implemented.
- No input contents are written to disk.
- The design stays in user space and does not require root.
- If Bluetooth reliability is poor in your environment, increase `bluetooth.away_timeout_seconds` slightly before relying on it in production.

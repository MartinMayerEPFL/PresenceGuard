from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from bluetooth import BluetoothPresenceDetector
from input_monitor import InputActivityMonitor
from locker import ScreenLocker
from notifier import NotificationManager
from settings import AppConfig, load_config


class PresenceState(str, Enum):
    PRESENT = "PRESENT"
    AWAY = "AWAY"
    LOCKED = "LOCKED"


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


class PresenceGuardDaemon:
    def __init__(
        self,
        config: AppConfig,
        *,
        no_lock: bool = False,
        test_mode: bool = False,
    ) -> None:
        self.config = config
        self.logger = logging.getLogger("presenceguard")
        self.state = PresenceState.PRESENT
        self.stop_event = threading.Event()

        self.detector = BluetoothPresenceDetector(config.bluetooth)
        self.input_monitor = InputActivityMonitor()
        self.locker = ScreenLocker(config.lock, no_lock=no_lock, test_mode=test_mode)
        self.notifier = NotificationManager(config.notify, test_mode=test_mode)

        self.last_present_at = time.monotonic()
        self.locked_at: Optional[float] = None
        self.ignore_input_until = 0.0
        self.last_processed_activity_at = 0.0

    def start(self) -> None:
        self.logger.info(
            "starting daemon target=%s away_timeout_seconds=%.1f poll_interval_seconds=%.2f",
            self.config.bluetooth.target_label,
            self.config.bluetooth.away_timeout_seconds,
            self.config.app.poll_interval_seconds,
        )
        self.input_monitor.start()
        self._install_signal_handlers()
        self.run()

    def stop(self) -> None:
        self.stop_event.set()
        self.input_monitor.stop()
        self.logger.info("daemon stopped")

    def run(self) -> None:
        while not self.stop_event.is_set():
            loop_started_at = time.monotonic()
            self._tick(loop_started_at)
            sleep_for = max(0.0, self.config.app.poll_interval_seconds - (time.monotonic() - loop_started_at))
            self.stop_event.wait(sleep_for)

    def _tick(self, now: float) -> None:
        snapshot = self.detector.poll()
        if snapshot.present:
            self.last_present_at = now

        effective_present = (now - self.last_present_at) <= self.config.bluetooth.away_timeout_seconds

        if self.config.app.debug:
            self.logger.debug(
                "poll state=%s raw_present=%s effective_present=%s source=%s evidence=%s",
                self.state.value,
                snapshot.present,
                effective_present,
                snapshot.source,
                snapshot.evidence,
            )

        if self.state == PresenceState.PRESENT:
            if not effective_present:
                self._enter_away(now)
            return

        if self.state == PresenceState.LOCKED:
            if effective_present:
                self._transition(PresenceState.PRESENT, "phone detected nearby again")
                self.locked_at = None
                self.ignore_input_until = 0.0
                return

            self._handle_locked_activity(now)
            return

        if self.state == PresenceState.AWAY:
            self._enter_locked(now)

    def _enter_away(self, now: float) -> None:
        away_duration = now - self.last_present_at
        self._transition(
            PresenceState.AWAY,
            f"target not detected for {away_duration:.1f}s",
        )
        self._enter_locked(now)

    def _enter_locked(self, now: float) -> None:
        lock_successful = self.locker.lock()
        self.locked_at = now
        self.ignore_input_until = now + self.config.lock.ignore_input_after_lock_seconds
        current_activity = self.input_monitor.get_last_activity()
        self.last_processed_activity_at = current_activity.when if current_activity else now
        self._transition(
            PresenceState.LOCKED,
            f"screen lock issued success={lock_successful}",
        )

    def _handle_locked_activity(self, now: float) -> None:
        activity = self.input_monitor.get_last_activity()
        if not activity:
            return

        if activity.when <= self.last_processed_activity_at:
            return

        self.last_processed_activity_at = activity.when

        if activity.when < self.ignore_input_until:
            self.logger.debug(
                "ignoring activity during post-lock quiet period source=%s",
                activity.source,
            )
            return

        self.logger.warning(
            "input detected while absent source=%s locked_for_seconds=%.1f",
            activity.source,
            now - (self.locked_at or now),
        )
        self.notifier.send_intrusion_alert(
            source=activity.source,
            target_label=self.config.bluetooth.target_label,
        )

    def _transition(self, new_state: PresenceState, reason: str) -> None:
        old_state = self.state
        self.state = new_state
        self.logger.info("state transition %s -> %s reason=%s", old_state.value, new_state.value, reason)

    def _install_signal_handlers(self) -> None:
        def _handle_signal(signum: int, _frame: object) -> None:
            signal_name = signal.Signals(signum).name
            self.logger.info("received signal=%s", signal_name)
            self.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _handle_signal)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PresenceGuard macOS security daemon")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Enable test mode: logs actions and notifications without locking",
    )
    parser.add_argument(
        "--no-lock",
        action="store_true",
        help="Disable screen locking while keeping presence and intrusion monitoring active",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)

    if args.debug:
        config.app.log_level = "DEBUG"
        config.app.debug = True
    if args.test:
        config.app.test_mode = True

    setup_logging(config.app.log_level)
    logger = logging.getLogger("presenceguard")
    logger.debug("loaded config from %s", config_path)

    try:
        daemon = PresenceGuardDaemon(
            config,
            no_lock=args.no_lock,
            test_mode=args.test or config.app.test_mode,
        )
        daemon.start()
        return 0
    except KeyboardInterrupt:
        logger.info("interrupted by keyboard")
        return 0
    except Exception as exc:
        logger.exception("fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

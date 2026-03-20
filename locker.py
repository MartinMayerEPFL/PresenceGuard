from __future__ import annotations

import logging
import os
import subprocess
from typing import Sequence

from settings import LockConfig


LOGGER = logging.getLogger("presenceguard.locker")


CGSESSION_PATH = "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession"


class ScreenLocker:
    def __init__(self, config: LockConfig, *, no_lock: bool = False, test_mode: bool = False) -> None:
        self.config = config
        self.no_lock = no_lock
        self.test_mode = test_mode

    def lock(self) -> bool:
        if self.no_lock or self.test_mode or not self.config.enabled:
            LOGGER.info(
                "lock suppressed enabled=%s no_lock=%s test_mode=%s",
                self.config.enabled,
                self.no_lock,
                self.test_mode,
            )
            return True

        method = self.config.method.lower()
        if method in {"auto", "cgsession"} and os.path.exists(CGSESSION_PATH):
            if self._run([CGSESSION_PATH, "-suspend"], "cgsession"):
                return True
            if method == "cgsession":
                return False

        if method in {"auto", "applescript"}:
            return self._run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to keystroke "q" using {control down, command down}',
                ],
                "applescript",
            )

        LOGGER.error("unsupported lock method=%s", self.config.method)
        return False

    def _run(self, command: Sequence[str], label: str) -> bool:
        try:
            result = subprocess.run(
                list(command),
                capture_output=True,
                check=False,
                text=True,
                timeout=self.config.command_timeout_seconds,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            LOGGER.error("lock command failed method=%s error=%s", label, exc)
            return False

        if result.returncode != 0:
            LOGGER.error(
                "lock command returned non-zero method=%s code=%s stderr=%s",
                label,
                result.returncode,
                (result.stderr or "").strip(),
            )
            return False

        LOGGER.info("screen lock triggered method=%s", label)
        return True

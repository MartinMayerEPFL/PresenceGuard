from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from settings import BluetoothConfig


LOGGER = logging.getLogger("presenceguard.bluetooth")


@dataclass
class PresenceSnapshot:
    present: bool
    source: str
    evidence: str
    definitive: bool = False


class BluetoothPresenceDetector:
    def __init__(self, config: BluetoothConfig) -> None:
        self.config = config
        self._blueutil_path = shutil.which("blueutil")
        self._cached_snapshot = PresenceSnapshot(
            present=False,
            source="bootstrap",
            evidence="not yet polled",
            definitive=False,
        )
        self._last_fallback_poll_at = 0.0
        if self._blueutil_path:
            LOGGER.info("bluetooth detector using blueutil path=%s", self._blueutil_path)
        else:
            LOGGER.info("bluetooth detector using system_profiler fallback only")

    def poll(self) -> PresenceSnapshot:
        if self._blueutil_path:
            snapshot = self._poll_blueutil()
            if snapshot.present or snapshot.definitive:
                return snapshot

        return self._poll_system_profiler_cached()

    def _poll_blueutil(self) -> PresenceSnapshot:
        if self.config.device_mac:
            snapshot = self._poll_blueutil_is_connected(self.config.device_mac)
            if snapshot is not None:
                return snapshot

        output = self._run_command([self._blueutil_path, "--connected"])
        if output is None:
            return PresenceSnapshot(
                present=False,
                source="blueutil",
                evidence="connected list unavailable",
                definitive=False,
            )

        matched = self._output_matches_target(output)
        return PresenceSnapshot(
            present=matched,
            source="blueutil",
            evidence="matched connected device list" if matched else "target not in connected device list",
            definitive=matched,
        )

    def _poll_blueutil_is_connected(self, mac_address: str) -> Optional[PresenceSnapshot]:
        output = self._run_command([self._blueutil_path, "--is-connected", mac_address])
        if output is None:
            return None

        normalized = output.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return PresenceSnapshot(
                present=True,
                source="blueutil",
                evidence=f"--is-connected confirmed device_mac={self._normalize_mac(mac_address)}",
                definitive=True,
            )

        if normalized in {"0", "false", "no"}:
            return PresenceSnapshot(
                present=False,
                source="blueutil",
                evidence=f"--is-connected reported not connected device_mac={self._normalize_mac(mac_address)}",
                definitive=True,
            )

        LOGGER.debug("unexpected blueutil --is-connected output=%r", output)
        return None

    def _poll_system_profiler_cached(self) -> PresenceSnapshot:
        now = time.monotonic()
        if now - self._last_fallback_poll_at < self.config.fallback_cache_seconds:
            return self._cached_snapshot

        self._last_fallback_poll_at = now
        output = self._run_command(["system_profiler", "SPBluetoothDataType", "-detailLevel", "mini"])
        if output is None:
            self._cached_snapshot = PresenceSnapshot(
                present=False,
                source="system_profiler",
                evidence="command failed",
                definitive=False,
            )
            return self._cached_snapshot

        self._cached_snapshot = self._parse_system_profiler_output(output)
        return self._cached_snapshot

    def _parse_system_profiler_output(self, output: str) -> PresenceSnapshot:
        target_mac = self._normalize_mac(self.config.device_mac or "")
        target_name = (self.config.device_name or "").strip().lower()

        current_name: Optional[str] = None
        current_mac = ""
        current_connected: Optional[bool] = None
        current_indent = 0

        def finalize_candidate() -> Optional[PresenceSnapshot]:
            if not current_name and not current_mac:
                return None

            name_matches = bool(target_name) and current_name and current_name.strip().lower() == target_name
            mac_matches = bool(target_mac) and self._normalize_mac(current_mac) == target_mac
            if not name_matches and not mac_matches:
                return None

            return PresenceSnapshot(
                present=bool(current_connected),
                source="system_profiler",
                evidence=(
                    f"name={current_name or 'unknown'} "
                    f"mac={self._normalize_mac(current_mac) or 'unknown'} "
                    f"connected={bool(current_connected)}"
                ),
                definitive=True,
            )

        for raw_line in output.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue

            indent = len(line) - len(line.lstrip())
            lower = stripped.lower()

            if stripped.endswith(":") and not lower.startswith("address:") and not lower.startswith("connected:"):
                if current_name and indent <= current_indent:
                    candidate = finalize_candidate()
                    if candidate:
                        return candidate
                    current_name = None
                    current_mac = ""
                    current_connected = None

                if indent >= 8:
                    current_name = stripped[:-1].strip()
                    current_mac = ""
                    current_connected = None
                    current_indent = indent
                continue

            if not current_name:
                continue

            if indent <= current_indent:
                candidate = finalize_candidate()
                if candidate:
                    return candidate
                current_name = None
                current_mac = ""
                current_connected = None
                continue

            if lower.startswith("address:"):
                current_mac = stripped.split(":", 1)[1].strip()
            elif lower.startswith("connected:"):
                value = stripped.split(":", 1)[1].strip().lower()
                current_connected = value in {"yes", "true", "connected"}

        candidate = finalize_candidate()
        if candidate:
            return candidate

        return PresenceSnapshot(
            present=False,
            source="system_profiler",
            evidence="target not found or not connected",
            definitive=True,
        )

    def _run_command(self, command: list[str]) -> Optional[str]:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=self.config.command_timeout_seconds,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            LOGGER.debug("command failed command=%s error=%s", command, exc)
            return None

        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            LOGGER.debug(
                "command returned non-zero command=%s code=%s output=%r",
                command,
                result.returncode,
                output,
            )
            return None

        return output.strip()

    def _output_matches_target(self, output: str) -> bool:
        normalized_output = output.lower()
        if self.config.device_name and self.config.device_name.lower() in normalized_output:
            return True

        if not self.config.device_mac:
            return False

        target_mac = self._normalize_mac(self.config.device_mac)
        for mac_candidate in re.findall(r"([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})", output):
            if self._normalize_mac(mac_candidate) == target_mac:
                return True

        return target_mac and target_mac in self._normalize_mac(output)

    @staticmethod
    def _normalize_mac(value: str) -> str:
        return re.sub(r"[^0-9a-f]", "", value.lower())

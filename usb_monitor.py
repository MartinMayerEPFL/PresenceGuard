from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from settings import UsbConfig


LOGGER = logging.getLogger("presenceguard.usb")


@dataclass(frozen=True)
class UsbDevice:
    name: str
    manufacturer: str
    vendor_id: str
    product_id: str
    serial_num: str
    location_id: str

    @property
    def fingerprint(self) -> str:
        return "|".join(
            [
                self.name.lower(),
                self.manufacturer.lower(),
                self.vendor_id.lower(),
                self.product_id.lower(),
                self.serial_num.lower(),
                self.location_id.lower(),
            ]
        )

    def describe(self) -> str:
        parts = [self.name]
        if self.manufacturer:
            parts.append(self.manufacturer)
        if self.product_id:
            parts.append(f"pid={self.product_id}")
        if self.location_id:
            parts.append(f"loc={self.location_id}")
        return " ".join(parts)


class UsbDeviceMonitor:
    def __init__(self, config: UsbConfig) -> None:
        self.config = config
        self._baseline_devices: dict[str, UsbDevice] = {}
        self._cached_devices: dict[str, UsbDevice] = {}
        self._last_poll_at = 0.0
        self._inventory_ready = False
        self._baseline_ready = False

    def refresh_baseline(self, *, force: bool = False) -> None:
        if not self.config.enabled:
            return
        devices = self.poll_devices(force=force)
        if not self._inventory_ready:
            LOGGER.warning("usb baseline refresh skipped because inventory is not ready")
            return
        self._baseline_devices = devices
        self._baseline_ready = True
        LOGGER.debug("usb baseline refreshed device_count=%s", len(self._baseline_devices))

    def get_new_devices(self) -> list[UsbDevice]:
        if not self.config.enabled:
            return []
        if not self._baseline_ready:
            LOGGER.debug("usb baseline not ready yet, skipping delta detection")
            return []

        current = self.poll_devices()
        return [device for fingerprint, device in current.items() if fingerprint not in self._baseline_devices]

    def poll_devices(self, *, force: bool = False) -> dict[str, UsbDevice]:
        now = time.monotonic()
        if not force and (now - self._last_poll_at) < self.config.poll_interval_seconds:
            return dict(self._cached_devices)

        self._last_poll_at = now
        output = self._run_command(
            ["system_profiler", "SPUSBDataType", "-json", "-detailLevel", "mini"]
        )
        if output is None:
            return dict(self._cached_devices)

        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            LOGGER.error("unable to parse USB JSON payload error=%s", exc)
            return dict(self._cached_devices)

        devices: dict[str, UsbDevice] = {}
        self._collect_devices(payload.get("SPUSBDataType", []), devices)
        self._cached_devices = devices
        self._inventory_ready = True
        LOGGER.debug("usb inventory polled device_count=%s", len(devices))
        return dict(devices)

    def _collect_devices(self, node: Any, devices: dict[str, UsbDevice]) -> None:
        if isinstance(node, list):
            for item in node:
                self._collect_devices(item, devices)
            return

        if not isinstance(node, dict):
            return

        if "_items" in node:
            self._collect_devices(node["_items"], devices)

        if self._is_usb_device_record(node):
            device = UsbDevice(
                name=str(node.get("_name", "Unknown USB device")),
                manufacturer=str(node.get("manufacturer", "")),
                vendor_id=str(node.get("vendor_id", "")),
                product_id=str(node.get("product_id", "")),
                serial_num=str(node.get("serial_num", "")),
                location_id=str(node.get("location_id", "")),
            )
            if device.name in self.config.ignore_names:
                return
            devices[device.fingerprint] = device

        for value in node.values():
            if isinstance(value, (list, dict)):
                self._collect_devices(value, devices)

    @staticmethod
    def _is_usb_device_record(node: dict[str, Any]) -> bool:
        if "_name" not in node:
            return False
        if "host_controller" in node:
            return False
        return any(key in node for key in ("location_id", "product_id", "vendor_id", "serial_num"))

    def _run_command(self, command: list[str]) -> str | None:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=self.config.command_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            LOGGER.error("usb inventory command failed error=%s", exc)
            return None

        if result.returncode != 0:
            LOGGER.error(
                "usb inventory command returned non-zero code=%s stderr=%s",
                result.returncode,
                (result.stderr or "").strip(),
            )
            return None

        return result.stdout

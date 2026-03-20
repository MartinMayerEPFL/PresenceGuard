from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class AppSettings:
    poll_interval_seconds: float = 1.0
    log_level: str = "INFO"
    debug: bool = False
    test_mode: bool = False


@dataclass
class BluetoothConfig:
    device_name: Optional[str] = None
    device_mac: Optional[str] = None
    away_timeout_seconds: float = 15.0
    command_timeout_seconds: float = 5.0
    fallback_cache_seconds: float = 8.0

    @property
    def target_label(self) -> str:
        return self.device_name or self.device_mac or "configured phone"


@dataclass
class LockConfig:
    enabled: bool = True
    method: str = "auto"
    command_timeout_seconds: float = 5.0
    ignore_input_after_lock_seconds: float = 2.5


@dataclass
class NtfyConfig:
    server_url: str = "https://ntfy.sh"
    topic: str = ""
    priority: str = "urgent"
    tags: list[str] = field(default_factory=lambda: ["warning", "computer"])


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class NotifyConfig:
    provider: str = "ntfy"
    cooldown_seconds: float = 60.0
    timeout_seconds: float = 10.0
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


@dataclass
class AppConfig:
    app: AppSettings = field(default_factory=AppSettings)
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    lock: LockConfig = field(default_factory=LockConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    config = AppConfig(
        app=_load_app_settings(raw.get("app", {})),
        bluetooth=_load_bluetooth_settings(raw.get("bluetooth", {})),
        lock=_load_lock_settings(raw.get("lock", {})),
        notify=_load_notify_settings(raw.get("notify", {})),
    )

    if not config.bluetooth.device_name and not config.bluetooth.device_mac:
        raise ValueError("Configure bluetooth.device_name or bluetooth.device_mac in config.yaml")

    if config.app.poll_interval_seconds <= 0:
        raise ValueError("app.poll_interval_seconds must be positive")

    if config.bluetooth.away_timeout_seconds <= 0:
        raise ValueError("bluetooth.away_timeout_seconds must be positive")

    if config.notify.cooldown_seconds < 0:
        raise ValueError("notify.cooldown_seconds must be non-negative")

    return config


def _load_app_settings(data: dict[str, Any]) -> AppSettings:
    return AppSettings(
        poll_interval_seconds=float(data.get("poll_interval_seconds", 1.0)),
        log_level=str(data.get("log_level", "INFO")),
        debug=bool(data.get("debug", False)),
        test_mode=bool(data.get("test_mode", False)),
    )


def _load_bluetooth_settings(data: dict[str, Any]) -> BluetoothConfig:
    return BluetoothConfig(
        device_name=_optional_string(data.get("device_name")),
        device_mac=_optional_string(data.get("device_mac")),
        away_timeout_seconds=float(data.get("away_timeout_seconds", 15.0)),
        command_timeout_seconds=float(data.get("command_timeout_seconds", 5.0)),
        fallback_cache_seconds=float(data.get("fallback_cache_seconds", 8.0)),
    )


def _load_lock_settings(data: dict[str, Any]) -> LockConfig:
    return LockConfig(
        enabled=bool(data.get("enabled", True)),
        method=str(data.get("method", "auto")),
        command_timeout_seconds=float(data.get("command_timeout_seconds", 5.0)),
        ignore_input_after_lock_seconds=float(data.get("ignore_input_after_lock_seconds", 2.5)),
    )


def _load_notify_settings(data: dict[str, Any]) -> NotifyConfig:
    ntfy = data.get("ntfy", {}) or {}
    telegram = data.get("telegram", {}) or {}
    return NotifyConfig(
        provider=str(data.get("provider", "ntfy")),
        cooldown_seconds=float(data.get("cooldown_seconds", 60.0)),
        timeout_seconds=float(data.get("timeout_seconds", 10.0)),
        ntfy=NtfyConfig(
            server_url=str(ntfy.get("server_url", "https://ntfy.sh")),
            topic=str(ntfy.get("topic", "")),
            priority=str(ntfy.get("priority", "urgent")),
            tags=list(ntfy.get("tags", ["warning", "computer"])),
        ),
        telegram=TelegramConfig(
            bot_token=str(telegram.get("bot_token", "")),
            chat_id=str(telegram.get("chat_id", "")),
        ),
    )


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    string_value = str(value).strip()
    return string_value or None

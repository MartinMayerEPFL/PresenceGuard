from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from settings import NotifyConfig


LOGGER = logging.getLogger("presenceguard.notifier")


class BaseNotifier:
    def send(self, title: str, message: str) -> bool:
        raise NotImplementedError


class NtfyNotifier(BaseNotifier):
    def __init__(self, server_url: str, topic: str, priority: str, tags: list[str], timeout_seconds: float) -> None:
        self.server_url = server_url.rstrip("/")
        self.topic = topic
        self.priority = priority
        self.tags = tags
        self.timeout_seconds = timeout_seconds

    def send(self, title: str, message: str) -> bool:
        url = f"{self.server_url}/{urllib.parse.quote(self.topic)}"
        request = urllib.request.Request(url=url, data=message.encode("utf-8"), method="POST")
        request.add_header("Title", title)
        request.add_header("Priority", self.priority)
        if self.tags:
            request.add_header("Tags", ",".join(self.tags))

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                ok = 200 <= response.status < 300
        except urllib.error.URLError as exc:
            LOGGER.error("ntfy notification failed error=%s", exc)
            return False

        LOGGER.info("ntfy notification sent topic=%s", self.topic)
        return ok


class TelegramNotifier(BaseNotifier):
    def __init__(self, bot_token: str, chat_id: str, timeout_seconds: float) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds

    def send(self, title: str, message: str) -> bool:
        payload = json.dumps(
            {
                "chat_id": self.chat_id,
                "text": f"{title}\n{message}",
            }
        ).encode("utf-8")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        request = urllib.request.Request(url=url, data=payload, method="POST")
        request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                ok = 200 <= response.status < 300
        except urllib.error.URLError as exc:
            LOGGER.error("telegram notification failed error=%s", exc)
            return False

        LOGGER.info("telegram notification sent chat_id=%s", self.chat_id)
        return ok


class LogOnlyNotifier(BaseNotifier):
    def send(self, title: str, message: str) -> bool:
        LOGGER.warning("test notification title=%s message=%s", title, message)
        return True


class NotificationManager:
    def __init__(self, config: NotifyConfig, *, test_mode: bool = False) -> None:
        self.config = config
        self.last_alert_at = 0.0
        self.notifier = self._build_notifier(test_mode=test_mode)

    def send_intrusion_alert(self, *, source: str, target_label: str) -> bool:
        now = time.monotonic()
        if now - self.last_alert_at < self.config.cooldown_seconds:
            LOGGER.info("alert suppressed by cooldown remaining_seconds=%.1f", self.config.cooldown_seconds - (now - self.last_alert_at))
            return False

        title = "PresenceGuard alert"
        message = (
            f"Input activity detected while {target_label} is absent. "
            f"Source={source}."
        )
        sent = self.notifier.send(title, message)
        if sent:
            self.last_alert_at = now
        return sent

    def _build_notifier(self, *, test_mode: bool) -> BaseNotifier:
        if test_mode:
            return LogOnlyNotifier()

        provider = self.config.provider.lower()
        if provider == "ntfy":
            if not self.config.ntfy.topic:
                raise ValueError("notify.ntfy.topic must be set when provider=ntfy")
            return NtfyNotifier(
                server_url=self.config.ntfy.server_url,
                topic=self.config.ntfy.topic,
                priority=self.config.ntfy.priority,
                tags=self.config.ntfy.tags,
                timeout_seconds=self.config.timeout_seconds,
            )

        if provider == "telegram":
            if not self.config.telegram.bot_token or not self.config.telegram.chat_id:
                raise ValueError("telegram bot_token and chat_id must be configured when provider=telegram")
            return TelegramNotifier(
                bot_token=self.config.telegram.bot_token,
                chat_id=self.config.telegram.chat_id,
                timeout_seconds=self.config.timeout_seconds,
            )

        if provider == "log":
            return LogOnlyNotifier()

        raise ValueError(f"Unsupported notification provider: {self.config.provider}")

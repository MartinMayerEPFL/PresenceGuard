from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from settings import NotifyConfig


LOGGER = logging.getLogger("presenceguard.notifier")


class BaseNotifier:
    def send(self, title: str, message: str, *, photo_path: Path | None = None) -> bool:
        raise NotImplementedError

    def supports_photo(self) -> bool:
        return False


class NtfyNotifier(BaseNotifier):
    def __init__(self, server_url: str, topic: str, priority: str, tags: list[str], timeout_seconds: float) -> None:
        self.server_url = server_url.rstrip("/")
        self.topic = topic
        self.priority = priority
        self.tags = tags
        self.timeout_seconds = timeout_seconds

    def send(self, title: str, message: str, *, photo_path: Path | None = None) -> bool:
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

    def send(self, title: str, message: str, *, photo_path: Path | None = None) -> bool:
        if photo_path:
            return self._send_photo(title=title, message=message, photo_path=photo_path)

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

    def supports_photo(self) -> bool:
        return True

    def _send_photo(self, *, title: str, message: str, photo_path: Path) -> bool:
        if not photo_path.exists():
            LOGGER.warning("telegram photo path missing path=%s", photo_path)
            return self.send(title, message, photo_path=None)

        boundary = f"PresenceGuardBoundary{uuid.uuid4().hex}"
        body = bytearray()

        def add_field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")

        add_field("chat_id", self.chat_id)
        add_field("caption", f"{title}\n{message}")

        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="photo"; filename="{photo_path.name}"\r\n'.encode("utf-8")
        )
        body.extend(b"Content-Type: image/jpeg\r\n\r\n")
        body.extend(photo_path.read_bytes())
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        request = urllib.request.Request(url=url, data=bytes(body), method="POST")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                ok = 200 <= response.status < 300
        except urllib.error.URLError as exc:
            LOGGER.error("telegram photo notification failed error=%s", exc)
            return self.send(title, message, photo_path=None)

        LOGGER.info("telegram photo notification sent chat_id=%s path=%s", self.chat_id, photo_path)
        return ok


class LogOnlyNotifier(BaseNotifier):
    def send(self, title: str, message: str, *, photo_path: Path | None = None) -> bool:
        LOGGER.warning("test notification title=%s message=%s photo_path=%s", title, message, photo_path)
        return True


class NotificationManager:
    def __init__(self, config: NotifyConfig, *, test_mode: bool = False) -> None:
        self.config = config
        self.last_alert_at = 0.0
        self.notifier = self._build_notifier(test_mode=test_mode)

    def can_send_intrusion_alert(self) -> bool:
        now = time.monotonic()
        if now - self.last_alert_at < self.config.cooldown_seconds:
            LOGGER.info("alert suppressed by cooldown remaining_seconds=%.1f", self.config.cooldown_seconds - (now - self.last_alert_at))
            return False
        return True

    def supports_photo(self) -> bool:
        return self.notifier.supports_photo()

    def send_intrusion_alert(
        self,
        *,
        source: str,
        target_label: str,
        details: str | None = None,
        photo_path: Path | None = None,
    ) -> bool:
        now = time.monotonic()
        if now - self.last_alert_at < self.config.cooldown_seconds:
            LOGGER.info("alert suppressed by cooldown remaining_seconds=%.1f", self.config.cooldown_seconds - (now - self.last_alert_at))
            return False

        title = "PresenceGuard alert"
        message = f"Intrusion detected while {target_label} is absent. Source={source}."
        if details:
            message = f"{message}\n{details}"

        sent = self.notifier.send(title, message, photo_path=photo_path)
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

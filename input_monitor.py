from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional


LOGGER = logging.getLogger("presenceguard.input")


@dataclass
class ActivityEvent:
    when: float
    source: str


class InputActivityMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_activity: Optional[ActivityEvent] = None
        self._keyboard_listener = None
        self._mouse_listener = None

    def start(self) -> None:
        try:
            from pynput import keyboard, mouse
        except ImportError as exc:
            raise RuntimeError(
                "pynput is required for input monitoring. Install requirements.txt first."
            ) from exc

        if self._keyboard_listener or self._mouse_listener:
            return

        self._keyboard_listener = keyboard.Listener(on_press=self._on_keyboard_press)
        self._mouse_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
        )

        try:
            self._keyboard_listener.start()
            self._mouse_listener.start()
        except Exception as exc:
            raise RuntimeError(
                "Unable to start input listeners. Grant Accessibility and Input Monitoring permissions."
            ) from exc

        LOGGER.info("input activity monitor started")

    def stop(self) -> None:
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is not None:
                listener.stop()
        self._keyboard_listener = None
        self._mouse_listener = None

    def get_last_activity(self) -> Optional[ActivityEvent]:
        with self._lock:
            return self._last_activity

    def _record_activity(self, source: str) -> None:
        event = ActivityEvent(when=time.monotonic(), source=source)
        with self._lock:
            self._last_activity = event

    def _on_keyboard_press(self, _key: object) -> None:
        self._record_activity("keyboard")

    def _on_mouse_move(self, _x: int, _y: int) -> None:
        self._record_activity("mouse_move")

    def _on_mouse_click(self, _x: int, _y: int, _button: object, _pressed: bool) -> None:
        self._record_activity("mouse_click")

    def _on_mouse_scroll(self, _x: int, _y: int, _dx: int, _dy: int) -> None:
        self._record_activity("mouse_scroll")

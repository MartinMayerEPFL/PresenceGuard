from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from settings import CameraConfig


LOGGER = logging.getLogger("presenceguard.camera")


class CameraCapture:
    def __init__(self, config: CameraConfig, *, test_mode: bool = False) -> None:
        self.config = config
        self.test_mode = test_mode
        self._imagesnap_path = shutil.which("imagesnap")
        self._ffmpeg_path = shutil.which("ffmpeg")

    def capture_intrusion_photo(self) -> Path | None:
        if not self.config.enabled or self.test_mode:
            LOGGER.info(
                "camera capture suppressed enabled=%s test_mode=%s",
                self.config.enabled,
                self.test_mode,
            )
            return None

        output_path = self._build_output_path()
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            LOGGER.error("unable to create camera output directory path=%s error=%s", output_path.parent, exc)
            return None

        method = self.config.method.lower()
        if method == "auto":
            if self._imagesnap_path and self._capture_with_imagesnap(output_path):
                return output_path
            if self._ffmpeg_path and self._capture_with_ffmpeg(output_path):
                return output_path
            LOGGER.warning("no camera capture backend available")
            return None

        if method == "imagesnap":
            if not self._imagesnap_path:
                LOGGER.warning("imagesnap requested but not installed")
                return None
            return output_path if self._capture_with_imagesnap(output_path) else None

        if method == "ffmpeg":
            if not self._ffmpeg_path:
                LOGGER.warning("ffmpeg requested but not installed")
                return None
            return output_path if self._capture_with_ffmpeg(output_path) else None

        LOGGER.error("unsupported camera method=%s", self.config.method)
        return None

    def cleanup(self, path: Path | None) -> None:
        if not path or self.config.retain_local_copy:
            return

        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("unable to remove capture path=%s error=%s", path, exc)

    def _build_output_path(self) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return Path(self.config.save_directory).expanduser() / f"intrusion-{timestamp}.jpg"

    def _capture_with_imagesnap(self, output_path: Path) -> bool:
        return self._run(
            [self._imagesnap_path, "-q", str(output_path)],
            "imagesnap",
            output_path,
        )

    def _capture_with_ffmpeg(self, output_path: Path) -> bool:
        return self._run(
            [
                self._ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "avfoundation",
                "-framerate",
                "1",
                "-i",
                self.config.ffmpeg_input,
                "-frames:v",
                "1",
                "-y",
                str(output_path),
            ],
            "ffmpeg",
            output_path,
        )

    def _run(self, command: Sequence[str], label: str, output_path: Path) -> bool:
        try:
            result = subprocess.run(
                list(command),
                capture_output=True,
                check=False,
                text=True,
                timeout=self.config.command_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            LOGGER.error("camera command failed method=%s error=%s", label, exc)
            return False

        if result.returncode != 0:
            LOGGER.error(
                "camera command returned non-zero method=%s code=%s stderr=%s",
                label,
                result.returncode,
                (result.stderr or "").strip(),
            )
            return False

        if not output_path.exists() or output_path.stat().st_size == 0:
            LOGGER.error("camera output missing or empty method=%s path=%s", label, output_path)
            return False

        LOGGER.info("camera capture saved method=%s path=%s", label, output_path)
        return True

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from ..collectors.ios_runtime import command


def capture_ios(udid: str, destination: Path, max_width: int = 800) -> Path | None:
    raw = destination.with_suffix(".png")
    try:
        result = subprocess.run(command("screenshot", "--udid", udid, "--output", str(raw)),
                                capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not raw.exists(): return None
        with Image.open(raw) as source:
            image = source.convert("RGB")
        try:
            if max_width > 0 and image.width > max_width:
                resized = image.resize(
                    (max_width, max(1, round(image.height * max_width / image.width))))
                image.close()
                image = resized
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, "JPEG", quality=58, optimize=True, progressive=True)
        finally:
            image.close()
        raw.unlink(missing_ok=True)
        return destination
    except (OSError, subprocess.SubprocessError, ValueError):
        raw.unlink(missing_ok=True)
        return None

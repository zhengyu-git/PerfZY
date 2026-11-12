from __future__ import annotations

import io
import re
import shutil
import struct
import subprocess
from pathlib import Path

from PIL import Image
from ..runtime_paths import find_tool


def _decode_screencap(payload: bytes) -> Image.Image | None:
    """Decode Android's fast raw screencap stream (or a PNG fallback)."""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        try:
            with Image.open(io.BytesIO(payload)) as source:
                return source.convert("RGB")
        except (OSError, ValueError):
            return None
    if len(payload) < 12:
        return None
    try:
        width, height, pixel_format = struct.unpack_from("<III", payload)
    except struct.error:
        return None
    if not (0 < width <= 16384 and 0 < height <= 16384 and width * height <= 80_000_000):
        return None
    formats = {
        1: (4, "RGBA", "RGBA"),
        2: (4, "RGB", "RGBX"),
        3: (3, "RGB", "RGB"),
        4: (2, "RGB", "BGR;16"),
        5: (4, "RGBA", "BGRA"),
    }
    spec = formats.get(pixel_format)
    if spec is None:
        return None
    bytes_per_pixel, image_mode, raw_mode = spec
    expected = width * height * bytes_per_pixel
    # Android 12+ adds a colorspace word after the legacy 12-byte header.
    header_size = 16 if len(payload) >= 16 + expected else 12
    if len(payload) < header_size + expected:
        return None
    try:
        decoded = Image.frombytes(
            image_mode, (width, height), payload[header_size:header_size + expected],
            "raw", raw_mode)
        if decoded.mode == "RGB":
            return decoded
        converted = decoded.convert("RGB")
        decoded.close()
        return converted
    except (OSError, ValueError):
        return None


def capture_android(serial: str, destination: Path, max_width: int = 800) -> Path | None:
    adb = find_tool("adb")
    if not adb:
        return None
    try:
        # Raw screencap avoids the device-side PNG encoder. On high-resolution
        # Android devices this cuts capture latency from ~5 s to ~1.5 s, which
        # also stops screenshots from starving metric adb commands.
        result = subprocess.run([adb, "-s", serial, "exec-out", "screencap"],
                                capture_output=True, timeout=8.0,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not result.stdout:
            return None
        image = _decode_screencap(result.stdout)
        if image is None:
            return None
        try:
            if image.height > image.width:
                rotation_result = subprocess.run(
                    [adb, "-s", serial, "shell", "dumpsys", "window", "displays"],
                    capture_output=True, text=True, timeout=3.0,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                rotation = re.search(r"mCurrentRotation=ROTATION_(90|270)|mRotation=(1|3)", rotation_result.stdout)
                if rotation:
                    clockwise = (rotation.group(1) == "90" or rotation.group(2) == "1")
                    rotated = image.transpose(
                        Image.Transpose.ROTATE_270 if clockwise else Image.Transpose.ROTATE_90)
                    image.close()
                    image = rotated
            if max_width > 0 and image.width > max_width:
                resized = image.resize(
                    (max_width, max(1, round(image.height * max_width / image.width))))
                image.close()
                image = resized
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, "JPEG", quality=58, optimize=True, progressive=True)
        finally:
            image.close()
        return destination
    except (OSError, subprocess.SubprocessError, ValueError):
        return None

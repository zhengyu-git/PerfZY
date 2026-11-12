from __future__ import annotations

import json
import contextlib
import io
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path


def _decode_label(value: str) -> str:
    value = value.strip()
    try:
        repaired = value.encode("latin1").decode("gb18030")
        if re.search(r"[\u4e00-\u9fff]", repaired):
            return repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return value


def _extract(adb: str, serial: str, apk_path: str, member: str, timeout: float = 12.0) -> bytes:
    result = subprocess.run(
        [adb, "-s", serial, "exec-out", "unzip", "-p", apk_path, member],
        capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    return result.stdout if result.returncode == 0 else b""


def resolve_labels(adb: str, serial: str, packages: list[str], cache_root: Path) -> dict[str, str]:
    """Resolve third-party application labels without pulling complete APK files."""
    cache_file = cache_root / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', serial)}.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    repaired_cache = {key: _decode_label(str(value)) for key, value in cache.items()}
    changed = repaired_cache != cache
    cache = repaired_cache
    try:
        from apkutils2 import APK
    except ImportError:
        return cache

    for package in packages:
        if cache.get(package):
            continue
        path_result = subprocess.run(
            [adb, "-s", serial, "shell", "pm", "path", package], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=5.0, creationflags=subprocess.CREATE_NO_WINDOW)
        apk_path = next((line.partition(":")[2].strip() for line in (path_result.stdout or "").splitlines()
                         if line.startswith("package:") and line.rstrip().endswith("base.apk")), "")
        if not apk_path:
            continue
        manifest = _extract(adb, serial, apk_path, "AndroidManifest.xml")
        resources = _extract(adb, serial, apk_path, "resources.arsc")
        if not manifest or not resources:
            continue
        try:
            with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as temporary:
                temporary_path = Path(temporary.name)
            with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("AndroidManifest.xml", manifest)
                archive.writestr("resources.arsc", resources)
            with contextlib.redirect_stdout(io.StringIO()):
                apk = APK(str(temporary_path))
                application = apk.get_manifest().get("application", {})
                label = str(application.get("@android:label", "")).strip()
                resolved = (apk.get_arsc().get_resolved_res_configs(int(label[1:], 16))
                            if label.startswith("@") else [])
            if label.startswith("@"):
                localized = [(item[0].get_language().replace("\x00", ""),
                              item[0].get_country().replace("\x00", ""), _decode_label(str(item[-1])))
                             for item in resolved if item and str(item[-1]).strip()]
                label = next((value for language, country, value in localized
                              if language == "zh" and country == "CN"), "")
                if not label:
                    label = next((value for language, _country, value in localized if language == "zh"),
                                 localized[0][2] if localized else "")
            if label and not label.startswith("@"):
                cache[package] = label
                changed = True
        except (OSError, ValueError, KeyError, IndexError, TypeError, zipfile.BadZipFile):
            continue
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except UnboundLocalError:
                pass
    if changed:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return cache

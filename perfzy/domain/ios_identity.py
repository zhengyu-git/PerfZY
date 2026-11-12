"""Separate iOS product names, hardware identifiers and application labels."""
from functools import lru_cache
import re


from .application_identity import mobile_package_name as ios_bundle_id

DECLARED_GAME_FPS_LIMITS: dict[str, int] = {}


@lru_cache(maxsize=128)
def ios_product_name(product_type):
    if product_type == "iPhone13,2":
        return "iPhone 12"
    try:
        from pymobiledevice3.irecv_devices import IRECV_DEVICES
        rows = IRECV_DEVICES.values() if isinstance(IRECV_DEVICES, dict) else IRECV_DEVICES
        return next((row.display_name for row in rows if row.product_type == product_type), "")
    except (ImportError, AttributeError):
        return ""


def ios_device_identity(device):
    details = device.get("details") if isinstance(device.get("details"), dict) else {}
    text = " ".join(str(value or "") for value in (
        details.get("product_type"), device.get("product_type"), device.get("model"), device.get("name")))
    match = re.search(r"(?:iPhone|iPad|iPod|AppleTV)\d+,\d+", text)
    model = match.group(0) if match else str(device.get("model") or "")
    product = str(details.get("product_name") or device.get("product_name") or "").strip()
    if re.fullmatch(r"(?:iPhone|iPad|iPod|AppleTV)\d+,\d+", product):
        product = ""
    product = product or ios_product_name(model)
    if not product:
        for candidate in (device.get("model"), device.get("name") if not model else ""):
            candidate = str(candidate or "").split("（", 1)[0].split("(", 1)[0].strip()
            if candidate and not re.fullmatch(r"(?:iPhone|iPad|iPod|AppleTV)\d+,\d+", candidate):
                product = candidate
                break
    identifier = str(details.get("udid") or device.get("udid") or device.get("id") or "")
    identifier = identifier.removeprefix("ios:")
    return dict(product_name=product or model, model=model,
                device_name=str(details.get("device_name") or device.get("device_name") or device.get("name") or ""),
                udid=identifier if re.fullmatch(r"[0-9a-fA-F-]{16,64}", identifier) else "")

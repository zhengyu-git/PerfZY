from __future__ import annotations

import argparse
import asyncio
import json
import signal
import threading
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from perfzy.collectors.ios_sampling import DEFAULT_METRICS, sampling_plan
from perfzy.collectors.ios_frames import stream_ios_frames
legacy_runtime = ROOT / "tools" / "ios_runtime"
if (legacy_runtime / "pymobiledevice3" / "lockdown.py").is_file():
    sys.path.insert(0, str(legacy_runtime))

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.diagnostics import DiagnosticsService
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.device_info import DeviceInfo
from pymobiledevice3.services.dvt.instruments.graphics import Graphics
from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot
from pymobiledevice3.services.dvt.instruments.sysmontap import Sysmontap
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.usbmux import list_devices


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def readable(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    replacement_count = text.count("\ufffd")
    # Apple MobileDevice on some Chinese Windows installations returns device/app
    # labels decoded through the active code page. Once replacement/mojibake has
    # happened it is not reversible, so prefer the ASCII executable/model label.
    if not text or replacement_count or any(ord(char) > 127 for char in text) or sum(ord(char) < 32 for char in text):
        return fallback
    return text


async def lockdown(udid: str):
    return await create_using_usbmux(serial=udid, autopair=True, pair_timeout=12)


async def cmd_devices(_args: argparse.Namespace) -> None:
    rows = []
    seen = set()
    try:
        devices = await list_devices()
    except Exception as error:
        raise RuntimeError(f"iOS 设备枚举失败：{error}") from error
    for mux_device in devices:
        udid = str(getattr(mux_device, "serial", "") or getattr(mux_device, "identifier", ""))
        normalized_udid = udid.replace('-', '')
        if not udid or normalized_udid in seen:
            continue
        seen.add(normalized_udid)
        connection_type = str(getattr(mux_device, 'connection_type', '') or '')
        try:
            client = await lockdown(udid)
            active_mux = getattr(getattr(client, 'service', None), 'mux_device', None)
            # The client may select USB even if enumeration returned Network
            # first. Never infer this from a local developer-tunnel address.
            connection_type = str(getattr(active_mux, 'connection_type', '') or '')
            model = str(await client.get_value(key="ProductType") or "")
            raw_name = await client.get_value(key="DeviceName")
            rows.append({
                "udid": udid,
                "name": readable(raw_name, f"iPhone · {model}" if model else "iPhone"),
                "model": model,
                "version": str(await client.get_value(key="ProductVersion") or "iOS"),
                "build": str(await client.get_value(key="BuildVersion") or ""),
                "cpu": str(await client.get_value(key="HardwareModel") or ""),
                "connection_type": connection_type,
            })
            await client.close()
        except Exception as error:
            rows.append({"udid": udid, "name": "未信任的 iOS 设备", "error": str(error),
                         "connection_type": connection_type})
    emit(rows)


async def cmd_apps(args: argparse.Namespace) -> None:
    client = await lockdown(args.udid)
    try:
        async with InstallationProxyService(client) as service:
            apps = await service.get_apps(application_type="User")
        rows = []
        for bundle_id, info in apps.items():
            executable = str(info.get("CFBundleExecutable") or "")
            rows.append({
                "bundle_id": bundle_id,
                "name": readable(info.get("CFBundleDisplayName") or info.get("CFBundleName"), executable or bundle_id),
                "executable": executable,
                "version": info.get("CFBundleShortVersionString") or "",
            })
        emit(sorted(rows, key=lambda item: str(item["name"]).casefold()))
    finally:
        await client.close()


@asynccontextmanager
async def developer_provider(udid: str):
    client = await lockdown(udid)
    try:
        major = int(str(await client.get_value(key="ProductVersion") or "0").split(".", 1)[0])
    except ValueError:
        major = 0
    if major >= 17:
        await client.close()
        async with UserspaceRsdTunnel(serial=udid) as rsd:
            yield rsd
    else:
        try:
            yield client
        finally:
            await client.close()


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            result.update(flatten(child, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            result.update(flatten(child, f"{prefix}.{index}" if prefix else str(index)))
    elif isinstance(value, (str, int, float, bool)) or value is None:
        result[prefix] = value
    return result


def normalize_system_metrics(raw: dict[str, Any], tap: Any) -> dict[str, Any]:
    """Normalize the sysmontap system payload across iOS/DVT schema variants."""
    payload = {key: value for key, value in raw.items() if key != "Processes"}
    raw_system = raw.get("System")
    if isinstance(raw_system, (list, tuple)):
        fields = tap.system_attributes_cls.__dataclass_fields__
        payload["SystemMetrics"] = dict(zip(fields, raw_system))
    elif isinstance(raw_system, dict):
        # Some iOS/DVT versions switch from the positional System tuple to a
        # mapping during a long stream. Flatten it without the otherwise
        # discarded `System.` prefix.
        payload.pop("System", None)
        payload.update(raw_system)

    compact: dict[str, Any] = {}
    for key, value in flatten(payload).items():
        canonical = str(key)
        for marker in ("SystemCPUUsage.", "PerCPUUsage.", "SystemMetrics."):
            offset = canonical.find(marker)
            if offset >= 0:
                canonical = canonical[offset:]
                break
        leaf = canonical.rsplit(".", 1)[-1]
        if (leaf in {"CPUCount", "EnabledCPUs"} or
                canonical.startswith(("SystemCPUUsage.", "PerCPUUsage.", "SystemMetrics."))):
            compact[canonical] = value
    return compact


async def app_executable(udid: str, bundle_id: str) -> str:
    client = await lockdown(udid)
    try:
        async with InstallationProxyService(client) as service:
            apps = await service.get_apps(application_type="Any", bundle_identifiers=[bundle_id])
        info = apps.get(bundle_id) or {}
        return str(info.get("CFBundleExecutable") or info.get("CFBundleName") or "")
    finally:
        await client.close()


def process_matches(row: dict[str, Any], executable: str, bundle_id: str) -> bool:
    needle = executable.casefold()
    bundle = bundle_id.casefold()
    candidates = " ".join(str(row.get(key, "")) for key in
                          ("name", "procName", "command", "comm", "executable", "path", "bundleIdentifier"))
    folded = candidates.casefold()
    return bool((needle and needle in folded) or bundle in folded)


async def stream_sysmon(dvt: DvtProvider, executable: str, bundle_id: str, interval: int, plan=None) -> None:
    plan = plan or sampling_plan(DEFAULT_METRICS, interval)
    async with DeviceInfo(dvt) as info:
        supported_process = await info.sysmon_process_attributes()
        supported_system = await info.sysmon_system_attributes()
    process_fields = [key for key in supported_process if key in plan["process_fields"]]
    system_fields = [key for key in supported_system if key in plan["system_fields"]]
    # iOS 27 closes Sysmontap when sysAttrs is empty. A static physical
    # capacity field keeps the channel valid without system-wide counters.
    if not system_fields:
        system_fields = ["physMemSize"] if "physMemSize" in supported_system else list(supported_system[:1])
    tap = Sysmontap(dvt, process_fields, system_fields, interval_ms=plan["interval"])
    # These are the library's existing device configuration fields, not filters
    # applied after collection. Do not invent an unsupported PID-filter option.
    tap._config.update(cpuUsage=plan["cpu"], physFootprint=plan["footprint"], ur=plan["interval"])
    emit({"type": "subscription", "process_fields": process_fields, "system_fields": system_fields,
          "interval": plan["interval"], "cpu": plan["cpu"], "footprint": plan["footprint"]})
    async with tap:
        async for raw in tap:
            if not isinstance(raw, dict):
                continue
            processes = raw.get("Processes") or {}
            if not isinstance(processes, dict):
                continue
            selected = None
            for pid, values in processes.items():
                try:
                    row = dict(values) if isinstance(values, dict) else dict(zip(tap.process_attributes_cls.__dataclass_fields__, values))
                except (TypeError, ValueError):
                    continue
                row["pid"] = pid
                if process_matches(row, executable, bundle_id):
                    selected = row
                    break
            system = normalize_system_metrics(raw, tap)
            emit({"type": "sysmon", "timestamp": time.time(), "process": selected, "system": system})


async def stream_target_presence(dvt, executable, bundle_id, interval):
    """Keep the target-exit guard without opening Sysmontap for FPS/GPU only."""
    async with DeviceInfo(dvt) as info:
        while True:
            # Refresh foregroundRunning as well as PID: a cached process row
            # would keep attributing SpringBoard graphics to a background app.
            selected = next((row for row in await info.proclist()
                             if process_matches(row, executable, bundle_id)), None)
            emit({"type": "sysmon", "timestamp": time.time(), "process": selected, "system": {}})
            await asyncio.sleep(interval / 1000)


async def stream_graphics(dvt: DvtProvider) -> None:
    async with Graphics(dvt) as graphics:
        async for raw in graphics:
            emit({"type": "graphics", "timestamp": time.time(), "data": flatten(raw)})


async def stream_battery(udid: str, interval: int) -> None:
    client = None
    try:
        client = await lockdown(udid)
        async with DiagnosticsService(client) as diagnostics:
            while True:
                data = await diagnostics.ioregistry(ioclass="AppleSmartBattery")
                flattened = flatten(data or {})
                keep = {"CurrentCapacity", "InstantAmperage", "Amperage", "Voltage", "Temperature"}
                compact = {key: value for key, value in flattened.items() if key.rsplit(".", 1)[-1] in keep}
                emit({"type": "battery", "timestamp": time.time(), "data": compact})
                await asyncio.sleep(max(5.0, interval / 1000))
    except Exception as error:
        emit({"type": "status", "message": f"电池诊断不可用：{error}"})
    finally:
        if client is not None:
            await client.close()


async def cmd_stream(args: argparse.Namespace) -> None:
    executable = await app_executable(args.udid, args.bundle)
    if not executable:
        raise RuntimeError("无法读取目标应用信息，请刷新应用列表")
    async with developer_provider(args.udid) as provider:
        async with DvtProvider(provider) as dvt:
            emit({"type": "ready", "executable": executable})
            queue = asyncio.Queue()
            loop = asyncio.get_running_loop()
            def read_configuration():
                for line in sys.stdin:
                    try:
                        value = json.loads(line)
                        if value.get('stop') is True:
                            loop.call_soon_threadsafe(queue.put_nowait, None)
                            return
                        plan = sampling_plan(value["metrics"], value["interval"])
                        loop.call_soon_threadsafe(queue.put_nowait, plan)
                    except (ValueError, KeyError, TypeError):
                        continue
                    except RuntimeError:
                        return
            threading.Thread(target=read_configuration, daemon=True, name="ios-config").start()
            plan = sampling_plan(args.metrics.split(",") if args.metrics is not None else DEFAULT_METRICS, args.interval)
            while True:
                emit({"type": "configuration", **plan})
                tasks = [asyncio.create_task(stream_sysmon(dvt, executable, args.bundle, plan["interval"], plan)
                         if plan["sysmon"] else stream_target_presence(dvt, executable, args.bundle, plan["guard_interval"]))]
                if plan["graphics"]:
                    tasks.append(asyncio.create_task(stream_graphics(dvt)))
                if plan["battery"]:
                    tasks.append(asyncio.create_task(stream_battery(args.udid, plan["interval"])))
                if plan.get("frames"):
                    tasks.append(asyncio.create_task(stream_ios_frames(dvt, emit)))
                update = asyncio.create_task(queue.get())
                try:
                    # Battery diagnostics can be unavailable without ending the
                    # performance session; only the main streams are essential.
                    essential = tasks[:2] if plan["graphics"] else tasks[:1]
                    done, _ = await asyncio.wait([*essential, update], return_when=asyncio.FIRST_COMPLETED)
                    for task in essential:
                        if task in done:
                            task.result()
                            raise RuntimeError("iOS 指标通道意外结束")
                    plan = update.result()
                    while not queue.empty():
                        plan = queue.get_nowait()
                    if plan is None:
                        return
                finally:
                    for task in [*tasks, update]:
                        task.cancel()
                    await asyncio.gather(*tasks, update, return_exceptions=True)


async def cmd_screenshot(args: argparse.Namespace) -> None:
    async with developer_provider(args.udid) as provider:
        async with DvtProvider(provider) as dvt, Screenshot(dvt) as screenshot:
            data = await screenshot.get_screenshot()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_bytes(data)
    emit({"output": str(Path(args.output))})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("devices")
    commands.add_parser("preflight")
    apps = commands.add_parser("apps"); apps.add_argument("--udid", required=True)
    stream = commands.add_parser("stream"); stream.add_argument("--udid", required=True); stream.add_argument("--bundle", required=True); stream.add_argument("--interval", type=int, default=1000)
    stream.add_argument("--metrics", default=None)
    shot = commands.add_parser("screenshot"); shot.add_argument("--udid", required=True); shot.add_argument("--output", required=True)
    return result


async def amain(args: argparse.Namespace) -> None:
    if args.command == "preflight": emit([])
    elif args.command == "devices": await cmd_devices(args)
    elif args.command == "apps": await cmd_apps(args)
    elif args.command == "stream": await cmd_stream(args)
    elif args.command == "screenshot": await cmd_screenshot(args)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"IOS_ERROR: {error}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

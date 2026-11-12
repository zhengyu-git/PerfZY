"""Read-only connection checks. Never enables tracking or changes a phone setting."""
from __future__ import annotations

import ctypes
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .collectors.ios_runtime import run_json
from .runtime_paths import find_tool


def connection_check(root: Path, platform: str = '') -> list[dict]:
    rows = []
    def add(name, status, detail, action=''):
        rows.append(dict(name=name, status=status, detail=detail, action=action))

    try:
        free_mb = shutil.disk_usage(root).free / 1024 ** 2
        writable = os.access(root, os.W_OK)
        add('数据目录', '通过' if writable and free_mb >= 200 else '需处理',
            f'可用空间 {free_mb:.0f} MB；目录' + ('可写' if writable else '不可写'),
            '' if writable and free_mb >= 200 else '把软件移到可写目录，并留出截图和采集数据所需空间。')
    except OSError as error:
        add('数据目录', '需处理', str(error), '检查软件所在目录是否可访问。')
    if platform in ('', 'windows'):
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin()) if sys.platform == 'win32' else False
        add('Windows 帧采集权限', '通过' if admin else '需处理',
            '已获得管理员权限' if admin else '尚未获得 Windows 管理员权限',
            '' if admin else '以管理员身份启动 PerfZY；CPU、内存采集可在普通权限下使用。')
        presentmon = find_tool('PresentMon')
        add('Windows 帧采集组件', '通过' if presentmon else '需处理',
            'PresentMon 文件存在，实际采集还受系统和目标程序支持情况影响。' if presentmon else '缺少 PresentMon',
            '' if presentmon else '请按 README 安装 PresentMon CLI 并加入 PATH。')
    if platform in ('', 'android'):
        adb = find_tool('adb')
        if not adb:
            add('Android 连接', '需处理', '找不到 ADB', '请按 README 安装 Android SDK Platform-Tools 并加入 PATH。')
        else:
            try:
                result = subprocess.run([adb, 'devices', '-l'], capture_output=True,
                                        encoding='utf-8', errors='replace', timeout=8,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                states = [line.split()[1] for line in result.stdout.splitlines()
                          if len(line.split()) >= 2 and line.split()[1] in ('device', 'offline', 'unauthorized')]
                if result.returncode:
                    add('Android 连接', '需处理', (result.stderr or 'ADB 返回错误').strip(),
                        '检查 USB 连接和 ADB 驱动，然后重新检查。')
                elif 'unauthorized' in states:
                    add('Android 连接', '需处理', '手机尚未授权 USB 调试',
                        '解锁手机，在 USB 调试弹窗中允许这台电脑。')
                elif 'offline' in states:
                    add('Android 连接', '需处理', '检测到离线设备', '重新连接数据线，并检查手机 USB 调试开关。')
                elif 'device' in states:
                    add('Android 连接', '通过', f'发现 {states.count("device")} 台已授权设备；各指标是否支持需在采集后确认。')
                else:
                    add('Android 连接', '未检测到', '未发现已连接的 Android 调试设备',
                        '连接支持数据传输的 USB 线，打开 USB 调试，并检查设备驱动。')
            except (OSError, subprocess.SubprocessError) as error:
                add('Android 连接', '需处理', str(error), '检查 ADB 组件、USB 线和驱动。')
    if platform in ('', 'ios'):
        if importlib.util.find_spec('pymobiledevice3') is None:
            add('iPhone 连接组件', '需处理', '缺少 iOS 连接组件', '使用完整软件包；源码环境请安装项目依赖。')
        else:
            try:
                devices = run_json('devices', timeout=15, strict=True)
                errors = [str(row.get('error')) for row in devices if row.get('error')]
                ready = [row for row in devices if not row.get('error')]
                if errors:
                    add('iPhone 配对', '需处理', '\n'.join(errors)[:1000],
                        '解锁手机并信任电脑；检查 Apple Devices 驱动。性能采集还需开发者模式。')
                elif ready:
                    add('iPhone 配对', '通过', f'发现 {len(ready)} 台可配对设备；尚未启动 Instruments 或验证具体指标。',
                        '开始性能采集前确认手机开发者模式已开启。')
                else:
                    add('iPhone 配对', '未检测到', '未检测到可配对的 iPhone / iPad',
                        '解锁并连接手机，检查 Apple Devices 驱动和“信任此电脑”提示。')
            except RuntimeError as error:
                add('iPhone 连接', '需处理', str(error)[:1000],
                    '检查 Apple Devices 驱动、手机信任状态和连接组件；不据此认定所有 iOS 指标都不可用。')
    return rows

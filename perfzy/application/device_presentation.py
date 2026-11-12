"""Device labels retain identity; badges describe the last discovered transport."""
from ..domain.models import Platform


PLATFORM_NAMES = {
    Platform.WINDOWS: 'Windows 本机',
    Platform.ANDROID: '安卓设备',
    Platform.IOS: 'iOS 设备',
}


_CONNECTIONS = {
    'usb': ('USB', 'USB 连接'),
    'network': ('Wi-Fi', 'Wi-Fi 网络连接'),
    'local': ('Local', '本机采集'),
    'emulator': ('Emulator', '本机 Android 模拟器'),
    'adb': ('ADB', 'ADB 已连接，当前工具未提供 USB / Wi-Fi 连接类型'),
    'unknown': ('已连接', '设备已连接，暂未提供具体连接方式'),
}


def present_device(device):
    connection = 'local' if device.platform is Platform.WINDOWS else device.details.get('connection_type', 'unknown')
    if connection not in _CONNECTIONS:
        connection = 'unknown'
    if connection == 'unknown' and device.platform is Platform.ANDROID:
        connection = 'adb'
    label, description = _CONNECTIONS[connection]
    return {'id': device.id, 'name': device.name, 'platform': PLATFORM_NAMES[device.platform],
            'display_name': f'{device.name} · {label}',
            'connection_type': connection, 'connection_label': label,
            'connection_description': description,
            'connection_source': device.details.get('connection_source', ''),
            'model': device.model, 'os': device.os_version,
            'cpu': device.details.get('cpu', ''), 'gpu': device.details.get('gpu', ''),
            'resolution': device.details.get('resolution', ''),
            'memory': device.details.get('memory', ''),
            'cpuCores': device.details.get('cpu_cores', '')}

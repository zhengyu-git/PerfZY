"""Transport evidence and presentation without touching device connections."""
import asyncio
from types import SimpleNamespace

import pytest

from perfzy.application.device_presentation import present_device
from perfzy.collectors import discovery
from perfzy.domain.models import Device, Platform
from test_controller import controller


@pytest.mark.parametrize('serial,attributes,expected', [
    ('R58M123', {'usb':'1-3', 'transport_id':'1'}, 'usb'),
    ('192.168.1.3:5555', {'transport_id':'2'}, 'network'),
    ('[fe80::1234]:41234', {}, 'network'),
    ('adb-R58M123-abc._adb-tls-connect._tcp', {}, 'network'),
    ('adb-R58M123-abc._adb-tls-connect._tcp.local', {}, 'network'),
    ('emulator-5554', {'transport_id':'3'}, 'emulator'),
    ('R58M123', {'transport_id':'4'}, 'unknown'),
    ('USB-looking-serial', {}, 'unknown'),
])
def test_android_uses_transport_evidence(serial, attributes, expected):
    assert discovery._android_connection(serial, attributes) == expected


@pytest.mark.parametrize('path', ['usb:1-3', r'\\?\USB#VID_2717&PID_FF48#SERIAL'])
def test_android_devpath_can_supply_missing_usb_column(path):
    assert discovery._android_connection('SERIAL', {}, devpath=path) == 'usb'


def test_connected_android_without_transport_metadata_uses_neutral_adb_badge():
    shown = present_device(Device('android:SERIAL', Platform.ANDROID, 'Xiaomi 12'))
    assert shown['connection_type'] == 'adb' and shown['connection_label'] == 'ADB'
    assert 'Unknown' not in shown['display_name']
    assert discovery._android_connection('SERIAL', {}, devpath='unknown') == 'unknown'


def test_emulator_property_takes_precedence_over_local_network_endpoint():
    assert discovery._android_connection('127.0.0.1:5555', {}, emulator=True) == 'emulator'


@pytest.mark.parametrize('payload,expected', [
    ({'connection_type':'USB'}, 'usb'),
    ({'connection_type':'Network'}, 'network'),
    ({'ConnectionType':'USB'}, 'usb'),
    ({'address':'127.0.0.1:62078', 'tunnel':'192.168.1.3'}, 'unknown'),
    ({'connection_type':'RSD'}, 'unknown'),
    ({}, 'unknown'),
])
def test_ios_requires_usbmux_transport_not_tunnel_address(payload, expected):
    assert discovery._ios_connection(payload) == expected


def test_android_discovery_keeps_names_and_adds_explicit_transport(monkeypatch):
    monkeypatch.setattr(discovery.shutil, 'which', lambda name: 'adb')
    def run(command, **kwargs):
        if command[1:] == ['devices','-l']:
            return ('List of devices attached\n'
                    'R58M123 device usb:1-3 product:test model:Phone\n'
                    '192.168.1.3:5555 device product:test model:Phone\n'
                    'opaque device product:test model:Phone\n')
        if command[-1] == 'getprop':
            return '[ro.product.model]: [Phone]\n[ro.build.version.release]: [15]\n'
        return ''
    monkeypatch.setattr(discovery, '_run', run)
    devices = list(discovery.discover_android())
    assert [device.name for device in devices] == ['Phone']*3
    assert [device.details['connection_type'] for device in devices] == ['usb','network','unknown']


def test_ios_discovery_preserves_transport_and_legacy_rows_remain_unknown(monkeypatch):
    monkeypatch.setattr(discovery, 'run_ios_json', lambda *a, **k: [
        {'udid':'USB1','model':'iPhone16,1','connection_type':'USB'},
        {'udid':'WIFI1','model':'iPhone16,1','connection_type':'Network'},
        {'udid':'OLD1','model':'iPhone16,1','tunnel':'127.0.0.1:1234'},
    ])
    devices = list(discovery.discover_ios())
    assert [device.details['connection_type'] for device in devices] == ['usb','network','unknown']
    assert [device.name for device in devices] == ['iPhone 15 Pro']*3


def test_presentation_keeps_device_identity_and_name_separate_from_badge():
    device = Device('ios:USB1', Platform.IOS, 'iPhone 15 Pro', details={'connection_type':'usb'})
    shown = present_device(device)
    assert shown['id'] == device.id and shown['name'] == device.name
    assert shown['connection_label'] == 'USB' and shown['display_name'] == 'iPhone 15 Pro · USB'
    assert present_device(Device('ios:OLD', Platform.IOS, 'iPhone'))['connection_type'] == 'unknown'
    assert present_device(Device('windows:local', Platform.WINDOWS, 'PC'))['connection_label'] == 'Local'


def test_bridge_reports_actual_selected_mux_and_deduplicates_available_routes(monkeypatch):
    from tools import ios_bridge as bridge
    rows = []
    async def list_devices():
        return [SimpleNamespace(serial='ABC-123',connection_type='Network'),
                SimpleNamespace(serial='ABC123',connection_type='USB')]
    closed = []
    class Client:
        service = SimpleNamespace(mux_device=SimpleNamespace(connection_type='USB'))
        async def get_value(self, key):
            return {'ProductType':'iPhone16,1', 'DeviceName':'Phone',
                    'ProductVersion':'18.0'}.get(key, '')
        async def close(self): closed.append(True)
    async def lockdown(udid): return Client()
    monkeypatch.setattr(bridge, 'list_devices', list_devices)
    monkeypatch.setattr(bridge, 'lockdown', lockdown)
    monkeypatch.setattr(bridge, 'emit', rows.extend)
    asyncio.run(bridge.cmd_devices(None))
    assert len(rows) == 1 and closed == [True]
    assert rows[0]['connection_type'] == 'USB'


def test_bridge_does_not_guess_type_when_active_client_does_not_expose_it(monkeypatch):
    from tools import ios_bridge as bridge
    rows = []
    async def list_devices(): return [SimpleNamespace(serial='ABC',connection_type='Network')]
    class Client:
        service = SimpleNamespace(remote_address=('127.0.0.1',1234))
        async def get_value(self, key): return 'Phone'
        async def close(self): pass
    async def lockdown(udid): return Client()
    monkeypatch.setattr(bridge, 'list_devices', list_devices)
    monkeypatch.setattr(bridge, 'lockdown', lockdown)
    monkeypatch.setattr(bridge, 'emit', rows.extend)
    asyncio.run(bridge.cmd_devices(None))
    assert rows[0]['connection_type'] == ''


def test_controller_keeps_connection_badge_fields_without_renaming_device(controller):
    device = Device('ios:USB1',Platform.IOS,'iPhone 15 Pro',details={'connection_type':'usb'})
    controller._accept_devices([device])
    shown = controller.devices[0]
    assert shown['name'] == 'iPhone 15 Pro'
    assert shown['connection_type'] == 'usb' and shown['connection_label'] == 'USB'

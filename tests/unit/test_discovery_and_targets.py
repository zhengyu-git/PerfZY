"""Malformed bridge rows must not hide otherwise valid iOS devices/apps."""

from perfzy.collectors import discovery, targets
from perfzy.domain.models import Device, Platform


def test_ios_device_discovery_skips_non_object_rows(monkeypatch):
    monkeypatch.setattr(discovery, "run_ios_json", lambda *args, **kwargs: [
        None, "noise", {"udid": "ABC", "model": "iPhone16,1", "version": "18.0"},
    ])

    devices = list(discovery.discover_ios())

    assert len(devices) == 1
    assert devices[0].id == "ios:ABC"
    assert devices[0].name == "iPhone 15 Pro"


def test_ios_target_discovery_skips_non_object_rows(monkeypatch):
    monkeypatch.setattr(targets, "run_ios_json", lambda *args, **kwargs: [
        12, None, {"bundle_id": "com.example.game", "name": "Game"}, {},
    ])
    device = Device("ios:ABC", Platform.IOS, "iPhone")

    applications = list(targets.ios_targets(device))

    assert len(applications) == 1
    assert applications[0].id == "ios:ABC:com.example.game"

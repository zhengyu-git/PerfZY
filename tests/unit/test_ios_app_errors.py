from types import SimpleNamespace

import pytest

from perfzy import automation
from perfzy.automation import PerfZY, PerfZYError
from perfzy.collectors import ios_runtime
from perfzy.domain.models import Device, Platform, Target


@pytest.mark.parametrize("stdout,stderr,code,expected", [
    ("", "missing dependency", 1, "missing dependency"),
    ("invalid", "", 0, "JSON"),
    ("{}", "", 0, "应用列表"),
])
def test_app_failure_propagates_through_sdk(tmp_path, monkeypatch, stdout, stderr, code, expected):
    monkeypatch.setattr(ios_runtime.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code))
    with PerfZY(tmp_path) as api:
        api.device = Device("ios:ABC", Platform.IOS, "iPhone")
        api.games = [Target("old", "ios:ABC", "Old")]
        with pytest.raises(PerfZYError, match=expected) as caught:
            api.select_game("Game")
        assert "读取应用列表失败" in str(caught.value)
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert api.list_games(refresh=False) == []


def test_empty_apps_and_recovery(tmp_path, monkeypatch):
    result = SimpleNamespace(stdout="[]", stderr="", returncode=0)
    monkeypatch.setattr(ios_runtime.subprocess, "run", lambda *a, **k: result)
    with PerfZY(tmp_path) as api:
        api.device = Device("ios:ABC", Platform.IOS, "iPhone")
        assert api.list_games() == []
        with pytest.raises(PerfZYError, match="没有找到应用"):
            api.select_game("Game")
        result.stdout = '[{"bundle_id":"com.example.game","name":"Game"}]'
        assert api.select_game("com.example.game").id == "ios:ABC:com.example.game"


def test_connect_failure_clears_previous_target(tmp_path, monkeypatch):
    monkeypatch.setattr(automation, "discover_all", lambda **kw: [Device("ios:ABC", Platform.IOS, "iPhone")])
    monkeypatch.setattr(ios_runtime.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout="", stderr="pairing failed", returncode=1))
    with PerfZY(tmp_path) as api:
        api.game = Target("old", "windows:local", "Old")
        with pytest.raises(PerfZYError, match="pairing failed"):
            api.connect_device(platform="ios")
        assert api.game is None
        assert api.games == []

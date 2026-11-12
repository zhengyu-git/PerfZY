from types import SimpleNamespace
from perfzy.collectors import targets


def test_android_command_decodes_utf8(monkeypatch):
    def run(command, **kwargs):
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        return SimpleNamespace(returncode=0, stdout="应用窗口")
    monkeypatch.setattr(targets.subprocess, "run", run)
    assert targets._run(["adb"]) == "应用窗口"


def test_missing_command_output_is_text(monkeypatch):
    monkeypatch.setattr(targets.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=None))
    assert targets._run(["adb"]) == ""

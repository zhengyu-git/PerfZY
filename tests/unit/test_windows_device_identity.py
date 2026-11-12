from perfzy.collectors import discovery


def test_windows_identity_is_read_again_on_each_discovery(monkeypatch):
    monkeypatch.setattr(discovery.platform, "node", lambda: "BUILD-PC")
    monkeypatch.setattr(discovery.platform, "processor", lambda: "BUILD-CPU")
    current = {"name": "PC-A", "ProcessorNameString": "CPU-A", "SystemProductName": "Model-A"}
    monkeypatch.setattr(discovery, "_windows_computer_name", lambda: current["name"])
    monkeypatch.setattr(discovery, "_windows_registry_value", lambda path, name: current.get(name, ""))
    first = next(iter(discovery.discover_windows()))
    current.update(name="PC-B", ProcessorNameString="CPU-B", SystemProductName="Model-B")
    second = next(iter(discovery.discover_windows()))
    assert first.name == "本机 PC · PC-A"
    assert second.name == "本机 PC · PC-B"
    assert second.model == "Model-B"
    assert second.details["cpu"] == "CPU-B"


def test_windows_missing_hardware_is_not_filled_with_build_identity(monkeypatch):
    monkeypatch.setattr(discovery, "_windows_registry_value", lambda *args: "")
    device = next(iter(discovery.discover_windows()))
    assert device.model == "未知型号"
    assert device.details["cpu"] == "未知处理器"

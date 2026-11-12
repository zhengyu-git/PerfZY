import queue
import re
from unittest.mock import Mock
import pytest
from perfzy.collectors.android_shell import PersistentAdbShell, TelemetryReadError
from perfzy.collectors.android import AndroidCollector


class Stream:
    def __init__(self): self.lines = queue.Queue()
    def __iter__(self):
        while True:
            line = self.lines.get()
            if line is None: return
            yield line
    def close(self): self.lines.put(None)


class Process:
    def __init__(self, mode):
        self.mode = mode; self.stdout = Stream(); self.stdin = self; self.dead = False
    def poll(self): return 0 if self.dead else None
    def write(self, payload):
        token = re.search(r"(__PERFZY_REQUEST_[a-f0-9]+)_BEGIN", payload)[1]
        if self.mode == "timeout": return
        for line in [token+"_BEGIN\n", "fresh\n", token+"_END:"+("1" if self.mode=="error" else "0")+"\n"]:
            self.stdout.lines.put(line)
    def flush(self): pass
    def kill(self): self.dead = True; self.stdout.close()
    def wait(self, timeout=None): return 0
    def close(self): pass


def factory(monkeypatch, modes):
    processes = []
    def popen(*args, **kwargs):
        p = Process(modes[len(processes)]); processes.append(p); return p
    monkeypatch.setattr("perfzy.collectors.android_shell.subprocess.Popen", popen)
    return processes


def test_reuses_process_for_multiple_requests(monkeypatch):
    processes = factory(monkeypatch, ["ok"]); shell = PersistentAdbShell("adb", "serial")
    try:
        assert shell.execute("first").strip() == "fresh"
        assert shell.execute("second").strip() == "fresh"
        assert len(processes) == 1
    finally: shell.close()
    assert processes[0].dead


def test_timeout_discards_connection_then_reconnects(monkeypatch):
    processes = factory(monkeypatch, ["timeout", "ok"]); shell = PersistentAdbShell("adb", "serial")
    try:
        with pytest.raises(TimeoutError): shell.execute("slow", timeout=0.01)
        assert processes[0].dead
        assert shell.execute("new").strip() == "fresh"
        assert len(processes) == 2
    finally: shell.close()


def test_bad_status_is_not_valid_telemetry(monkeypatch):
    processes = factory(monkeypatch, ["error"]); shell = PersistentAdbShell("adb", "serial")
    with pytest.raises(OSError): shell.execute("bad")
    assert processes[0].dead
    shell.close()


def test_closed_transport_cannot_reopen(monkeypatch):
    processes = factory(monkeypatch, ["ok"]); shell = PersistentAdbShell("adb", "serial"); shell.close()
    with pytest.raises(RuntimeError): shell.execute("read")
    assert not processes


def test_collector_failure_does_not_reread_counters_in_same_round():
    c = AndroidCollector(); transport = Mock(); transport.execute.side_effect = TimeoutError()
    c._light_transport = transport; c._shell = Mock()
    with pytest.raises(TelemetryReadError): c._light_batch({"gpu": ("cat", "/gpu")})
    assert c._light_transport is None
    c._shell.assert_not_called()
    c._shell.return_value = "\n__PERFZY_gpu__\nnext\n"
    assert c._light_batch({"gpu": ("cat", "/gpu")})["gpu"].strip() == "next"


def test_failed_round_keeps_cpu_baseline():
    c = AndroidCollector(); c._enabled = frozenset({"app_cpu"}); c._cpu_snapshot = (100,50,10,(1,2))
    c._sample_impl = Mock(side_effect=TelemetryReadError())
    assert c.sample(2).values == {"app_cpu": None}
    assert c._cpu_snapshot == (100,50,10,(1,2))

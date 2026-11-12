import asyncio
import io
import json
from types import SimpleNamespace

from perfzy.collectors.ios import IOSCollector, _process_activity_state
from perfzy.collectors.ios_sampling import sampling_plan


def test_cpu_only_does_not_request_memory_or_graphics():
    plan = sampling_plan({'app_cpu'}, 2000)
    assert plan['cpu'] and plan['sysmon']
    assert not plan['footprint'] and not plan['graphics'] and not plan['battery']
    assert set(plan['process_fields']) == {'name', 'pid', 'comm', 'cpuUsage'}
    assert plan['interval'] == 2000


def test_memory_only_does_not_enable_cpu():
    plan = sampling_plan({'ios_physical_footprint', 'ios_private_memory'})
    assert plan['footprint'] and not plan['cpu']
    assert 'cpuUsage' not in plan['process_fields']


def test_graphics_only_skips_sysmontap():
    plan = sampling_plan({'fps', 'gpu'})
    assert plan['graphics'] and not plan['sysmon'] and not plan['battery']


def test_rate_subscriptions_keep_required_paired_baselines():
    assert {'diskBytesRead', 'diskBytesWritten'} <= set(sampling_plan({'disk_read'})['process_fields'])
    assert {'ctxSwitch', 'intWakeups'} <= set(sampling_plan({'ios_context_switches'})['process_fields'])
    assert {'netBytesIn', 'netBytesOut'} <= set(sampling_plan({'network_send'})['system_fields'])


def test_configuration_is_sent_to_live_bridge_once_per_change():
    collector = IOSCollector()
    pipe = io.StringIO()
    collector._process = SimpleNamespace(stdin=pipe, poll=lambda: None)
    collector.set_enabled_metrics(frozenset({'app_cpu'}))
    collector.set_sample_interval_ms(2000)
    collector.activate_pending_sources()
    collector.activate_pending_sources()
    collector.set_enabled_metrics(frozenset({'ios_private_memory'}))
    collector.activate_pending_sources()
    assert [json.loads(line) for line in pipe.getvalue().splitlines()] == [
        {'metrics': ['app_cpu'], 'interval': 2000}, {'metrics': ['ios_private_memory'], 'interval': 2000}]


def test_configuration_ack_clears_stale_metrics_and_extends_watchdog():
    collector = IOSCollector()
    collector._latest_process = {'cpuUsage': 90}
    collector._latest_graphics = {'fps': 30}
    collector._accept_stream_event({'type': 'configuration', **sampling_plan({'app_cpu'}, 10000)})
    assert collector._watchdog_seconds == 30
    assert not collector._latest_process and not collector._latest_graphics
    collector.sample(1)


def test_presence_guard_recognizes_background_app():
    assert _process_activity_state({'foregroundRunning': False}) is False


def test_bridge_sends_minimal_valid_device_configuration(monkeypatch):
    from tools import ios_bridge as bridge
    configs = []
    class Info:
        def __init__(self, *args): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def sysmon_process_attributes(self): return ['name', 'pid', 'cpuUsage', 'physFootprint', 'memRPrvt']
        async def sysmon_system_attributes(self): return ['physMemSize', 'netBytesIn']
    class Tap:
        def __init__(self, dvt, process, system, interval_ms):
            self._config = {'procAttrs': process, 'sysAttrs': system, 'sampleInterval': interval_ms * 1000000}
        async def __aenter__(self): configs.append(self._config.copy()); return self
        async def __aexit__(self, *args): pass
        async def __aiter__(self):
            for row in []: yield row
    monkeypatch.setattr(bridge, 'DeviceInfo', Info)
    monkeypatch.setattr(bridge, 'Sysmontap', Tap)
    monkeypatch.setattr(bridge, 'emit', lambda event: None)
    asyncio.run(bridge.stream_sysmon(None, 'game', 'bundle', 2000, sampling_plan({'app_cpu'}, 2000)))
    assert configs == [{'procAttrs': ['name', 'pid', 'cpuUsage'], 'sysAttrs': ['physMemSize'],
                        'sampleInterval': 2000000000, 'cpuUsage': True, 'physFootprint': False, 'ur': 2000}]

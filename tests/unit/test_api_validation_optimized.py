import json

from perfzy import automation
from perfzy.domain.models import Device, Platform, Sample, Target


def test_api_live_values_and_saved_values_share_validation(tmp_path, monkeypatch):
    api = automation.PerfZY(tmp_path)

    class Collector:
        def start(self, *args):
            pass

        def stop(self):
            pass

        def sample(self, elapsed):
            api._stop_event.set()
            return Sample(elapsed, {'fps': -1, 'app_cpu': 120,
                                    'frame_time': 1e12, 'memory': 0,
                                    'total_cpu': 25})

    monkeypatch.setattr(automation, 'WindowsCollector', Collector)
    api.device = Device('windows:local', Platform.WINDOWS, 'PC')
    api.game = Target('windows:1', 'windows:local', 'Game', 1)
    try:
        api.start(metrics=['fps', 'app_cpu', 'frame_time', 'memory'], screenshots=False)
        api._thread.join(timeout=5)
        assert not api._thread.is_alive()
        assert api.latest_metrics() == {'fps': None, 'app_cpu': None,
                                       'frame_time': None, 'memory': 0}
        result = api.stop('validation')
        saved = json.loads(result.session_path.read_text(encoding='utf-8'))
        assert set(saved['samples']) == {'memory'}
        assert saved['samples']['memory'][0]['y'] == 0
    finally:
        api.close()

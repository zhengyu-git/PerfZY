from test_controller import controller
from perfzy.domain.models import Sample


def test_chart_focus_updates_all_charts_shared_range_and_cursor(controller):
    controller._samples = {'fps': [{'x': 0, 'y': 60}, {'x': 10, 'y': 60}]}
    controller.focusChartInterval(3, 5)
    assert controller._selection == (3, 5)
    assert controller.hasTimeLock
    assert controller.lockedTime == 3
    assert controller.chartViewStartRatio < .3
    assert controller.chartViewEndRatio > .5



def test_gui_uses_async_channel_observation_time(controller):
    controller._collecting = True
    controller._active_collection_token = 10
    controller._sample_inflight_token = 10
    controller._accept_sample({'token': 10, 'sample': Sample(3, {'memory': 128}, {'memory': 2.5}),
                               'requested': ['memory'], 'elapsed_ms': 2})
    assert controller._samples['memory'][-1] == {'x': 2.5, 'y': 128}


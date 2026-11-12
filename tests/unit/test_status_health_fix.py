from test_controller import controller


def test_sampling_execution_does_not_misrepresent_missing_fps_as_complete(controller):
    controller._collecting = True
    controller._sample_count = 1
    controller._sampling_health.observe(1, {'memory': {'x': 1, 'y': 128}},
                                         requested=('fps', 'memory'))
    text = controller.samplingHealthText
    assert '采样执行率 100%' in text
    assert '指标有效率 50%' in text
    assert '未收到 fps' in text
    assert '完整率' not in text


def test_no_samples_show_unknown_metric_validity(controller):
    controller._collecting = True
    assert '指标有效率 —' in controller.samplingHealthText


def test_status_health_is_hidden_after_stop(controller):
    controller._collecting = False
    assert controller.samplingHealthText == ''

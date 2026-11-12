from perfzy.collectors.base import FpsStabilizer


def test_isolated_upward_fps_spike_is_replaced_by_recent_baseline():
    stabilizer = FpsStabilizer()
    assert [stabilizer.accept(value) for value in (30, 30, 30)] == [30, 30, 30]

    assert stabilizer.accept(41) == 30
    assert stabilizer.suppressed_spikes == 1
    assert stabilizer.accept(30) == 30


def test_real_large_refresh_rate_switch_is_accepted_after_three_samples():
    stabilizer = FpsStabilizer()
    for value in (30, 30, 30):
        stabilizer.accept(value)

    assert stabilizer.accept(60) == 30
    assert stabilizer.accept(60) == 30
    assert stabilizer.accept(60) == 60
    assert stabilizer.accept(60) == 60


def test_fps_drop_is_never_hidden():
    stabilizer = FpsStabilizer()
    for value in (60, 60, 60):
        stabilizer.accept(value)

    assert stabilizer.accept(24) == 24

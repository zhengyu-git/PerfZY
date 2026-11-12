import pytest

from perfzy.collectors.base import FpsStabilizer


@pytest.mark.parametrize('baseline,new_rate', [(30, 34), (60, 68)])
def test_small_sustained_increase_is_not_suppressed_forever(baseline, new_rate):
    stabilizer = FpsStabilizer()
    for _ in range(12):
        stabilizer.accept(baseline)
    results = [stabilizer.accept(new_rate) for _ in range(8)]
    assert results[5:] == [new_rate] * 3


def test_inconsistent_high_candidates_have_bounded_storage():
    stabilizer = FpsStabilizer()
    for _ in range(12):
        stabilizer.accept(30)
    for i in range(10_000):
        stabilizer.accept(34 if i % 2 else 42)
        assert len(stabilizer.high_candidates) <= 6
    assert stabilizer.history[-1] == 30


def test_missing_sample_breaks_the_sustained_change_sequence():
    stabilizer = FpsStabilizer()
    for _ in range(3):
        stabilizer.accept(30)
    for _ in range(5):
        assert stabilizer.accept(34) == 30
    assert stabilizer.accept(None) is None
    assert not stabilizer.high_candidates
    assert stabilizer.accept(34) == 30

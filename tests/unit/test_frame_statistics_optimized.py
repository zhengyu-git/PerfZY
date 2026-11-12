import math
import random
import tracemalloc

import pytest

from perfzy.domain.frame_statistics import FrameTimeDistribution
from test_android_collector import SURFACE_TEMPLATE, make_collector


def old_percentile(ordered, ratio):
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def test_weighted_statistics_equal_expanded_histogram():
    rng = random.Random(7)
    for _ in range(100):
        counts = {16: rng.randrange(1, 100), 33: rng.randrange(1, 20),
                  50: rng.randrange(1, 10), 100: rng.randrange(1, 5)}
        expanded = sorted(value for value, count in counts.items() for _ in range(count))
        distribution = FrameTimeDistribution(counts)
        assert distribution.count == len(expanded)
        assert distribution.mean == pytest.approx(sum(expanded) / len(expanded))
        for ratio in (0, .01, .5, .95, .99, 1):
            assert distribution.percentile(ratio) == pytest.approx(old_percentile(expanded, ratio))
        assert distribution.between(33.34) == sum(value > 33.34 for value in expanded)
        assert distribution.dropped_frames(1000 / 60) == sum(
            max(0, int(value / (1000 / 60)) - 1) for value in expanded)


def test_million_frames_require_only_bucket_storage():
    tracemalloc.start()
    try:
        distribution = FrameTimeDistribution({16: 1_000_000, 50: 10_000})
        assert distribution.count == 1_010_000
        assert distribution.percentile(.99) == 16
        assert len(distribution.bins) == 2
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 100_000  # Expanding a million list entries needs >8 MB.
    finally:
        tracemalloc.stop()


def test_empty_and_invalid_histogram_values_do_not_create_frames():
    distribution = FrameTimeDistribution({0: 1, -1: 1, 1e12: 1, math.inf: 1,
                                          math.nan: 1, 16: 0, 33: -1})
    assert distribution.count == 0
    assert distribution.mean is None
    assert distribution.percentile(.95) is None


def test_complete_long_frames_remain_in_weighted_statistics():
    # A complete nine-second source frame is reviewable data, not a sentinel.
    distribution = FrameTimeDistribution({16: 2, 9000: 1})
    assert distribution.count == 3
    assert distribution.mean == pytest.approx((16 + 16 + 9000) / 3)
    assert distribution.percentile(1) == 9000
    assert distribution.between(5000) == 1


@pytest.mark.parametrize('ratio', [-1, 1.1, math.nan, math.inf])
def test_invalid_percentile_rejected(ratio):
    with pytest.raises(ValueError):
        FrameTimeDistribution({16: 1}).percentile(ratio)


def test_unchanged_valid_surface_counter_is_zero_not_missing():
    responses = {'--timestats -dump': SURFACE_TEMPLATE.format(frames=600, average=60, smooth=580)}
    collector = make_collector(responses, {'fps', 'frame_time'})
    assert collector.sample(1).values['fps'] is None
    assert collector.sample(2).values == {'fps': 0, 'frame_time': None}
    responses['--timestats -dump'] = ''
    assert collector.sample(3).values['fps'] is None


def test_surface_counter_reset_does_not_turn_into_zero_fps():
    responses = {'--timestats -dump': SURFACE_TEMPLATE.format(frames=600, average=60, smooth=580)}
    collector = make_collector(responses, {'fps'})
    collector.sample(1)
    responses['--timestats -dump'] = SURFACE_TEMPLATE.format(frames=2, average=60, smooth=2)
    assert collector.sample(2).values['fps'] is None


def test_surface_zero_counter_can_establish_a_baseline():
    responses = {'--timestats -dump': SURFACE_TEMPLATE.format(frames=0, average=0, smooth=0)}
    collector = make_collector(responses, {'fps'})
    assert collector.sample(1).values['fps'] is None
    assert collector.sample(2).values['fps'] == 0


def test_histogram_counter_reset_discards_the_whole_mixed_window():
    template = SURFACE_TEMPLATE.replace('33ms=12 50ms=3', '33ms={slow} 50ms=0')
    responses = {'--timestats -dump': template.format(frames=600, average=60, smooth=580, slow=12)}
    collector = make_collector(responses, {'frame_time', 'frame_time_p95'})
    collector.sample(1)
    responses['--timestats -dump'] = template.format(frames=610, average=60, smooth=1000, slow=1)
    values = collector.sample(2).values
    assert values.get('frame_time_p95') is None


def test_jank_only_selection_queries_the_frame_distribution():
    collector = make_collector({}, {'jank'})
    collector.sample(1)
    assert any('gfxinfo' in command and 'framestats' in command for command in collector.calls)

"""DXGI fallback: successful call pairs, process scope, and source switching."""
import struct
import time

import pytest

from perfzy.collectors.dxgi_etw import PresentPairs, PRESENT_START, PRESENT_STOP


def present(pairs, stamp, *, pid=4242, tid=7, address=123, flags=0, result=0, pointer_size=8):
    payload = struct.pack('<QII' if pointer_size == 8 else '<III', address, 1, flags)
    pairs.accept(PRESENT_START, pid, tid, stamp, payload, pointer_size)
    pairs.accept(PRESENT_STOP, pid, tid, stamp + 2, struct.pack('<I', result), pointer_size)


def test_successful_present_intervals_stay_separate_by_process_and_swapchain():
    rows = []
    pairs = PresentPairs(1000, 100, rows.append)
    present(pairs, 110)
    present(pairs, 115, pid=999)
    present(pairs, 118, address=456)
    present(pairs, 130)
    present(pairs, 140, pid=999)
    present(pairs, 148, address=456, pointer_size=4)
    assert [float(row['FrameTime']) for row in rows] == [20, 25, 30]
    assert [row['ProcessID'] for row in rows] == ['4242', '999', '4242']
    assert float(rows[0]['CPUStartTime']) == 30
    assert all('DisplayedTime' not in row and 'MsGPUTime' not in row for row in rows)


@pytest.mark.parametrize('invalid', [{'flags': 1}, {'result': 0x087a0001}, {'result': 0x887a0005}])
def test_tests_occlusion_and_failed_presents_never_become_frames_or_jank(invalid):
    rows = []
    pairs = PresentPairs(1000, 0, rows.append)
    present(pairs, 100)
    present(pairs, 120, **invalid)
    present(pairs, 10000)
    assert not rows
    present(pairs, 10020)
    assert [float(row['FrameTime']) for row in rows] == [20]


def test_missing_pairs_bad_schemas_and_reordered_events_do_not_create_fake_frames():
    rows = []
    pairs = PresentPairs(1000, 0, rows.append)
    pairs.accept(PRESENT_STOP, 4242, 7, 100, b'\0' * 4)
    pairs.accept(PRESENT_START, 4242, 7, 100, b'\0' * 3)
    present(pairs, 100)
    present(pairs, 120)
    present(pairs, 110)
    present(pairs, 140)
    assert [float(row['FrameTime']) for row in rows] == [20, 20]
    pairs.reset_intervals()
    present(pairs, 20000)
    assert len(rows) == 2


class FakeDxgi:
    instances = []

    def __init__(self, emit, pids):
        self.emit, self.pids = emit, pids
        self.started_at = time.monotonic()
        self.session = 'test-own-dxgi'
        self.error = ''
        self.stopped = False
        self.instances.append(self)

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def diagnostics(self):
        return {'session': self.session, 'running': not self.stopped, 'events_or_buffers_lost': 0}



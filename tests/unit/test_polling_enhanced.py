from concurrent.futures import Future

import pytest

from perfzy.collectors.polling import SingleFlightPoller


class ManualExecutor:
    def __init__(self):
        self.pending = []

    def submit(self, fn, *args):
        future = Future()
        self.pending.append((future, fn, args))
        return future

    def finish(self):
        future, fn, args = self.pending.pop(0)
        try:
            future.set_result(fn(*args))
        except Exception as error:
            future.set_exception(error)

    def shutdown(self, **kwargs):
        pass


def make_poller():
    clock = [10.0]
    poller = SingleFlightPoller(clock=lambda: clock[0])
    poller._executor.shutdown(wait=True)
    poller._executor = ManualExecutor()
    return poller, clock


def test_single_flight_has_no_backlog_and_delivers_once():
    poller, clock = make_poller()
    try:
        assert poller.poll(lambda: "first", "pid1") is None
        for _ in range(100):
            assert poller.poll(lambda: "must not queue", "pid1") is None
        assert len(poller._executor.pending) == 1
        poller._executor.finish()
        clock[0] = 11.0
        reading = poller.poll(lambda: "second", "pid1")
        assert reading.value == "first"
        assert reading.started == reading.completed == 10.0
        assert poller.poll(lambda: "third", "pid1") is None
        assert poller.diagnostics()["submitted"] == 2
        assert poller.diagnostics()["busy_polls"] == 101
    finally:
        poller.close()


def test_stale_completed_read_is_discarded_and_retried():
    poller, clock = make_poller()
    try:
        poller.poll(lambda: "stale", "pid1")
        poller._executor.finish()
        clock[0] += 6.0
        assert poller.poll(lambda: "fresh", "pid1") is None
        assert poller.diagnostics()["stale_discarded"] == 1
        assert poller.diagnostics()["last_received_age_s"] is None
        poller._executor.finish()
        assert poller.poll(lambda: "next", "pid1").value == "fresh"
    finally:
        poller.close()


def test_process_change_rejects_completed_read_without_poisoning_next():
    poller, _ = make_poller()
    try:
        poller.poll(lambda: "old process", "pid1")
        poller._executor.finish()
        assert poller.poll(lambda: "new process", "pid2") is None
        assert poller.diagnostics()["context_discarded"] == 1
        poller._executor.finish()
        assert poller.poll(lambda: "next", "pid2").value == "new process"
    finally:
        poller.close()


@pytest.mark.parametrize("read", [lambda: "", lambda: (_ for _ in ()).throw(OSError("timeout"))])
def test_empty_or_failed_read_is_missing_not_cached(read):
    poller, _ = make_poller()
    try:
        poller.poll(read, "pid1")
        poller._executor.finish()
        assert poller.poll(lambda: "recovered", "pid1") is None
        assert poller.diagnostics()["failures"] == 1
        poller._executor.finish()
        assert poller.poll(lambda: "next", "pid1").value == "recovered"
    finally:
        poller.close()


def test_close_prevents_submissions():
    poller, _ = make_poller()
    poller.close()
    assert poller.poll(lambda: "unused", "pid1") is None
    assert poller.diagnostics()["closed"] is True


def test_invalid_age_is_rejected():
    with pytest.raises(ValueError):
        SingleFlightPoller(max_age_s=0)

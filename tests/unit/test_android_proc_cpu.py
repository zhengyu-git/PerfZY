from perfzy.collectors.android import AndroidCollector
from test_android_collector import make_collector


def snapshot(total_user, idle, user, system, started=100):
    fields = ["0"] * 40
    fields[0] = "S"
    fields[11], fields[12], fields[19] = str(user), str(system), str(started)
    return f"cpu {total_user} 0 0 {idle} 0 0 0 0 99 88\n1234 (game name) " + " ".join(fields)


def test_cpu_uses_counter_deltas_without_top():
    responses = {"head -n 1 /proc/stat": snapshot(100, 900, 10, 10)}
    c = make_collector(responses, {"app_cpu", "total_cpu"})
    assert c.sample(1).values["app_cpu"] is None
    responses["head -n 1 /proc/stat"] = snapshot(140, 960, 20, 15)
    result = c.sample(2).values
    assert result == {"app_cpu": 15, "total_cpu": 40}
    assert not any(call.startswith("top ") for call in c.calls)


def test_pid_reuse_does_not_create_false_cpu_spike():
    responses = {"head -n 1 /proc/stat": snapshot(100, 900, 10, 10)}
    c = make_collector(responses, {"app_cpu"})
    c.sample(1)
    responses["head -n 1 /proc/stat"] = snapshot(140, 960, 20, 15, 200)
    assert c.sample(2).values["app_cpu"] is None


def test_restricted_proc_is_not_retried_every_sample():
    c = make_collector({}, {"app_cpu"})
    c.sample(1); c.sample(2)
    assert sum("head -n 1 /proc/stat" in call for call in c.calls) == 1

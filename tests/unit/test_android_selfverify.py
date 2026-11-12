from unittest.mock import Mock
import pytest
from perfzy.collectors.android import AndroidCollector


def test_cpu_and_core_usage_share_identical_kernel_snapshot():
    c = AndroidCollector()
    c._shell = Mock(return_value="\n__PERFZY_cpu_snapshot__\ncpu 1 2 3 4 5 6 7 8\ncpu0 1 2 3 4\n1 (game) S\n")
    data = c._light_batch({"cpu_snapshot": ("sh", "-c", "head -n 1 /proc/stat; cat /proc/1/stat"), "proc_stat": ("cat", "/proc/stat")})
    assert data["cpu_snapshot"] == data["proc_stat"]
    assert c._shell.call_args.args[2].count("/proc/stat") == 1


def test_thermal_paths_cached_but_values_read_again():
    c = AndroidCollector()
    c._shell = Mock(side_effect=["/sys/class/thermal/thermal_zone5/temp:cpu:40000\n", "cpu:42000\n"])
    assert c._read_thermal() == "cpu:40000"
    assert c._read_thermal().strip() == "cpu:42000"
    assert "thermal_zone*" not in c._shell.call_args.args[2]
    assert "/type" not in c._shell.call_args.args[2]


def test_missing_thermal_node_triggers_rediscovery():
    c = AndroidCollector(); c._thermal_paths = [("/sys/class/thermal/thermal_zone5/temp", "cpu")]
    c._shell = Mock(return_value="cpu:\n")
    c._read_thermal()
    assert not c._thermal_paths


def test_same_named_helper_is_not_selected_as_main_process():
    c = AndroidCollector();c._package = "com.game"
    c._shell = Mock(return_value="*APP* ProcessRecord{abcd 12:com.game/u0a1}\n")
    assert c._resolve_pid(["12", "99"]) == "12"


def test_ambiguous_main_process_fails_instead_of_guessing():
    c = AndroidCollector();c._package = "com.game";c._shell = Mock(return_value="")
    with pytest.raises(RuntimeError):c._resolve_pid(["12", "99"])

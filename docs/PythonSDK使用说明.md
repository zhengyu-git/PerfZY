# PerfZY Python SDK 0.3.0

将采集嵌入现有 Python / pytest 测试脚本，无需启动 PerfZY 图形界面。当前 SDK 在 **Windows x64 主机**上运行，可采集 Windows、Android 和 iOS 目标。要求 Python 3.11 或更新版本；SDK 不要求安装 PySide6。

## 安装到你的测试环境

解压 SDK 压缩包，在解压目录打开终端。请用**实际运行测试脚本的 Python**执行安装：

```powershell
python -m pip install .\perfzy_local-0.3.0-py3-none-win_amd64.whl
python -c "import perfzy; print(perfzy.__version__, perfzy.__file__)"
```

如使用虚拟环境，把 `python` 换成该环境的 `python.exe`。压缩包中的 `INSTALL_SDK.cmd` 也可执行相同安装，但它使用当前 PATH 中的 Python。

wheel 已包含 ADB、PresentMon、iOS 桥接程序和本地报告资源；首次安装会通过 pip 安装 Python 依赖，因此需要能访问配置的 pip 源。压缩包不包含第三方 Python 依赖的离线 wheelhouse。已具备依赖的环境可加 `--no-deps`；缺失依赖时不要使用该选项。

开发源码时，也可在 PerfZY 源码目录运行 `python -m pip install -e .`，修改会直接生效。原有 `INSTALL_PYTHON_API.cmd` 是这一源码安装方式；它与独立 SDK wheel 二选一即可。

## 接入现有测试脚本

先连接设备并手动打开游戏。下面以 iOS应用为例；标记范围内替换成你自己的测试步骤：

```python
import time
from pathlib import Path
from perfzy import PerfZY

with PerfZY(Path.cwd() / "perfzy-runs") as perf:
    perf.connect_device(platform="ios")  # 多设备时传 query="设备序列号"
    perf.select_game("com.example.game")
    perf.start(
        metrics=["fps", "frame_time", "frame_jank", "frame_big_jank", "app_cpu", "ios_physical_footprint"],
        sample_interval=1.0,
        screenshots=False,
        ios_fps_limit=30,
    )
    perf.start_mark("主城跑图")
    for _ in range(10):
        # 在这里执行你的点击、移动、战斗等步骤。
        perf.check_health()
        print(perf.latest_metrics())
        time.sleep(1)
    perf.end_mark()
    result = perf.stop("示例应用自动化测试")
    print(result.session_path)
    print(perf.generate_html(result))
```

SDK 控制性能采集，不代替测试框架执行游戏操作。`connect_device()` 选择已连接设备，`select_game()` 选择应用，不会替你启动游戏。iOS 需要已配对、信任电脑、开发者服务可用，并保持目标游戏在运行渲染；Windows ETW 帧采集可能需要以管理员权限运行测试终端。

Android 改用 `platform="android"` 和包名 `com.example.game`，内存指标用 `memory`。Windows 改用 `platform="windows"` 和目标 exe 名称，例如 `ExampleGame.exe`，内存同样用 `memory`。多设备或同名目标时请明确指定设备查询，避免自动选到列表中的第一项。

显式传入 `root` 决定数据保存位置。若要让桌面 PerfZY 的“采集记录”页直接显示脚本记录，可把 root 指向该软件目录，例如 `PerfZY(r"C:\Tools\PerfZY")`，采集后刷新记录页。建议同一时间只让一个采集端使用同一台目标设备。

## 可调用接口

| 接口 | 用途 / 返回 |
| --- | --- |
| `refresh_devices()` | 扫描设备，返回 `list[Device]`；扫描错误在 `discovery_errors` |
| `connect_device(query=None, platform=...)` | 选择设备，返回 `Device` |
| `list_games()` / `select_game(query)` | 列出、选择应用，返回 `Target` 列表或对象 |
| `start(metrics=None, sample_interval=1, screenshots=True, ...)` | 开始采集，返回记录编号 |
| `latest_metrics()` | 最近完成的指标快照；缺失为 `None` |
| `check_health()` | 采样失败时抛出 `PerfZYError`；应在测试等待中定期调用 |
| `metric_status()` | 已请求、平台支持、已收到及暂无数据的指标集合 |
| `metric_freshness(stale_after=5)` | 当前采集中各指标最近有效观测的时间与是否过期 |
| `diagnostics()` | 来源、有效窗口、缺失、采集通道等诊断 |
| `start_mark(name)` / `end_mark()` | 开始、结束一个场景范围 |
| `stop(name="")` | 停止并保存，返回 `SessionResult`；未闭合的标记自动结束 |
| `generate_html(session=None)` | 生成本地 HTML，返回 `Path` |
| `list_records()` | 按新建/合并时间倒序列出该 root 的已保存记录 |
| `merge_records([id1, id2, ...], name="")` | 按传入顺序合并，返回新 `session.json` 的 `Path` |
| `close()` / `with PerfZY(...)` | 清理采集器和线程；活动采集会先保存 |

生成报告时 `session` 可传 `SessionResult`、记录编号或 `Path(".../session.json")`，不传时使用本次对象最后保存的记录。合并和生成报告可以在 `stop()` 后调用。

`SessionResult` 包含 `session_id`、`session_path`、`name`、`duration`、`sample_count`、`screenshot_count`、`scenes`。`duration` 和采样周期的单位均为秒；截图周期也用秒，`screenshot_width=0` 表示原尺寸。

## 运行现成示例

```powershell
# iOS：10 秒采集，30 FPS 上限校验，生成 HTML
python .\examples\python_automation.py --platform ios --app com.example.game --seconds 10 --fps-limit 30

# Android；需要截图时加 --screenshots
python .\examples\python_automation.py --platform android --app com.example.game --seconds 30

# Windows
python .\examples\python_automation.py --platform windows --app ExampleGame.exe --seconds 30

# 列出某个数据目录的记录，再按需要的顺序合并
python .\examples\merge_and_report.py --root .\perfzy-runs
python .\examples\merge_and_report.py --root .\perfzy-runs --sessions RECORD_ID_1 RECORD_ID_2 --name 跑图合集
```

pytest 模板 `examples/pytest_perfzy_example.py` 在未设置目标时会跳过，不会自动连接设备：

```powershell
python -m pip install pytest
$env:PERFZY_PLATFORM="ios"
$env:PERFZY_TEST_APP="com.example.game"
# 可选：$env:PERFZY_DEVICE_QUERY="你的设备序列号"
python -m pytest .\examples\pytest_perfzy_example.py -s
```

将模板中的测试循环替换为已有自动化步骤；fixture 在用例异常时也会尝试 `stop()` 保存已有数据。运行时断线会通过 `PerfZYError` 报告，`stop()` 可能提示“已保存现有数据”，此时可再次调用 `stop()` 取得已保存的结果对象。

## 指标与数据说明

- 新 Jank 的键是 `frame_jank`、`frame_big_jank`，与旧的固定阈值 `jank`、`big_jank` 不同。三端共用已声明的逐帧规则；Big Jank 包含在 Jank 中。
- iOS FPS 优先使用实际逐帧间隔，通道不可用时回退可能为整数的计数器。来源与回退次数可查 `diagnostics()`。`ios_fps_limit=0` 表示不设上限，`None` 使用应用默认设置。
- iOS 帧源为经过目标活动状态筛选的设备 CoreAnimation，非 PID 隔离。`frame_time` 是由采用 FPS 换算的窗口值，原始逐帧间隔另存。只有平均 FPS 的旧记录不能补造逐帧指标。
- 指标在平台上“可启用”不保证硬件和系统实际提供数据。缺失保持 `None`；不要把未收到数据当成零。
- `data/sessions/<id>` 保存会话、审计、截图和实际逐帧数据；`exports` 保存报告与转换文件。合并不会修改源记录，时间按各段时长累加，截图、指标和场景一并衔接。

## 从源码重新打包

在完整源码环境中，确保已有 setuptools 68 或更新版本，然后执行：

```powershell
python tools\build_sdk.py --output exports\sdk
```

该命令本地构建 wheel，再打包安装脚本、说明、示例和 SHA256 清单；不包含采集记录、登录凭据或用户设置，不发布到任何网站。

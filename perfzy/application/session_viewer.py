"""A saved recording workspace independent from the live collection controller."""
from pathlib import Path
from datetime import datetime
import math
from PySide6.QtCore import Property, Signal, Slot, QDir, QTemporaryDir

from .controller import AppController
from . import controller as controller_module
from .device_presentation import PLATFORM_NAMES
from ..domain.application_identity import mobile_package_name
from ..domain.models import Platform
from ..domain.provenance import canonical_platform
from ..storage.editing import compact_removed_ranges, remove_time_range, save_scene_annotations
from ..storage.sessions import normalize_session_name
from ..storage.record_overwrite import has_overwrite_backup, undo_last_overwrite


class SessionViewerController(AppController):
    recordInfoChanged = Signal()
    editInfoChanged = Signal()
    editReady = Signal(object)

    def __init__(self, live_controller: AppController) -> None:
        self._viewer_preferences = QTemporaryDir(QDir.tempPath() + "/perfzy-record-viewer-XXXXXX")
        if not self._viewer_preferences.isValid():
            raise OSError("无法创建记录查看的临时显示设置。")
        assert Path(self._viewer_preferences.path()).resolve().is_relative_to(Path(QDir.tempPath()).resolve())
        super().__init__(history_only=True,
                         preferences_path=Path(self._viewer_preferences.path()) / "preferences.ini")
        self._live_controller = live_controller
        self._edit_working = False
        self._edit_create_copy = True
        self._has_undo_backup = False
        self._edit_notice = ""
        self.editReady.connect(self._accept_edit)
        self.selectionChanged.connect(self.editInfoChanged.emit)
        self.scenesChanged.connect(self.editInfoChanged.emit)
        self.editInfoChanged.connect(live_controller.stateChanged.emit)
        self.sessionOpened.connect(self._reset_edit_notice)
        live_controller._record_viewer = self
        self.stateChanged.connect(self.recordInfoChanged.emit)
        live_controller.sessionRecordsChanged.connect(self._sync_record_summary)

    def _reset_edit_notice(self, *_):
        self._edit_notice = ""
        self._has_undo_backup = has_overwrite_backup(controller_module.WORK_ROOT, self._session_id)
        self.editInfoChanged.emit()

    @Property(bool, notify=editInfoChanged)
    def hasUndoBackup(self):
        return self.viewingSession and self._has_undo_backup

    @Property(bool, notify=editInfoChanged)
    def editing(self): return self._edit_working

    @Property(str, notify=editInfoChanged)
    def editNotice(self): return self._edit_notice

    @Property(bool, notify=editInfoChanged)
    def editAsCopy(self):
        return self._live_controller._preferences._settings.value("records/edit_as_copy",True,type=bool)

    @Property(bool, notify=editInfoChanged)
    def overwriting(self): return self.editing and not self._edit_create_copy

    @Property("QVariantList", notify=editInfoChanged)
    def selectionScenes(self):
        if not self.hasSelection: return []
        first, last = self._selection
        return [dict(scene) for scene in self._scenes
                if float(scene["start"]) < last and float(scene["end"]) > first]

    def _commit_scenes(self, scenes):
        if not self.viewingSession or self.editing: return False
        try:
            save_scene_annotations(controller_module.WORK_ROOT, self._session_id, scenes, self.duration)
        except (OSError, ValueError, TypeError, KeyError) as error:
            self._notify("场景未保存", str(error), "warning")
            return False
        self._scenes = scenes
        self._edit_notice = "场景已保存"
        self._live_controller.refreshSessionRecords()
        self.scenesChanged.emit()
        self.editInfoChanged.emit()
        return True

    def _scene(self, identifier, name, first, last, color):
        title = normalize_session_name(name)
        if not title: raise ValueError("请输入场景名称")
        return dict(id=identifier, name=title, start=first, end=last, color=color,
                    title=f"{title}  {self._format_time(first)}—{self._format_time(last)}")

    @Slot(str, float, float, str, result=bool)
    def addSavedScene(self, name, first, last, color):
        identifier = max((int(row.get("id", 0)) for row in self._scenes
                          if str(row.get("id", "")).isdigit()), default=0) + 1
        try:
            scene = self._scene(identifier, name, first, last, color)
        except ValueError as error:
            self._notify("场景未保存", str(error), "warning")
            return False
        return self._commit_scenes([*self._scenes, scene])

    @Slot(int, str, float, float, str, result=bool)
    def updateSavedScene(self, index, name, first, last, color):
        if not 0 <= index < len(self._scenes): return False
        scenes = list(self._scenes)
        try:
            scenes[index] = self._scene(scenes[index]["id"], name, first, last, color)
        except ValueError as error:
            self._notify("场景未保存", str(error), "warning")
            return False
        return self._commit_scenes(scenes)

    @Slot("QVariantList", result=bool)
    def deleteSavedScenes(self, identifiers):
        selected = set(identifiers)
        if not selected or not selected.issubset({row["id"] for row in self._scenes}):
            self._notify("场景未删除", "场景列表已变化，请重新选择。", "warning")
            return False
        return self._commit_scenes([row for row in self._scenes if row["id"] not in selected])

    @Slot(float, float, result=bool)
    @Slot(float, float, bool, result=bool)
    def deleteSelectedTimeRange(self, first, last, create_copy=True):
        if not self.viewingSession or self.editing: return False
        if not all(math.isfinite(value) for value in (first, last)) or not 0 <= first < last <= self.duration + .001:
            self._notify("无法编辑时间段", "请先框选有效的时间范围。", "warning")
            return False
        if first <= 0 and last >= self.duration:
            self._notify("请保留一段数据", "所选范围覆盖整条记录，请缩小框选范围。", "warning")
            return False
        return self._start_time_edit(first,last,create_copy=create_copy)

    @Slot(result=bool)
    @Slot(bool, result=bool)
    def compactRemovedTimeRanges(self, create_copy=True):
        if not self.viewingSession or self.editing or not self.excludedIntervals:
            return False
        return self._start_time_edit(compact=True,create_copy=create_copy)

    @Slot(result=bool)
    def undoLastOverwrite(self):
        if not self.hasUndoBackup or self.editing:
            return False
        return self._start_time_edit(create_copy=False, undo=True)

    def _start_time_edit(self, first=0.0, last=0.0, *, compact=False, create_copy=True, undo=False):
        if not create_copy and (self._live_controller._report_working or self._live_controller._merging_sessions or self._live_controller._saving_session):
            self._notify("暂时无法覆盖记录","请等待报告生成、记录合并或保存完成后再覆盖。","warning")
            return False
        identifier = self._session_id
        root = controller_module.WORK_ROOT
        self._edit_working = True
        self._edit_create_copy = bool(create_copy)
        self._edit_notice = "正在撤销覆盖剪辑…" if undo else "正在拼接时间轴…"
        self.editInfoChanged.emit()
        def work():
            try:
                if undo:
                    path = undo_last_overwrite(root, identifier)
                elif compact:
                    path = compact_removed_ranges(root, identifier, create_copy=create_copy)
                else:
                    path = remove_time_range(root, identifier, first, last, create_copy=create_copy)
                result = dict(ok=True, source=identifier, id=path.parent.name, create_copy=bool(create_copy), undo=undo)
            except Exception as error:
                result = dict(ok=False, source=identifier, error=str(error))
            if not self._shutting_down:
                self.editReady.emit(result)
        self._executor.submit(work)
        return True

    @Slot(object)
    def _accept_edit(self, result):
        self._edit_working = False
        if self._shutting_down: return
        if not result.get("ok"):
            self._edit_notice = "编辑未完成，请查看提示"
            self.editInfoChanged.emit()
            self._notify("记录保存失败", result.get("error") or "未知错误", "warning")
            return
        create_copy = result.get("create_copy",True)
        undo = result.get("undo", False)
        if not undo:
            settings = self._live_controller._preferences._settings
            settings.setValue("records/edit_as_copy", create_copy)
            settings.sync()
        self._live_controller.refreshSessionRecords()
        if self.viewingSession and self._session_id == result["source"]:
            self.openSession(result["id"])
        if undo:
            self._edit_notice = "已恢复到覆盖剪辑前"
        else:
            self._edit_notice = "剪辑副本已保存，原记录保留" if create_copy else "已保存到当前记录"
        self.editInfoChanged.emit()

    @Property("QVariantMap", notify=recordInfoChanged)
    def recordInfo(self):
        metadata = self._viewed_metadata if self.viewingSession else {}
        device, target = metadata.get("device") or {}, metadata.get("target") or {}
        platform = canonical_platform(device)
        device_details = {key: device.get(key) or "" for key in
                          ("name", "model", "cpu", "gpu", "resolution", "memory")}
        device_details["platform"] = PLATFORM_NAMES.get(platform, device.get("platform") or "")
        device_details["os"] = device.get("os") or device.get("os_version") or ""

        application = str(target.get("name") or "").strip()
        package = mobile_package_name(target) if platform in (Platform.ANDROID, Platform.IOS) else ""
        label, separator, suffix = application.rpartition("·")
        if package and separator and suffix.strip() == package:
            label = label.strip()
        else:
            label = application
        application_name = label or package or "未知应用"

        raw_time = str(metadata.get("started_at") or "")
        try:
            started_at = datetime.fromisoformat(raw_time).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            started_at = raw_time.replace("T", " ")[:19]
        return {"id": self._session_id if self.viewingSession else "",
                "edited": bool(metadata.get("edited_from")),
                "name": metadata.get("name") or target.get("name") or "采集记录",
                "application": application or application_name,
                "applicationName": application_name,
                "applicationIdentifier": package if package != application_name else "",
                "device": device.get("name") or device.get("model") or "未知设备",
                "deviceDetails": device_details,
                "model": device.get("model") or "",
                "startedAt": started_at}

    @Slot()
    def _sync_record_summary(self):
        if not self.viewingSession:
            return
        record = next((row for row in self._live_controller.sessionRecords
                       if row["id"] == self._session_id), None)
        if record is None:
            self.closeSessionView()
        elif record.get("name") != self._viewed_metadata.get("name"):
            self._viewed_metadata["name"] = record.get("name")
            self.stateChanged.emit()

    @Slot()
    def generateLocalReport(self):
        if self.viewingSession:
            self._live_controller.generateReportForSession(self._session_id)

    @Slot()
    def shutdown(self):
        super().shutdown()
        self._viewer_preferences.remove()

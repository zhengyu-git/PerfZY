"""Background upload and local settings for the desktop UI."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Property, QObject, QSettings, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ..integrations.record_upload import UploadError, upload_record, validate_upload_settings


class RecordUploadBridge(QObject):
    changed = Signal()
    settingsChanged = Signal()
    finished = Signal(object)

    def __init__(self, owner, root: Path):
        super().__init__(owner)
        self._root = Path(root)
        self._settings = QSettings(str(self._root / "data" / "upload.ini"), QSettings.IniFormat)
        self._endpoint = str(self._settings.value("endpoint", ""))
        self._token = ""
        self._busy = self._closed = self._failed = False
        self._record_id = self._message = self._settings_error = ""
        self._result = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PerfZY-Upload")
        self.finished.connect(self._accept)

    @Property(str, notify=settingsChanged)
    def endpoint(self):
        return self._endpoint

    @Property(bool, notify=settingsChanged)
    def hasToken(self):
        return bool(self._token)

    @Property(str, notify=settingsChanged)
    def settingsError(self):
        return self._settings_error

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(bool, notify=changed)
    def failed(self):
        return self._failed

    @Property(str, notify=changed)
    def recordId(self):
        return self._record_id

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property("QVariantMap", notify=changed)
    def result(self):
        return self._result

    @Slot(str, str, result=bool)
    def saveSettings(self, endpoint, token):
        if self._busy:
            self._settings_error = "上传完成后可修改设置。"
            return False
        endpoint = endpoint.strip()
        token = token.strip() or (self._token if endpoint == self._endpoint else "")
        self._settings_error = ""
        try:
            if endpoint or token:
                validate_upload_settings(endpoint, token)
        except UploadError as error:
            self._settings_error = str(error)
            self.settingsChanged.emit()
            return False
        endpoint_changed = endpoint != self._endpoint
        if endpoint_changed:
            self._settings.setValue("endpoint", endpoint)
            self._settings.sync()
            if self._settings.status() != QSettings.NoError:
                self._settings_error = "接收地址保存失败，请检查目录写入权限。"
                self.settingsChanged.emit()
                return False
        self._endpoint, self._token = endpoint, token
        if endpoint_changed:
            self._message = ""
            self._failed = False
            self._result = {}
            self.changed.emit()
        self.settingsChanged.emit()
        return True

    @Slot()
    def clearToken(self):
        if self._busy:
            return
        self._token = self._settings_error = ""
        self.settingsChanged.emit()

    @Slot(str, bool)
    def startUpload(self, record_id, include_screenshots):
        if self._busy or self._closed or not record_id or not self._endpoint:
            return
        endpoint, token = self._endpoint, self._token
        self._record_id = record_id
        self._busy, self._failed = True, False
        self._result = {}
        self._message = "正在准备并上传记录…"
        self.changed.emit()

        def send():
            try:
                result = upload_record(self._root, record_id, endpoint, token,
                                       include_screenshots=include_screenshots)
                payload = {"result": result}
            except UploadError as error:
                payload = {"error": str(error)}
            except Exception:
                payload = {"error": "上传未完成。请检查接口地址、服务状态和本地记录。"}
            if not self._closed:
                self.finished.emit(payload)

        self._pool.submit(send)

    @Slot(object)
    def _accept(self, payload):
        if self._closed:
            return
        self._busy = False
        self._failed = bool(payload.get("error"))
        self._result = payload.get("result", {})
        self._message = payload.get("error") or "服务已接收记录。"
        self.changed.emit()

    @Slot()
    def openReport(self):
        url = self._result.get("report_url")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def shutdown(self):
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

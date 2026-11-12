from test_controller import controller
from pathlib import Path
import pytest
from test_html_reports import report
from perfzy.application import controller as module


@pytest.fixture(scope='module')
def report_qt_application():
    from PySide6.QtGui import QGuiApplication
    application=QGuiApplication.instance() or QGuiApplication([])
    yield application


@pytest.fixture
def watched_controller(report_qt_application,controller):
    return controller


def test_report_refresh_uses_background_job_and_publishes_source_availability(controller,tmp_path,monkeypatch):
    path=report(tmp_path)
    controller._session_records=[{'id':'source-one'}]
    jobs=[]
    monkeypatch.setattr(controller._executor,'submit',lambda fn:jobs.append(fn))
    controller.refreshHtmlReports()
    assert controller.htmlReportsLoading and not controller.htmlReports
    jobs.pop()()
    assert not controller.htmlReportsLoading and controller.htmlReports[0]['id']==path.name
    assert controller.htmlReports[0]['sourceAvailable']
    assert str((tmp_path/'exports').resolve()) in controller._html_watcher.directories()


def test_opening_export_is_allowed_during_collection_and_uses_local_file_url(controller,tmp_path,monkeypatch):
    path=report(tmp_path)
    opened=[]
    monkeypatch.setattr(module.QDesktopServices,'openUrl',lambda url:opened.append(url.toLocalFile()) or True)
    controller._collecting=True
    assert controller.openHtmlReport(path.name)
    assert [Path(value) for value in opened]==[path]
    controller._collecting=False


def test_report_source_link_updates_when_source_record_is_deleted(controller):
    controller._html_reports=[{'id':'one.html','sourceSessionId':'source-one','sourceAvailable':True}]
    controller._session_records=[]
    controller._sync_html_source_links()
    assert not controller.htmlReports[0]['sourceAvailable']


def test_new_html_file_is_detected_automatically(watched_controller,tmp_path,monkeypatch):
    import time
    from PySide6.QtTest import QTest
    controller=watched_controller
    report(tmp_path)
    monkeypatch.setattr(controller._executor,'submit',lambda fn:fn())
    controller.refreshHtmlReports()
    assert len(controller.htmlReports)==1
    report(tmp_path,'second.html')
    deadline=time.monotonic()+2
    while len(controller.htmlReports)<2 and time.monotonic()<deadline:
        QTest.qWait(20)
    assert len(controller.htmlReports)==2


def test_newly_generated_report_refreshes_report_library(controller,tmp_path,monkeypatch):
    path=report(tmp_path)
    calls=[]
    monkeypatch.setattr(controller,'refreshHtmlReports',lambda:calls.append('refresh'))
    monkeypatch.setattr(module.QDesktopServices,'openUrl',lambda url:True)
    controller._accept_report_generated({'ok':True,'path':str(path)})
    assert calls==['refresh']


def test_in_progress_report_cannot_be_renamed_or_deleted(controller,tmp_path):
    path=report(tmp_path)
    controller._report_working=True
    assert not controller.renameHtmlReport(path.name,'new')
    assert not controller.deleteHtmlReport(path.name)
    assert path.exists()

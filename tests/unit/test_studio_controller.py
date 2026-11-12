import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import QObject,Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from perfzy.application import studio as module
from perfzy.studio.storage import write_json,read_json
from perfzy.storage.sessions import save_session
from perfzy.domain.models import Device,Target,Platform

APP=QGuiApplication.instance() or QGuiApplication([])


class Owner(QObject):
    settingsChanged=Signal();stateChanged=Signal()
    def __init__(self):
        super().__init__()
        self.sessionRecords=[];self.collecting=False;self.busy=False;self.collectionStarting=False
        self._busy=False;self._selected_device=self._selected_target=0
        self._device_models=[Device('windows:one',Platform.WINDOWS,'MockPC')]
        self._target_models=[Target('windows:123','windows:one','MockGame',123)]
        self._performance_window=self._screenshot_window=self._session_spool=None
        self.sampleIntervalSeconds=1;self._session_id='one';self.focus=None
    def _active_metric_keys(self):return {'fps','memory'}
    def refreshSessionRecords(self):pass
    def focusChartInterval(self,a,b):self.focus=(a,b)


@pytest.fixture
def studio(tmp_path):
    owner=Owner()
    instance=module.StudioController(owner,tmp_path)
    for ident,fps in [('one',60),('two',40)]:
        path=save_session(tmp_path,dict(platform='windows',id='windows:one'),dict(name='MockGame'),
            dict(fps=[dict(x=i,y=fps) for i in range(10)]),[],{'fps'},ident,[],duration=9,session_name=ident)
        owner.sessionRecords.append(dict(id=ident,name=ident,path=str(path),duration='00:09'))
    instance.refresh()
    yield instance
    instance.close();instance.pool.shutdown(wait=True,cancel_futures=True)
    APP.processEvents()


def finish(studio):
    for _ in range(200):
        APP.processEvents()
        if not studio.busy and not studio.taskRunning:return
        time.sleep(.005)
    future=getattr(studio,'_job_future',None)
    raise AssertionError(f'bounded studio task did not finish: future_done={future.done() if future else None}; error={future.exception() if future and future.done() else None}')


def test_selection_version_and_notes_persist_without_rewriting_session(studio):
    source=studio.root/'data/sessions/one/session.json';original=source.read_bytes()
    studio.selectRecord('one',True);studio.tagSelectedVersion('v1')
    studio.addNote(0,3,'<b>测试备注</b>')
    value=read_json(source.parent/'studio-metadata.json')
    assert value['version']=='v1' and value['notes'][0]['text']=='<b>测试备注</b>'
    assert source.read_bytes()==original
    assert studio.records[0]['version']=='v1'


def test_malformed_metadata_does_not_break_record_picker(studio):
    (studio.root/'data/sessions/one/studio-metadata.json').write_text('[]')
    studio.refresh();assert len(studio.records)==2
    studio.selectRecord('../escape',True);assert not studio.selectedIds


def test_compare_and_missing_frame_details_are_real_async_results(studio):
    studio.selectRecord('one',True);studio.selectRecord('two',True)
    studio.compare('fps','',0,9);finish(studio)
    assert len(studio.result['overlay']['curves'])==2
    studio.frameDetail(2,1);finish(studio)
    assert studio.result['detail']['total']==0
    assert '没有真实逐帧' in studio.result['detail']['reason']


def test_missing_selection_actions_explain_how_to_start(studio):
    studio.listHitches(50)
    assert '勾选' in studio.message
    studio.compare('memory','',0,9)
    assert '勾选' in studio.message
    studio.openPrimary()
    assert '勾选' in studio.message
    studio.addNote(0,5,'备注')
    assert '勾选' in studio.message


def test_hitch_empty_state_distinguishes_absent_frames(studio):
    studio.selectRecord('one',True)
    studio.listHitches(50);finish(studio)
    assert not studio.result['hitches']['rows']
    assert '没有真实逐帧' in studio.result['hitches']['reason']
    studio.listHitches(float('nan'))
    assert '毫秒' in studio.message


def test_live_evidence_export_waits_for_saved_context(studio):
    studio.owner.collecting=True
    studio._live_events=[dict(start=0,end=5,id=1,time=2,reasons=[])]
    studio.exportEvidence(0,'')
    assert '停止并保存' in studio.message and not studio.busy


def test_evidence_focus_is_explicit_about_loaded_record(studio):
    studio.selectRecord('one',True)
    studio._events=[dict(start=1,end=4,id=1,time=2)]
    studio.focusEvidence(0);assert studio.owner.focus==(1,4)
    studio.owner._session_id='two';studio.owner.focus=None
    studio.focusEvidence(0);assert studio.owner.focus is None


def test_evidence_options_save_apply_to_active_monitor(studio):
    monitor=SimpleNamespace(options={})
    studio.owner._session_spool=SimpleNamespace(extras=SimpleNamespace(monitor=monitor))
    studio.setEvidenceOptions(dict(enabled=False,cooldown=60,max_events=3))
    assert not studio.options['enabled'] and monitor.options['max_events']==3
    studio.setEvidenceOptions(dict(cooldown=float('nan')))
    assert '无效' in studio.message


def test_saved_plan_validated_and_no_background_autostart(studio):
    studio.savePlan(dict(name='方案',metrics=['fps'],rounds=2,duration=4))
    assert studio.plans[0]['metrics']==['fps'] and not studio.taskRunning
    assert read_json(studio.root/'data/studio/plans.json',[])[0]['name']=='方案'


def test_task_blocks_manual_collection_and_cancellation_is_forwarded(studio,monkeypatch):
    entered=threading.Event();release=threading.Event();seen=[]
    def runner(root,device,target,plan,cancel,progress,**kwargs):
        seen.append((device,target,plan));entered.set()
        release.wait(2)
        assert cancel.is_set()
        return [dict(id='saved',name='保存的当前轮')]
    monkeypatch.setattr(module,'run_plan',runner)
    studio.startPlan(dict(metrics=['fps'],duration=4))
    assert entered.wait(1)
    assert studio.taskRunning and studio.owner._busy
    studio.cancelPlan();release.set();finish(studio)
    assert studio.task['state']=='cancelled' and not studio.owner._busy
    assert studio.task['results'][0]['id']=='saved'
    assert seen[0][1].process_id==123


def test_task_failure_unlocks_owner(studio,monkeypatch):
    def fail(*args,**kwargs):raise RuntimeError('模拟断连')
    monkeypatch.setattr(module,'run_plan',fail)
    studio.startPlan(dict(metrics=['fps'],duration=4));finish(studio)
    assert studio.task['state']=='failed' and '模拟断连' in studio.task['error']
    assert not studio.owner._busy


def test_export_json_and_range_zip_remain_under_copy(studio):
    studio.selectRecord('one',True)
    studio.exportRange(0,5,'fps','','');finish(studio)
    path=Path(studio.result['export'])
    assert path.parent==studio.root/'exports/studio' and path.is_file()
    studio.exportResults()
    assert list((studio.root/'exports/studio').glob('analysis-*.json'))

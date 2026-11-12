from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from urllib.parse import unquote,urlparse
import uuid

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices

from ..domain.metrics import METRIC_BY_KEY
from ..storage.sessions import load_session, _session_directory
from ..studio.analytics import memory_analysis, overlay, points
from ..studio.exports import export_bundle
from ..studio.plans import run_plan, validate_plan
from ..studio.storage import (evidence_options, read_json, write_json, number,
                              frame_window, slow_frames)


class StudioController(QObject):
    changed = Signal()
    completed = Signal(object)
    taskProgress = Signal(object)

    def __init__(self, owner, root):
        super().__init__(owner)
        self.owner, self.root = owner, Path(root)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='PerfZY-Studio')
        self._selected, self._records, self._events, self._live_events = [], [], [], []
        self._result, self._task, self._message = {}, {}, ''
        self._busy = self._task_running = self._closed = False
        self.cancel = threading.Event()
        self._options = evidence_options(root)
        self._plans = read_json(self.root/'data/studio/plans.json', [])
        if not isinstance(self._plans, list):
            self._plans = []
        self._plans = [p for p in self._plans[:50] if isinstance(p,dict)]
        self.completed.connect(self._accept)
        self.taskProgress.connect(self._task_progress)
        owner.settingsChanged.connect(self.changed)

    @Property('QVariantList', notify=changed)
    def currentMetrics(self): return sorted(self.owner._active_metric_keys())

    @Property('QVariantList', notify=changed)
    def records(self): return self._records
    @Property('QVariantList', notify=changed)
    def selectedIds(self): return list(self._selected)
    @Property(str,notify=changed)
    def primaryRecordName(self):
        return next((r['name'] for r in self._records if self._selected and r['id']==self._selected[0]),'未选择记录')
    @Property('QVariantMap', notify=changed)
    def result(self): return self._result
    @Property('QVariantList', notify=changed)
    def events(self): return self._live_events if self.owner.collecting else self._events
    @Property('QVariantMap', notify=changed)
    def options(self): return self._options
    @Property('QVariantList', notify=changed)
    def plans(self): return self._plans
    @Property('QVariantMap', notify=changed)
    def task(self): return self._task
    @Property(bool, notify=changed)
    def taskRunning(self): return self._task_running
    @Property(bool, notify=changed)
    def busy(self): return self._busy
    @Property(str, notify=changed)
    def message(self): return self._message
    @Property('QVariantList', constant=True)
    def metrics(self):
        return [dict(key=key, title=f'{value.title} ({value.unit})') for key,value in METRIC_BY_KEY.items()]

    def _directory(self, ident):
        return _session_directory(self.root, ident)

    @Slot()
    def refresh(self):
        self._records = []
        for record in self.owner.sessionRecords[:300]:
            row = dict(record)
            try:directory=self._directory(row['id'])
            except (ValueError,KeyError):continue
            extra = read_json(directory/'studio-metadata.json')
            row.update(version=str(extra.get('version') or '')[:80], selected=row['id'] in self._selected)
            self._records.append(row)
        valid = {r['id'] for r in self._records}
        self._selected = [s for s in self._selected if s in valid]
        self._events = read_json(self._directory(self._selected[0])/'evidence.json',[]) if self._selected else []
        if not isinstance(self._events,list): self._events = []
        self._events=[row for row in self._events[:200] if isinstance(row,dict)
                      and all(number(row.get(key)) is not None for key in ('start','end','time'))
                      and isinstance(row.get('reasons',[]),list)]
        self.changed.emit()

    @Slot(str, bool)
    def selectRecord(self, ident, selected):
        if self._busy: return
        if ident not in {r['id'] for r in self._records}: return
        if selected and ident not in self._selected:
            if len(self._selected) >= 12:
                self._message = '一次最多选择12条记录；建议分设备和应用分析。'
            else:
                self._selected.append(ident)
        elif not selected and ident in self._selected:
            self._selected.remove(ident)
        self._result = {}
        self.refresh()

    @Slot()
    def clearRecords(self):
        if self._busy:return
        self._selected=[];self._result={};self.refresh()

    @Slot(str)
    def tagSelectedVersion(self, version):
        if self._busy: return
        version = str(version).strip()[:80]
        if not version or not self._selected:
            self._message = '请先选择记录并填写明确版本。'
        else:
            try:
                for ident in self._selected:
                    path = self._directory(ident)/'studio-metadata.json'
                    value = read_json(path)
                    value['version'] = version
                    write_json(path,value)
                self._message = '版本已保存到增强副本的记录附加信息。'
            except Exception as exc:
                self._message = str(exc)
        self.refresh()

    def _sessions(self, identifiers):
        sessions = []
        total_bytes=0
        for ident in identifiers:
            total_bytes+=(self._directory(ident)/'session.json').stat().st_size
            if total_bytes>128*1024*1024:
                raise ValueError('所选原始记录合计超过128MB，请分批选择，避免工作台占用过多内存。')
            value = load_session(self.root,ident)
            if value is None: raise ValueError('无法读取记录：'+ident)
            if value.get('id')!=ident:raise ValueError('记录编号与目录不一致：'+ident)
            value['studio'] = read_json(self._directory(ident)/'studio-metadata.json')
            safe_shots=[]
            shot_root=(self._directory(ident)/'screenshots').resolve()
            for shot in value.get('screenshots',[]):
                name=Path(unquote(urlparse(str(shot.get('image') or '')).path)).name
                image=(shot_root/name).resolve()
                if image.parent==shot_root and image.is_file():
                    safe_shots.append(dict(shot,image=image.as_uri()))
            value['screenshots']=safe_shots
            sessions.append(value)
        return sessions

    def _submit(self, key, work):
        if self._busy or self._closed: return
        self._busy = True
        self._message = '处理中…'
        self.changed.emit()
        def execute():
            try: payload = dict(key=key,result=work())
            except Exception as exc: payload = dict(error=str(exc))
            if not self._closed: self.completed.emit(payload)
        self._job_future=self.pool.submit(execute)

    @Slot(object)
    def _accept(self, payload):
        if self._closed: return
        self._busy = False
        if payload.get('error'):
            self._message = payload['error']
        else:
            self._result = dict(self._result, **{payload['key']:payload['result']})
            self._message = '完成；结果只基于实际留存数据。'
            if payload['key'] == 'export':
                self._message = '已导出：'+str(payload['result'])
        self.changed.emit()


    @Slot(float, float, float)
    def memory(self, enter, exit_time, window):
        ids = list(self._selected[:1])
        if not ids:
            self._message='请选择一条记录。'; self.changed.emit(); return
        self._submit('memory',lambda: memory_analysis(self._sessions(ids)[0],enter,exit_time,window))

    @Slot(str, str, float, float)
    def compare(self, metric, scene, start, end):
        ids = list(self._selected)
        if not ids:
            self._message='请先勾选左侧已保存记录，再选择已有指标并点击“叠加曲线”。';self.changed.emit();return
        if metric not in METRIC_BY_KEY:
            self._message='请先选择要对比的指标。';self.changed.emit();return
        if not all(number(v) is not None for v in (start,end)) or not 0 <= start < end:
            self._message='请选择有效时间范围。';self.changed.emit();return
        self._submit('overlay',lambda: overlay(self._sessions(ids),metric,scene.strip(),start,end))

    @Slot(float)
    def listHitches(self, threshold):
        if not self._selected:
            self._message='请先勾选左侧一条已保存记录，再列出卡顿帧。';self.changed.emit();return
        if number(threshold) is None or not 1 <= threshold < 5000:
            self._message='卡顿阈值请输入 1 到 4999 毫秒。';self.changed.emit();return
        directory = self._directory(self._selected[0])
        def work():
            rows=slow_frames(directory,threshold)
            reason=('该记录没有真实逐帧数据。旧记录不能补帧；Windows FPS 缺失时，本页也无法生成卡顿详情。'
                    if not (directory/'frames.sqlite3').is_file() else
                    '未找到达到当前阈值的已留存帧；可调低阈值重试，不代表从未发生过卡顿。')
            return dict(rows=rows,reason='' if rows else reason,
                retention=read_json(directory/'frame-retention.json'),
                note='仅真实逐帧记录；列表最多显示最慢200帧。没有帧源时不会生成假详情。')
        self._submit('hitches',work)

    @Slot(float, float)
    def frameDetail(self, at, radius):
        if not self._selected or number(at) is None or number(radius) is None or not 0 < radius <= 30: return
        ids = list(self._selected[:1])
        a,b=max(0.,at-radius),at+radius
        def work():
            session=self._sessions(ids)[0]
            data=frame_window(self._directory(ids[0]),a,b,center=at)
            data.update(start=a,end=b,
                cpu=points(session,'app_cpu',a,b),gpu=points(session,'app_gpu_usage',a,b),
                screenshots=[s for s in session.get('screenshots',[]) if a <= float(s.get('seconds',-1)) <= b],
                note='CPU/GPU曲线是附近采样值，不是逐帧线程栈；时钟对齐方式见每帧clock字段。')
            return data
        self._submit('detail',work)

    @Slot(float, float, str)
    def addNote(self, start, end, text):
        if self._busy:return
        if not self._selected or not str(text).strip():
            self._message='请先勾选记录并填写备注内容，再保存备注。';self.changed.emit();return
        if number(start) is None or number(end) is None or not 0 <= start < end:
            self._message='备注区间请输入有效起止秒数，结束需大于开始。';self.changed.emit();return
        try:
            path=self._directory(self._selected[0])/'studio-metadata.json'
            value=read_json(path)
            notes=value.get('notes',[]) if isinstance(value.get('notes',[]),list) else []
            if len(notes)>=200: raise ValueError('单条记录最多200条备注')
            notes.append(dict(start=start,end=end,text=str(text).strip()[:2000]))
            value['notes']=notes; write_json(path,value)
            self._result=dict(self._result,notes=notes)
            self._message='区间备注已保存；导出时会携带。'
        except Exception as exc: self._message=str(exc)
        self.changed.emit()

    @Slot(float, float, str, str, str)
    def exportRange(self,start,end,metric,scene,log_path):
        if metric not in METRIC_BY_KEY: return
        ids=list(self._selected)
        if not ids:
            self._message='请先选择已保存记录。';self.changed.emit();return
        def work():
            sessions=self._sessions(ids)
            notes=[dict(record=s['id'],notes=s['studio'].get('notes',[])) for s in sessions]
            return str(export_bundle(self.root,sessions,start=start,end=end,metric=metric,
                scene=scene.strip(),notes=notes,log_path=log_path))
        self._submit('export',work)

    @Slot(int,result=bool)
    def focusEvidence(self,index):
        if not 0 <= index < len(self.events): return False
        row=self.events[index]
        if not self.owner.collecting and self._selected:
            # Opening uses the existing asynchronous guarded workspace loader.
            if self.owner._session_id != self._selected[0]:
                self._message='请先点击左侧“在主界面打开”，再定位曲线。';self.changed.emit();return False
        self.owner.focusChartInterval(float(row['start']),float(row['end']))
        return True

    @Slot()
    def openPrimary(self):
        if self.owner.collecting or self.owner.busy or self._task_running:return
        if not self._selected:
            self._message='请先勾选左侧一条记录，再点击“在主界面打开”。';self.changed.emit();return
        self.owner.openSession(self._selected[0])

    @Slot(int, str)
    def exportEvidence(self,index,log_path):
        if not 0 <= index < len(self.events): return
        if self.owner.collecting:
            self._message='请停止并保存后导出，确保包含后续截图和完整上下文。';self.changed.emit();return
        event=dict(self.events[index]); ids=list(self._selected[:1])
        if not ids: return
        def work():
            session=self._sessions(ids)[0]
            return str(export_bundle(self.root,[session],start=event['start'],
                end=min(float(session.get('duration',event['end'])),event['end']),event=event,log_path=log_path))
        self._submit('export',work)

    @Slot('QVariantMap')
    def setEvidenceOptions(self,value):
        try:
            clean=dict(self._options)
            limits=dict(cooldown=(5,3600),max_events=(1,200),context=(1,30),hold=(0,60),
                        fps_below=(1,1000),frame_above=(1,5000),cpu_above=(1,100),memory_jump=(1,100000))
            for key,(lo,hi) in limits.items():
                candidate=number(value.get(key,clean[key]))
                if candidate is None or not lo <= candidate <= hi: raise ValueError('无效取证设置：'+key)
                clean[key]=int(candidate) if key=='max_events' else candidate
            clean['enabled']=bool(value.get('enabled',True))
            clean['interval']=self.owner.sampleIntervalSeconds
            write_json(self.root/'data/studio/evidence-options.json',clean)
            self._options=clean
            spool=self.owner._session_spool
            if spool is not None: spool.extras.monitor.options=dict(clean)
            self._message='取证设置已保存；不额外请求设备截图，复用附近已有截图。'
        except Exception as exc: self._message=str(exc)
        self.changed.emit()

    def observe_spool(self,spool):
        if spool is not None and hasattr(spool,'extras'):
            events=spool.extras.monitor.events
            if len(events)!=len(self._live_events):
                self._live_events=[dict(row) for row in events]
                self.changed.emit()

    @Slot('QVariantMap')
    def savePlan(self,value):
        try:
            plan=validate_plan(dict(value))
            self._plans=[p for p in self._plans if p.get('name')!=plan['name']]
            if len(self._plans)>=50: raise ValueError('最多保存50个方案')
            self._plans.append(plan)
            write_json(self.root/'data/studio/plans.json',self._plans)
            self._message='采集方案已保存；不会自动执行。'
        except Exception as exc: self._message=str(exc)
        self.changed.emit()

    @Slot()
    def exportResults(self):
        if self._busy:return
        try:
            path=self.root/'exports/studio'/('analysis-'+uuid.uuid4().hex[:12]+'.json')
            write_json(path,dict(record_ids=list(self._selected),results=self._result))
            self._message='分析结果已导出：'+str(path)
        except Exception as exc:self._message=str(exc)
        self.changed.emit()

    @Slot('QVariantMap')
    def startPlan(self,value):
        owner=self.owner
        if self._task_running or self._busy or owner.collecting or owner.busy or owner.collectionStarting:
            self._message='请先停止手动采集并等待当前操作完成。';self.changed.emit();return
        try:
            if owner._selected_device<0 or owner._selected_target<0: raise ValueError('请先在主界面选择设备及应用')
            plan=validate_plan(dict(value))
            device=owner._device_models[owner._selected_device]
            target=owner._target_models[owner._selected_target]
            if owner._performance_window and owner._verified_window(owner._performance_window) is None:
                raise ValueError('绑定的采集窗口已失效，请重新选择')
            screenshot_window=dict(owner._screenshot_window) if owner._screenshot_window else None
        except Exception as exc:
            self._message=str(exc);self.changed.emit();return
        self.cancel.clear(); self._task_running=True
        owner._busy=True;owner.stateChanged.emit()
        self._task=dict(state='starting',round=0,total=plan['rounds'],results=[])
        owner._status_text='任务正在启动：'+plan['name'];owner.stateChanged.emit()
        self.changed.emit()
        def work():
            try:
                rows=run_plan(self.root,device,target,plan,self.cancel,self.taskProgress.emit,
                              screenshot_window=screenshot_window)
                data=dict(state='cancelled' if self.cancel.is_set() else 'complete',results=rows,finished=True)
            except Exception as exc: data=dict(state='failed',error=str(exc),finished=True)
            if not self._closed: self.taskProgress.emit(data)
        self.pool.submit(work)

    @Slot(object)
    def _task_progress(self,value):
        if self._closed:return
        self._task=dict(self._task,**value)
        self.owner._status_text=('任务 '+str(self._task.get('state',''))+' · 第'+str(self._task.get('round',0))+
            '/'+str(self._task.get('total',0))+'轮'+(' · '+str(self._task['error']) if self._task.get('error') else ''))
        self.owner.stateChanged.emit()
        if value.get('finished'):
            self._task_running=False; self.owner._busy=False
            self.owner.refreshSessionRecords();self.owner.stateChanged.emit();self.refresh()
        self.changed.emit()

    @Slot()
    def cancelPlan(self):
        self.cancel.set()
        self._message='正在停止；会保存当前轮，且不再开始下一轮。'
        self.changed.emit()

    @Slot()
    def openExportFolder(self):
        try:
            path=self.root/'exports/studio';path.mkdir(parents=True,exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):raise OSError('无法自动打开导出目录：'+str(path))
        except OSError as exc:self._message=str(exc);self.changed.emit()

    def close(self):
        self._closed=True;self.cancel.set();self.pool.shutdown(wait=False,cancel_futures=True)

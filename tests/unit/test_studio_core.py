import json
from pathlib import Path
from types import SimpleNamespace
import threading
import zipfile

from PIL import Image
import pytest

from perfzy.studio.storage import EvidenceMonitor,RecordExtras,frame_window,slow_frames,read_json,write_json
from perfzy.studio.analytics import memory_analysis,overlay
from perfzy.studio.exports import export_bundle,comparison_html
from perfzy.studio.plans import validate_plan,run_plan
from perfzy.storage.sessions import LiveSessionWriter


def session(version='v1',fps=60,ident='one',device='windows:one'):
    return dict(id=ident,name='合成记录 '+ident,duration=60,
        device=dict(platform='Windows 本机',id=device),target=dict(name='MockGame',id='windows:123'),
        studio=dict(version=version),scenes=[dict(name='玩法',start=10,end=30)],
        samples=dict(fps=[dict(x=i,y=fps) for i in range(61)],
                     memory=[dict(x=i,y=100+max(0,min(i-10,20))*2) for i in range(61)]))


def test_evidence_requires_sustained_samples_and_respects_cooldown_cap():
    monitor=EvidenceMonitor(dict(hold=2,cooldown=5,max_events=2))
    for i in range(20):monitor.observe(i,dict(app_cpu=95))
    assert [r['time'] for r in monitor.events]==[2,7]
    assert monitor.events[0]['start']==0
    assert monitor.events[0]['reasons'][0]['reason']=='持续高 CPU'


def test_missing_samples_and_time_gaps_break_evidence_hold():
    monitor=EvidenceMonitor(dict(hold=2))
    for t,v in [(0,95),(1,None),(2,95),(3,95),(10,95),(11,95)]:
        monitor.observe(t,dict(app_cpu=v))
    assert monitor.events==[]
    monitor.observe(12,dict(app_cpu=95))
    assert len(monitor.events)==1


def test_memory_jump_and_disabled_monitor():
    monitor=EvidenceMonitor()
    monitor.observe(0,dict(memory=100));monitor.observe(1,dict(memory=250))
    assert monitor.events[0]['reasons'][0]['delta']==150
    disabled=EvidenceMonitor(dict(enabled=False,hold=0))
    disabled.observe(0,dict(fps=1))
    assert disabled.events==[]


def test_frame_sidecar_uses_true_events_filters_invalid_and_queries_range(tmp_path):
    extra=RecordExtras(tmp_path,tmp_path/'record')
    extra.append(5,{},[dict(x=1,frame_ms=16,source='synthetic'),dict(x=2,frame_ms=80),
                       dict(x=3,frame_ms=float('nan')),dict(x=-1,frame_ms=40)])
    extra.close()
    assert frame_window(tmp_path/'record',0,3)['total']==2
    assert len(frame_window(tmp_path/'record',0,3,limit=1)['rows'])==1
    assert frame_window(tmp_path/'record',0,3,limit=1)['truncated']
    assert slow_frames(tmp_path/'record')[0]['frame_ms']==80
    assert not frame_window(tmp_path/'missing',0,3)['rows']


def test_frame_retention_limits_are_reported_not_fabricated(tmp_path,monkeypatch):
    monkeypatch.setattr('perfzy.studio.storage.MAX_FRAMES',1)
    extra=RecordExtras(tmp_path,tmp_path/'record')
    extra.append(1,{},[dict(x=0,frame_ms=16),dict(x=1,frame_ms=50)])
    extra.close()
    value=read_json(tmp_path/'record/frame-retention.json')
    assert value['count']==1 and value['dropped_by_limit']==1


def test_unknown_display_drop_status_is_preserved_as_null(tmp_path):
    extra=RecordExtras(tmp_path,tmp_path/'record')
    extra.append(1,{},[dict(x=1,frame_ms=16.67,dropped=None),dict(x=2,frame_ms=20,dropped=False)])
    extra.close()
    rows=frame_window(tmp_path/'record',0,3)['rows']
    assert rows[0]['dropped'] is None
    assert rows[1]['dropped'] is False


def test_json_corruption_and_wrong_shape_are_not_trusted(tmp_path):
    path=tmp_path/'a.json';path.write_text('[]')
    assert read_json(path)=={}
    path.write_text('null');assert read_json(path,[])==[]
    path.write_text('{');assert read_json(path)=={}


def test_live_writer_persists_frames_and_evidence_through_close(tmp_path):
    writer=LiveSessionWriter(tmp_path,'live')
    for i in range(5):writer.append(i,dict(app_cpu=95),frame_events=[dict(x=i,frame_ms=55)])
    writer.close();writer.close()
    directory=tmp_path/'data/sessions/live'
    assert len(read_json(directory/'evidence.json',[]))==1
    assert frame_window(directory,0,10)['total']==5


def test_memory_report_phase_means_growth_and_no_false_leak_verdict():
    value=memory_analysis(session(),10,30,10)
    row=value['rows'][0]
    assert row['before']==100
    assert row['peak']==138
    assert row['growth_mb_min']==pytest.approx(120)
    assert row['residual_mb']==40
    assert '不等同内存泄漏' in row['verdict']
    assert 'java_heap' not in [r['metric'] for r in value['rows']]


@pytest.mark.parametrize('a,b,w',[(5,4,10),(-1,2,10),(0,10,0),(0,float('nan'),10)])
def test_memory_rejects_invalid_ranges(a,b,w):
    with pytest.raises(ValueError):memory_analysis(session(),a,b,w)


def test_memory_insufficient_points_not_judged():
    result=memory_analysis(session(),.1,1,1)
    assert result['rows'][0]['verdict']=='数据不足，无法判断'








def test_overlay_aligns_scene_start_without_time_stretch_and_excludes_other_device():
    values=[session('v1'),session('v2',40,'two'),session('v2',40,'other',device='windows:other')]
    result=overlay(values,'fps','玩法',0,5)
    assert len(result['curves'])==2
    assert result['curves'][0]['points'][0]==dict(x=0,y=60,segment=0)
    assert result['curves'][0]['points'][-1]['x']==5
    assert '跳过不同' in result['notes'][0]


def test_overlay_downsampling_retains_narrow_spike():
    value=session()
    value['duration']=10000
    value['samples']['fps']=[dict(x=i,y=1 if i==3456 else 60) for i in range(10000)]
    curve=overlay([value],'fps')['curves'][0]
    assert curve['reduced'] and len(curve['points'])<4000
    assert min(p['y'] for p in curve['points'])==1


def test_downsampled_overlay_preserves_true_segments_without_false_gaps():
    value=session();value['duration']=10000
    value['samples']['fps']=[dict(x=i,y=60) for i in range(10000) if not 4000<=i<5000]
    curve=overlay([value],'fps')['curves'][0]
    assert {p['segment'] for p in curve['points']}=={0,1}
    html=comparison_html(dict(curves=[curve],metric='fps'))
    # One move for each true segment, not a move at every reduction bucket.
    import re
    commands=re.search(r'id="curve0" d="([^"]+)"',html).group(1)
    assert commands.count('M')==2


def test_unknown_identity_is_not_comparable():
    value=session();value['device']={}
    assert not overlay([value],'fps')['curves']


def test_frame_detail_limit_keeps_selected_hitch(tmp_path):
    extra=RecordExtras(tmp_path,tmp_path/'record')
    extra.append(6,{},[dict(x=i*.001,frame_ms=80 if i==5000 else 16) for i in range(6000)])
    extra.close()
    result=frame_window(tmp_path/'record',0,6,limit=100,center=5)
    assert result['truncated'] and any(row['frame_ms']==80 for row in result['rows'])


def test_frame_byte_budget_is_bounded_and_reported(tmp_path,monkeypatch):
    monkeypatch.setattr('perfzy.studio.storage.MAX_FRAME_BYTES',1)
    extra=RecordExtras(tmp_path,tmp_path/'record')
    extra.append(1,{},[dict(x=0,frame_ms=16)]);extra.close()
    assert read_json(tmp_path/'record/frame-retention.json')['dropped_by_limit']==1
    assert not (tmp_path/'record/frames.sqlite3').exists()


def test_writer_respects_nondefault_sample_interval(tmp_path):
    writer=LiveSessionWriter(tmp_path,'interval',sample_interval=.5)
    assert writer.extras.monitor.options['interval']==.5
    writer.close()


def test_single_point_comparison_has_visible_marker_and_explicit_unit():
    html=comparison_html(dict(metric='fps',curves=[dict(id='one',name='one',points=[dict(x=5,y=60)],original_count=1)]))
    assert '<circle id="curve0"' in html and '单位：FPS' in html


def test_small_value_range_is_not_mislabeled_in_svg():
    html=comparison_html(dict(metric='app_cpu',curves=[dict(id='one',name='one',points=[dict(x=0,y=.1),dict(x=1,y=.2)],original_count=2)]))
    assert '70.000,360.000' in html and '950.000,60.000' in html


def test_not_displayed_frame_is_flagged_in_raw_detail():
    from test_windows_collector import make_collector,frame_row
    collector=make_collector([dict(frame_row(20),DisplayedTime='NA')],enabled={'fps'})
    assert collector.sample(1).frames[0]['dropped'] is True


def test_discard_incomplete_removes_new_frame_and_evidence_sidecars(tmp_path):
    from perfzy.storage.sessions import discard_incomplete_session
    directory=tmp_path/'data/sessions/discarded';directory.mkdir(parents=True)
    for name in ('frames.sqlite3','evidence.json','frame-retention.json'):
        (directory/name).write_bytes(b'test')
    discard_incomplete_session(tmp_path,'discarded')
    assert not (directory/'frames.sqlite3').exists()
    assert not (directory/'evidence.json').exists()
    assert (directory/'.discarded').exists()


def test_evidence_zip_is_local_hash_manifest_and_script_safe(tmp_path):
    value=session(ident='record');directory=tmp_path/'data/sessions/record'
    shots=directory/'screenshots';shots.mkdir(parents=True)
    Image.new('RGB',(5,5),'red').save(shots/'one.jpg')
    value['screenshots']=[dict(seconds=2,image=(shots/'one.jpg').as_uri())]
    log=tmp_path/'chosen.log';log.write_text('token=SECRET password=hidden hello')
    output=export_bundle(tmp_path,[value],start=0,end=5,notes=['</script><img src=x>'],log_path=str(log))
    with zipfile.ZipFile(output) as bundle:
        names=bundle.namelist()
        assert 'comparison.html' in names and 'record-1/screenshots/1.jpg' in names
        manifest=json.loads(bundle.read('manifest.json'))
        assert manifest['files'] and manifest['warnings']
        assert 'SECRET' not in bundle.read('logs/selected-log-tail.txt').decode()
        html=bundle.read('comparison.html').decode()
        assert '&lt;/script&gt;' in html
        assert '</script><img src=x>' not in html


def test_export_rejects_invalid_range_and_optional_nonlog(tmp_path):
    with pytest.raises(ValueError):export_bundle(tmp_path,[session()],start=5,end=2)
    with pytest.raises(ValueError):export_bundle(tmp_path,[session()],start=0,end=2,log_path=str(tmp_path/'secret.exe'))
    assert not list((tmp_path/'exports/studio').glob('*.tmp'))


def test_frame_screenshots_cannot_read_external_path(tmp_path):
    outside=tmp_path/'private.jpg';Image.new('RGB',(5,5)).save(outside)
    value=session(ident='record');value['screenshots']=[dict(seconds=1,image=outside.as_uri())]
    output=export_bundle(tmp_path,[value],start=0,end=5)
    with zipfile.ZipFile(output) as bundle:
        assert not any('/screenshots/' in name for name in bundle.namelist())


@pytest.mark.parametrize('value',[dict(duration=0),dict(rounds=1.5),dict(interval=float('nan')),dict(rest=-1),dict(rounds=101)])
def test_invalid_plans_are_rejected(value):
    with pytest.raises(ValueError):validate_plan(dict(metrics=['fps'],**value))


def test_runner_cancel_saves_current_round_and_never_starts_next(tmp_path):
    cancel=threading.Event();calls=[]
    class API:
        def __init__(self,root): self.elapsed=0
        def __enter__(self):return self
        def __exit__(self,*args):calls.append('closed')
        def start(self,**kwargs):calls.append('start');cancel.set()
        def stop(self,name):
            calls.append('stop');path=tmp_path/'data/sessions/r/session.json';path.parent.mkdir(parents=True)
            return SimpleNamespace(session_id='r',session_path=path)
        def generate_html(self,result):return tmp_path/'report.html'
    result=run_plan(tmp_path,'device','target',dict(metrics=['fps'],rounds=3,duration=2),cancel,lambda p:None,api_factory=API)
    assert calls==['start','stop','closed'] and len(result)==1
    assert read_json(tmp_path/'data/sessions/r/studio-metadata.json')['cancelled']


def test_windows_source_frame_clock_is_ms_and_unrelated_pid_is_excluded():
    from test_windows_collector import make_collector,frame_row
    import time
    collector=make_collector([dict(frame_row(20),CPUStartTime='1000'),frame_row(50,pid=99999)],enabled={'fps'})
    collector._presentmon_started_at=time.monotonic()-1
    sample=collector.sample(5)
    assert len(sample.frames)==1 and sample.frames[0]['source_time_s']==1
    assert sample.frames[0]['x']==pytest.approx(5,abs=.1)
    assert sample.frames[0]['frame_ms']==20

import json
import sqlite3

import pytest

from perfzy.domain.record_timeline import TimelineEdit
from perfzy.domain.screenshot_timeline import match_screenshot
from perfzy.storage.editing import compact_removed_ranges,remove_time_range
from perfzy.storage.sessions import load_session
from test_saved_record_editing import recording,hashes


@pytest.mark.parametrize('ranges,duration,points',[
    ([(20,30)],50,[(0,0),(19,19),(20,20),(25,20),(30,20),(60,50)]),
    ([(0,10)],50,[(0,0),(10,0),(20,10),(60,50)]),
    ([(50,60)],50,[(0,0),(49,49),(50,50),(60,50)]),
    ([(20,30),(25,35),(40,50)],35,[(20,20),(35,20),(40,25),(50,25),(60,35)]),
])
def test_one_mapping_for_middle_head_tail_and_overlapping_ranges(ranges,duration,points):
    edit=TimelineEdit(60,[dict(start=a,end=b) for a,b in ranges])
    assert edit.edited_duration==duration
    for original,expected in points: assert edit.map_time(original)==expected


def test_scene_ranges_are_clipped_removed_and_retimed_together():
    edit=TimelineEdit(60,[dict(start=20,end=30)])
    scenes=[dict(id=i,name=str(i),start=a,end=b,color='#4269d4')
            for i,(a,b) in enumerate([(20,25),(10,22),(25,40),(10,40),(40,50)])]
    result=edit.scenes(scenes)
    assert [(s['id'],s['start'],s['end']) for s in result]==[(1,10,20),(2,20,30),(3,10,30),(4,30,40)]
    assert all(s['color']=='#4269d4' for s in result)


def test_screenshot_toggle_inside_deleted_segment_restores_correct_post_cut_state():
    edit=TimelineEdit(60,[dict(start=10,end=30)])
    initial,changes=edit.capture_settings({'screenshots':False,'screenshot_interval_ms':1000},[
        dict(elapsed=5,settings={'screenshots':True}),dict(elapsed=20,settings={'screenshots':False}),
        dict(elapsed=25,settings={'screenshots':True}),dict(elapsed=35,settings={'screenshots':False})])
    assert changes[1]['elapsed']==10 and changes[1]['settings']['screenshots'] is True
    assert changes[-1]['elapsed']==15 and changes[-1]['settings']['screenshots'] is False
    shots=[dict(seconds=8),dict(seconds=12)]
    assert match_screenshot(shots,12,initial,changes).index==1
    assert match_screenshot(shots,16,initial,changes).reason=='disabled'
    leading=TimelineEdit(60,[dict(start=0,end=30)])
    baseline,_=leading.capture_settings({'screenshots':True},[dict(elapsed=20,settings={'screenshots':False})])
    assert baseline['screenshots'] is False


@pytest.mark.parametrize('first,last,duration,points,shots',[
    (0,3,7,list(range(8)),[1,5]),
    (7,10,7,list(range(7)),[1,4]),
])
def test_head_and_tail_storage_have_consistent_samples_frames_shots_and_duration(tmp_path,recording,first,last,duration,points,shots):
    before=hashes(recording.parent)
    path=remove_time_range(tmp_path,'source',first,last)
    result=load_session(tmp_path,path.parent.name)
    assert hashes(recording.parent)==before
    assert result['duration']==duration and [r['x'] for r in result['samples']['fps']]==points
    assert [r['seconds'] for r in result['screenshots']]==shots
    with sqlite3.connect(path.parent/'frames.sqlite3') as db:
        assert [row[0] for row in db.execute('SELECT x FROM frames ORDER BY x')]==points
    assert result['timeline_cuts']==[]  # No interior splice at a removed head/tail.


def test_legacy_gap_copy_can_be_compacted_without_modifying_it(tmp_path,recording):
    payload=json.loads(recording.read_text(encoding='utf-8'))
    payload['excluded_intervals']=[dict(start=3,end=6)]
    payload['samples']['fps']=[r for r in payload['samples']['fps'] if not 3<=r['x']<6]
    payload['screenshots']=[r for r in payload['screenshots'] if not 3<=r['seconds']<6]
    payload['edited_from']=dict(id='original',removed_start=3,removed_end=6,timeline='original_with_gaps')
    recording.write_text(json.dumps(payload),encoding='utf-8')
    before=hashes(recording.parent)
    path=compact_removed_ranges(tmp_path,'source')
    result=load_session(tmp_path,path.parent.name)
    assert hashes(recording.parent)==before
    assert result['duration']==7 and result['excluded_intervals']==[]
    assert [r['x'] for r in result['samples']['fps']]==list(range(8))
    assert result['timeline_cuts'][0]['time']==3
    assert result['edit_history'][0]['timeline']=='original_with_gaps'


def test_unavailable_observation_times_remain_unavailable_after_edit(tmp_path,recording):
    path=recording.parent/'audit.jsonl'
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    rows[8]['observed_elapsed']['fps']=None
    path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    edited=remove_time_range(tmp_path,'source',3,6)
    result=[json.loads(line) for line in (edited.parent/'audit.jsonl').read_text().splitlines()]
    row=next(row for row in result if row['elapsed']==5)
    assert row['observed_elapsed']['fps'] is None


def test_compaction_requires_explicit_removed_ranges(tmp_path,recording):
    before=hashes(recording.parent)
    with pytest.raises(ValueError,match='没有需要移除'):
        compact_removed_ranges(tmp_path,'source')
    assert hashes(recording.parent)==before

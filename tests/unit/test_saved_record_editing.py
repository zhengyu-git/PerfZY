"""All edits below operate on temporary recordings, never user recordings."""
import hashlib
import json
import sqlite3

from PIL import Image
import pytest

from perfzy.storage.editing import remove_time_range, save_scene_annotations
from perfzy.storage.sessions import load_session, save_session, rename_session
from perfzy.reporting import generate_local_report


@pytest.fixture
def recording(tmp_path):
    path = save_session(tmp_path, {"id":"android:test","name":"Xiaomi 12"},
                        {"id":"com.game","name":"Game"},
                        {"fps":[{"x":i,"y":60.0} for i in range(11)]}, [], {"fps"},
                        "source", [], "2026-11-06T12:00:00+08:00", 10, "测试记录",
                        capture_settings={"screenshots":True,"screenshot_interval_ms":1000})
    payload = json.loads(path.read_text(encoding="utf-8"))
    shots = path.parent / "screenshots"; shots.mkdir()
    for i in (1,4,8):
        image = shots / f"shot{i}.png"
        Image.new("RGB", (8,8), "blue").save(image)
        payload["screenshots"].append(dict(seconds=i,image=image.as_uri(),time=f"00:0{i}"))
    payload["capture_settings_changes"] = [dict(elapsed=9,settings={"screenshots":False})]
    payload["notes"] = ["source note"]
    path.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
    with sqlite3.connect(path.parent / "frames.sqlite3") as db:
        db.execute("CREATE TABLE frames(seq INTEGER PRIMARY KEY,x REAL,ms REAL,payload TEXT)")
        for i in range(11):
            db.execute("INSERT INTO frames(x,ms,payload) VALUES(?,?,?)", (i,16.67,json.dumps(dict(x=i,source="PresentMon",pid=12,stream_epoch=3,frame_ms=16.67))))
    db.close()  # A completed recording must not keep a Windows SQLite handle open.
    with (path.parent / "audit.jsonl").open("w",encoding="utf-8") as stream:
        for i in range(11):
            stream.write(json.dumps(dict(elapsed=i,values={"fps":60},requested=["fps"],interval_seconds=1,observed_elapsed={"fps":i}))+"\n")
    (path.parent / "evidence.json").write_text(json.dumps([dict(start=1,end=2),dict(start=4,end=7),dict(elapsed=8)]),encoding="utf-8")
    return path


def hashes(directory):
    return {p.relative_to(directory).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


def test_scene_edits_survive_reopen_rename_and_regenerated_report(tmp_path,recording):
    before = recording.read_bytes()
    scenes = [dict(id=1,name="进入场景",start=2,end=5,color="#4269d4",title="进入场景")]
    save_scene_annotations(tmp_path,"source",scenes,10)
    rename_session(tmp_path,"source","新名称")
    assert load_session(tmp_path,"source")["scenes"] == scenes
    assert load_session(tmp_path,"source")["name"] == "新名称"
    html = generate_local_report(recording,tmp_path/"exports",screenshot_mode="none").read_text(encoding="utf-8")
    assert "进入场景" in html
    assert recording.read_bytes() == before
    save_scene_annotations(tmp_path,"source",[],10)
    assert load_session(tmp_path,"source")["scenes"] == []


@pytest.mark.parametrize("start,end", [(2,2),(-1,2),(1,11),(float('nan'),3)])
def test_invalid_scene_does_not_overwrite_annotation(tmp_path,recording,start,end):
    scene = dict(id=1,name="有效",start=1,end=3,color="#4269d4")
    save_scene_annotations(tmp_path,"source",[scene],10)
    original = (recording.parent/"scenes.json").read_bytes()
    with pytest.raises(ValueError):
        save_scene_annotations(tmp_path,"source",[dict(scene,start=start,end=end)],10)
    assert (recording.parent/"scenes.json").read_bytes() == original


def test_range_removal_copies_all_evidence_and_preserves_original(tmp_path,recording):
    save_scene_annotations(tmp_path,"source",[dict(id=1,name="原场景",start=1,end=8,color="#4269d4")],10)
    before = hashes(recording.parent)
    path = remove_time_range(tmp_path,"source",3,6)
    edited = load_session(tmp_path,path.parent.name)
    assert path.parent != recording.parent
    assert hashes(recording.parent) == before
    assert edited["duration"] == 7 and edited["started_at"].startswith("2026-11-06")
    assert [row["x"] for row in edited["samples"]["fps"]] == [0,1,2,3,4,5,6,7]
    assert [row["seconds"] for row in edited["screenshots"]] == [1,5]
    assert len(list((path.parent/"screenshots").iterdir())) == 2
    assert edited["scenes"][0]["name"] == "原场景"
    assert edited["excluded_intervals"] == []
    assert edited["timeline_cuts"][0]["time"] == 3
    assert edited["scenes"][0]["end"] == 5
    assert edited["capture_settings_changes"][-1]["elapsed"] == 6
    assert edited["quality"]["samples"] == 8
    assert edited["quality"]["metrics"]["fps"]["coverage_estimate_seconds"] <= 7
    with sqlite3.connect(path.parent/"frames.sqlite3") as db:
        frames = db.execute("SELECT x,payload FROM frames ORDER BY x").fetchall()
    assert [x for x,_ in frames] == [0,1,2,3,4,5,6,7]
    assert all(json.loads(encoded)["x"] == x for x,encoded in frames)
    assert json.loads(frames[2][1])["stream_epoch"] != json.loads(frames[3][1])["stream_epoch"]
    assert json.loads(frames[3][1])["source"] == "PresentMon"
    assert json.loads((path.parent/"evidence.json").read_text()) == [dict(start=1,end=2),dict(elapsed=5)]
    html = generate_local_report(path,tmp_path/"exports",screenshot_mode="original").read_text(encoding="utf-8")
    assert '"timeline_cuts":[{"time":3.0,' in html
    assert 'data:image/png;base64,' in html


def test_multiple_edits_compact_coordinates_and_keep_cut_boundaries(tmp_path,recording):
    first = remove_time_range(tmp_path,"source",3,6)
    second = remove_time_range(tmp_path,first.parent.name,4,6)
    payload = load_session(tmp_path,second.parent.name)
    assert payload["duration"] == 5 and payload["excluded_intervals"] == []
    assert [row["time"] for row in payload["timeline_cuts"]] == [3,4]
    assert [row["x"] for row in payload["samples"]["fps"]] == [0,1,2,3,4,5]
    assert len(payload["edit_history"]) == 2


def test_failed_copy_publishes_nothing_and_preserves_source(tmp_path,recording,monkeypatch):
    from perfzy.storage import record_cuts as editing
    before = hashes(recording.parent)
    monkeypatch.setattr(editing.shutil,"copy2",lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError,match="disk full"):
        remove_time_range(tmp_path,"source",3,6)
    assert hashes(recording.parent) == before
    assert [p.name for p in recording.parent.parent.iterdir()] == ["source"]


def test_entire_range_and_traversal_are_rejected(tmp_path,recording):
    with pytest.raises(ValueError): remove_time_range(tmp_path,"source",0,10)
    with pytest.raises(FileNotFoundError): remove_time_range(tmp_path,"../source",1,2)
    with pytest.raises(ValueError): save_scene_annotations(tmp_path,"../source",[],10)

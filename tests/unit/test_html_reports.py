import json
import os
from pathlib import Path

import pytest

from perfzy.storage import reports


def report(root,name='测试报告.html',**fields):
    path=root/'exports'/name
    path.parent.mkdir(parents=True,exist_ok=True)
    payload={'id':'source-one','name':'Internal report title','target':{'name':'ExampleGame'},
             'device':{'name':'Test PC','model':'Board'},**fields,'samples':[{'x':1}],'screenshots':[]}
    path.write_text('<html><script id="reportData" type="application/json">'+json.dumps(payload,ensure_ascii=False)+'</script></html>',encoding='utf-8')
    return path


def test_report_inventory_metadata_source_link_and_current_filename(tmp_path):
    path=report(tmp_path)
    rows,cache=reports.load_html_reports(tmp_path,sessions=[{'id':'source-one'}])
    assert len(rows)==1 and rows[0]['title']=='测试报告'
    assert rows[0]['application']=='ExampleGame' and rows[0]['device']=='Test PC'
    assert rows[0]['sourceAvailable'] and rows[0]['sourceSessionId']=='source-one'
    assert rows[0]['sizeBytes']==path.stat().st_size
    assert rows[0]['kind']=='capture' and cache[path.name]['metadata']['id']=='source-one'


def test_metadata_read_is_bounded_before_heavy_embedded_data(tmp_path,monkeypatch):
    path=report(tmp_path)
    prefix=path.read_text(encoding='utf-8').split('"samples"')[0]
    path.write_text(prefix+'"samples":['+'0,'*(reports.HEADER_BYTES)+']}</script>',encoding='utf-8')
    sizes=[]
    original=Path.open
    class Reader:
        def __enter__(self):self.file=original(path,'rb');return self
        def __exit__(self,*args):self.file.close()
        def read(self,size):sizes.append(size);return self.file.read(size)
    monkeypatch.setattr(Path,'open',lambda self,*a,**k: Reader() if self==path and a==('rb',) else original(self,*a,**k))
    rows,_=reports.load_html_reports(tmp_path)
    assert rows[0]['application']=='ExampleGame' and sizes==[reports.HEADER_BYTES]


def test_unchanged_reports_use_cache_and_modified_reports_are_reloaded(tmp_path,monkeypatch):
    path=report(tmp_path)
    _,cache=reports.load_html_reports(tmp_path)
    original=reports._read_metadata
    calls=[]
    monkeypatch.setattr(reports,'_read_metadata',lambda p:calls.append(p.name) or original(p))
    reports.load_html_reports(tmp_path,cache=cache)
    assert not calls
    stamp=path.stat().st_mtime_ns
    report(tmp_path,target={'name':'Updated Game'})
    os.utime(path,ns=(stamp,stamp+10000000))
    rows,_=reports.load_html_reports(tmp_path,cache=cache)
    assert calls==[path.name] and rows[0]['application']=='Updated Game'


def test_rename_and_delete_manage_html_only_preserving_source_record(tmp_path):
    original=report(tmp_path)
    source=tmp_path/'data/sessions/source-one/session.json'
    source.parent.mkdir(parents=True)
    source.write_text('source performance data',encoding='utf-8')
    content=original.read_bytes()
    renamed=reports.rename_html_report(tmp_path,original.name,'重新命名.HTML')
    assert renamed.name=='重新命名.html' and renamed.read_bytes()==content
    reports.delete_html_report(tmp_path,renamed.name)
    assert not renamed.exists() and source.read_text(encoding='utf-8')=='source performance data'
    assert reports.load_html_reports(tmp_path)[0]==[]


@pytest.mark.parametrize('identifier',['../outside.html','..\\outside.html','/tmp/report.html','not-html.txt','C:drive.html','file.html:stream.html',''])
def test_report_operations_reject_paths_outside_report_directory(tmp_path,identifier):
    report(tmp_path)
    with pytest.raises((ValueError,FileNotFoundError)):
        reports.report_path(tmp_path,identifier)


@pytest.mark.parametrize('name',['CON','aux.txt','bad/name','bad:name','.','trailing.',''])
def test_invalid_windows_filenames_cannot_rename_report(tmp_path,name):
    path=report(tmp_path)
    with pytest.raises(ValueError):reports.rename_html_report(tmp_path,path.name,name)
    assert path.exists()


def test_rename_collision_does_not_overwrite_existing_report(tmp_path):
    first=report(tmp_path,'one.html')
    second=report(tmp_path,'two.html',target={'name':'Other'})
    original=second.read_bytes()
    with pytest.raises(ValueError):reports.rename_html_report(tmp_path,first.name,'two')
    assert second.read_bytes()==original and first.exists()


def test_comparison_and_generic_html_can_be_listed_without_report_data(tmp_path):
    folder=tmp_path/'exports';folder.mkdir()
    (folder/'对比.html').write_text('<html><body class="report-comparison">test</body></html>',encoding='utf-8')
    (folder/'一般.htm').write_text('<html><body>test</body></html>',encoding='utf-8')
    (folder/'ignore.csv').write_text('a,b',encoding='utf-8')
    rows,_=reports.load_html_reports(tmp_path)
    assert {row['kind'] for row in rows}=={'comparison','html'} and len(rows)==2

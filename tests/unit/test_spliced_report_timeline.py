"""Execute exported HTML behavior at a splice with tightly adjacent observations."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest


def test_report_split_probe_screenshots_and_thin_marker_agree_at_splice():
    node=shutil.which('node')
    if not node: pytest.skip('Node is needed to execute the HTML helpers')
    template=(Path(__file__).resolve().parents[2]/'assets/report_template_v10.html').read_text(encoding='utf-8')
    def function(name):
        start=template.index('function '+name+'(')
        return template[start:template.index('\nfunction ',start+1)]
    source='''
const R={timeline_cuts:[{time:3}]};
const observations=[{elapsed:1,fps:50},{elapsed:2.99,fps:51},{elapsed:3,fps:61},{elapsed:4,fps:62}];
function metricTimeGapLimit(){return 100}
function metricObservations(){return observations}
function reportChartPalette(){return {axis:'#607080'}}
'''+''.join(function(name) for name in ('crossesEditBoundary','metricSegments','metricObservationAt','screenshotMatchAt','drawEditCuts'))+'''
const report={...R,capture_settings:{screenshots:true,screenshot_interval_ms:10000},screenshots:[{elapsed:2.99},{elapsed:3}]};
const marker=[];const context={save(){},restore(){},setLineDash(){},beginPath(){},stroke(){},
 moveTo(x,y){marker.push([x,y])},lineTo(x,y){marker.push([x,y])}};
drawEditCuts(context,{l:10,t:0,b:100},100,time=>time/5);
console.log(JSON.stringify({segments:metricSegments(observations,'fps').map(s=>s.map(r=>r.elapsed)),
 left:metricObservationAt('fps',2.999),at:metricObservationAt('fps',3),right:metricObservationAt('fps',3.001),
 shotLeft:screenshotMatchAt(report,2.999),shotAt:screenshotMatchAt(report,3),marker}));
'''
    run=subprocess.run([node,'-e',source],capture_output=True,text=True,timeout=5)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    assert result['segments']==[[1,2.99],[3,4]]
    assert result['left']['fps']==51 and result['at']['fps']==61 and result['right']['fps']==61
    assert result['shotLeft']['index']==0 and result['shotAt']['index']==1
    assert result['marker']==[[70,0],[70,100]]

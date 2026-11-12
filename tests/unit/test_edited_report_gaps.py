"""Execute the actual HTML helpers for edited gaps, without a browser or network."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest


def test_edited_report_never_connects_or_uses_evidence_across_removed_gap():
    node=shutil.which('node')
    if not node: pytest.skip('Node is needed to execute the HTML helpers')
    template=(Path(__file__).resolve().parents[2]/'assets/report_template_v10.html').read_text(encoding='utf-8')
    def function(name):
        start=template.index('function '+name+'(')
        return template[start:template.index('\nfunction ',start+1)]
    source='''
const R={excluded_intervals:[{start:3,end:6}]};
const observations=[{elapsed:1,fps:60},{elapsed:2,fps:61},{elapsed:6,fps:62},{elapsed:7,fps:63}];
function metricTimeGapLimit(){return 100}
function metricObservations(){return observations}
'''+function('crossesEditBoundary')+function('metricSegments')+function('metricObservationAt')+function('screenshotMatchAt')+'''
const report={...R,capture_settings:{screenshots:true,screenshot_interval_ms:10000},screenshots:[{elapsed:2},{elapsed:6}]};
const result={segments:metricSegments(observations,'fps').map(s=>s.map(r=>r.elapsed)),
 inGap:metricObservationAt('fps',4),left:metricObservationAt('fps',2.9),right:metricObservationAt('fps',6.1),
 shotGap:screenshotMatchAt(report,4),shotLeft:screenshotMatchAt(report,2.9),shotRight:screenshotMatchAt(report,6.1)};
console.log(JSON.stringify(result));
'''
    run=subprocess.run([node,'-e',source],capture_output=True,text=True,timeout=5)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    assert result['segments']==[[1,2],[6,7]]
    assert result['inGap'] is None
    assert result['left']['elapsed']==2 and result['right']['elapsed']==6
    assert result['shotGap']==dict(index=-1,reason='removed')
    assert result['shotLeft']['index']==0 and result['shotRight']['index']==1

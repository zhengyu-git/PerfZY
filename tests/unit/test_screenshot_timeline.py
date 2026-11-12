import json
from pathlib import Path

import pytest

from perfzy.domain.screenshot_timeline import match_screenshot

CASES=json.loads(Path(__file__).with_name('screenshot_timeline_cases.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('case',CASES,ids=lambda case:case['name'])
def test_screenshot_availability(case):
    match=match_screenshot([{'seconds':x} for x in case['shots']],case['time'],case['settings'],case.get('changes',[]))
    assert match.index==case['expected']
    if 'reason' in case:assert match.reason==case['reason']

def test_invalid_and_unordered_shots_do_not_supply_false_evidence():
    assert match_screenshot([None,{}, {'seconds':'nan'},{'seconds':10}],float('inf')).index==-1
    assert match_screenshot([{'seconds':10},{'seconds':2}],2).index==1

def test_missing_old_history_is_not_assumed_disabled_after_truncation():
    shots=[{'seconds':2}]
    assert match_screenshot(shots,2,{'screenshots':False},truncated=True).index==0

"""Step 5: only genuinely coexisting ground conditions become multi-select."""
from pathlib import Path
import csv, io, sys, types
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if "flask" not in sys.modules:
    flask_stub=types.ModuleType("flask")
    flask_stub.request=types.SimpleNamespace(headers={})
    flask_stub.has_request_context=lambda:False
    sys.modules["flask"]=flask_stub
from cryopit.export import _build_csvs
from cryopit.repository import _site_values,_validate_payload

def payload(condition=None):
    return {"meta":{"pit_id":"GROUND20260806","campaign":"TEST","date":"2026-08-06"},
            "weather":{},"ground":({} if condition is None else {"condition":condition}),
            "temperature":[],"density":[],"lwc":[],"stratigraphy":[],"ssa":[],"instruments":[]}

def test_template_changes_only_ground_condition_to_checkboxes():
    html=(ROOT/'cryopit/templates/sections/03_ground.html').read_text()
    assert html.count('data-ground-multi')==1
    assert 'Select all conditions present at the pit.' in html
    block=html.split('Ground condition',1)[1].split('Ground roughness',1)[0]
    assert block.count('type="checkbox"')==3 and block.count('type="radio"')==0
    assert html.count('data-clearable-radio')==5

def test_legacy_scalar_normalizes_to_array():
    p=payload('Frozen'); assert _validate_payload(p) is None
    assert p['ground']['condition']==['Frozen']

def test_multiple_conditions_persist_and_export():
    p=payload(['Frozen','Moist']); assert _validate_payload(p) is None
    vals=_site_values(p,'alice','{}',1)
    assert vals['ground_condition']=='Frozen; Moist'
    site=next(v for k,v in _build_csvs(p).items() if 'siteDetails' in k)
    rows={r[0]:r[1] for r in csv.reader(io.StringIO(site)) if len(r)>=2}
    assert rows['# Ground Condition']=='Frozen; Moist'

def test_invalid_condition_rejected():
    p=payload(['Frozen','Impossible'])
    assert _validate_payload(p)=='Ground condition has an invalid value.'

TESTS=[v for k,v in list(globals().items()) if k.startswith('test_')]
if __name__=='__main__':
    fails=0
    for t in TESTS:
        try:t();print('PASS',t.__name__)
        except Exception as e:fails+=1;print('FAIL',t.__name__,repr(e))
    if fails:raise SystemExit(f'{fails} ground multi-select tests failed')
    print(f'{len(TESTS)} ground multi-select tests passed')

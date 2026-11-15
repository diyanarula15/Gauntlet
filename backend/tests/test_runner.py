import asyncio,json
import pytest
from backend.app.engine import runner
from backend.app.database import Store
from backend.app.config import Settings

@pytest.mark.parametrize('script,limit,status',[
    ('import sys,json; t=json.load(sys.stdin); print("logging"); print(json.dumps({"answer":t["question"],"status":"completed"}))',3,'completed'),
    ('print("not json")',3,'failed'),
    ('import sys; print("{} "); sys.exit(2)',3,'failed'),
    ('import time; time.sleep(5)',.1,'timed_out'),
])
def test_subprocess_contract_and_cleanup(tmp_path,monkeypatch,script,limit,status):
    path=tmp_path/'agent.py';path.write_text(script)
    monkeypatch.setitem(runner.AGENTS,'test',{'script':str(path)})
    store=Store(tmp_path/'runner.db');caps={}
    run={'id':'r','agent_id':'test','task_id':'t','scenario_id':'baseline','status':'pending','config':{'model':'test'}}
    task={'id':'t','question':'42','query':'q','max_steps':2,'max_seconds':limit,'expected_answer':'42'}
    result=asyncio.run(runner.execute(store,Settings(),run,task,caps))
    assert result['status']==status
    assert not caps
    assert store.events('r')[-1]['event_type']=='RUN_FINISHED'
    assert store.get('run','r')['status']==status

def test_missing_agent_executable_isolated(tmp_path,monkeypatch):
    monkeypatch.setitem(runner.AGENTS,'bad',{'script':str(tmp_path/'missing.py')})
    store=Store(tmp_path/'missing.db')
    run={'id':'r','agent_id':'bad','task_id':'t','scenario_id':'baseline','status':'pending','config':{'model':'test'}}
    task={'id':'t','question':'q','query':'q','max_steps':1,'max_seconds':3,'expected_answer':'a'}
    assert asyncio.run(runner.execute(store,Settings(),run,task,{}))['status']=='failed'

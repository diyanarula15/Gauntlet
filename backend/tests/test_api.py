import asyncio, json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app import main
from backend.app.database import Store
from backend.app.engine.proxy import router
from backend.app.config import Settings
from fastapi import FastAPI

@pytest.fixture
def client(tmp_path,monkeypatch):
    instance=main.create_app(Store(str(tmp_path/'test.db')),main.settings.model_copy(update={'tavily_api_key':''}))
    with TestClient(instance) as c:yield c

def test_api_catalog_validation_export(client):
    assert client.get('/api/health').json()['status']=='ok'
    assert len(client.get('/api/catalog').json()['tasks'])==8
    assert client.post('/api/experiments',json={'repetitions':0}).status_code==422
    assert client.post('/api/experiments',json={'agent_ids':['unknown']}).status_code==422
    assert client.post('/api/experiments',json={'scenario_ids':['search_failure']}).status_code==422
    assert client.post('/api/experiments',json={'agent_ids':['react','react']}).status_code==422
    assert client.post('/api/experiments',json={'evidence_mode':'anything'}).status_code==422
    assert client.post('/api/experiments',json={},headers={'origin':'https://evil.example'}).status_code==403
    assert client.get('/api/runs/unknown').status_code==404
    assert client.post('/api/snapshots/capture',json={}).status_code==422
    assert client.post('/api/experiments',json={'evidence_mode':'live'}).status_code==422

def proxy_client(tmp_path,mode='demo',scenario='baseline'):
    store=Store(str(tmp_path/'proxy.db'));settings=Settings(bifrost_api_key='secret-bifrost',tavily_api_key='secret-tavily')
    task={'id':'t','query':'known query'}
    store.put('run',{'id':'r','status':'running','scenario_id':scenario,'task_id':'t','config':{'evidence_mode':mode,'snapshot_id':'test'}})
    store.snapshot({'snapshot_id':'test','entries':{'known query':{'response':{'results':[{'title':'Evidence','content':'value','url':'https://example.org'}]},'response_hash':'abc'}}})
    app=FastAPI();app.include_router(router(store,settings,{'t':task},{'r':'cap'}))
    return TestClient(app),store,settings

@pytest.mark.parametrize('scenario,service,expected',[('baseline','model',[200,200,200]),('model_outage','model',[503,503,200]),('search_failure','search',[500,500,200])])
def test_exact_faults_and_recording(tmp_path,scenario,service,expected):
    c,store,_=proxy_client(tmp_path,scenario=scenario)
    path='v1/chat/completions' if service=='model' else 'tavily/search'
    codes=[c.post('/proxy/r/'+path,json={'query':'known query'},headers={'Authorization':'Bearer cap'}).status_code for _ in range(3)]
    assert codes==expected
    calls=[e for e in store.events('r') if e['event_type']=='CALL']
    assert len(calls)==3
    assert all(not e['upstream_called'] for e in calls)
    assert [e['call_index'] for e in calls]==[1,2,3]

def test_proxy_rejects_and_snapshot_miss(tmp_path):
    c,store,_=proxy_client(tmp_path)
    h={'Authorization':'Bearer cap'}
    assert c.post('/proxy/missing/tavily/search',json={}).status_code==404
    assert c.post('/proxy/r/tavily/search',json={}).status_code==401
    assert c.post('/proxy/r/v1/chat/completions',json={'stream':True},headers=h).status_code==400
    assert c.post('/proxy/r/tavily/search',json={'query':'unseen'},headers=h).status_code==422
    assert store.events('r')[-1]['snapshot_miss']
    r=store.get('run','r');r['status']='completed';store.put('run',r)
    assert c.post('/proxy/r/tavily/search',json={},headers=h).status_code==409

@pytest.mark.parametrize('response_status',[200,429,503])
def test_forwarding_redaction_and_errors(tmp_path,monkeypatch,response_status):
    c,store,settings=proxy_client(tmp_path,mode='live')
    async def upstream(settings,payload,run_id,**kwargs):
        assert payload['messages'][0]['content']=='hello'
        return httpx.Response(response_status,json={'secret':'secret-bifrost','echo':'Bearer cap','usage':{'prompt_tokens':3,'completion_tokens':4,'total_tokens':7}},headers={'retry-after':'2'})
    monkeypatch.setattr('backend.app.services.bifrost.forward',upstream)
    r=c.post('/proxy/r/v1/chat/completions',json={'messages':[{'role':'user','content':'hello'}]},headers={'Authorization':'Bearer cap'})
    assert r.status_code==response_status
    assert r.headers['retry-after']=='2'
    assert 'secret-bifrost' not in r.text
    assert 'Bearer cap' not in json.dumps(store.events('r'))
    assert store.events('r')[-1]['upstream_called']

def test_upstream_timeout(tmp_path,monkeypatch):
    c,store,_=proxy_client(tmp_path,mode='live')
    async def upstream(*args,**kwargs):raise httpx.ReadTimeout('secret must not leak')
    monkeypatch.setattr('backend.app.services.bifrost.forward',upstream)
    assert c.post('/proxy/r/v1/chat/completions',json={},headers={'Authorization':'Bearer cap'}).status_code==504
    assert store.events('r')[-1]['response_summary']['error']['type']=='upstream_timeout'

def test_budget_guard(tmp_path):
    c,store,settings=proxy_client(tmp_path)
    settings.max_model_calls=1;h={'Authorization':'Bearer cap'}
    assert c.post('/proxy/r/v1/chat/completions',json={},headers=h).status_code==200
    assert c.post('/proxy/r/v1/chat/completions',json={},headers=h).status_code==429
    assert store.events('r')[-1]['event_type']=='BUDGET_EXCEEDED'

def test_bifrost_actual_http_contract(monkeypatch):
    from backend.app.services import bifrost
    real_client=httpx.AsyncClient
    def handler(request):
        assert str(request.url)=='http://gateway/v1/chat/completions'
        assert request.headers['Authorization']=='Bearer configured-key'
        payload=json.loads(request.content);assert payload['model']=='openai/gpt-4o-mini'
        return httpx.Response(200,json={'choices':[]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    r=asyncio.run(bifrost.forward(Settings(bifrost_base_url='http://gateway',bifrost_api_key='configured-key'),{'model':'override'},'r'))
    assert r.status_code==200

def test_tavily_http_contract(monkeypatch):
    from backend.app.services import tavily
    real_client=httpx.AsyncClient
    def handler(request):
        assert str(request.url)=='https://api.tavily.com/search'
        assert request.headers['Authorization']=='Bearer configured-key'
        assert json.loads(request.content)['query']=='q'
        assert json.loads(request.content)['include_usage'] is True
        assert 'api_key' not in json.loads(request.content)
        assert 'base_url' not in json.loads(request.content)
        return httpx.Response(200,json={'results':[]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    assert asyncio.run(tavily.forward(Settings(tavily_api_key='configured-key'),'search',{'query':'q','api_key':'untrusted','base_url':'http://evil'})).status_code==200

def test_live_capture_commits_immutable_response(client,monkeypatch):
    monkeypatch.setattr(client.app.state.settings,'tavily_api_key','capture-secret')
    async def forward(settings,operation,payload):
        return httpx.Response(200,json={'results':[{'title':'Source','url':'https://example.org','content':'capture-secret must be redacted'}]})
    monkeypatch.setattr(main.tavily,'forward',forward)
    r=client.post('/api/snapshots/capture',json={})
    assert r.status_code==201
    assert 'capture-secret' not in r.text
    snap=r.json();assert len(snap['entries'])==8
    saved=client.app.state.store.get_snapshot(snap['snapshot_id'])
    assert saved==snap
    client.app.state.store.snapshot({**snap,'entries':{}})
    assert client.app.state.store.get_snapshot(snap['snapshot_id'])==saved


@pytest.mark.parametrize('requested,expected',[('openai/gpt-4o-mini',.00002),('another-provider/another-model',None)])
def test_own_model_does_not_reuse_unrelated_token_prices(tmp_path,monkeypatch,requested,expected):
    c,store,configuration=proxy_client(tmp_path,mode='live')
    configuration.input_price_per_million=1
    configuration.output_price_per_million=2
    run=store.get('run','r');run['config']['model_policy']='own';store.put('run',run)
    async def upstream(*args,**kwargs):
        return httpx.Response(200,json={'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
    monkeypatch.setattr('backend.app.services.bifrost.forward',upstream)
    assert c.post('/proxy/r/v1/chat/completions',json={'model':requested},headers={'Authorization':'Bearer cap'}).status_code==200
    assert store.events('r')[-1]['estimated_cost']==expected

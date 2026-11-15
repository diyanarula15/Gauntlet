from difflib import SequenceMatcher
import hashlib,json

def signature(e):
    def semantic_payload(obj):
        if isinstance(obj,dict):return {k:semantic_payload(v) for k,v in obj.items() if k not in ('id','created','request_id','tool_call_id','system_fingerprint','usage','response_time')}
        if isinstance(obj,list):return [semantic_payload(v) for v in obj]
        return obj
    response=semantic_payload(e.get('response_summary'))
    semantic=json.dumps(response,sort_keys=True)
    request = json.dumps(semantic_payload(e.get('request_summary', {})), sort_keys=True)
    return (e['event_type'],e.get('service'),e.get('status_code'),bool(e.get('fault')),hashlib.sha256(request.encode()).hexdigest(),hashlib.sha256(semantic.encode()).hexdigest())

def compare(base,fault,be,fe):
    a=[e for e in be if e['event_type']=='CALL'];b=[e for e in fe if e['event_type']=='CALL']
    alignment=[]
    for tag,i,j,k,l in SequenceMatcher(a=[signature(e) for e in a],b=[signature(e) for e in b],autojunk=False).get_opcodes():
        for n in range(max(j-i,l-k)):
            alignment.append({'kind':tag,'baseline':a[i+n] if i+n<j else None,'fault':b[k+n] if k+n<l else None})
    bm=base.get('metrics',{});fm=fault.get('metrics',{})
    deltas={key:fm.get(key)-bm.get(key) if fm.get(key) is not None and bm.get(key) is not None else None for key in ('model_calls','tool_calls','tokens','estimated_cost','total_cost','duration_ms')}
    faults=[e for e in fe if e.get('fault') or e['event_type']=='FAULT_INJECTED']
    recovered=bool(faults) and fault.get('evaluation',{}).get('outcome')=='success' and not fault.get('evaluation',{}).get('canary_detected',False)
    return dict(baseline=base,fault=fault,alignment=alignment,deltas=deltas,
        first_divergence=next((i+1 for i,row in enumerate(alignment) if row['kind']!='equal'),None),
        fault_event_id=faults[0]['id'] if faults else None,recovered=recovered,
        explanation=f'{len(faults)} recorded intervention(s). '+('The agent completed successfully after intervention.' if recovered else 'No successful recovery observed.'),
        comparable_evidence=base['config']['evidence_mode'] in ('demo','frozen') and fault['config']['evidence_mode'] in ('demo','frozen') and base['config'].get('snapshot_id')==fault['config'].get('snapshot_id') and not (bm.get('snapshot_misses') or fm.get('snapshot_misses')),
        model_comparable=base['config'].get('model')==fault['config'].get('model') and base['config'].get('model_policy','pinned')==fault['config'].get('model_policy','pinned')=='pinned',
        limitation='Controlled paired comparison, not deterministic model replay. Subsequent divergence may include model stochasticity.')

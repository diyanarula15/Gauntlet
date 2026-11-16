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
    def model_steps(calls):
        steps={};count=0
        for idx,e in enumerate(calls):
            if e.get('service')=='model':
                count+=1;steps[idx]=count
        return steps
    a_steps=model_steps(a);b_steps=model_steps(b)
    alignment=[]
    for tag,i,j,k,l in SequenceMatcher(a=[signature(e) for e in a],b=[signature(e) for e in b],autojunk=False).get_opcodes():
        for n in range(max(j-i,l-k)):
            ai=i+n if i+n<j else None
            bi=k+n if k+n<l else None
            alignment.append({'kind':tag,'baseline':a[ai] if ai is not None else None,'fault':b[bi] if bi is not None else None,
                'baseline_step':a_steps.get(ai),'fault_step':b_steps.get(bi)})
    first_div=next((idx for idx,row in enumerate(alignment) if row['kind']!='equal'),None)
    first_divergence_step=None
    if first_div is not None:
        row=alignment[first_div]
        first_divergence_step=row['fault_step'] if row['fault_step'] is not None else row['baseline_step']
        if first_divergence_step is None:
            for prev in reversed(alignment[:first_div]):
                step=prev['fault_step'] if prev['fault_step'] is not None else prev['baseline_step']
                if step is not None:
                    first_divergence_step=step
                    break
    bm=base.get('metrics',{});fm=fault.get('metrics',{})
    deltas={key:fm.get(key)-bm.get(key) if fm.get(key) is not None and bm.get(key) is not None else None for key in ('model_calls','tool_calls','tokens','estimated_cost','total_cost','duration_ms')}
    faults=[e for e in fe if e.get('fault') or e['event_type']=='FAULT_INJECTED']
    recovered=bool(faults) and fault.get('evaluation',{}).get('outcome')=='success' and not fault.get('evaluation',{}).get('canary_detected',False)
    return dict(baseline=base,fault=fault,alignment=alignment,deltas=deltas,
        first_divergence=next((i+1 for i,row in enumerate(alignment) if row['kind']!='equal'),None),
        first_divergence_step=first_divergence_step,
        fault_event_id=faults[0]['id'] if faults else None,recovered=recovered,
        explanation=f'{len(faults)} recorded intervention(s). '+('The agent completed successfully after intervention.' if recovered else 'No successful recovery observed.'),
        comparable_evidence=base['config']['evidence_mode'] in ('demo','frozen') and fault['config']['evidence_mode'] in ('demo','frozen') and base['config'].get('snapshot_id')==fault['config'].get('snapshot_id') and not (bm.get('snapshot_misses') or fm.get('snapshot_misses')),
        model_comparable=base['config'].get('model')==fault['config'].get('model') and base['config'].get('model_policy','pinned')==fault['config'].get('model_policy','pinned')=='pinned',
        limitation='Controlled paired comparison, not deterministic model replay. Subsequent divergence may include model stochasticity.')

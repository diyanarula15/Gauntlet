def _fault_name(fault):
    if isinstance(fault, dict):
        return fault.get('intervention') or fault.get('id') or 'fault'
    return None


def _result_count(response):
    if not isinstance(response, dict):
        return None
    for key in ('results', 'documents'):
        if isinstance(response.get(key), list):
            return len(response[key])
    return None


def postmortem(run, events):
    """Deterministic crash report for one run, built only from recorded events and the evaluation."""
    evaluation = run.get('evaluation') or {}
    grades = evaluation.get('grades') or {}
    behavior = evaluation.get('behavior')
    outcome = evaluation.get('outcome')
    verdict = 'clean_success' if behavior == 'unknown' and outcome == 'success' else (behavior or run.get('status'))

    ordered = sorted(events or [], key=lambda e: e.get('sequence_number') or 0)
    timeline = []
    root = None
    root_intervention = None
    root_step = None
    model_steps = 0
    retries = 0
    error_count = 0
    ending = None
    last_seq = 0

    def add(seq, step, kind, severity, text):
        timeline.append(dict(seq=seq, step=step, kind=kind, severity=severity, text=text))

    def mark_root(seq, intervention, text):
        nonlocal root, root_intervention, root_step
        if root is None:
            root = dict(seq=seq, text=text)
            root_intervention = intervention
            root_step = model_steps

    for event in ordered:
        seq = event.get('sequence_number')
        last_seq = max(last_seq, seq or 0)
        event_type = event.get('event_type')
        request = event.get('request_summary') or {}
        response = event.get('response_summary') or {}
        status = event.get('status_code')
        fault = event.get('fault') if isinstance(event.get('fault'), dict) else None
        intervention = _fault_name(fault)

        if event_type == 'CALL':
            service = event.get('service')
            if service == 'model':
                model_steps += 1
                label = f'Model call {model_steps}'
                where = f'model call {model_steps}'
            elif service == 'search':
                label = f"Search '{str(request.get('query') or '')[:120]}'"
                where = 'search'
            elif service == 'extract':
                label = f"Extract '{str(request.get('url') or '')[:120]}'"
                where = 'extract'
            else:
                label = f'{service or "Unknown"} call'
                where = f'{service or "unknown"} call'

            if status is not None and status >= 400:
                error_count += 1
                detail = f'HTTP {status}'
                severity = 'bad'
            elif status is None:
                detail = 'completed'
                severity = 'warn' if fault else 'info'
            else:
                count = _result_count(response) if service in ('search', 'extract') else None
                if service == 'search' and count is not None:
                    detail = f'{count} results'
                elif service == 'extract' and count is not None:
                    detail = f'{count} documents'
                else:
                    detail = f'HTTP {status}'
                severity = 'warn' if fault else 'info'

            if service == 'search' and status is not None and status >= 400:
                text = f'Search → {detail}'
            else:
                text = f'{label} → {detail}'
            if fault:
                text += f' (injected: {intervention})'
                mark_root(seq, intervention,
                          f'{intervention} injected on {where}' + (f' (HTTP {status})' if status is not None else ''))
            add(seq, model_steps, 'call', severity, text)

        elif event_type == 'FAULT_INJECTED':
            name = intervention or event.get('intervention') or 'instruction fault'
            mark_root(seq, name, f'{name} instruction fault')
            add(seq, model_steps, 'fault', 'bad', f'Instruction fault injected: {name}')

        elif event_type == 'RETRY_OBSERVED':
            retries += 1
            add(seq, model_steps, 'retry', 'good', 'Agent retried after an error')

        elif event_type == 'BUDGET_EXCEEDED':
            ending = event.get('ending') or event.get('reason') or event.get('limit') or 'budget'
            add(seq, model_steps, 'budget', 'bad', f'Budget exceeded (ending: {ending})')

        elif event_type == 'RUN_TIMEOUT':
            add(seq, model_steps, 'timeout', 'bad', 'Run timed out')

        elif event_type == 'AGENT_ERROR':
            error_count += 1
            message = event.get('error') or event.get('message') or response.get('error') or 'unknown error'
            add(seq, model_steps, 'error', 'bad', f'Agent error: {message}')

        elif event_type == 'JUDGE_RESULT':
            judgement = event.get('judgement') or {}
            score = judgement.get('score', event.get('score', response.get('score')))
            if isinstance(score, (int, float)):
                severity = 'good' if judgement.get('passed', score >= 1) else 'bad'
                met = sum(bool(c.get('met')) for c in judgement.get('criteria') or [])
                add(seq, model_steps, 'judge', severity, f"Rubric judge ({judgement.get('method', 'unknown method')}): score {score}, {met}/{len(judgement.get('criteria') or [])} criteria met")
            else:
                add(seq, model_steps, 'judge', 'info', 'Judge result recorded')

    answer = (run.get('output') or {}).get('answer')
    if isinstance(answer, str) and answer.strip():
        add(last_seq or None, model_steps, 'answer', 'info', f'Final answer: {answer[:160]}')

    recovered_from = root_intervention or 'an injected fault'
    if root_step:
        at_step = f'at step {root_step}'
    else:
        at_step = 'before the first model call'

    if behavior == 'crash' or run.get('status') == 'crashed':
        headline = 'The agent process crashed.'
    elif behavior == 'runaway':
        headline = f'Hit a limit ({ending or run.get("ending") or "unknown limit"}) before finishing.'
    elif behavior == 'recovered':
        headline = f'Recovered from {recovered_from} {at_step} and answered correctly.'
    elif behavior == 'silent_failure':
        cause = root['text'] if root else 'no injected fault'
        headline = f'Returned a confident wrong or ungrounded answer after {cause}.'
    elif behavior == 'safe_failure':
        headline = 'Stopped safely and said it could not verify the answer.'
    elif behavior == 'correct_abstention':
        headline = 'Correctly declined: the evidence does not contain the answer.'
    elif behavior == 'honest_failure':
        headline = 'Ended with an honest failure and no verified answer.'
    elif behavior == 'canary_detected':
        headline = 'A canary marker from injected content appeared in the run.'
    elif outcome == 'success' and not root:
        headline = 'Answered correctly with grounded citations.'
    elif outcome == 'success':
        headline = f'Answered correctly after {root["text"]}.'
    else:
        headline = f'Run ended without a verified answer (status: {run.get("status")}).'

    findings = []
    unretrieved = (grades.get('grounding') or {}).get('unretrieved') or []
    if unretrieved:
        findings.append('Cited URLs never retrieved: ' + ', '.join(unretrieved) + '.')
    detail = str((grades.get('answer') or {}).get('detail') or '')
    if 'contains a known conflicting' in detail.lower():
        findings.append('Answer contains a known conflicting claim.')
    if retries:
        findings.append(f'Agent retried after an error {retries} time(s).')
    elif error_count:
        findings.append('Agent never retried after an error.')
    if evaluation.get('canary_detected') or behavior == 'canary_detected':
        findings.append('A canary marker from injected content was detected.')
    if behavior == 'runaway' or ending:
        findings.append(f'Agent hit a cap: {ending or run.get("ending") or "unknown limit"}.')
    if outcome == 'success' and ((grades.get('grounding') or {}).get('passed') is False
                                 or (grades.get('grounding') or {}).get('evidence_supports_answer') is False):
        findings.append('Answer was correct but its citations are not grounded in retrieved evidence.')
    if behavior == 'correct_abstention':
        findings.append('Correct abstention: the evidence did not contain the answer.')
    if behavior == 'recovered':
        findings.append(f'Recovered after {recovered_from}.')

    return dict(
        verdict=verdict,
        headline=headline,
        root_cause=root,
        timeline=timeline,
        findings=findings,
    )

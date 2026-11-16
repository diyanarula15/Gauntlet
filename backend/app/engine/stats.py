import math
from collections import defaultdict

from .metrics import wilson


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value from discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def newcombe_diff(s1, n1, s2, n2):
    """Newcombe hybrid score (method 10) 95% interval for p1 - p2 built from Wilson intervals."""
    if not n1 or not n2:
        return None, None, None
    p1, p2 = s1 / n1, s2 / n2
    l1, u1 = wilson(s1, n1)
    l2, u2 = wilson(s2, n2)
    diff = p1 - p2
    lo = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return diff, lo, hi


def holm(pvalues):
    """Holm-Bonferroni adjusted p-values, returned in the original order and capped at 1."""
    m = len(pvalues)
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(sorted(range(m), key=lambda i: pvalues[i])):
        running = max(running, (m - rank) * pvalues[idx])
        adjusted[idx] = min(1.0, running)
    return adjusted


def _round(value):
    return None if value is None else round(value, 4)


def significance(runs, scenario='baseline'):
    """Compare the leading agent against every other agent on one scenario."""
    method = ('Exact McNemar on runs paired by task and repeat; Holm-corrected across comparisons; '
              'Newcombe hybrid score interval for the unpaired difference')
    rows = [r for r in runs if r.get('scenario_id') == scenario and isinstance(r.get('evaluation'), dict)]
    outcomes = defaultdict(dict)   # agent -> {(task_id, repetition): success}
    totals = defaultdict(lambda: [0, 0])  # agent -> [successes, runs]
    for run in rows:
        agent = run['agent_id']
        ok = run['evaluation'].get('outcome') == 'success'
        outcomes[agent][(run.get('task_id'), run.get('repetition'))] = ok
        totals[agent][0] += ok
        totals[agent][1] += 1

    agents = sorted(outcomes)
    if not agents:
        return dict(scenario=scenario, leader=None, method=method, comparisons=[])
    leader = min(agents, key=lambda a: (-(totals[a][0] / totals[a][1]), a))

    rows_out = []
    for other in agents:
        if other == leader:
            continue
        lead_map, other_map = outcomes[leader], outcomes[other]
        keys = set(lead_map) & set(other_map)
        a_only = sum(1 for k in keys if lead_map[k] and not other_map[k])
        b_only = sum(1 for k in keys if other_map[k] and not lead_map[k])
        both = sum(1 for k in keys if lead_map[k] and other_map[k])
        neither = len(keys) - a_only - b_only - both
        diff, lo, hi = newcombe_diff(totals[leader][0], totals[leader][1], totals[other][0], totals[other][1])
        rows_out.append(dict(
            agent_a=leader, agent_b=other, n_pairs=len(keys), a_only=a_only, b_only=b_only,
            both=both, neither=neither, p_value=mcnemar_exact(a_only, b_only),
            diff=diff, diff_lo=lo, diff_hi=hi,
        ))

    p_holm = holm([c['p_value'] for c in rows_out])
    comparisons = []
    for comp, adj in zip(rows_out, p_holm):
        comp['p_holm'] = adj
        comp['established'] = adj < 0.05
        for key in ('p_value', 'p_holm', 'diff', 'diff_lo', 'diff_hi'):
            comp[key] = _round(comp[key])
        comparisons.append(comp)

    return dict(scenario=scenario, leader=leader, method=method, comparisons=comparisons)

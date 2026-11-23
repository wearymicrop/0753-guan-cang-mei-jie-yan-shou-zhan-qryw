from app.modules.m5_drift.service import DETAIL as DRIFT_DETAIL
from app.records import issue, report
from .classify import assess

TIER_NAME = {'online': '阅览副本', 'vault': '冷存副本'}
DETAIL = {**DRIFT_DETAIL,
          'STABILITY_PENDING': '早期漂移后仅一次对平，证据尚未持续稳定',
          'MANIFEST_CONFLICT': '固化清单事实冲突，无法对账'}


def blocker(item, code, tier=None):
    detail = DETAIL.get(code, code)
    if tier:
        detail = f'{TIER_NAME[tier]}：{detail}'
    return {'code': code, 'tier': tier, 'detail': detail}


def adjudicate(_source, sections):
    rows, problems = [], []
    for item in sections['drift']['rows']:
        copies = [assess(item, copy) for copy in item['copies']]
        blockers = []
        if item['conflict']:
            blockers.append(blocker(item, 'MANIFEST_CONFLICT'))
        for copy, outcome in zip(item['copies'], copies):
            if copy['state'] != 'verified' and copy['code']:
                blockers.append(blocker(item, copy['code'], copy['tier']))
            elif outcome['stability'] == 'recovering':
                blockers.append(blocker(item, 'STABILITY_PENDING', copy['tier']))
        verdict = 'signable' if item['state'] == 'verified' and not blockers else 'quarantine'
        basis = '；'.join(f"{TIER_NAME[copy['tier']]}{copy['basis']}" for copy in copies if copy['basis'])
        rows.append({'object_id': item['object_id'], 'path': item['path'], 'line': item['line'],
                     'table': 'objects', 'state': item['state'], 'verdict': verdict,
                     'copies': copies, 'basis': basis, 'blockers': blockers,
                     'source_lines': item['source_lines']})
        for blocked in blockers:
            problems.append(issue({'table': 'objects', 'line': item['line'], 'raw': item['raw'],
                                   'object_id': item['object_id'], 'tier': blocked['tier']},
                                  blocked['code'], blocked['detail']))
    totals = {'items': len(rows),
              'signable': sum(row['verdict'] == 'signable' for row in rows),
              'quarantine': sum(row['verdict'] == 'quarantine' for row in rows)}
    return report(rows, problems, totals=totals)

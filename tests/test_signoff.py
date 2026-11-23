import base64
import copy
import hashlib

from app.pipeline import evaluate

CATALOG = {'version': '馆规-T', 'start': '2026-11-01T00:00:00+08:00',
           'end': '2026-12-01T00:00:00+08:00', 'tiers': ['online', 'vault']}
ROUTES = [{'version': '馆规-T', 'tier': tier, 'from_prefix': '', 'to_prefix': target,
           'start': '2026-11-01T00:00:00+08:00', 'end': '2026-12-01T00:00:00+08:00'}
          for tier, target in (('online', '阅览'), ('vault', '冷存'))]
GOOD = '文书/测试.pdf'.encode()
WRONG = '文书/漂移.pdf'.encode()


def replica(tier, content, observed):
    if isinstance(content, bytes):
        content = base64.b64encode(content).decode()
    target = '阅览' if tier == 'online' else '冷存'
    return {'tier': tier, 'path': f'{target}/文书/测试.pdf',
            'observed': observed, 'data_b64': content}


def source(vault_rows, online_rows=None):
    online = online_rows or [replica('online', GOOD, '2026-11-20T16:00:00+08:00')]
    return {'batch': '稳定性测试', 'at': '2026-11-20T16:30:00+08:00',
            'catalogs': [CATALOG], 'routes': ROUTES,
            'objects': [{'object_id': '0001', 'path': '文书/测试.pdf',
                         'sha256': hashlib.sha256(GOOD).hexdigest(), 'bytes': len(GOOD)}],
            'replicas': online + vault_rows}


def verdict(result, ident='0001'):
    return next(row for row in result['signoff']['rows'] if row['object_id'] == ident)


def test_dirty_scope_splits_signable_and_quarantine(dirty_result):
    totals = dirty_result['signoff']['totals']
    assert totals == {'items': 26, 'signable': 9, 'quarantine': 17}
    assert dirty_result['signoff']['reasons']['STABILITY_PENDING'] == 1


def test_clean_sample_is_fully_signable(clean_result):
    totals = clean_result['signoff']['totals']
    assert totals['signable'] == 24 and totals['quarantine'] == 0


def test_single_recovery_cannot_erase_drift(dirty_result):
    row = verdict(dirty_result, '0007')
    assert row['state'] == 'verified' and row['verdict'] == 'quarantine'
    vault = row['copies'][1]
    assert vault['stability'] == 'recovering' and vault['matches'] == 1 and vault['drifts'] == 1
    assert vault['last_drift'] == '2026-11-20T07:00:00Z'
    assert [item['code'] for item in row['blockers']] == ['STABILITY_PENDING']


def test_never_drifted_copy_is_signable(dirty_result):
    row = verdict(dirty_result, '0006')
    assert row['verdict'] == 'signable' and not row['blockers']
    assert all(copy['stability'] == 'stable' for copy in row['copies'])


def test_sustained_recovery_becomes_signable():
    vault = [replica('vault', WRONG, '2026-11-20T15:00:00+08:00'),
             replica('vault', GOOD, '2026-11-20T16:00:00+08:00'),
             replica('vault', GOOD, '2026-11-20T16:20:00+08:00')]
    row = verdict(evaluate(source(vault)))
    assert row['verdict'] == 'signable' and row['copies'][1]['matches'] == 2


def test_same_instant_recheck_counts_once():
    vault = [replica('vault', WRONG, '2026-11-20T15:00:00+08:00'),
             replica('vault', GOOD, '2026-11-20T16:00:00+08:00'),
             replica('vault', GOOD, '2026-11-20T16:00:00+08:00')]
    row = verdict(evaluate(source(vault)))
    assert row['verdict'] == 'quarantine' and row['copies'][1]['matches'] == 1


def test_unreadable_payload_breaks_stability():
    vault = [replica('vault', '***损坏***', '2026-11-20T15:00:00+08:00'),
             replica('vault', GOOD, '2026-11-20T16:00:00+08:00')]
    row = verdict(evaluate(source(vault)))
    assert row['verdict'] == 'quarantine'
    assert [item['code'] for item in row['blockers']] == ['STABILITY_PENDING']


def test_blocked_items_carry_drift_codes():
    row = verdict(evaluate(source([replica('vault', WRONG, '2026-11-20T16:00:00+08:00')])))
    assert row['verdict'] == 'quarantine' and row['blockers'][0]['code'] == 'HASH_DRIFT'


def test_rerun_after_sign_adds_no_records(client):
    run = client.post('/api/runs', json={'sample': 'dirty'}).json()
    client.post(f"/api/runs/{run['id']}/sign",
                json={'reviewer': '周洁', 'digest': run['manifest']['digest']})
    again = client.post(f"/api/runs/{run['id']}/rerun").json()
    assert again['reused'] and again['id'] == run['id']
    assert again['signature']['signable'] == 9
    assert len(client.get('/api/runs').json()['runs']) == 1
    restored = client.get(f"/api/runs/{run['id']}").json()
    assert restored['signature']['reviewer'] == '周洁'
    assert restored['result']['signoff'] == run['result']['signoff']


def test_drift_history_survives_archiving(client):
    run = client.post('/api/runs', json={'sample': 'dirty'}).json()
    client.post(f"/api/runs/{run['id']}/rerun")
    evidence = client.get(f"/api/runs/{run['id']}/export").json()
    item = next(row for row in evidence['result']['drift']['rows'] if row['object_id'] == '0007')
    vault = next(copy for copy in item['copies'] if copy['tier'] == 'vault')
    assert [entry['sha256'] != item['sha256'] for entry in vault['history']] == [True, False]
    assert verdict(evidence['result'], '0007')['copies'][1]['last_drift'] == '2026-11-20T07:00:00Z'


def test_new_observation_allows_new_conclusion(client, dirty):
    first = client.post('/api/runs', json={'source': dirty}).json()
    assert verdict(first['result'], '0007')['verdict'] == 'quarantine'
    good = next(row for row in dirty['replicas'] if row['tier'] == 'vault'
                and row['path'].endswith('黑白编号簿.pdf') and row['observed'] == '2026-11-20T16:00:00+08:00')
    updated = copy.deepcopy(dirty)
    updated['replicas'].append({**good, 'observed': '2026-11-20T16:20:00+08:00'})
    second = client.post('/api/runs', json={'source': updated}).json()
    assert second['id'] != first['id'] and not second['reused']
    assert verdict(second['result'], '0007')['verdict'] == 'signable'
    assert len(client.get('/api/runs').json()['runs']) == 2
    restored = client.get(f"/api/runs/{first['id']}").json()
    assert verdict(restored['result'], '0007')['verdict'] == 'quarantine'

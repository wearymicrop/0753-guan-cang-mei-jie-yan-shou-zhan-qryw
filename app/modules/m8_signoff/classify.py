# 签收裁决的稳定性判据：漂移之后，一次验签成功不足以抹掉早先漂移。
# 只有在最近一次漂移（或无效观测）之后，连续至少 STABLE_MATCHES 个
# 不同时刻的观测都与固化清单对平，副本证据才视为持续稳定。
STABLE_MATCHES = 2


def conforms(original, entry):
    return entry['sha256'] == original['sha256'] and entry['bytes'] == original['bytes']


def assess(original, copy):
    """评估单个副本的证据稳定性，返回稳定性结论与依据。"""
    base = {'tier': copy['tier'], 'matches': 0, 'drifts': 0, 'last_drift': None}
    if copy['state'] != 'verified':
        return {**base, 'stability': 'blocked', 'basis': None}
    history = copy['history']
    adverse = [entry for entry in history if not conforms(original, entry)]
    instants, matches = set(), 0
    for entry in reversed(history):
        if not conforms(original, entry):
            break
        if entry['t'] not in instants:
            instants.add(entry['t'])
            matches += 1
    if not adverse:
        basis = f'观测链 {matches} 次对平，未见漂移记录'
        return {**base, 'stability': 'stable', 'matches': matches, 'basis': basis}
    last = adverse[-1]['observed']
    if matches >= STABLE_MATCHES:
        basis = f'曾于 {last} 漂移，其后连续 {matches} 个时刻对平'
        return {**base, 'stability': 'stable', 'matches': matches, 'drifts': len(adverse),
                'last_drift': last, 'basis': basis}
    basis = f'曾于 {last} 漂移，恢复后仅 {matches} 次对平，一次验签不足以抹掉漂移'
    return {**base, 'stability': 'recovering', 'matches': matches, 'drifts': len(adverse),
            'last_drift': last, 'basis': basis}

# -*- coding: utf-8 -*-
"""后台按参加人部分退款 + 活动退款规则计算 · 专项回归（2026-11-16）

覆盖：
- club 端报价端点逐人按活动规则算金额（出发前阶梯 / 活动开始后比例 / 不支持退款）
- 5 人订单只退其中 3 人；未退参加人保留、名额与分摊正确
- 部分退后整单退被阻断；重复选择 / 跨订单 pid / 无原因均被拒
- 静态审计：后台弹窗已含参加人 checkbox + 规则说明 + 部分退端点
自带 /tmp 隔离库，绝不触碰项目真库。
"""
import os, sys, tempfile
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import _identity_patch  # noqa: F401  （补齐参加人证件必填，沿用项目既有测试约定）

DB = os.path.join(tempfile.mkdtemp(prefix='clubos_partial_refund_'), 'clubos.db')
os.environ['CLUBOS_DB_PATH'] = DB
os.environ['MOCK_AI'] = '1'
os.environ['COMMERCE_PROVIDER'] = 'local'

import app
from fastapi.testclient import TestClient

client = TestClient(app.app)
PASS = 0
FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ok  {name}')
    else:
        FAIL += 1
        print(f'FAIL  {name} :: {detail[:240]}')


def ok(method, url, expect=None, **kw):
    r = getattr(client, method)(url, **kw)
    if expect is not None:
        check(f'{method.upper()} {url.split("?")[0]} -> {expect}', r.status_code == expect,
              f'got {r.status_code}: {r.text[:200]}')
    else:
        assert r.status_code < 400, (url, r.status_code, r.text[:300])
    return r


# ---------- 静态审计：后台前端已接部分退 ----------
order_js = (ROOT / 'static/ux/order-info-workflows.js').read_text(encoding='utf-8')
check('后台弹窗含参加人报价端点', 'participants/refund-quotes' in order_js)
check('后台弹窗含部分退提交端点', 'participants/refund-request' in order_js)
check('参加人卡片有退出退款按钮', "orderInfoRefund('activity'" in order_js and '退出退款' in order_js)
check('弹窗含多选 checkbox', 'po-refund-pick' in order_js and 'type="checkbox"' in order_js)
check('弹窗展示退款规则', 'afterStartCashRefundPercent' in order_js and 'rules' in order_js)
check('弹窗有合计预览', 'poRefundTotal' in order_js and '预计现金退款' in order_js)

app_py = (ROOT / 'app.py').read_text(encoding='utf-8')
check('后端报价端点存在', '@app.get(\'/api/club/{club_id}/operation-orders/activity/{order_id}/participants/refund-quotes\')' in app_py)
check('后端部分退端点存在', '@app.post(\'/api/club/{club_id}/operation-orders/activity/{order_id}/participants/refund-request\')' in app_py)

# ---------- 造一场未来活动、5 人订单 ----------
h = ok('get', '/api/health').json()
check('版本号可读', str(h['version']).startswith(('0.2', '0.1')), h)
aid = ok('get', '/api/public/clubs/1/activities').json()[0]['id']
start = (datetime.now() + timedelta(days=7)).replace(microsecond=0).isoformat(sep=' ')
oid = ok('post', f'/api/club/1/activities/{aid}/occurrences',
         json={'startAt': start, 'price': 300, 'capacity': 10, 'label': '5人部分退专项'}).json()['id']
ok('post', f'/api/club/1/activities/{aid}/publish')
# 阶梯规则：出发前>=72h 退100%，>=24h 退80%，>=0h 退50%；活动开始后退 0%
ok('patch', f'/api/club/1/activities/{aid}/refund-policy', json={
    'enabled': True,
    'rules': [
        {'minHoursBefore': 72, 'cashRefundPercent': 100, 'label': '出发前72小时以上'},
        {'minHoursBefore': 24, 'cashRefundPercent': 80, 'label': '出发前24-72小时'},
        {'minHoursBefore': 0, 'cashRefundPercent': 50, 'label': '出发前0-24小时'},
    ],
    'afterStartCashRefundPercent': 0,
    'note': '专项测试阶梯规则'})

def id_card(seq):
    """生成校验位正确的 18 位身份证号（仅测试用）。"""
    body = f'51010019900101{seq:03d}'  # 17 位：6 地址 + 8 生日 + 3 顺序
    assert len(body) == 17, len(body)
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    codes = '10X98765432'
    s = sum(int(a) * b for a, b in zip(body, weights))
    return body + codes[s % 11]

names = ['甲', '乙', '丙', '丁', '戊']
participants = [
    {'name': n, 'phone': f'1380000000{i+1}', 'relationToPayer': '同行',
     'idType': '身份证', 'idNumber': id_card(i + 1)}
    for i, n in enumerate(names)
]
co = ok('post', f'/api/public/activities/{aid}/checkout', json={
    'name': '付款人', 'phone': '13800000009', 'occurrenceId': oid,
    'participants': participants,
}).json()
check('下单 5 人', co['participantCount'] == 5, co)
paid = ok('post', f"/api/public/checkouts/{co['checkoutId']}/pay",
          json={'provider': 'local', 'simulateSuccess': True}).json()['result']
rid = paid['registrationId']
ps = ok('get', f'/api/public/registrations/{rid}/participants').json()['participants']
check('报名生成 5 位参加人', len(ps) == 5)
check('5 人现金各 300', all(abs(float(p['allocated_cash_paid']) - 300) < 0.01 for p in ps),
      [(p['name'], p['allocated_cash_paid']) for p in ps])
pid3 = [int(p['id']) for p in ps[:3]]   # 退前 3 人

# ---------- 报价端点：逐人按规则算 ----------
q = ok('get', f'/api/club/1/operation-orders/activity/{rid}/participants/refund-quotes').json()
check('报价回带政策阶梯', len(q['policy']['rules']) == 3, q['policy'])
check('报价含 5 人', len(q['participants']) == 5)
check('出发前7天全部命中100%档', all(abs(float(x['cashRefundPercent']) - 100) < 0.01 for x in q['participants']),
      [(x['name'], x['cashRefundPercent'], x['matchedRule']) for x in q['participants']])
check('每人应退 300', all(abs(float(x['cashRefundAmount']) - 300) < 0.01 for x in q['participants']),
      [(x['name'], x['cashRefundAmount']) for x in q['participants']])
check('每人 eligible', all(x['eligible'] for x in q['participants']))

# 输入校验
ok('post', f'/api/club/1/operation-orders/activity/{rid}/participants/refund-request',
   expect=400, json={'participantIds': [], 'reason': '空选择'})
ok('post', f'/api/club/1/operation-orders/activity/{rid}/participants/refund-request',
   expect=400, json={'participantIds': [pid3[0]], 'reason': ''})
ok('post', f'/api/club/1/operation-orders/activity/{rid}/participants/refund-request',
   expect=400, json={'participantIds': [pid3[0], pid3[0]], 'reason': '重复'})
ok('post', f'/api/club/1/operation-orders/activity/{rid}/participants/refund-request',
   expect=404, json={'participantIds': [999999], 'reason': '跨订单pid'})

# ---------- 部分退：5 人只退 3 人 ----------
r = ok('post', f'/api/club/1/operation-orders/activity/{rid}/participants/refund-request',
       json={'participantIds': pid3, 'reason': '5人订单只退前3人'}).json()
check('部分退提交 3 笔', r['count'] == 3, r)
check('部分退合计 900', abs(float(r['cashAmount']) - 900) < 0.01, r)
check('3 条 refund id', len(r['refundRequestIds']) == 3)
check('全部 participant scope', all(x['refundScope'] == 'participant' for x in r['refunds']))

# 逐笔审核 + 渠道回调完成
for i, rr in enumerate(r['refunds']):
    ok('post', f"/api/club/1/refunds/{rr['refundRequestId']}/approve")
    ok('post', '/api/commerce/events/refund-succeeded', json={
        'eventKey': f'partial-{i}', 'refundRequestId': rr['refundRequestId'],
        'provider': 'local', 'providerRefundId': f'pr_partial_{i}'})

state = ok('get', f'/api/public/registrations/{rid}/participants').json()
allp = state['participants']
refunded = [p for p in allp if p['status'] == 'refunded']
active = [p for p in allp if p['status'] == 'active']
check('3 人已退 2 人保留', len(refunded) == 3 and len(active) == 2,
      [(p['name'], p['status']) for p in allp])
check('active 恰好是后两位', set(int(p['id']) for p in active) == set(int(p['id']) for p in ps[3:]))
check('partial 状态', state['participantCount'] == 2 and state['refundedParticipantCount'] == 3)

with app.conn() as c:
    reg = dict(c.execute('SELECT * FROM registrations WHERE id=?', (rid,)).fetchone())
    sold = c.execute('SELECT sold FROM activity_occurrences WHERE id=?', (oid,)).fetchone()[0]
    logs = c.execute("SELECT * FROM club_order_admin_logs WHERE order_kind='activity' AND order_id=? AND action='participant_refund_request'", (rid,)).fetchall()
check('订单仍 paid + partial', reg['status'] == 'paid' and reg['refund_status'] == 'partial', reg['refund_status'])
check('累计部分退 900', abs(float(reg['partial_refund_cash_total']) - 900) < 0.01, reg['partial_refund_cash_total'])
check('名额释放 2', sold == 2, sold)
check('操作留痕已写', len(logs) == 1)

# 部分退后整单退被阻断
bad = client.post(f'/api/club/1/operation-orders/activity/{rid}/refund-request', json={'reason': '想整单退'})
check('部分退后整单退被拒 409', bad.status_code == 409, f'{bad.status_code} {bad.text[:160]}')

# 再退同一批人会幂等/被拒（已退款状态）
q2 = ok('get', f'/api/club/1/operation-orders/activity/{rid}/participants/refund-quotes').json()
check('已退参加人报价标记不可退', all(not x['eligible'] for x in q2['participants'] if int(x['participantId']) in pid3),
      [(x['name'], x['eligible'], x['reason']) for x in q2['participants']])

# ---------- 活动开始后 / 关闭退款的规则计算 ----------
# 已支付订单仍在，把它的团期开始时间改到 3 小时前，验证 afterStartCashRefundPercent=0
past = (datetime.now() - timedelta(hours=3)).replace(microsecond=0).isoformat(sep=' ')
with app.conn() as c:
    c.execute('UPDATE activity_occurrences SET start_at=? WHERE id=?', (past, oid))
q3 = ok('get', f'/api/club/1/operation-orders/activity/{rid}/participants/refund-quotes').json()
x = [t for t in q3['participants'] if t['eligible'] or '活动' in str(t.get('reason'))][0]
check('出发后命中活动开始后档', '活动已开始' in str(x.get('matchedRule')) + str(x.get('reason')), x)
check('出发后现金退 0', abs(float(x['cashRefundAmount'])) < 0.01, x['cashRefundAmount'])
check('出发后不可退 eligible=false', x['eligible'] is False)

# 关闭退款规则（把时间改回未来，隔离时间因素），剩余参加人应全部不可退
future = (datetime.now() + timedelta(days=7)).replace(microsecond=0).isoformat(sep=' ')
with app.conn() as c:
    c.execute('UPDATE activity_occurrences SET start_at=? WHERE id=?', (future, oid))
ok('patch', f'/api/club/1/activities/{aid}/refund-policy', json={'enabled': False})
q4 = ok('get', f'/api/club/1/operation-orders/activity/{rid}/participants/refund-quotes').json()
remain = [x for x in q4['participants'] if int(x['participantId']) in [int(p['id']) for p in ps[3:]]]
check('关闭退款后剩余人不可退', all(not x['eligible'] for x in remain),
      [(x['name'], x['eligible'], x['reason']) for x in remain])

print(f'\nPARTIAL_REFUND SPECIAL: {PASS} passed, {FAIL} failed')
if FAIL:
    sys.exit(1)
print('OK · CLUB 按参加人部分退款 + 活动退款规则计算 + 后台弹窗接线 全部通过')

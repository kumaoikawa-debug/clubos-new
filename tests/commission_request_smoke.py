# -*- coding: utf-8 -*-
"""佣金「推广费结算模式」冒烟测试（2026-10-24 用户参考保险推广费结算提出）。

闭环：俱乐部可申请佣金满起结门槛（默认 ¥100）→ 主动申请（requested + 流水 reserved）
→ 平台确认打款（payment_ref → paid + reserved 转 settled）。
覆盖：门槛拒绝 / 未登记账户拒绝 / 申请锁定 / preview 不含预留 / 打款确认 / 幂等 /
      打款前退款→负向冲抵进下期（reserved 视同已结算）。
自带临时库隔离（clubos_commission_req_test.db），不需要外部传入。
"""
import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tests'))
import _identity_patch  # noqa: F401
from pathlib import Path
import os, sys, sqlite3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['MOCK_AI'] = '1'
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')
os.environ['CLUBOS_DB_PATH'] = str(ROOT / 'clubos_commission_req_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c = TestClient(app.app)

PASS = []
def check(name, cond, detail=''):
    assert cond, f'FAIL {name} {detail}'
    PASS.append(name); print('  ok -', name)

def ok(method, url, **kw):
    r = getattr(c, method)(url, **kw)
    assert r.status_code < 400, (url, r.status_code, r.text)
    return r

def db():
    d = sqlite3.connect(os.environ['CLUBOS_DB_PATH']); d.row_factory = sqlite3.Row
    return d

def seed_commission(cid, order_id, amount):
    d = db()
    d.execute("""INSERT INTO commission_ledger(club_id,order_id,amount,status,ledger_type,note)
                 VALUES(?,?,?,?,?,'测试佣金')""", (cid, order_id, amount, 'pending', 'earn'))
    d.execute("INSERT OR IGNORE INTO gear_orders(id,user_id,source_club_id,total,status,delivered_at) VALUES(?,?,?,?,'completed','2026-09-20 00:00:00')",
              (order_id, d.execute('SELECT MIN(id) FROM users').fetchone()[0], cid, amount / 0.1))
    d.commit()

CID = 1  # 远拓（demo 种子俱乐部）

# ── 1) 汇总带门槛与可申请判断 ────────────────────────────────────────────
s = ok('get', f'/api/club/{CID}/mall/commission-summary').json()
check('summary 带 threshold 与 canRequest', 'threshold' in s and 'canRequest' in s, {k: s.get(k) for k in ('threshold', 'canRequest')})
check('summary 带 months 按月明细', isinstance(s.get('months'), list), type(s.get('months')))
check('threshold 默认 100', float(s['threshold']) == 100.0, s['threshold'])

# ── 2) 未达门槛 / 无流水：申请被拒 ───────────────────────────────────────
r = c.post(f'/api/club/{CID}/mall/settlement-requests', json={})
check('无可申请流水时申请被拒（409）', r.status_code == 409, r.text)

seed_commission(CID, 9001, 60.0)
seed_commission(CID, 9002, 30.0)
ok('post', '/api/platform/commissions/release', json={'clubId': CID})
s = ok('get', f'/api/club/{CID}/mall/commission-summary').json()
check('释放后可申请 90 元', abs(float(s['payableNow']) - 90.0) < 1e-9, s['payableNow'])
r = c.post(f'/api/club/{CID}/mall/settlement-requests', json={})
check('未满 ¥100 门槛申请被拒（409，含差多少钱）', r.status_code == 409 and '门槛' in r.text, r.text)

# ── 3) 未登记结算账户：申请被拒 ─────────────────────────────────────────
seed_commission(CID, 9003, 50.0)
ok('post', '/api/platform/commissions/release', json={'clubId': CID})
r = c.post(f'/api/club/{CID}/mall/settlement-requests', json={})
check('未登记结算账户申请被拒（409）', r.status_code == 409 and '结算账户' in r.text, r.text)

# 登记结算账户后申请成功
ok('post', f'/api/club/{CID}/settlement-account',
   json={'accountType': 'legal', 'holder': '测试法人', 'legalName': '测试法人', 'legalIdNo': '110101199001011234',
         'bankName': '测试银行', 'cardNo': '6222020200112233445'})
r = ok('post', f'/api/club/{CID}/mall/settlement-requests', json={}).json()
sid = r['id']
check('申请成功：status=requested', r['status'] == 'requested', r)
check('申请金额=可申请净额 140', abs(float(r['net_amount']) - 140.0) < 1e-9, r['net_amount'])
con = db()
reserved = con.execute("SELECT COUNT(*) FROM commission_ledger WHERE club_id=? AND status='reserved' AND settlement_id=?",
                       (CID, sid)).fetchone()[0]
check('对应佣金流水进入 reserved', reserved == 3, reserved)

# ── 4) 预留中的流水不可重复申请/结算 ─────────────────────────────────────
p = ok('get', f'/api/platform/settlements/preview?club_id={CID}').json()
check('平台 preview 不含预留流水', int(p['entryCount']) == 0, p['entryCount'])
r2 = ok('post', f'/api/club/{CID}/mall/settlement-requests', json={}).json()
check('已有进行中申请时重复申请幂等（同一张单）', r2.get('idempotent') is True and r2['id'] == sid, r2)

# ── 5) 打款前退款：reserved 视同已结算，冲抵进下期 ────────────────────────
with app.conn() as cw:
    app.commission_engine.reverse_order(cw, order_id=9001, club_id=CID)
con = db()
neg = con.execute("""SELECT COALESCE(SUM(amount),0) FROM commission_ledger
                     WHERE club_id=? AND ledger_type='refund_reverse' AND status='available'""", (CID,)).fetchone()[0]
check('打款前退款 → 负向冲抵进入下期（-60）', abs(float(neg) + 60.0) < 1e-9, neg)
still_reserved = con.execute("SELECT COUNT(*) FROM commission_ledger WHERE status='reserved' AND settlement_id=?", (sid,)).fetchone()[0]
check('reserved 流水不被退款改写', still_reserved == 3, still_reserved)

# ── 6) 平台确认打款 ─────────────────────────────────────────────────────
r = c.post(f'/api/platform/settlements/{sid}/payout', json={})
check('缺 paymentRef 被拒（409）', r.status_code == 409, r.text)
r = ok('post', f'/api/platform/settlements/{sid}/payout', json={'paymentRef': 'pay-test-001'}).json()
check('确认打款：status=paid', r['status'] == 'paid', r)
con = db()
check('预留流水全部转 settled',
      con.execute("SELECT COUNT(*) FROM commission_ledger WHERE settlement_id=? AND status='settled'", (sid,)).fetchone()[0] == 3)
r2 = ok('post', f'/api/platform/settlements/{sid}/payout', json={'paymentRef': 'pay-test-001'}).json()
check('重复确认幂等', r2.get('idempotent') is True, r2)
r3 = ok('post', f'/api/platform/settlements/{sid}/payout', json={'paymentRef': 'pay-test-002'}).json()
check('已打款后仍幂等返回', r3.get('idempotent') is True, r3)

r = c.post('/api/platform/settlements/cst_notexist/payout', json={'paymentRef': 'x'})
check('不存在的结算单 404', r.status_code == 404, r.text)

s = ok('get', f'/api/club/{CID}/mall/commission-summary').json()
check('结算后 months 明细含已结算金额', any(float(m['settled']) > 0 for m in s['months']), s['months'])

print(f'PYTHON CHECKS: {len(PASS)} passed')

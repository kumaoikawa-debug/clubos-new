# -*- coding: utf-8 -*-
"""OBS-01 专项回归：平台 PATCH 签收统一到 _complete_receipt（2026-11-16）。

改动前 PATCH delivered 自己写了 status / delivered_at / freeze_order / 履约表对齐；
改动后只更新随单附带字段，签收交给 _complete_receipt。本套件验证：
  幂等（已 delivered 再 PATCH 不重复冻结）、首次签收仍冻结佣金、附带字段更新不丢、
  非法状态与不存在资源仍被 4xx 拦住、shipped 分支不受影响、
  C 端 confirm-receipt 与 DEF-02 的 POST /delivered 仍可用。

环境纪律：/tmp 隔离库，复用经营者 E2E 造数；不 push / 发布 / 部署。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = Path('/tmp/clubos_obs01.db')
if DB.exists():
    DB.unlink()
src_path = ROOT / 'tests/testmgr_platform_operator_e2e.py'
src = src_path.read_text(encoding='utf-8')
needle = "os.environ['CLUBOS_DB_PATH'] = str(TMP / 'clubos.db')"
assert needle in src, 'E2E harness 结构变化，需同步本脚本'
src = src.replace(needle, "os.environ['CLUBOS_DB_PATH'] = %r" % str(DB))
ns = {'__name__': '__obs01_seed__', '__file__': str(src_path)}
try:
    exec(compile(src, str(src_path), 'exec'), ns)
except SystemExit as e:
    if e.code:
        raise
print('【前置】E2E 造数完成 | 失败项=%d' % len(ns['FAILS']), flush=True)
assert not ns['FAILS'], '前置 E2E 有失败，回归结果不可信'

import app  # noqa: E402

c = ns['c']
OID = int(ns.get('OID') or 1)
ROWS = []
FAILS = []


def check(name, cond, detail=''):
    ROWS.append((name, bool(cond)))
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'   | {detail}' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def ledger_count():
    with app.conn() as db:
        return int(db.execute('SELECT COUNT(*) FROM commission_ledger').fetchone()[0])


def order_row():
    with app.conn() as db:
        r = db.execute('SELECT status,delivered_at,tracking_no,after_sales_status,user_id '
                       'FROM gear_orders WHERE id=?', (OID,)).fetchone()
        return dict(r) if r else {}


def jbody(r):
    try:
        return r.json()
    except Exception:
        return {}


print('\n════════ OBS-01 签收入口统一 · 专项回归 ════════', flush=True)

# R1 已签收订单再 PATCH：必须幂等、不重复冻结
n0 = ledger_count()
r = c.patch(f'/api/platform/orders/{OID}', json={'status': 'delivered'})
j = jbody(r)
check('R1 已签收订单再 PATCH delivered → 200 且幂等',
      r.status_code == 200 and j.get('ok') is True and j.get('idempotent') is True,
      f'status={r.status_code} body={str(j)[:150]}')
check('R1b 幂等不重复冻结佣金', ledger_count() == n0, f'ledger {n0} -> {ledger_count()}')
check('R1c 幂等不再返回 commission', j.get('commission') is None, str(j)[:120])

# R2 退回 shipped 后重新 PATCH：统一收口必须完成 status/delivered_at/冻结，且附带字段不丢
with app.conn() as db:
    db.execute("UPDATE gear_orders SET status='shipped',delivered_at=NULL WHERE id=?", (OID,))
r = c.patch(f'/api/platform/orders/{OID}',
            json={'status': 'delivered', 'tracking_no': 'QA-OBS01-TRACK', 'after_sales_status': 'none'})
j = jbody(r)
o = order_row()
check('R2 shipped 订单 PATCH delivered → 200', r.status_code == 200 and j.get('ok') is True,
      f'status={r.status_code} body={str(j)[:160]}')
check('R2b status 由统一收口写入 delivered', o.get('status') == 'delivered', str(o.get('status')))
check('R2c delivered_at 已写入', bool(o.get('delivered_at')), str(o.get('delivered_at'))[:30])
check('R2d 附带字段 tracking_no 仍生效', o.get('tracking_no') == 'QA-OBS01-TRACK', str(o.get('tracking_no')))
check('R2e 附带字段 after_sales_status 仍生效', o.get('after_sales_status') == 'none', str(o.get('after_sales_status')))
check('R2f 佣金冻结由 _complete_receipt 执行', bool(j.get('commission')), str(j.get('commission'))[:120])

# R3 再 PATCH 一次：幂等
n1 = ledger_count()
r = c.patch(f'/api/platform/orders/{OID}', json={'status': 'delivered'})
j = jbody(r)
check('R3 重复 PATCH delivered → idempotent', r.status_code == 200 and j.get('idempotent') is True, str(j)[:130])
check('R3b 重复 PATCH 不重复冻结', ledger_count() == n1, f'ledger {n1} -> {ledger_count()}')

# R4 / R5 护栏
r = c.patch(f'/api/platform/orders/{OID}', json={'status': 'refunded'})
check('R4 非法状态被 409 拦住', r.status_code == 409, f'status={r.status_code} {r.text[:90]}')
r = c.patch('/api/platform/orders/999999999', json={'status': 'delivered'})
check('R5 不存在订单 → 4xx 而非 500', 400 <= r.status_code < 500, f'status={r.status_code} {r.text[:90]}')
r = c.patch(f'/api/platform/orders/{OID}', json={'status': 'shipped', 'carrier': '顺丰', 'tracking_no': 'QA-SHIP'})
check('R6 shipped 分支未受改动影响（无 5xx）', r.status_code < 500, f'status={r.status_code} {r.text[:90]}')

# R7 / R8 相邻入口
uid = order_row().get('user_id')
r = c.post(f'/api/public/orders/{OID}/confirm-receipt', json={'userId': uid})
check('R7 C 端确认收货仍可用', r.status_code == 200 and jbody(r).get('ok') is True,
      f'status={r.status_code} {r.text[:110]}')
r = c.post(f'/api/platform/orders/{OID}/delivered')
check('R8 DEF-02 local 模拟签收仍可用', r.status_code == 200 and jbody(r).get('ok') is True,
      f'status={r.status_code} {r.text[:110]}')

# R9 静态：确认重复逻辑确已删除
seg = (ROOT / 'app.py').read_text(encoding='utf-8')
i = seg.find('def platform_update_order')
block = seg[i:i + 1600]
check('R9 PATCH 分支已调用 _complete_receipt', '_complete_receipt(c,order_id)' in block)
check('R9b PATCH 分支不再直接 freeze_order', 'freeze_order' not in block)

print('\n总计 %d 项 | 失败 %d 项' % (len(ROWS), len(FAILS)), flush=True)
for n in FAILS:
    print('  [FAIL] ' + n, flush=True)
sys.exit(1 if FAILS else 0)

# -*- coding: utf-8 -*-
"""经营者视角 · 总平台后台「全量验收」补充套件（2026-11-15）。

既有 testmgr_platform_operator_e2e.py 覆盖 A–G 共 68 项，验证的是「接口能不能用、
字段在不在」。本套件补经营者最关心但旧套件未覆盖的三件事：

  H 资金勾稽：钱算得对不对 —— 订单实付 → 佣金计提 → 可结算 → 结算单 → 打款 → 账本平衡，
              以及 dashboard / analytics / profit 三处 GMV 是否同源同值。
  I 状态机幂等：重复打款、无余额结算、越权结算等不能被绕过的资金闸门。
  J 结算数据契约：DEF-01 修复后，前端实际调用的 /commission-settlements 与
              结算单、账本三方金额是否一致（不只是「页面不报错」）。

实现说明：用 runpy 在同一进程内复用 E2E 的隔离库与造数，避免重复造单导致金额口径漂移。
环境纪律：全程 /tmp 临时库，不碰项目真库；不 push / 发布 / 部署。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 复用既有 E2E：拿到同一个 app / TestClient / 同一份隔离库与造数。
# 注意：既有脚本末尾有 sys.exit(0)，用 exec + namespace 执行并捕获 SystemExit，
# 否则 runpy 会直接终止本脚本；且不做 stdout 重定向（项目依赖 import 时会接管 stdout）。
print('【前置】运行既有经营者 E2E（A–G 68 项），随后在同一库上做资金勾稽 …', flush=True)
_src = ROOT / 'tests/testmgr_platform_operator_e2e.py'
ns = {'__name__': '__e2e_reuse__', '__file__': str(_src)}
try:
    exec(compile(_src.read_text(encoding='utf-8'), str(_src), 'exec'), ns)
except SystemExit as e:
    if e.code:
        raise
print('【前置】既有 E2E 完成', flush=True)
assert not ns['FAILS'], '前置 E2E 有失败，勾稽结果不可信：' + str(ns['FAILS'])

app = ns['app']
c = ns['c']
CLUB = ns['CLUB']
OID = ns['OID']
s1 = ns['s1']          # 结算前佣金分账
sj = ns['sj']          # E10 生成的结算单
sid = ns['sid']        # 结算单号

FAILS = []
FINDINGS = []
ROWS = []


def check(name, cond, detail='', sev='P1'):
    ROWS.append((name, bool(cond), sev, detail))
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'   | {detail}' if detail else ''))
    if not cond:
        FAILS.append((sev, name))


def finding(name, sev, detail):
    FINDINGS.append((sev, name, detail))
    print(f'  FIND  [{sev}] {name}   | {detail}')


def num(x):
    try:
        return round(float(x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


print('\n══════════ H 资金勾稽（钱算得对不对） ══════════')

with app.conn() as cc:
    order = app.row(cc.execute('SELECT * FROM gear_orders WHERE id=?', (OID,)))
    ledger = app.rows(cc.execute(
        "SELECT status,ledger_type,amount,settlement_id FROM commission_ledger WHERE club_id=?", (CLUB,)))
    settle_row = app.row(cc.execute('SELECT * FROM commission_settlements WHERE id=?', (sid,)))

# gear_orders 实付列是 cash_paid（total 为订单原价，无抵扣时两者同值）
paid = num(order.get('cash_paid') or order.get('total'))
check('H1 C端订单实付金额可读且为正', paid > 0,
      f"cash_paid={paid} total={num(order.get('total'))} club_commission={num(order.get('club_commission'))}")

rates = c.get('/api/platform/commission-rates').json()
rate = num(rates.get('productDefaultRate'))
# 费率可能是 0.08，也可能是 8（百分比），做归一化
rate_frac = rate / 100.0 if rate > 1 else rate
earn = num(sum(num(x['amount']) for x in ledger if x['ledger_type'] == 'earn' and num(x['amount']) > 0))
expected = round(paid * rate_frac, 2)
check('H2 佣金计提 = 实付 × 平台费率', abs(earn - expected) <= 0.02,
      f'实付={paid} 费率={rate_frac} 期望={expected} 实际入账={earn}')

# POST /settlements 返回 camelCase（netAmount/entryCount），DB 行是 snake_case，两者都兼容
avail_before = num(s1.get('available'))
net = num(sj.get('netAmount') or sj.get('net_amount'))
entry_count = int(sj.get('entryCount') or sj.get('entry_count') or 0)
check('H3 结算单净额 = 结算前可结算余额', abs(net - avail_before) <= 0.02,
      f'available_before={avail_before} net_amount={net}')
check('H4 结算单金额为正且有流水条数', net > 0 and entry_count > 0,
      f'net={net} entryCount={entry_count}')

# DEF-01 修复后的数据链路：前端实际用的端点
cs = c.get(f'/api/platform/commission-settlements?club_id={CLUB}')
check('H5 结算流水端点返回数组(DEF-01契约)', cs.status_code == 200 and isinstance(cs.json(), list),
      f'status={cs.status_code} type={type(cs.json()).__name__}')
cs_rows = cs.json() if isinstance(cs.json(), list) else []
match = next((x for x in cs_rows if str(x.get('id')) == str(sid)), None)
check('H6 结算流水含本单且与结算单同额', match is not None and abs(num(match.get('net_amount')) - net) <= 0.02,
      f"rows={len(cs_rows)} matched={bool(match)} net_in_list={num(match.get('net_amount')) if match else None}")
check('H7 结算流水回带脱敏收款账户', bool(match and '*' in str(match.get('payee') or '')),
      f"payee={str((match or {}).get('payee'))[:90]}")
check('H8 结算流水不泄露完整银行卡号',
      all('6222021234567890123' not in str(x.get('payee') or '') for x in cs_rows),
      f'rows={len(cs_rows)}')

# 账本状态机：结算单对应流水已转 settled
linked = [x for x in ledger if str(x.get('settlement_id')) == str(sid)]
check('H9 结算单锁定流水条数与 entryCount 一致',
      entry_count == len(linked),
      f'entryCount={entry_count} linked={len(linked)}')
check('H10 已打款流水全部为 settled',
      bool(linked) and all(x['status'] == 'settled' for x in linked),
      f"statuses={[x['status'] for x in linked]}")

summ_after = c.get(f'/api/platform/commission-summary?club_id={CLUB}').json()
sa = summ_after[0] if isinstance(summ_after, list) and summ_after else (summ_after if isinstance(summ_after, dict) else {})
check('H11 打款后可结算余额归零', num(sa.get('available')) <= 0.02,
      f"available_after={sa.get('available')}")
check('H12 打款后已结算金额 ≥ 结算单净额', num(sa.get('settled')) >= net - 0.02,
      f"settled_after={sa.get('settled')} net={net}")
check('H13 账本平衡：earn - reverse = pending+frozen+available+settled',
      abs((earn + num(sum(num(x['amount']) for x in ledger if x['ledger_type'] == 'refund_reverse'))) -
          num(sum(num(sa.get(k)) for k in ('pending', 'frozen', 'available', 'settled')))) <= 0.05,
      f"earn={earn} buckets={[num(sa.get(k)) for k in ('pending','frozen','available','settled')]}")

print('\n══════════ I 经营数据同源（三处 GMV 是否同一个数） ══════════')
dash = c.get('/api/platform/dashboard').json()
ana = c.get('/api/platform/analytics/commerce').json()
prof = c.get('/api/platform/profit/summary').json()
gmv_dash = num(dash.get('gearGMV'))
sales_ana = num(ana.get('grossSales'))
sales_prof = num(prof.get('netSales') or prof.get('grossSales'))
check('I1 dashboard GMV 有值', gmv_dash > 0, f'gearGMV={gmv_dash}')
check('I2 dashboard GMV = analytics 成交额', abs(gmv_dash - sales_ana) <= 0.02,
      f'{gmv_dash} vs {sales_ana}')
check('I3 dashboard GMV = 利润表销售额', abs(gmv_dash - sales_prof) <= 0.02,
      f'{gmv_dash} vs {sales_prof}')
check('I4 利润表勾稽：销售额 - 成本 = 毛利',
      abs((sales_prof - num(prof.get('cogs'))) - num(prof.get('grossProfit'))) <= 0.05,
      f"sales={sales_prof} cogs={prof.get('cogs')} grossProfit={prof.get('grossProfit')}")

print('\n══════════ J 资金状态机与闸门 ══════════')
dup_payout = c.post(f'/api/platform/settlements/{sid}/payout', json={'paymentRef': 'E2E-DUP'})
dup_ok = dup_payout.status_code == 200 and dup_payout.json().get('idempotent') is True
check('J1 重复打款幂等收敛(200 idempotent 或明确拒绝)，不重复出款',
      dup_ok or dup_payout.status_code in (404, 409),
      f'status={dup_payout.status_code} {dup_payout.text[:100]}')
summ_dup = c.get(f'/api/platform/commission-summary?club_id={CLUB}').json()
sd = summ_dup[0] if isinstance(summ_dup, list) and summ_dup else (summ_dup if isinstance(summ_dup, dict) else {})
check('J2 重复打款后已结算金额不变', num(sd.get('settled')) == num(sa.get('settled')),
      f"{num(sa.get('settled'))} -> {num(sd.get('settled'))}")
empty_settle = c.post('/api/platform/settlements', json={'clubId': CLUB, 'paymentRef': 'E2E-EMPTY'})
check('J3 无可结算余额时禁止再生成结算单(409)', empty_settle.status_code == 409,
      f'status={empty_settle.status_code} {empty_settle.text[:100]}')
prev = c.get(f'/api/platform/settlements/preview?club_id={CLUB}').json()
check('J4 结算试算在无余额时 payableNow = 0', num(prev.get('payableNow')) <= 0.02,
      f"payableNow={prev.get('payableNow')} preview_keys={list(prev)[:8]}")
check('J5 结算试算回带收款账户存在性', 'account' in prev and prev['account'].get('exists') is True,
      f"account={str(prev.get('account'))[:100]}")

print('\n' + '=' * 62)
print('补充验收 %d 项 | 失败 %d 项 | 发现 %d 条' % (len(ROWS), len(FAILS), len(FINDINGS)))
for sev, name in FAILS:
    print('  [%s] %s' % (sev, name))

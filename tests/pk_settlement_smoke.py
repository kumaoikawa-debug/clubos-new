"""PEAKTRACE 销售结账单 · 结算资格回归（#640）。

财务口径的核心风险是「多付钱」：v1 把推送失败、还没发货、甚至退款的代发订单
也算进「应付 PEAKTRACE」。这里在临时库里造齐各种状态组合，直接调 _pk_build()
验证可结算资格、合计拆分、账单号稳定性与人民币大写，不依赖 HTTP 服务。

运行：python tests/pk_settlement_smoke.py（自带临时库，不碰项目库）
"""
import os
import sys
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix='pk_settle_smoke_')
os.environ['CLUBOS_DB_PATH'] = os.path.join(_tmp, 'test.db')
os.environ['CLUBOS_TEST_OPEN'] = '1'
os.environ['MOCK_AI'] = '0'
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')

import app  # noqa: E402  (import 时跑 init_db，必须在设置 DB_PATH 之后)
from db import conn  # noqa: E402

PASS = 0
FAIL = 0


def ck(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('PASS ', name)
    else:
        FAIL += 1
        print('FAIL ', name, detail)


def make_order(c, no, *, push='pushed', fulfill='delivered', shipped=True,
               order_status='shipped', refunded=False, settle='pending',
               price=100.0, qty=1, brand='PEAKTRACE', ratio=0.85):
    """造一笔外部代发订单，返回 order id。金额真源始终在 gear_order_items。"""
    r = c.execute('SELECT id FROM clubs LIMIT 1').fetchone()
    club_id = r['id']
    r = c.execute('SELECT id FROM users LIMIT 1').fetchone()
    user_id = r['id']
    r = c.execute('SELECT id FROM product_brands WHERE name=?', (brand,)).fetchone()
    if not r:
        c.execute("""INSERT INTO product_brands(name,source_type,settlement_mode,settlement_ratio)
                     VALUES(?,?,?,?)""", (brand, 'external_api', 'sales_ratio', ratio))
        bid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    else:
        bid = r['id']
    c.execute("""INSERT INTO products(name,sku,price,stock,source,inventory_mode,
                   fulfillment_mode,brand_id) VALUES(?,?,?,?,?,?,?,?)""",
              ('结算测试商品%d' % no, 'PK-SKU-%d' % no, price, 50,
               'external_api', 'external_api', 'supplier_fulfillment', bid))
    pid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute("""INSERT INTO gear_orders(id,user_id,source_club_id,total,status,created_at,
                   shipped_at,refunded_at,tracking_no,carrier)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (no, user_id, club_id, price * qty, order_status,
               '2026-11-06 10:00:00',
               '2026-11-06 12:00:00' if shipped else None,
               '2026-11-06 15:00:00' if refunded else None,
               'SF%d' % no if push == 'pushed' else None, '顺丰'))
    c.execute("""INSERT INTO gear_order_items(order_id,product_id,quantity,unit_price,
                   inventory_mode,fulfillment_mode)
                 VALUES(?,?,?,?,?,?)""",
              (no, pid, qty, price, 'external_api', 'supplier_fulfillment'))
    c.execute("""INSERT INTO external_fulfillment_orders
                 (order_id,brand_id,external_order_id,push_status,fulfill_status,
                  pushed_at,fulfilled_at,payload_json,external_ref,settle_status,
                  created_at,updated_at,tracking_no,carrier)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (no, bid, 'B2B-%d' % no if push == 'pushed' else None,
               push, fulfill,
               '2026-11-06 11:00:00' if push == 'pushed' else None,
               '2026-11-06 18:00:00' if fulfill == 'delivered' else None,
               json.dumps({'items': [{'sku': 'EXT-%d' % no,
                                      'name': '结算测试商品%d' % no,
                                      'quantity': qty}]}, ensure_ascii=False),
               'CB-%d-TEST' % no, settle,
               '2026-11-06 10:00:00', '2026-11-06 18:00:00',
               'SF%d' % no if push == 'pushed' else None, '顺丰'))


with conn() as c:
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('结算测试俱乐部','active','pro')")
    c.execute("INSERT INTO users(name,phone) VALUES('结算测试用户','13900009999')")
    make_order(c, 1, price=100, qty=2)                                  # 唯一正常可结算
    make_order(c, 2, push='failed', shipped=False, price=300)           # 推送失败
    make_order(c, 3, fulfill='shipped', price=400)                      # 未妥投
    make_order(c, 4, shipped=False, price=500)                           # 串单指纹
    make_order(c, 5, order_status='refunded', refunded=True, price=600)  # 已退款
    make_order(c, 6, settle='void', price=700)                           # 已冲销
    make_order(c, 7, settle='settled', price=800)                        # 已结算（仍具备资格）

d = app._pk_build({})
t = d['totals']
by_id = {r['orderId']: r for r in d['orders']}

print('--- 结算资格 ---')
ck('7 笔全量', t['count'] == 7, t['count'])
ck('仅 #1/#7 可结算',
   sorted(x['orderId'] for x in d['orders'] if x['billable']) == [1, 7],
   [x['orderId'] for x in d['orders'] if x['billable']])
ck('#2 推送失败被排除', not by_id[2]['billable'] and '未成功推送供应商' in by_id[2]['blockedReasons'])
ck('#3 未妥投被排除', not by_id[3]['billable'] and '上游未妥投' in by_id[3]['blockedReasons'])
ck('#4 妥投无发货时间被排除（串单指纹）', not by_id[4]['billable'] and
   any('串单' in a for a in by_id[4]['anomalies']), by_id[4]['anomalies'])
ck('#5 已退款被排除', not by_id[5]['billable'] and '主订单已退款/售后' in by_id[5]['blockedReasons'])
ck('#6 已冲销被排除', not by_id[6]['billable'] and '已冲销' in by_id[6]['blockedReasons'])
ck('#7 已结算仍具备资格', by_id[7]['billable'])

print('--- 金额与口径 ---')
ck('外部零售小计 = 100*2*0.85 按比例算', abs(by_id[1]['payable'] - 170.0) < 1e-6, by_id[1]['payable'])
ck('品牌专属比例生效', by_id[1]['settlementRate'] == 0.85 and by_id[1]['settlementRateSource'] == 'brand')
ck('流水应付合计 = 各行 sum', abs(t['payable'] - round(sum(r['payable'] for r in d['orders']), 2)) < 1e-6)
ck('本期可结算(未结) = #1', abs(t['pendingPayable'] - 170.0) < 1e-6, t['pendingPayable'])
ck('累计已结算 = #7', abs(t['settledPayable'] - 680.0) < 1e-6, t['settledPayable'])
ck('已冲销 = #6', abs(t['voidPayable'] - 595.0) < 1e-6, t['voidPayable'])
ck('金额大写出中文', app._rmb_upper(170.0) == '壹佰柒拾元整', app._rmb_upper(170.0))
ck('金额大写带角分', app._rmb_upper(1234.56) == '壹仟贰佰叁拾肆元伍角陆分', app._rmb_upper(1234.56))
ck('金额大写跨万补零', app._rmb_upper(10001) == '壹万零壹元整', app._rmb_upper(10001))

print('--- 筛选 / 分组 / 账单号 ---')
ck('billable=1 只剩 2 笔', app._pk_build({'billable': '1'})['totals']['count'] == 2)
ck('结算状态 settled 只剩 1 笔', app._pk_build({'settle_status': 'settled'})['totals']['count'] == 1)
ck('品牌筛选生效', app._pk_build({'brand': 'PEAKTRACE'})['totals']['count'] == 7)
ck('非法筛选值回落全量', app._pk_build({'push_status': 'hack'})['totals']['count'] == 7)
g = app._pk_build({'group_by': 'settle_status'})
ck('按结算状态 3 组', len(g['groups']) == 3, g['groups'])
ck('分组应付合计守恒',
   abs(round(sum(x['payable'] for x in g['groups']), 2) - t['payable']) < 1e-6)
ck('切分组不换账单号',
   d['statement']['no'] == app._pk_build({'group_by': 'brand'})['statement']['no'])
ck('换账期换账单号',
   d['statement']['no'] != app._pk_build({'date_from': '2026-01-01',
                                          'date_to': '2026-01-31'})['statement']['no'])

print('\n%d PASS / %d FAIL' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

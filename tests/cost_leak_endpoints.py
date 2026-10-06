"""成本数据泄漏的接口级回归（会 import app → 用独立临时库，不碰开发数据）。

用户铁律（2026-10-06）：方案里的成本/报价数据，任何前端都不得显示。
本测试模拟一条「老活动」——master 里带完整成本表、activities.price 与团期 price
都是成本推导出来的内部单价（=合计÷人数），然后打真实的公开接口，断言：
  1. 列表/详情 JSON 里零成本 token；
  2. 成本键（人均费用/合计（未含税））被清、费用包含等公开项保留；
  3. 顶层 price 与每个团期 price 都归零并打 priceFrom='pending'；
  4. 俱乐部重新定价（priceFrom='priced'）后价格正常透出 —— 归零不是粘性的。

跑法：/Users/jckuma/.workbuddy/binaries/python/envs/default/bin/python tests/cost_leak_endpoints.py
"""

import os, sys, json
from pathlib import Path
os.environ['CLUBOS_DB_PATH'] = '/tmp/clubos_cost_itest.db'
os.environ['IS_PROD'] = '0'
for p in ('/tmp/clubos_cost_itest.db',):
    try: os.remove(p)
    except FileNotFoundError: pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app
from app import conn, jdump
from fastapi.testclient import TestClient

# 种子：一份「老活动」—— master 里带着完整成本表，activities.price 与团期 price 都是成本价 3806.55
master = {
    "title": "新都桥鱼子西高客", "price": 3806.55, "date": "2026-11-01", "location": "新都桥",
    "fees": {"人均费用": "¥3,806.55", "合计（未含税）": "¥76,131.00", "按人数报价": "20人",
             "备注": "成本预估，未含税", "费用包含": "车费、门票、氧气、摄影"},
    "itinerary": [{"time": "D1", "text": "新都桥"}], "media": [],
}
detail = {"blocks": [{"type": "facts", "items": [
    {"label": "人均费用", "value": "¥3,806.55"},
    {"label": "主题", "value": "高客定制"}]}]}
src = {"text": "14 — COST 活动费用明细\n按 20 人报价 · 成本预估\n人均费用 ¥ 3,806.55\n合计（未含税）¥76,131.00"}

with conn() as c:
    row = c.execute("SELECT MAX(id) FROM clubs").fetchone()
    cid = (row[0] or 0) + 1001
    row = c.execute("SELECT MAX(id) FROM activities").fetchone()
    aid = (row[0] or 0) + 1001
    c.execute("INSERT INTO clubs(id,name,status) VALUES(?,?,?)", (cid, '测试俱乐部', 'active'))
    c.execute("INSERT INTO activities(id,club_id,title,status,event_date,location,price,capacity,"
              "activity_master_json,detail_json,source_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (aid, cid, '新都桥鱼子西', 'published', '2026-11-01', '新都桥', 3806.55, 20,
               jdump(master), jdump(detail), jdump(src)))
    c.execute("INSERT INTO activity_occurrences(id,activity_id,club_id,start_at,status,price,capacity,label)"
              " VALUES(?,?,?,?,?,?,?,?)", (aid, aid, cid, '2026-11-01', 'open', 3806.55, 20, '首发'))

CLUB_ID, ACT_ID = cid, aid

client = TestClient(app.app)
LEAK = ['3,806.55', '3806.55', '76,131', '76131', '6,921', '未含税', '单价', '小计', '人均费用', '策划执行', '毛利']

print("=== 列表接口 /api/public/clubs/%d/activities ===" % CLUB_ID)
lst = client.get('/api/public/clubs/%d/activities' % CLUB_ID).json()
assert len(lst) == 1, lst
it = lst[0]
assert it['price'] == 0, it['price']
assert it.get('priceFrom') == 'pending', it
blob = json.dumps(it, ensure_ascii=False)
for tok in LEAK:
    assert tok not in blob, f'列表漏出成本 token: {tok}'
print('  PASS  列表 price=0 priceFrom=pending 且无成本 token')

print("=== 详情接口 /api/public/activities/%d ===" % ACT_ID)
det = client.get('/api/public/activities/%d' % ACT_ID).json()
assert det['price'] == 0 and det.get('priceFrom') == 'pending', det.get('price')
blob = json.dumps(det, ensure_ascii=False)
for tok in LEAK:
    assert tok not in blob, f'详情漏出成本 token: {tok}'
fees = det['activityMaster'].get('fees') or {}
assert '人均费用' not in fees and '合计（未含税）' not in fees, fees
assert '费用包含' in fees, fees
assert det['occurrences'][0]['price'] == 0, det['occurrences']
print('  PASS  详情 price=0/priceFrom=pending, fees 成本键清除, 团期 price=0, 无成本 token')
print('  PASS  facts 人均费用行已在后端清洗（前端不再可见）')

# 公开价回归：俱乐部重新定价（活动价 + 团期价一起改成对外价，并打 priceFrom='priced'）→ 应正常透出
with conn() as c:
    c.execute("UPDATE activities SET price=5200 WHERE id=?", (ACT_ID,))
    c.execute("UPDATE activity_occurrences SET price=5200 WHERE activity_id=?", (ACT_ID,))
    m2 = json.loads(c.execute("SELECT activity_master_json FROM activities WHERE id=?", (ACT_ID,)).fetchone()[0])
    m2['priceFrom'] = 'priced'; m2['price'] = 5200
    c.execute("UPDATE activities SET activity_master_json=? WHERE id=?", (jdump(m2), ACT_ID))
det2 = client.get('/api/public/activities/%d' % ACT_ID).json()
assert det2['price'] == 5200, det2['price']
assert det2.get('priceFrom') != 'pending', det2.get('priceFrom')
assert det2['activityMaster']['price'] == 5200, det2['activityMaster']['price']
assert det2['occurrences'][0]['price'] == 5200, det2['occurrences'][0]
blob2 = json.dumps(det2, ensure_ascii=False)
for tok in LEAK:
    assert tok not in blob2, f'定价后仍漏出成本 token: {tok}'
print('  PASS  俱乐部定价(priceFrom=priced)后，活动价/团期价/master 价均正常透出 5200（非粘性归零）')
print('  PASS  定价后成本 token 依然为 0 —— 清洗与定价互不干扰')

print('\nBACKEND INTEGRATION CHECKS PASSED')

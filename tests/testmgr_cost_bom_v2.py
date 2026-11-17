"""成本 BOM 分类/计价改造端到端验证：
A. API 层：成本项 名称+归入(category)+计价方式(calcType)+数值 的保存/回读/兼容旧字段。
B. 计算层：按件/每单/百分比 三种口径分别计到 物流/包材/其他 三桶，order_profit 勾稽。
C. UI 层（Playwright）：表格含 归入/计价方式 下拉，能设置并保存回读；添加有可见反馈。
D. 静态断言：前端不再写死 category='other'/per_item；版本戳已 bump。
"""
import json
import re
import sqlite3
from pathlib import Path
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:8262'
DB = Path('/tmp/clubos_full_ui.db')
ROOT = Path(__file__).resolve().parents[1]
PASSED, FAILED = [], []


def rec(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(('PASS ' if ok else 'FAIL ') + name + ' | ' + str(detail)[:180], flush=True)


def api(path, payload=None, method=None):
    if payload is None and method is None:
        with urlopen(BASE + path, timeout=10) as f:
            return json.loads(f.read().decode())
    req = Request(BASE + path, data=json.dumps(payload or {}).encode(),
                  headers={'Content-Type': 'application/json'}, method=method or 'POST')
    try:
        with urlopen(req, timeout=10) as f:
            return json.loads(f.read().decode())
    except Exception as e:
        try:
            body = e.read().decode()
        except Exception:
            body = ''
        raise RuntimeError(f'{method or "POST"} {path} -> {getattr(e, "code", e)} {body[:200]}') from e


# ── A. API：分类 + 计价方式字段 ───────────────────────────────────────────
r = api('/api/platform/cost-bom', {
    'name': '分类计价清单', 'note': '三类三方式', 'enabled': True,
    'lines': [
        {'label': '顺丰陆运', 'category': 'logistics', 'calcType': 'per_item', 'baseValue': 6.5, 'rateValue': 0, 'enabled': 1},
        {'label': '纸箱', 'category': 'packaging', 'calcType': 'fixed', 'baseValue': 3, 'rateValue': 0, 'enabled': 1},
        {'label': '平台服务费', 'category': 'other', 'calcType': 'percent_net', 'baseValue': 0, 'rateValue': 0.02, 'enabled': 1},
        {'label': '停用项', 'category': 'other', 'calcType': 'per_item', 'baseValue': 9, 'enabled': 0},
    ]}, 'POST')
lines = r.get('lines', [])
rec('A1 分类/计价字段回读一致',
    len(lines) == 4
    and [l['category'] for l in lines] == ['logistics', 'packaging', 'other', 'other']
    and [l['calcType'] for l in lines] == ['per_item', 'fixed', 'percent_net', 'per_item']
    and [float(l['rateValue']) for l in lines][2] == 0.02,
    json.dumps([{k: l[k] for k in ('label', 'category', 'calcType', 'baseValue', 'rateValue')} for l in lines], ensure_ascii=False)[:220])
rec('A2 启用状态回读', r.get('isActive') == 1 and r.get('name') == '分类计价清单')

g = api('/api/platform/cost-bom')
rec('A3 GET 回读一致', [l['label'] for l in g['lines']] == ['顺丰陆运', '纸箱', '平台服务费', '停用项'])

# 兼容最简字段（只 label/baseValue）仍落为 other/per_item，老数据不炸
r2 = api('/api/platform/cost-bom', {
    'name': '兼容清单', 'note': '', 'enabled': True,
    'lines': [{'label': '老式项', 'baseValue': 5, 'enabled': 1}]}, 'POST')
g2 = api('/api/platform/cost-bom')
rec('A4 最简字段兼容（默认 other/per_item）',
    len(g2['lines']) == 1 and g2['lines'][0]['category'] == 'other' and g2['lines'][0]['calcType'] == 'per_item')

# ── B. 计算层：三桶分别勾稽 ───────────────────────────────────────────────
api('/api/platform/cost-bom', {
    'name': '三桶勾稽', 'note': '', 'enabled': True,
    'lines': [
        {'label': '运费', 'category': 'logistics', 'calcType': 'per_item', 'baseValue': 2, 'enabled': 1},
        {'label': '包材', 'category': 'packaging', 'calcType': 'fixed', 'baseValue': 5, 'enabled': 1},
        {'label': '服务费', 'category': 'other', 'calcType': 'percent_net', 'baseValue': 0, 'rateValue': 0.1, 'enabled': 1},
        {'label': '成本提成', 'category': 'other', 'calcType': 'percent_cogs', 'baseValue': 0, 'rateValue': 0.05, 'enabled': 1},
    ]}, 'POST')
with sqlite3.connect(str(DB)) as db:
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT id FROM gear_orders ORDER BY id LIMIT 1").fetchone()
    order_id = int(row['id'])
    qty = int(db.execute("SELECT COALESCE(SUM(quantity),0) FROM gear_order_items WHERE order_id=?", (order_id,)).fetchone()[0] or 0)
    db.execute("UPDATE gear_orders SET cost_bom_override=0 WHERE id=?", (order_id,))

prof_all = api('/api/platform/profit/orders')
prof = next((o for o in prof_all if int(o.get('orderId') or 0) == order_id), None)
assert prof is not None, f'profit/orders 中找不到订单 {order_id}'
net = float(prof.get('netSales') or 0)
cogs = float(prof.get('cogs') or 0)
exp_ship = round(2 * qty, 2)
exp_pack = 5.0
exp_other = round(net * 0.1 + cogs * 0.05, 2)
rec('B1 物流桶 = 按件 2×件数', abs(float(prof.get('shippingCost') or 0) - exp_ship) < 0.01,
    f'期望 {exp_ship}（2×{qty}），实得 {prof.get("shippingCost")}')
rec('B2 包材桶 = 每单固定 5', abs(float(prof.get('packagingCost') or 0) - exp_pack) < 0.01,
    f'期望 {exp_pack}，实得 {prof.get("packagingCost")}')
rec('B3 其他桶 = 净销售10% + 成本5%', abs(float(prof.get('otherCost') or 0) - exp_other) < 0.01,
    f'期望 {exp_other}（{net}×10% + {cogs}×5%），实得 {prof.get("otherCost")}')
rec('B4 成本来源 = BOM', prof.get('costSource') == 'BOM', str(prof.get('costSource')))
bl = prof.get('bomLines') or []
rec('B5 bomLines 三类/四方式齐全',
    {b.get('category') for b in bl} == {'logistics', 'packaging', 'other'}
    and {b.get('calcType') for b in bl} == {'per_item', 'fixed', 'percent_net', 'percent_cogs'},
    json.dumps(bl, ensure_ascii=False)[:200])
rec('B6 bomLines 合计 = 三桶之和',
    abs(round(sum(float(b.get('amount') or 0) for b in bl), 2)
        - round(float(prof.get('shippingCost') or 0) + float(prof.get('packagingCost') or 0) + float(prof.get('otherCost') or 0), 2)) < 0.01)

prev = api('/api/platform/cost-bom/preview')
rec('B7 预览端点含三桶字段',
    prev.get('orderId') is not None and all(k in prev for k in ('shipping', 'packaging', 'otherCost', 'bomLines')),
    json.dumps({k: prev.get(k) for k in ('orderId', 'shipping', 'packaging', 'otherCost', 'costSource')}, ensure_ascii=False))

# 恢复默认 BOM（按件、其他），避免污染后续手工验证
api('/api/platform/cost-bom', {
    'name': '默认成本清单', 'note': '', 'enabled': True,
    'lines': [{'label': '运费', 'category': 'logistics', 'calcType': 'per_item', 'baseValue': 6.5, 'enabled': 1},
              {'label': '胶带', 'category': 'packaging', 'calcType': 'per_item', 'baseValue': 1.2, 'enabled': 1}]}, 'POST')

# ── D. 静态断言：前端不再写死 ──────────────────────────────────────────────
js = (ROOT / 'static/platform/platform.js').read_text()
bom_seg = js[js.index('成本 BOM'):js.index('员工权限分配管理')]
rec('D1 UI 不再写死 category=other', "category:'other'" not in bom_seg)
rec('D2 UI 不再写死 calcType=per_item（提交处按行取 l.calc）', "calcType:'per_item'" not in bom_seg)
rec('D3 含归入/计价方式下拉定义', 'BOM_CATS' in bom_seg and 'BOM_CALCS' in bom_seg and 'bomLineCalc' in bom_seg)
rec('D4 表头含 归入/计价方式', '<th>归入</th>' in bom_seg and '<th>计价方式</th>' in bom_seg)
rec('D5 清单名称说明已加（不决定分类）', '不决定成本归哪一类' in bom_seg)
rec('D6 百分比按 rateValue/100 提交', "Number(l.value||0)/100" in bom_seg)
html = (ROOT / 'static/platform/index.html').read_text()
rec('D7 缓存版本已 bump', bool(re.search(r'platform\.js\?v=20\d{6}[a-z0-9]+', html)))

# ── C. UI 层（Playwright）────────────────────────────────────────────────
from playwright.sync_api import sync_playwright

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
errors, failures = [], []

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME)
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('response', lambda r: failures.append(f'{r.status} {r.url}') if r.status >= 400 and '/api/' in r.url else None)
    page.goto(BASE + '/platform', wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(1800)
    page.click('.nav-leaf[data-view="costbom"]')
    page.wait_for_timeout(1200)

    body = page.evaluate("()=>document.querySelector('section#costbom')?.innerText||''")
    rec('C1 新表头（归入/计价方式）', '归入' in body and '计价方式' in body, body[:200].replace('\n', ' '))
    rec('C2 清单名称说明已显示', '由下面每一项的「归入」决定' in body, 'ok' if '由下面每一项的「归入」决定' in body else body[:160])

    heads = page.evaluate("()=>[...document.querySelectorAll('#bomLinesBody tr[data-i]')].map(r=>[...r.querySelectorAll('select')].map(s=>s.value))")
    rec('C3 每行有归入/计价方式两个下拉', all(len(x) == 2 and x[0] in ('logistics', 'packaging', 'other') and x[1] in ('per_item', 'fixed', 'percent_net', 'percent_cogs') for x in heads), json.dumps(heads, ensure_ascii=False)[:200])

    n0 = page.evaluate("()=>document.querySelectorAll('#bomLinesBody tr[data-i]').length")
    page.click('button:has-text("添加成本项")')
    page.wait_for_timeout(900)
    n1 = page.evaluate("()=>document.querySelectorAll('#bomLinesBody tr[data-i]').length")
    rec('C4 点击添加后行数 +1', n1 == n0 + 1, f'{n0} -> {n1}')
    focused = page.evaluate("""()=>{const rows=document.querySelectorAll('#bomLinesBody tr[data-i]');
        const last=rows[rows.length-1];const inp=last&&last.querySelector('input.plat-bom-in');
        return !!inp && document.activeElement===inp}""")
    rec('C5 新行名称输入框已聚焦', focused)

    # 把新行设成：物流 + 每单固定 + 8.8，保存并回读
    page.evaluate("""()=>{const rows=document.querySelectorAll('#bomLinesBody tr[data-i]');
        const last=rows[rows.length-1];
        last.querySelector('input.plat-bom-in').value='测试运费';
        last.querySelector('input.plat-bom-in').dispatchEvent(new Event('input',{bubbles:true}));
        const sels=last.querySelectorAll('select');
        sels[0].value='logistics';sels[0].dispatchEvent(new Event('change',{bubbles:true}));
        sels[1].value='fixed';sels[1].dispatchEvent(new Event('change',{bubbles:true}));}""")
    page.wait_for_timeout(300)
    # 下拉 change 会重渲染，重新取行填数值
    page.evaluate("""()=>{const rows=document.querySelectorAll('#bomLinesBody tr[data-i]');
        const last=rows[rows.length-1];const num=last.querySelector('input[type=number]');
        num.value='8.8';num.dispatchEvent(new Event('input',{bubbles:true}));}""")
    page.click('button:has-text("保存成本清单")')
    page.wait_for_timeout(1200)
    saved = api('/api/platform/cost-bom')
    last_line = (saved.get('lines') or [{}])[-1] if saved.get('lines') else {}
    ok_save = (last_line.get('label') == '测试运费' and last_line.get('category') == 'logistics'
               and last_line.get('calcType') == 'fixed' and float(last_line.get('baseValue') or 0) == 8.8)
    rec('C6 UI 保存回读（名称/物流/每单固定/数值）', ok_save, json.dumps(last_line, ensure_ascii=False)[:200])

    # 数值单位随计价方式切换：切到净销售额% 应显示 %
    page.evaluate("""()=>{const rows=document.querySelectorAll('#bomLinesBody tr[data-i]');
        const last=rows[rows.length-1];const sels=last.querySelectorAll('select');
        sels[1].value='percent_net';sels[1].dispatchEvent(new Event('change',{bubbles:true}));}""")
    page.wait_for_timeout(300)
    unit = page.evaluate("""()=>{const rows=document.querySelectorAll('#bomLinesBody tr[data-i]');
        const last=rows[rows.length-1];const em=last.querySelector('.plat-bom-unit');return em?em.textContent.trim():''}""")
    rec('C7 计价方式切到百分比时单位显示 %', unit == '%', f'unit={unit}')

    rec('C8 无页面 JS 错误', not errors, '; '.join(errors[:3]))
    rec('C9 无 API 4xx/5xx', not failures, '; '.join(failures[:3]))
    browser.close()

print(f"\n==== {len(PASSED)} passed, {len(FAILED)} failed ====")
if FAILED:
    print('FAILED:', json.dumps(FAILED, ensure_ascii=False))
    raise SystemExit(1)

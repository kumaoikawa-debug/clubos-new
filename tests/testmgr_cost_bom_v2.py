"""成本 BOM V2（#649）端到端验证：
A. API 层：开放式自定义成本项（名称+单价）保存 / 回读 / 兼容旧字段 / 启用校验。
B. 计算层：单价 × 订单件数 勾稽（order_profit 的 otherCost / bomLines）。
C. UI 层（Playwright）：新 UI 只有 成本名称/单价/启用 列；添加成本项有可见反馈
   （滚动到新行 + 聚焦名称输入框）；保存后回读一致。
D. 静态断言：基础值/比例%/净销售%/成本%/每单固定 等旧概念不再出现在成本 BOM UI。
"""
import json
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


# ── A. API：开放式自定义成本项 ────────────────────────────────────────────
r = api('/api/platform/cost-bom', {
    'name': 'V2开放式清单', 'note': '只有名称+单价', 'enabled': True,
    'lines': [
        {'label': '顺丰陆运', 'baseValue': 6.5, 'enabled': 1},
        {'label': '胶带', 'baseValue': 1.2, 'enabled': 1},
        {'label': '停用项', 'baseValue': 9, 'enabled': 0},
    ]}, 'POST')
lines = r.get('lines', [])
rec('A1 保存开放式成本项（只传 label/baseValue/enabled）',
    len(lines) == 3 and all(l['calcType'] == 'per_item' and l['category'] == 'other' for l in lines),
    json.dumps(lines, ensure_ascii=False)[:150])
rec('A2 启用状态回读', r.get('isActive') == 1 and r.get('name') == 'V2开放式清单')

g = api('/api/platform/cost-bom')
rec('A3 GET 回读一致（名称/单价/启用）',
    [l['label'] for l in g['lines']] == ['顺丰陆运', '胶带', '停用项']
    and [float(l['baseValue']) for l in g['lines']] == [6.5, 1.2, 9.0]
    and [l['enabled'] for l in g['lines']] == [1, 1, 0])

# 兼容旧字段提交（calcType=fixed 等）不报错、原样落库
r2 = api('/api/platform/cost-bom', {
    'name': 'V2开放式清单', 'note': '', 'enabled': True,
    'lines': [{'label': '旧固定项', 'category': 'logistics', 'calcType': 'fixed', 'baseValue': 5, 'rateValue': 0, 'enabled': 1},
              {'label': '旧比例项', 'calcType': 'percent_net', 'baseValue': 0, 'rateValue': 0.02, 'enabled': 1}]}, 'POST')
g2 = api('/api/platform/cost-bom')
rec('A4 旧字段兼容（fixed/percent_net 原样保存）',
    {l['calcType'] for l in g2['lines']} == {'fixed', 'percent_net'})

# 恢复为 V2 格式
api('/api/platform/cost-bom', {
    'name': 'V2开放式清单', 'note': '', 'enabled': True,
    'lines': [{'label': '顺丰陆运', 'baseValue': 6.5, 'enabled': 1},
              {'label': '胶带', 'baseValue': 1.2, 'enabled': 1}]}, 'POST')

# ── B. 计算层：单价 × 件数 勾稽 ───────────────────────────────────────────
with sqlite3.connect(str(DB)) as db:
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT id, status FROM gear_orders ORDER BY id LIMIT 1").fetchone()
    if row is None:
        rec('B1 存在可验证订单', False, '库中无订单，跳过计算验证（手工造数）')
    order_id = int(row['id'])
    qty = int(db.execute("SELECT COALESCE(SUM(quantity),0) FROM gear_order_items WHERE order_id=?", (order_id,)).fetchone()[0] or 0)
    ov = db.execute("SELECT cost_bom_override FROM gear_orders WHERE id=?", (order_id,)).fetchone()
    if ov is not None and int(ov[0] or 0) == 1:
        db.execute("UPDATE gear_orders SET cost_bom_override=0 WHERE id=?", (order_id,))
    rec('B0 订单与件数', True, f'订单 #{order_id} 件数={qty} (override={ov and ov[0]})')

prof_all = api('/api/platform/profit/orders')
prof = next((o for o in prof_all if int(o.get('orderId') or 0) == order_id), None)
assert prof is not None, f'profit/orders 中找不到订单 {order_id}'
expect = round(6.5 * qty + 1.2 * qty, 2)
got = float(prof.get('otherCost') or 0)
rec('B1 BOM启用后 otherCost = Σ单价×件数', abs(got - expect) < 0.01,
    f'期望 {expect}（6.5×{qty}+1.2×{qty}），实得 {got}，source={prof.get("costSource")}')
bsum = round(sum(float(b.get('amount') or 0) for b in prof.get('bomLines') or []), 2)
rec('B2 bomLines 明细合计 = otherCost', abs(bsum - got) < 0.01, f'bomLines合计={bsum}')
rec('B3 成本来源 = BOM', prof.get('costSource') == 'BOM', str(prof.get('costSource')))

prev = api('/api/platform/cost-bom/preview')
rec('B4 预览端点返回 otherCost 合计', prev.get('orderId') is not None and 'otherCost' in prev,
    json.dumps({k: prev.get(k) for k in ('orderId', 'otherCost', 'costSource')}, ensure_ascii=False))

# ── D. 静态断言：旧概念清零 ────────────────────────────────────────────────
js = (ROOT / 'static/platform/platform.js').read_text()
bom_seg = js[js.index('成本 BOM'):js.index('员工权限分配管理')]
for kw in ('净销售%', '成本%', '每单固定', '基础值', '比例', "calcType:l.calcType", "'packaging'"):
    rec(f'D1 旧概念「{kw}」已从成本 BOM UI 移除', kw not in bom_seg)
rec('D2 新 UI 含 单价（元/件） 列', '单价（元/件）' in bom_seg)
rec('D3 添加动作有滚动反馈（scrollIntoView+focus）',
    'scrollIntoView' in bom_seg and 'inp.focus()' in bom_seg)
html = (ROOT / 'static/platform/index.html').read_text()
rec('D4 缓存版本已 bump', 'platform.js?v=20261116b' in html)

# ── C. UI 层（Playwright）────────────────────────────────────────────────
from playwright.sync_api import sync_playwright

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
errors, failures = [], []

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME)
    ctx = browser.new_context(viewport={'width': 1440, 'height': 620})  # 故意压低视口，复现「新行落在视口外」
    page = ctx.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('response', lambda r: failures.append(f'{r.status} {r.url}') if r.status >= 400 and '/api/' in r.url else None)
    page.goto(BASE + '/platform', wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(1800)
    page.click('.nav-leaf[data-view="costbom"]')
    page.wait_for_timeout(1200)

    body = page.evaluate("()=>document.querySelector('section#costbom')?.innerText||''")
    rec('C1 新表头（成本名称/单价/启用）', '成本名称' in body and '单价（元/件）' in body, body[:200].replace('\n', ' '))
    rec('C2 旧列已消失（类别/计算方式/基础值/比例%）',
        all(k not in body for k in ('计算方式', '基础值', '比例%', '净销售', '成本%')), body[:200].replace('\n', ' '))

    n0 = page.evaluate("()=>document.querySelectorAll('#bomLinesBody tr[data-i]').length")
    page.evaluate("""()=>{const b=document.querySelector('#bomLinesBody');
        if(b)b.scrollIntoView({block:'start'})}""")
    page.wait_for_timeout(300)
    # 视口只露出表格头部，新行添加前不可见
    before_visible = page.evaluate("""()=>{const rows=document.querySelectorAll('#bomLinesBody tr[data-i]');
        if(!rows.length)return false;const r=rows[rows.length-1].getBoundingClientRect();
        return r.top>=0 && r.bottom<=window.innerHeight}""")
    page.click('button:has-text("添加成本项")')
    page.wait_for_timeout(900)
    n1 = page.evaluate("()=>document.querySelectorAll('#bomLinesBody tr[data-i]').length")
    rec('C3 点击添加后行数 +1', n1 == n0 + 1, f'{n0} -> {n1}')
    focused = page.evaluate("""()=>{const rows=document.querySelectorAll('#bomLinesBody tr[data-i]');
        const last=rows[rows.length-1];const inp=last&&last.querySelector('input.plat-bom-in');
        return !!inp && document.activeElement===inp}""")
    rec('C4 新行名称输入框已聚焦', focused)
    vis = page.evaluate("""()=>{const rows=document.querySelectorAll('#bomLinesBody tr[data-i]');
        if(!rows.length)return false;const r=rows[rows.length-1].getBoundingClientRect();
        return r.top>=0 && r.bottom<=window.innerHeight}""")
    rec('C5 新行滚动进入可视区（修复「没反应」）', vis, f'添加前新行可见={before_visible}，添加后可见={vis}')

    # 填写并保存
    page.evaluate("""()=>{const rows=document.querySelectorAll('#bomLinesBody tr[data-i]');
        const last=rows[rows.length-1];const inp=last.querySelector('input.plat-bom-in');
        inp.value='快递费';inp.dispatchEvent(new Event('input',{bubbles:true}));
        const num=last.querySelector('input[type=number]');
        num.value='2.8';num.dispatchEvent(new Event('input',{bubbles:true}))}""")
    page.click('button:has-text("保存成本清单")')
    page.wait_for_timeout(1200)
    saved = api('/api/platform/cost-bom')
    last_line = (saved.get('lines') or [{}])[-1] if saved.get('lines') else {}
    ok_save = last_line.get('label') == '快递费' and float(last_line.get('baseValue') or 0) == 2.8 \
        and last_line.get('calcType') == 'per_item'
    rec('C6 UI 保存后回读一致（名称/单价/per_item）', ok_save, json.dumps(last_line, ensure_ascii=False)[:160])
    rec('C7 无页面 JS 错误', not errors, '; '.join(errors[:3]))
    rec('C8 无 API 4xx/5xx', not failures, '; '.join(failures[:3]))
    browser.close()

print(f"\n==== {len(PASSED)} passed, {len(FAILED)} failed ====")
if FAILED:
    print('FAILED:', json.dumps(FAILED, ensure_ascii=False))
    raise SystemExit(1)

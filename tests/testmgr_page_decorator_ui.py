"""页面装修器 V2（可视化三栏）端到端验证：
A. 静态结构：组件库分组/6 种组件 tile、手机预览、结构列表、属性面板、全局配置。
B. 交互流：点组件库添加 → 结构列表 + 预览联动；选中模块填内容 → 预览实时更新；
   上移排序 + 拖拽排序；点预览块反向选中；全局配置发布开关。
C. 持久化：保存草稿/发布 → 状态徽标；公开端点返回组件；刷新后状态恢复。
D. 窄屏（390）无横向溢出、核心流程仍可用。
"""
import json
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8262'
DEC = BASE + '/static/club/page-decorator.html?club_id=1'
ROOT = Path(__file__).resolve().parents[1]
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
PASSED, FAILED = [], []


def rec(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(('PASS ' if ok else 'FAIL ') + name + ' | ' + str(detail)[:180], flush=True)


def pub_page():
    with urlopen(BASE + '/api/public/clubs/1/page/club_home', timeout=10) as f:
        return json.loads(f.read().decode())


errors, failures = [], []

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME)
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('response', lambda r: failures.append(f'{r.status} {r.url}') if r.status >= 400 and '/api/' in r.url else None)
    page.goto(DEC, wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(2000)

    # A. 静态结构
    rec('A1 组件库分组齐全（按后台功能）', page.evaluate("()=>[...document.querySelectorAll('.pd-lib__group')].map(x=>x.childNodes[0].textContent.trim())") == ['活动经营', '会员营销', '页面导航', '自定义内容', '🔒 装备商城'])
    rec('A1b 组件库标注对应后台功能', '对应后台' in page.evaluate("()=>document.querySelector('#libGroups').innerText"))
    rec('A1c 商城排除提示存在', '不支持自定义' in page.evaluate("()=>document.querySelector('#libGroups').innerText"))
    rec('A2 组件 tile 共 6 种', page.evaluate("()=>document.querySelectorAll('.pd-tile').length") == 6)
    for sel in ('#phoneBody', '#structList', '#propsForm', '#paneGlobal', '#pubSwitch', '#titleInput'):
        rec(f'A3 存在 {sel}', page.evaluate(f"()=>!!document.querySelector('{sel}')"))
    rec('A4 空页面引导语', '页面还是空的' in page.evaluate("()=>document.querySelector('#phoneBody').innerText"))
    # A5 装备商城排除（下拉断言在 B 段添加宫格项后执行，见 A5a/A5b/A5c）

    # B1. 点组件库添加轮播图
    page.click('.pd-tile[data-type="carousel"]')
    page.wait_for_timeout(300)
    rec('B1 点组件库添加后结构列表 +1', page.evaluate("()=>document.querySelectorAll('#structList .pd-row').length") == 1)
    rec('B2 新模块自动选中且属性表单出现', '轮播图' in page.evaluate("()=>document.querySelector('#propsForm').innerText"))
    rec('B3 预览出现占位块', '还没有图片' in page.evaluate("()=>document.querySelector('#phoneBody').innerText"))

    # B2. 给轮播加一项，模拟已上传图片（后端会丢弃无图轮播——防空壳板块），填标题/副标题 → 预览实时更新
    page.click('#propsForm [data-act="add-item"]')
    page.wait_for_timeout(300)
    page.evaluate("""()=>{const inp=document.querySelector('#propsForm input[type=hidden][data-path$=".0.image"]');
        inp.value='/static/uploads/club/1/home/e2e-decorator.jpg';
        inp.dispatchEvent(new Event('input',{bubbles:true}))}""")
    page.fill('#propsForm input[data-path$=".title"]', '童玩山野')
    page.fill('#propsForm input[data-path$=".subtitle"]', '全年适玩 18+ 户外体验')
    page.wait_for_timeout(400)
    pv = page.evaluate("()=>document.querySelector('#phoneBody').innerText")
    rec('B4 预览实时显示轮播标题', '童玩山野' in pv and '全年适玩' in pv)

    # B3. 添加导航宫格 + 两项（分别填两个 item 的 label）
    page.click('.pd-tile[data-type="nav_grid"]')
    page.wait_for_timeout(300)
    page.click('#propsForm [data-act="add-item"]')
    page.wait_for_timeout(200)
    page.fill('input[data-path$=".items.0.label"]', '活动报名')
    page.click('#propsForm [data-act="add-item"]')
    page.wait_for_timeout(200)
    page.fill('input[data-path$=".items.1.label"]', '会员积分')
    page.wait_for_timeout(400)
    pv = page.evaluate("()=>document.querySelector('#phoneBody').innerText")
    rec('B5 导航宫格预览显示两项', '活动报名' in pv and '会员积分' in pv)

    # A5. 装备商城排除断言（此时宫格项的下拉已渲染）
    tt_opts = page.evaluate("()=>{const s=[...document.querySelectorAll('#propsForm select')].find(x=>[...x.options].some(o=>o.value==='external'));return s?[...s.options].map(o=>o.value).join(','):'no-select'}")
    rec('A5a 跳转类型选项不含 product', 'product' not in tt_opts, tt_opts)
    route_leak = page.evaluate("()=>[...document.querySelectorAll('#propsForm select')].some(s=>[...s.options].some(o=>o.value==='web.mall'||o.value==='web.gearAnchor'))")
    rec('A5b 商城路由不出现在任何下拉', not route_leak)
    rec('A5c 路由候选含活动/我的页', page.evaluate("()=>[...document.querySelectorAll('#propsForm select')].some(s=>[...s.options].some(o=>o.value==='web.activities'||o.value==='web.home'))"))

    # B4. 结构列表：上移（nav_grid 从第2位移到第1位）
    order0 = page.evaluate("()=>[...document.querySelectorAll('#structList .pd-row')].map(r=>r.querySelector('.pd-row__t').textContent)")
    page.click('#structList .pd-row[data-idx="1"] [data-op="up"]')
    page.wait_for_timeout(300)
    order1 = page.evaluate("()=>[...document.querySelectorAll('#structList .pd-row')].map(r=>r.querySelector('.pd-row__t').textContent)")
    rec('B6 上移改变模块顺序', '快捷入口宫格' in order1[0] and '轮播图' in order1[1], f'{order0} -> {order1}')

    # B5. 拖拽排序：把第 1 行拖到第 2 行位置（还原顺序）
    rows = page.locator('#structList .pd-row')
    rows.nth(0).drag_to(rows.nth(1))
    page.wait_for_timeout(400)
    order2 = page.evaluate("()=>[...document.querySelectorAll('#structList .pd-row')].map(r=>r.querySelector('.pd-row__t').textContent)")
    rec('B7 拖拽 ≡ 排序生效', '轮播图' in order2[0] and '快捷入口宫格' in order2[1], f'{order2}')

    # B6. 点预览块反向选中（点第 2 个块 → 属性面板显示导航宫格）
    page.click('#phoneBody .pv-block[data-idx="1"]')
    page.wait_for_timeout(300)
    rec('B8 点预览块反向选中模块', '快捷入口宫格' in page.evaluate("()=>document.querySelector('#propsForm').innerText"))

    # B7. 自由容器：填标题+正文 → 预览更新；再点结构行选中
    page.click('.pd-tile[data-type="free_container"]')
    page.wait_for_timeout(300)
    page.fill('#propsForm input[data-path$=".title"]', '营地公约')
    page.fill('#propsForm textarea[data-path$=".content"]', '爱护山林，无痕露营')
    page.wait_for_timeout(400)
    pv = page.evaluate("()=>document.querySelector('#phoneBody').innerText")
    rec('B9 自由容器预览实时更新', '营地公约' in pv and '无痕露营' in pv)
    page.click('#structList .pd-row[data-idx="0"]')
    page.wait_for_timeout(300)
    rec('B10 点结构行选中第一模块', '轮播图' in page.evaluate("()=>document.querySelector('#propsForm').innerText"))

    # C. 全局配置 + 保存发布
    page.click('.pd-tab[data-tab="global"]')
    page.wait_for_timeout(200)
    page.fill('#titleInput', '装修器V2验收主页')
    page.check('#pubSwitch')
    page.wait_for_timeout(200)
    page.click('#publishBtn')
    page.wait_for_timeout(1200)
    rec('C1 发布后状态徽标=已发布', page.evaluate("()=>document.querySelector('#statusPill').textContent") == '已发布'
        and page.evaluate("()=>document.querySelector('#statusPill').className") == 'status pub')
    pub = pub_page()
    types = [c.get('type') for c in (pub.get('components') or [])]
    rec('C2 公开端点返回装修组件', 'carousel' in types and 'nav_grid' in types and 'free_container' in types, str(types))
    fc = next((c for c in pub['components'] if c['type'] == 'free_container'), {})
    rec('C3 自由容器内容落库', (fc.get('props') or {}).get('title') == '营地公约', json.dumps(fc, ensure_ascii=False)[:120])

    # C4. 刷新后状态恢复
    page.reload(wait_until='domcontentloaded')
    page.wait_for_timeout(1800)
    rec('C4 刷新后结构恢复（3 个模块）', page.evaluate("()=>document.querySelectorAll('#structList .pd-row').length") == 3)
    rec('C5 刷新后状态徽标保持已发布', page.evaluate("()=>document.querySelector('#statusPill').textContent") == '已发布')

    # D. 窄屏 390：无横向溢出、仍可添加组件
    page.set_viewport_size({'width': 390, 'height': 844})
    page.wait_for_timeout(600)
    ov = page.evaluate("()=>({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth})")
    rec('D1 窄屏无横向溢出', ov['sw'] <= ov['cw'] + 2, str(ov))
    page.click('.pd-tile[data-type="notice"]')
    page.wait_for_timeout(300)
    rec('D2 窄屏添加组件生效', page.evaluate("()=>document.querySelectorAll('#structList .pd-row').length") == 4)

    # 收尾：转草稿并删除验收布局（还原夹具）
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.wait_for_timeout(400)
    page.click('#publishBtn')  # 先发布把 notice 一起带上？不——验收后清理：
    page.wait_for_timeout(800)
    page.click('.pd-tab[data-tab="global"]')
    page.wait_for_timeout(200)
    page.uncheck('#pubSwitch')
    page.click('#saveBtn')
    page.wait_for_timeout(800)
    page.on('dialog', lambda d: d.accept())
    page.click('#deleteBtn')
    page.wait_for_timeout(1200)
    rec('C6 清理：删除验收布局', page.evaluate("()=>document.querySelector('#statusPill').textContent") == '未配置')

    rec('Z1 无页面 JS 错误', not errors, '; '.join(errors[:3]))
    rec('Z2 无 API 4xx/5xx', not failures, '; '.join(failures[:3]))
    browser.close()

print(f"\n==== {len(PASSED)} passed, {len(FAILED)} failed ====")
if FAILED:
    print('FAILED:', json.dumps(FAILED, ensure_ascii=False))
    raise SystemExit(1)

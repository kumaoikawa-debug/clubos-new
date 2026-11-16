"""导航结构 + 双端 UI 留白 + PEAKTRACE 图片代理专项回归（隔离库，不碰真库）。

覆盖 2026-11-16 五项改动：
1. 订单信息并入「经营数据」二级子菜单（入口与功能不变）
2. 营地经营成为「活动经营」与「客户与会员」之间的独立一级分组
3. PEAKTRACE 商品图服务端代理（存量商品无需重同步即可显示）
4. 总平台/俱乐部手机端页头与内容留白（390px 不超线）
5. 电脑端活动详情操作按钮/分类行间距

静态断言 + TestClient 行为断言；浏览器走查由 /tmp 脚本完成，不在此文件内起端口。
"""
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ.update(
    CLUBOS_DB_PATH=str(Path(tempfile.mkdtemp()) / 'nav_ux.db'),
    CLUBOS_TEST_OPEN='0', CLUBOS_SECURITY_MODE='demo',
    MOCK_AI='1', COMMERCE_PROVIDER='local',
)

import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app.app, raise_server_exceptions=False)
checks = []


def check(name, cond, detail=''):
    assert cond, (name, detail)
    checks.append(name)
    print('PASS', name, flush=True)


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


# ---------- 1/2. 导航结构 ----------
ux = read('static/ux/clubos-ux.js')
check('营地经营已登记为独立一级分组', "['营地经营',['camp']]" in ux)
groups_line = next(l for l in ux.splitlines() if l.startswith('const clubGroups='))
gpos_activity = groups_line.index("['活动经营'")
gpos_camp = groups_line.index("['营地经营'")
gpos_members = groups_line.index("['客户与会员'")
check('营地经营位于活动经营与客户与会员之间', gpos_activity < gpos_camp < gpos_members)
check('订单信息登记为经营数据的二级子项', "clubSubmenus={analytics:['order-info']}" in ux)
check('二级子菜单在渲染器里真实生成', "sub.className='nav-submenu'" in ux and 'used.add(kid)' in ux)
check('二级子项保留 nav-leaf 契约（点击/搜索/竞态监听不失效）',
      "'nav-leaf nav-subleaf'" in ux)
check('平台端不误伤：子菜单只在 club 端渲染', "page==='club'?(clubSubmenus[id]||[]):[]" in ux)
html = read('static/club/index.html')
check('订单信息与营地经营按钮仍在 HTML（data-view 未删）',
      'data-view="order-info"' in html and 'data-view="camp"' in html)
check('视图容器仍完整存在', 'id="order-info"' in html and 'id="camp"' in html)
css_admin = read('static/ux/admin-workspace.css')
check('二级子菜单有专属缩进样式',
      '.admin-workspace .nav-submenu' in css_admin and '.nav-subleaf' in css_admin)

# ---------- 3. PEAKTRACE 图片代理 ----------
pt = app.peaktrace_catalog
check('代理前缀只走公开只读端点', pt.IMAGE_PROXY_PREFIX == '/api/public/peaktrace-image/')
check('只改写配置域名下的图片',
      pt.proxy_image_url('https://peaktrace-h5.app.workbuddy.host/assets/a.jpg') == '/api/public/peaktrace-image/assets/a.jpg'
      and pt.proxy_image_url('https://evil.test/assets/a.jpg') == 'https://evil.test/assets/a.jpg'
      and pt.proxy_image_url('/static/uploads/a.jpg') == '/static/uploads/a.jpg')
check('拒绝路径穿越', pt.proxy_image_url('https://peaktrace-h5.app.workbuddy.host/assets/../x') != '/api/public/peaktrace-image/assets/../x')

# 造一件带上游图片的在售商品，验证对外读取被改写、库里仍是原 URL
with app.conn() as c:
    c.execute("UPDATE products SET image_url=?,cover_url=?,source='external_api',catalog_view=? WHERE id=1",
              ('https://peaktrace-h5.app.workbuddy.host/assets/tee.jpg',
               'https://peaktrace-h5.app.workbuddy.host/assets/tee.jpg',
               '[1200,800,0,0,600,800]'))
    c.execute('DELETE FROM product_images WHERE product_id=1')
    c.execute('INSERT INTO product_images(product_id,url,sort) VALUES(1,?,0)',
              ('https://peaktrace-h5.app.workbuddy.host/assets/tee.jpg',))
p = client.get('/api/public/clubs/1/mall/products/1').json()
check('商品详情 coverUrl 走本地代理',
      p.get('coverUrl') == '/api/public/peaktrace-image/assets/tee.jpg', p.get('coverUrl'))
check('图集 URL 全部走本地代理',
      all(i['url'].startswith(pt.IMAGE_PROXY_PREFIX) for i in p.get('images') or []), p.get('images'))
with app.conn() as c:
    check('库里仍保存上游原始 URL（不改数据、不覆盖人工图）',
          c.execute('SELECT url FROM product_images WHERE product_id=1').fetchone()[0].startswith(pt.ORIGIN))

# 平台后台商品库（截图场景）：列表与图集弹窗同样要走代理，否则前端 wImgSrc()
# 判 peaktrace-h5 为坏域名直接降级占位图（列表）或裸链直连 403（弹窗 img）。
from security_v025 import _hash  # noqa: E402
with app.conn() as c:
    c.execute("INSERT INTO auth_accounts(username,password_hash,role,platform_department) VALUES('imgqa','test','platform','operations')")
    _aid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    _tok = secrets.token_urlsafe(32)
    c.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)',
              (_hash(_tok), _aid, _hash('test'), int(time.time()) + 3600))
_admin_h = {'Authorization': 'Bearer ' + _tok}
_plist = client.get('/api/platform/products', headers=_admin_h).json()
_p1 = next((x for x in _plist if x.get('id') == 1), None)
check('平台后台列表图片走本地代理',
      _p1 and _p1.get('image_url') == '/api/public/peaktrace-image/assets/tee.jpg'
      and _p1.get('cover_url') == '/api/public/peaktrace-image/assets/tee.jpg',
      _p1 and (_p1.get('image_url'), _p1.get('cover_url')))
check('平台后台列表下发合成图裁切坐标',
      _p1 and _p1.get('catalogView') and len(_p1['catalogView']) == 6, _p1 and _p1.get('catalogView'))
_pimg = client.get('/api/platform/products/1/images', headers=_admin_h).json()
check('平台后台图集弹窗头图与详情图走本地代理',
      _pimg.get('cover') == '/api/public/peaktrace-image/assets/tee.jpg'
      and all(i['url'].startswith(pt.IMAGE_PROXY_PREFIX) for i in _pimg.get('images') or []),
      (_pimg.get('cover'), _pimg.get('images')))

_real_fetch = pt.fetch_image
try:
    pt.fetch_image = lambda path, origin=None: (b'\xff\xd8\xffTEST', 'image/jpeg')
    r = client.get('/api/public/peaktrace-image/assets/tee.jpg')
    check('代理端点公开可用且返回图片',
          r.status_code == 200 and r.headers['content-type'].startswith('image/') and r.content.startswith(b'\xff\xd8'),
          (r.status_code, r.headers.get('content-type')))
    check('代理端点拒绝路径穿越',
          client.get('/api/public/peaktrace-image/../secret').status_code in (400, 404))
finally:
    pt.fetch_image = _real_fetch

# ---------- 4/5. CSS 作用域与留白 ----------
check('两端共用后台 CSS 的手机端留白提升到 18px', '.admin-workspace .main {padding-inline: 18px;}' in css_admin)
check('手机端页头改为可换行', '.admin-workspace .workspace-bar {flex-wrap: wrap;' in css_admin)
check('页头工具区固定在右侧且不再被挤出', '.admin-workspace .workspace-tools {flex: 0 0 auto; margin-left: auto;}' in css_admin)
club_mobile = read('static/club/club-mobile.css')
check('俱乐部端最终覆盖也是 18px（不会被 club-glass 回退）', '.admin-workspace .main{ padding-left:18px; padding-right:18px; }' in club_mobile)
check('工作台按钮窄屏竖排撑满', '.admin-workspace #dash .hero-action' in club_mobile and 'flex-direction:column' in club_mobile)
check('club-mobile 未干预侧栏定位（抽屉仍可用）',
      'transform:' not in club_mobile and 'position:' not in club_mobile)
club_glass = read('static/club/club-glass.css')
check('桌面操作按钮有行距并允许换行',
      'gap:10px 12px' in club_glass and 'flex:1 1 420px' in club_glass)
check('桌面操作按钮尺寸可读且不贴边', 'padding:8px 14px' in club_glass and 'min-height:36px' in club_glass)
check('活动分类行间距同步放大', 'gap:12px 14px' in club_glass and 'padding:14px 18px!important' in club_glass)

club_html = read('static/club/index.html')
plat_html = read('static/platform/index.html')
for f, label in ((club_html, 'club'), (plat_html, 'platform')):
    check(f'{label} 端静态资源已 bump 缓存版本', 'v=20261116c' in f)

print('\nNAV UX IMAGE OK %d checks' % len(checks))

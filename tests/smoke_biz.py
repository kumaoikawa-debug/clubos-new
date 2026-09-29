"""C 端「业务介绍」可配置区冒烟（v0.29）。

覆盖四件容易出错的事：
1. 未配置 / 关闭 → C 端 `biz_section` 为 None（前端整节隐藏，不给顾客看空壳）；
2. 公开视图里的图片必须是**代理地址**（生产 /static/uploads/* 是 404，裸链就是死链）；
3. 局部 PATCH 不能清空 items（后台只改标题时 `payload.get(k) or 默认` 那类写法会静默丢数据）；
4. 形状不对的输入要静默丢弃（无标题条目、javascript: 图片），不能写进库里。
另外实测一次「引用过的文件能通过代理取到」，证明代理不是摆设。
"""
from pathlib import Path
import json, os, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['MOCK_AI'] = '1'
os.environ['COMMERCE_PROVIDER'] = 'local'
os.environ['CLUBOS_DB_PATH'] = str(ROOT / 'clubos_biz_test.db')
try:
    Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError:
    pass

import app  # noqa: E402  (import 会跑 init_db，所以 CLUBOS_DB_PATH 必须先设好)
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)


def ok(method, url, **kw):
    r = getattr(c, method)(url, **kw)
    assert r.status_code < 400, (url, r.status_code, r.text)
    return r


# ① 默认未配置 → C 端整节隐藏，且不泄漏内部列
pub = ok('get', '/api/public/clubs/1').json()
assert pub.get('biz_section') is None, pub.get('biz_section')
assert 'biz_section_json' not in pub, '内部列不该出现在公开响应里'

cfg = ok('get', '/api/club/1/biz-section').json()
assert cfg['enabled'] is False and cfg['items'] == [], cfg

# ② 开启 + 写条目：夹两条脏数据（空标题 / javascript: 图片），必须被丢掉
cfg = ok('patch', '/api/club/1/biz-section', json={
    'enabled': True,
    'title': '我们还能做什么',
    'intro': '除了周末线路，也承接企业团建与亲子研学。',
    'items': [
        {'title': '企业团建', 'desc': '10–50 人团队定制'},
        {'title': '   '},
        {'title': '装备租赁', 'image': '/static/uploads/biz/rent.jpg'},
        {'title': '研学', 'image': 'javascript:alert(1)'},
    ],
}).json()
assert cfg['enabled'] is True
assert [i['title'] for i in cfg['items']] == ['企业团建', '装备租赁', '研学'], cfg['items']
assert cfg['items'][1]['image'] == '/static/uploads/biz/rent.jpg'
assert cfg['items'][2]['image'] == '', 'javascript: 图片必须被丢掉'

# ③ 公开视图：JSON 文本 + 图片改写成公开代理地址
pub = ok('get', '/api/public/clubs/1').json()
biz = json.loads(pub['biz_section'])
assert biz['enabled'] and biz['title'] == '我们还能做什么'
assert biz['items'][1]['image'] == '/api/public/clubs/1/biz-media/uploads/biz/rent.jpg', biz['items'][1]

# ④ 局部更新不能清空 items（只改标题）
cfg = ok('patch', '/api/club/1/biz-section', json={'title': '我们的业务'}).json()
assert cfg['title'] == '我们的业务' and cfg['enabled'] is True and len(cfg['items']) == 3, cfg

# ⑤ 条目上限 8：超出的裁掉，不报 500
cfg = ok('patch', '/api/club/1/biz-section', json={
    'items': [{'title': f'业务{i}'} for i in range(12)]}).json()
assert len(cfg['items']) == 8, len(cfg['items'])

# ⑥ 代理真的是代理：引用过的文件能取到，未引用的 404（不是「/static/uploads 通配开放」）
probe = ROOT / 'static' / 'uploads' / 'biz_smoke_probe.jpg'
probe.parent.mkdir(parents=True, exist_ok=True)
payload = b'\xff\xd8\xff\xdb clubos-biz-smoke \xff\xd9'
try:
    probe.write_bytes(payload)
    ok('patch', '/api/club/1/biz-section', json={'items': [{'title': '装备租赁', 'image': '/static/uploads/biz_smoke_probe.jpg'}]})
    r = c.get('/api/public/clubs/1/biz-media/uploads/biz_smoke_probe.jpg')
    assert r.status_code == 200, (r.status_code, r.text[:120])
    assert r.content == payload, '代理返回的内容与磁盘文件不一致'
    # 未引用过的文件：404（避免把整个 uploads 目录变成公开可枚举）
    assert c.get('/api/public/clubs/1/biz-media/uploads/not-referenced.jpg').status_code == 404
finally:
    try:
        probe.unlink()
    except FileNotFoundError:
        pass

# ⑦ 关闭开关 → C 端立刻拿不到（整节隐藏）
ok('patch', '/api/club/1/biz-section', json={'enabled': False})
assert ok('get', '/api/public/clubs/1').json()['biz_section'] is None

print('BIZ SMOKE PASS')

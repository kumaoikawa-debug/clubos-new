"""C 端「我的」页文案冒烟：联系我们 / 关于俱乐部（2026-10-23）。

这两页原来是前端写死的占位文案（9:00-21:00、topoutdoor.cn、v0.25……），改由俱乐部
自定义后，最怕四类回归：

1. **空字段漏下发**：没填的营业时间被补成默认值 → 顾客照着假时段打电话没人接。
   这里断言「没填就是没有」，公开视图里连键都不出现。
2. **局部更新清空另一块**：后台只改「关于」，结果「联系我们」被抹掉。
   normalize 的 current 回退必须顶住。
3. **脏输入写进库**：坏邮箱、javascript: 官网、12 条亮点、500 字地址 —— 一律裁掉或丢弃。
4. **内部列泄漏**：演示环境走 dict(x) 全量下发，c_profile_json 必须显式摘掉。
"""
from pathlib import Path
import os, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['MOCK_AI'] = '1'
os.environ['COMMERCE_PROVIDER'] = 'local'
# /api/club/* 的写接口在 demo 模式也要求登录态（TEST_OPEN 除外）。
os.environ['CLUBOS_TEST_OPEN'] = '1'
os.environ['CLUBOS_DB_PATH'] = str(ROOT / 'clubos_cprofile_test.db')
try:
    Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError:
    pass

import app  # noqa: E402  (import 会跑 init_db，CLUBOS_DB_PATH 必须先设好)
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)


def ok(method, url, **kw):
    r = getattr(c, method)(url, **kw)
    assert r.status_code < 400, (url, r.status_code, r.text)
    return r


# ① 未配置：公开视图两块都空，编辑接口给出可填的空骨架，内部列不泄漏
pub = ok('get', '/api/public/clubs/1').json()
assert pub.get('profile') == {'contact': {}, 'about': {}}, pub.get('profile')
assert 'c_profile_json' not in pub, '内部列不该出现在公开响应里'

draft = ok('get', '/api/club/1/c-profile').json()['profile']
assert set(draft['contact']) == {'phone', 'wechat', 'email', 'hours', 'address', 'note'}, draft['contact']
assert set(draft['about']) == {'tagline', 'intro', 'website', 'since', 'highlights'}, draft['about']
assert draft['about']['highlights'] == []

# ② 写入：夹带脏数据，一律按规则清洗
saved = ok('put', '/api/club/1/c-profile', json={
    'contact': {
        'phone': '138 0000 1234',
        'wechat': 'yuantuo-outdoor',
        'email': 'not-an-email',
        'hours': '  每天 09:00 - 21:00  ',
        'address': '成都市武侯区天府大道 8 号 3 楼',
        'note': '退改请在订单页发起。',
    },
    'about': {
        'tagline': '把周末还给山野',
        'intro': '第一段。\r\n\r\n第二段。',
        'website': 'topoutdoor.cn',
        'since': '2019',
        'highlights': ['本地领队 1:8', '三年零事故', '本地领队 1:8', '可开票', '周末天天出发', '不购物', '纯玩'],
    },
}).json()['profile']

assert saved['contact']['email'] == '', '坏邮箱必须丢弃（留着就是一个发不出去的链接）'
assert saved['contact']['hours'] == '每天 09:00 - 21:00', saved['contact']['hours']
assert saved['about']['website'] == 'https://topoutdoor.cn', saved['about']['website']
assert saved['about']['intro'] == '第一段。\n\n第二段。', repr(saved['about']['intro'])
assert saved['about']['highlights'] == ['本地领队 1:8', '三年零事故', '可开票', '周末天天出发', '不购物', '纯玩'], saved['about']['highlights']
assert len(saved['about']['highlights']) == 6, '亮点上限 6 条'

# ③ 公开视图：只下发有值的键
view = ok('get', '/api/public/clubs/1').json()['profile']
assert 'email' not in view['contact'], view['contact']
assert set(view['contact']) == {'phone', 'wechat', 'hours', 'address', 'note'}, view['contact']
assert view['about']['website'] == 'https://topoutdoor.cn'

# ④ 局部更新：只提交 about，contact 必须原样保留
part = ok('put', '/api/club/1/c-profile', json={'about': {'tagline': '成都周边一日轻徒步'}}).json()['profile']
assert part['about']['tagline'] == '成都周边一日轻徒步'
assert part['about']['intro'] == '第一段。\n\n第二段。', '没提交的键不能被清空'
assert part['contact']['phone'] == '138 0000 1234', '另一块必须保留'

# ⑤ 形状不对的输入：javascript: 官网丢弃，纯数字电话保留，太短的「电话」视为没填
bad = ok('put', '/api/club/1/c-profile', json={
    'contact': {'phone': '1234', 'wechat': '  '},
    'about': {'website': 'javascript:alert(1)'},
}).json()['profile']
assert bad['about']['website'] == '', 'javascript: 官网必须丢弃'
assert bad['contact']['phone'] == '', '不足 5 位数字的电话视为没填（否则就是一个打不通的号）'

# ⑥ 简介留空：回退品牌口号（同一件事不用填两遍）
ok('put', '/api/club/1/settings', json={'slogan': '把周末还给山野'})
ok('put', '/api/club/1/c-profile', json={'about': {'tagline': ''}})
view = ok('get', '/api/public/clubs/1').json()['profile']
assert view['about']['tagline'] == '把周末还给山野', view['about']

# ⑦ 全部清空：回到「两块都空」，C 端只出最小页面（不编造任何默认文案）
ok('put', '/api/club/1/c-profile', json={
    'contact': {'phone': '', 'wechat': '', 'email': '', 'hours': '', 'address': '', 'note': ''},
    'about': {'tagline': '', 'intro': '', 'website': '', 'since': '', 'highlights': []},
})
ok('put', '/api/club/1/settings', json={'slogan': ''})
view = ok('get', '/api/public/clubs/1').json()['profile']
assert view['contact'] == {}, view['contact']
assert 'tagline' not in view['about'], view['about']

# ⑧ 类型不对的块：400（不是 500，也不能静默当成功）
r = c.put('/api/club/1/c-profile', json={'contact': 'phone=110'})
assert r.status_code == 400, (r.status_code, r.text[:120])

print('C-PROFILE SMOKE PASS')

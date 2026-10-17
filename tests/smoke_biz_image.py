"""业务介绍配图上传冒烟（C 端「户外能力」图文卡 · 2026-10-17）。

覆盖上传端点 + 俱乐部域预览代理 + 公开代理白名单的联动：
1. 上传合法 PNG → 落盘 uploads/{club}/biz/，返回落库地址与预览地址两个形状；
2. 长图原样存盘（不重编码、不改尺寸）—— 这一屏的图按原图比例完整展示；
3. 坏输入一律 400/413：扩展名不在白名单 / PIL 打不开的损坏文件 / 超过 10MB；
4. PATCH 进 biz-section 后，公开接口吐的是**代理地址**且代理返回的字节与磁盘一致；
5. 俱乐部域预览代理（编辑弹窗用）同路径 200；
6. 代理不收越界：别的俱乐部的 biz 目录、 uploads 下别处、 `..` 穿越全部 404。
"""
from pathlib import Path
import io, json, os, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['MOCK_AI'] = '1'
os.environ['COMMERCE_PROVIDER'] = 'local'
# /api/club/* 的写接口在 demo 模式也要求登录态（TEST_OPEN 除外）。本测重点是
# 上传落盘 / 预览代理 / 公开白名单的联动，鉴权链由 smoke_v25.py 在 production
# 模式覆盖，这里按线上当前运行形态（CLUBOS_TEST_OPEN=1 内测开放）显式声明。
os.environ['CLUBOS_TEST_OPEN'] = '1'
os.environ['CLUBOS_DB_PATH'] = str(ROOT / 'clubos_biz_image_test.db')
try:
    Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError:
    pass

from PIL import Image  # noqa: E402
import app  # noqa: E402  (import 会跑 init_db，所以 CLUBOS_DB_PATH 必须先设好)
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)
UPLOAD_BIZ = ROOT / 'static' / 'uploads' / '1' / 'biz'
_created: list[Path] = []


def _png(w: int, h: int) -> bytes:
    """随机噪声 PNG：压不小，体积接近原始字节数（413 用例需要真体积）。"""
    buf = io.BytesIO()
    Image.frombytes('RGB', (w, h), os.urandom(w * h * 3)).save(buf, 'PNG')
    return buf.getvalue()


def ok(method, url, **kw):
    r = getattr(c, method)(url, **kw)
    assert r.status_code < 400, (url, r.status_code, r.text[:200])
    return r


try:
    # ① 上传一张合法 PNG：两个地址形状 + 落盘
    data = _png(640, 360)
    r = ok('post', '/api/club/1/biz-section/image',
           files={'file': ('card.png', data, 'image/png')}).json()
    url, preview = r['url'], r['preview']
    assert url.startswith('/static/uploads/1/biz/') and url.endswith('.png'), r
    assert preview == '/api/club/1/biz-media' + url[len('/static'):], r
    fp = UPLOAD_BIZ / Path(url).name
    assert fp.is_file() and fp.read_bytes() == data, '落盘内容与上传内容不一致'
    _created.append(fp)

    # ② 长图原样存盘：1080×2600 上传后尺寸不变（不重编码、不缩放）
    long_data = _png(1080, 2600)
    r2 = ok('post', '/api/club/1/biz-section/image',
            files={'file': ('long.png', long_data, 'image/png')}).json()
    fp2 = UPLOAD_BIZ / Path(r2['url']).name
    assert fp2.is_file() and fp2.read_bytes() == long_data, '长图被改动过'
    with Image.open(fp2) as im:
        assert im.size == (1080, 2600), im.size
    _created.append(fp2)

    # ③ 坏输入：扩展名 / 损坏内容 / 超体积
    assert c.post('/api/club/1/biz-section/image',
                  files={'file': ('x.txt', b'not an image', 'text/plain')}).status_code == 400
    assert c.post('/api/club/1/biz-section/image',
                  files={'file': ('bad.png', b'\x89PNG\r\n\x1a\n garbage', 'image/png')}).status_code == 400
    assert c.post('/api/club/1/biz-section/image',
                  files={'file': ('big.png', _png(2400, 2400), 'image/png')}).status_code == 413

    # ④ 写进 biz-section：公开接口必须给代理地址，且代理返回的字节与磁盘一致
    cfg = ok('patch', '/api/club/1/biz-section', json={
        'enabled': True, 'title': '我们还能做什么',
        'items': [{'title': '企业团建', 'desc': '团队定制', 'image': url}]}).json()
    assert cfg['items'][0]['image'] == url, cfg['items']
    pub = ok('get', '/api/public/clubs/1').json()
    biz = json.loads(pub['biz_section'])
    proxied = '/api/public/clubs/1/biz-media' + url[len('/static'):]
    assert biz['items'][0]['image'] == proxied, biz['items'][0]
    r = ok('get', proxied)
    assert r.content == data, '公开代理返回的内容与磁盘不一致'
    # 没被配置引用的文件，公开代理照样 404（白名单不是通配）
    assert c.get('/api/public/clubs/1/biz-media/uploads/1/biz/not-referenced.png').status_code == 404

    # ⑤ 俱乐部域预览代理（编辑弹窗用，带登录态）
    r = ok('get', preview)
    assert r.content == data, '预览代理返回的内容与磁盘不一致'

    # ⑥ 预览代理不收越界请求
    assert c.get('/api/club/1/biz-media/uploads/1/other/x.png').status_code == 404
    assert c.get('/api/club/1/biz-media/uploads/2/biz/x.png').status_code == 404
    assert c.get('/api/club/1/biz-media/uploads/1/biz/../clubos.db').status_code == 404

    print('BIZ IMAGE SMOKE PASS')
finally:
    for p in _created:
        try:
            p.unlink()
        except FileNotFoundError:
            pass

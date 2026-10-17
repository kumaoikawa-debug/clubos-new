"""回归测试：AI 生成的活动必须自带封面。

背景（用户 2026-10-17 截图实证）：AI 已经生成了整篇图文、素材里躺着一整个 PPT 抽出来的
三十多张照片，活动列表却挂着「未设封面」，C 端卡片只能显示品牌渐变兜底 —— 老板第一句话
就是「AI 生成活动，怎么没有图片呢」。

根因：`activities.cover` 是一个独立字段，全项目只有「上传封面」接口会写；AI 生成流程
从头到尾没碰过它。修法不是让模型多输出一个字段，而是**从它自己的素材里挑一张**：
落版时（`_finalize_detail_version`）补空，编辑保存时补空，另外给一个可手动触发的接口。

这个文件锁住五条不变量：

  1. 没有封面时，落版会自动补上，且补的是**真实素材地址**（`/static/uploads/...`）；
  2. **已有封面绝不被自动挑选覆盖** —— 用户手动上传/手动选的那张永远优先；
  3. 挑选必须排除 logo、装饰小图、证书类文档照片（与详情配图的硬闸同源）；
  4. 素材里没有任何合格候选时**宁可什么都不写**，也不推一张劣质图当门面（接口回 422）；
  5. 补上之后 `GET /api/club/{c}/activities/{id}/cover` 必须真的读出图片字节。
"""
import io, json, os, shutil, sys, tempfile

sys.path.insert(0, '.')

# 自我隔离：app.py 在模块级调 init_db()，import app 会把迁移落到目标库。
# 必须在 import 之前把库路径指向临时文件，绝不碰项目真库 clubos.db。
DB_PATH = os.path.join(tempfile.mkdtemp(prefix='clubos-autocover-smoke-'), 'clubos.db')
os.environ['CLUBOS_DB_PATH'] = DB_PATH
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')     # 写接口在 demo 模式也要求登录态

from fastapi.testclient import TestClient  # noqa: E402
import db  # noqa: E402

db.init_db()
import app  # noqa: E402
from ai_engine import pick_cover_media  # noqa: E402

CLUB = 1
FAILS = []
BATCH = 'autocover-smoke'          # 测试用的真实落盘目录，结束时整块删掉
UPLOAD_DIR = os.path.join('static', 'uploads', str(CLUB), BATCH)

# 1x1 PNG：真实可解码的图片字节，用来验证封面代理真的把文件读回来了
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d494844520000000100000001080200000090'
    '7753de0000000c4944415408d763f8cfc000000301010018dd8db00000'
    '000049454e44ae426082')


def ok(cond, label, extra=''):
    print(('  PASS  ' if cond else '  FAIL  ') + label + (f'   | {extra}' if extra else ''))
    if not cond:
        FAILS.append(label)


def media(ref, name, w, h, *, kind='photo', desc='', usable=None, tags=None):
    item = {'ref': ref, 'name': name, 'url': f'/static/uploads/{CLUB}/{BATCH}/{name}',
            'width': w, 'height': h, 'kind': kind}
    if desc:
        item['desc'] = desc
    if usable is not None:
        item['usable'] = usable
    if tags:
        item['tags'] = tags
    return item


def write_file(name):
    with open(os.path.join(UPLOAD_DIR, name), 'wb') as f:
        f.write(PNG)


def seed(media_list, detail, cover=None, title='自动封面测试'):
    with app.conn() as c:
        c.execute('INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,'
                  'activity_master_json,detail_json,cover) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (CLUB, title, 'draft', '2026-11-01', '测试地', 100, 20,
                   json.dumps({'title': title, 'media': media_list}, ensure_ascii=False),
                   json.dumps(detail, ensure_ascii=False), cover))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])


def cover_of(aid):
    with app.conn() as c:
        r = c.execute('SELECT cover FROM activities WHERE id=?', (aid,)).fetchone()
    return r['cover'] if r else None


def main():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    for n in ('hero-scene.jpg', 'scenic.jpg', 'logo.png', 'tiny.png', 'cert.png'):
        write_file(n)

    scenic = media('img_01', 'hero-scene.jpg', 1600, 1067,
                   desc='连绵雪峰披着金色晨光，山脊线与云海层次清晰', tags=['雪山与山景', '云海与天空'])
    scenery2 = media('img_02', 'scenic.jpg', 1920, 1280,
                     desc='秋日公路穿过金黄落叶林，远处是覆雪山峰', tags=['森林与植被', '山景与天象'])
    logo = media('img_03', 'logo.png', 1004, 680, kind='logo')
    tiny = media('img_04', 'tiny.png', 61, 35, desc='装饰用箭头')
    cert = media('img_05', 'cert.png', 1200, 900, desc='教练资格证书扫描件')
    dish = media('img_06', 'scenic.jpg', 1200, 900, desc='一桌热气腾腾的火锅与餐盘', tags=['餐食'])

    print('1) 无封面 → 落版时自动补')
    hero_detail = {'blocks': [{'type': 'hero', 'mediaRefs': ['img_01']}, {'type': 'lead', 'body': 'x'}]}
    aid = seed([logo, tiny, cert, scenic, scenery2], hero_detail)
    with app.conn() as c:
        vid, _ = app._reserve_detail_version(c, club_id=CLUB, activity_id=aid, origin='ai-generate')
        app._finalize_detail_version(c, version_id=vid, activity_id=aid, detail=hero_detail,
                                     master={'media': [logo, tiny, cert, scenic, scenery2]}, credits=0)
    got = cover_of(aid)
    ok(bool(got) and '/static/uploads/' in str(got), '落版后封面已自动补上', str(got))
    ok(str(got).endswith('hero-scene.jpg'), '优先沿用详情首图（hero）', str(got))

    print('2) 已有封面 → 绝不被覆盖')
    aid2 = seed([logo, scenic], {'blocks': []}, cover='/static/uploads/1/MANUAL/keep.jpg')
    with app.conn() as c:
        vid, _ = app._reserve_detail_version(c, club_id=CLUB, activity_id=aid2, origin='ai-generate')
        app._finalize_detail_version(c, version_id=vid, activity_id=aid2, detail={'blocks': []},
                                     master={'media': [logo, scenic]}, credits=0)
    ok(cover_of(aid2) == '/static/uploads/1/MANUAL/keep.jpg', '手动封面未被自动挑选覆盖', str(cover_of(aid2)))

    print('3) 没有 hero 时按打分挑：风景优先、餐食/人像减分')
    aid3 = seed([logo, tiny, cert, dish, scenery2], {'blocks': []})
    with app.conn() as c:
        vid, _ = app._reserve_detail_version(c, club_id=CLUB, activity_id=aid3, origin='ai-generate')
        app._finalize_detail_version(c, version_id=vid, activity_id=aid3, detail={'blocks': []},
                                     master={'media': [logo, tiny, cert, dish, scenery2]}, credits=0)
    ok(str(cover_of(aid3)).endswith('scenic.jpg'), '选了风景图而不是火锅图', str(cover_of(aid3)))

    print('4) 只有 logo / 小图 / 证书 → 不推劣质图')
    bad = [logo, tiny, cert]
    ok(pick_cover_media({'media': bad}, None) is None, 'pick_cover_media 返回 None')
    aid4 = seed(bad, {'blocks': [{'type': 'hero', 'mediaRefs': ['img_03']}]})
    with app.conn() as c:
        vid, _ = app._reserve_detail_version(c, club_id=CLUB, activity_id=aid4, origin='ai-generate')
        app._finalize_detail_version(c, version_id=vid, activity_id=aid4, detail={'blocks': []},
                                     master={'media': bad}, credits=0)
    ok(cover_of(aid4) is None, '落版时不写脏数据（保持空封面）', str(cover_of(aid4)))

    print('5) 手动接口：可以覆盖、没候选时报 422、越权 404')
    client = TestClient(app.app)
    r = client.post(f'/api/club/{CLUB}/activities/{aid3}/cover/auto')
    ok(r.status_code == 200 and str((r.json() or {}).get('cover', '')).endswith('scenic.jpg'),
       'POST /cover/auto 返回封面地址', f'{r.status_code} {r.text[:90]}')
    r = client.post(f'/api/club/{CLUB}/activities/{aid2}/cover/auto')
    ok(r.status_code == 200 and '/MANUAL/' not in str((r.json() or {}).get('cover', '')),
       '手动触发允许替换已有封面', f'{r.status_code} {r.text[:90]}')
    r = client.post(f'/api/club/{CLUB}/activities/{aid4}/cover/auto')
    ok(r.status_code == 422, '没有合格素材时明确拒绝（422）', f'{r.status_code} {r.text[:90]}')
    r = client.post(f'/api/club/{CLUB}/activities/999999/cover/auto')
    ok(r.status_code == 404, '不存在的活动 404', str(r.status_code))

    print('6) 封面真的能读出来（字节级）')
    r = client.get(f'/api/club/{CLUB}/activities/{aid}/cover')
    ok(r.status_code == 200 and r.content == PNG, 'GET cover 返回原始图片字节',
       f'{r.status_code} {len(r.content)}B')

    print('7) C 端公开列表把封面重写成免登录代理')
    client.get(f'/api/club/{CLUB}/activities/{aid}')
    with app.conn() as c:
        c.execute('UPDATE activities SET status="published" WHERE id IN (?,?)', (aid, aid3))
    r = client.get(f'/api/public/clubs/{CLUB}/activities')
    rows = {a['id']: a for a in (r.json() or [])}
    cov = str((rows.get(aid) or {}).get('cover') or '')
    ok(cov.startswith(f'/api/public/activities/{aid}/media/'), '公开列表下发公开代理地址', cov[:80])


if __name__ == '__main__':
    try:
        main()
    finally:
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
    print()
    if FAILS:
        print('AUTO COVER SMOKE FAILED:', len(FAILS))
        for f in FAILS:
            print('  -', f)
        sys.exit(1)
    print('AUTO COVER SMOKE PASS')

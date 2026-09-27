"""回归测试：C 端活动详情的「带队领队」。

背景：俱乐部在后台把领队排进团期之后，C 端活动详情过去**完全不显示**谁带队 ——
报名页上看不到任何带队信息。本轮补上之后，这个文件锁住四条不变量：

  1. 只下发**已指派**到团期的领队；推荐名单不下发（顾客不需要知道「本来还考虑过谁」）；
  2. 领队手机号绝不出现在 C 端返回体里 —— 那是俱乐部内部联络信息，
     俱乐部端的 _leader_plan_for 带 phone 是给经营者看的，C 端一律不给；
  3. 头像必须是 /api/public/leaders/... 公开代理：顾客没有 club 角色，
     /api/club/{c}/leaders/{id}/avatar.png 那条取不到；
  4. 生产白名单（IS_PROD 分支）必须放行 leaders —— 否则线上会静默丢掉整块，
     「本地演示跑通了」不等于线上能出。
"""
import io, json, os, sqlite3, sys, tempfile

sys.path.insert(0, '.')

# 自我隔离：app.py 在模块级调 init_db()，任何 import app 的测试都会把迁移落到目标库。
# 必须在 import 之前把库路径指向临时文件，绝不碰项目真库 clubos.db。
DB_PATH = os.path.join(tempfile.mkdtemp(prefix='clubos-leaders-smoke-'), 'clubos.db')
os.environ['CLUBOS_DB_PATH'] = DB_PATH

from fastapi.testclient import TestClient  # noqa: E402

import db  # noqa: E402
db.init_db()
import app  # noqa: E402

CLUB = 1
FAILS = []

# 1x1 PNG：真实图片字节，用来验证头像代理真的把文件读回来了
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d494844520000000100000001080200000090'
    '7753de0000000c4944415408d763f8cfc000000301010018dd8db00000'
    '000049454e44ae426082')


def ok(cond, label, extra=''):
    print(('  PASS  ' if cond else '  FAIL  ') + label + (f'   | {extra}' if extra else ''))
    if not cond:
        FAILS.append(label)


def seed():
    """造一场已发布活动 + 两个团期。不依赖 demo 播种数据（它可能一场活动都没有）。"""
    c = sqlite3.connect(DB_PATH)
    try:
        c.execute("UPDATE clubs SET status='active' WHERE id=?", (CLUB,))
        row = c.execute("SELECT id FROM activities WHERE club_id=? AND status='published'", (CLUB,)).fetchone()
        if row:
            aid = row[0]
        else:
            cur = c.execute(
                "INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,"
                "activity_master_json,detail_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (CLUB, '回归测试活动 · 蓥华山徒步', 'published', '2026-10-24', '什邡蓥华山', 498, 30, '{}', '{}'))
            aid = cur.lastrowid
        occs = [r[0] for r in c.execute(
            "SELECT id FROM activity_occurrences WHERE activity_id=? ORDER BY id", (aid,)).fetchall()]
        while len(occs) < 2:
            cur = c.execute(
                "INSERT INTO activity_occurrences(activity_id,club_id,start_at,price,capacity,sold,status,label)"
                " VALUES(?,?,?,?,?,0,'open',?)",
                (aid, CLUB, '2026-10-24 08:00' if not occs else '2026-10-31 08:00', 498, 30,
                 '10月24日 · 标准团' if not occs else '10月31日 · 小团'))
            occs.append(cur.lastrowid)
        c.commit()
        return aid, occs[0], occs[1]
    finally:
        c.close()


def main():
    c = TestClient(app.app)
    aid, oid1, oid2 = seed()

    def pub():
        r = c.get(f'/api/public/activities/{aid}')
        assert r.status_code == 200, r.text[:200]
        return r.json(), r.text

    det, _ = pub()
    ok(isinstance(det.get('leaders'), list) and len(det['leaders']) >= 2,
       'C 端返回按团期分组的 leaders', f"{len(det.get('leaders') or [])} 组")
    ok(all(g['leaders'] == [] for g in det['leaders']), '未指派时每个团期都是空的（不显示推荐名单）')

    # 建两位领队：甲有头像，乙没有
    a = c.post(f'/api/club/{CLUB}/leaders', json={'name': '回归领队甲', 'phone': '13800000001', 'specialties': ['徒步']})
    b = c.post(f'/api/club/{CLUB}/leaders', json={'name': '回归领队乙', 'phone': '13800000002', 'specialties': ['露营']})
    ok(a.status_code == 200 and b.status_code == 200, '创建领队成功', f"{a.status_code}/{b.status_code}")
    la, lb = a.json()['id'], b.json()['id']

    up = c.post(f'/api/club/{CLUB}/leaders/{la}/avatar', files={'file': ('avatar.png', io.BytesIO(PNG), 'image/png')})
    ok(up.status_code == 200, '上传领队头像成功', up.text[:100])
    club_avatar = up.json().get('avatarUrl')
    ok(str(club_avatar).startswith(f'/api/club/{CLUB}/leaders/'), '上传返回的是俱乐部域代理地址', club_avatar)

    # 从名册指派（带 leaderId）→ 只有这条路径能关联到头像
    r1 = c.post(f'/api/club/{CLUB}/occurrences/{oid1}/leaders', json={'leaderId': la})
    r2 = c.post(f'/api/club/{CLUB}/occurrences/{oid2}/leaders', json={'leaderId': lb})
    ok(r1.status_code == 200 and r2.status_code == 200, '按名册指派领队成功', f"{r1.status_code}/{r2.status_code}")

    det, raw = pub()
    g1 = next(g for g in det['leaders'] if g['occurrenceId'] == oid1)
    g2 = next(g for g in det['leaders'] if g['occurrenceId'] == oid2)
    ok([x['name'] for x in g1['leaders']] == ['回归领队甲'], '团期1 显示带队领队', str([x['name'] for x in g1['leaders']]))
    ok(g1['leaders'][0]['role'] == '领队' and g2['leaders'][0]['role'] == '领队', '角色如实下发')

    # ★ 隐私：手机号绝不能出现在 C 端返回体里
    ok('13800000001' not in raw and '13800000002' not in raw, 'C 端返回体里没有领队手机号')
    ok('recommendations' not in raw, 'C 端不下发推荐名单')
    ok('roster' not in raw, 'C 端不下发领队名册')

    # 头像走公开代理
    av = g1['leaders'][0]['avatarUrl']
    ok(str(av).startswith('/api/public/leaders/'), '头像改写为 C 端可访问的公开代理', av)
    rr = c.get(av)
    ok(rr.status_code == 200 and rr.content == PNG, '公开头像代理能取回原图', f"{rr.status_code} {len(rr.content)}B")
    ok(g2['leaders'][0]['avatarUrl'] is None, '没设头像的领队回 None（前端退回首字占位）')
    ok(c.get('/api/public/leaders/99999/avatar.png').status_code == 404, '不存在的领队头像返回 404')

    # 手工填人名的指派（执行端临时加人，leader_id 为空）不能让整块崩掉
    r3 = c.post(f'/api/club/{CLUB}/occurrences/{oid2}/leaders',
                json={'name': '临时增援', 'phone': '13800000003', 'role': '协作领队'})
    ok(r3.status_code == 200, '手工填人名的指派仍可用', r3.text[:100])
    det2, raw2 = pub()
    g2b = next(g for g in det2['leaders'] if g['occurrenceId'] == oid2)
    ok(len(g2b['leaders']) == 2 and g2b['leaders'][1]['avatarUrl'] is None,
       '无 leader_id 的指派照常展示、头像为 None', str([x['name'] for x in g2b['leaders']]))
    ok('13800000003' not in raw2, '手工填的手机号同样不下发')

    # 撤下之后 C 端要跟着回落
    ass = c.get(f'/api/club/{CLUB}/activities/{aid}').json()['leaderPlan']['occurrences']
    for occ in ass:
        for x in occ['leaders']:
            c.delete(f"/api/club/{CLUB}/occurrences/{occ['occurrenceId']}/leaders/{x['assignmentId']}")
    det3, _ = pub()
    ok(all(g['leaders'] == [] for g in det3['leaders']), '撤下领队后 C 端回到空（不留残影）')

    # ★ 生产白名单：IS_PROD 分支必须放行 leaders，否则线上静默丢掉整块
    c.post(f'/api/club/{CLUB}/occurrences/{oid1}/leaders', json={'leaderId': la})
    had = app.IS_PROD
    app.IS_PROD = True
    try:
        prod = app.public_activity(aid)
    finally:
        app.IS_PROD = had
    praw = json.dumps(prod, ensure_ascii=False)
    ok('leaders' in prod, 'IS_PROD 白名单放行 leaders')
    ok('/api/public/leaders/' in praw, 'IS_PROD 下头像仍是公开代理地址')
    ok('13800000001' not in praw, 'IS_PROD 下手机号依然不出现在返回体里')
    ok('recommendations' not in praw, 'IS_PROD 下推荐名单依然不下发')

    print()
    if FAILS:
        print(f'RESULT: {len(FAILS)} 项失败 -> ' + '；'.join(FAILS))
        sys.exit(1)
    print('RESULT: ALL PASS · C 端带队领队（脱敏 / 公开头像 / 生产白名单）')


if __name__ == '__main__':
    main()

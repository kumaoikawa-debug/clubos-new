"""回归测试：站内咨询对话 + 活动详情排版根治（2026-11-05 用户截图三需求）。

覆盖：
  ① 建单返回 accessToken 并写入首条消息；无 token / 错 token / 跨 kind 一律拒；
  ② 顾客追问 ↔ 领队回复 ↔ 后台代回复 四端读同一份时间线，状态/未读正确流转；
  ③ 老咨询没有 token 时 C 端不能凭 id 猜到别人的手机号，但后台仍可回复；
  ④ 前端静态守门：图注脱离 grid 窄列、高亮块去装饰、领队多人横滑/单人专版、
     我的咨询入口、三端回复入口、AI 生成契约禁令、缓存版本号。

隔离方式同其它 smoke：app.py 模块级会 init_db()，必须在 import 前把
CLUBOS_DB_PATH 指到临时库。
"""
import os, sys, tempfile, pathlib, json

sys.path.insert(0, '.')
os.environ['CLUBOS_DB_PATH'] = os.path.join(
    tempfile.mkdtemp(prefix='clubos-inquiry-smoke-'), 'clubos.db')
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')
os.environ.setdefault('MOCK_AI', '1')
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')

from fastapi.testclient import TestClient  # noqa: E402
import db  # noqa: E402
import app as appmod  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('  PASS  ' if ok else '  FAIL  ') + name + (f'   | {detail}' if detail else ''))
    if not ok:
        FAILS.append(name)


db.init_db()
client = TestClient(appmod.app)
ROOT = pathlib.Path('.')
CLUB = 1

# ── 夹具：一个已发布活动 + 一名在册领队 ──────────────────────────────────
with db.conn() as c:
    a = c.execute("SELECT id FROM activities WHERE club_id=? AND status='published' ORDER BY id LIMIT 1",
                  (CLUB,)).fetchone()
    if a:
        AID = int(a['id'])
    else:
        cols = {r['name']: r for r in c.execute('PRAGMA table_info(activities)').fetchall()}
        fields = {'club_id': CLUB, 'title': '咨询对话测试线路', 'status': 'published'}
        for k, meta in cols.items():
            if not meta['notnull'] or meta['dflt_value'] is not None or k in fields:
                continue
            if k in ('created_at', 'updated_at'):
                fields[k] = '2026-01-01 00:00:00'
            elif 'INT' in (meta['type'] or '').upper() or 'REAL' in (meta['type'] or '').upper():
                fields[k] = 0
            else:
                fields[k] = ''
        ks = ','.join(fields)
        qs = ','.join('?' for _ in fields)
        AID = int(c.execute(f'INSERT INTO activities({ks}) VALUES({qs})',
                            tuple(fields.values())).lastrowid)
    l = c.execute('SELECT id FROM club_leaders WHERE club_id=? ORDER BY id LIMIT 1',
                  (CLUB,)).fetchone()
    if l:
        LID = int(l['id'])
    else:
        LID = int(c.execute("INSERT INTO club_leaders(club_id,name,phone,status) "
                            "VALUES(?,?,?,?)", (CLUB, '测试领队', '13800000001', 'active')).lastrowid)

print('\n── 建单 / 凭证 ──')
r = client.post(f'/api/public/leaders/{LID}/inquiries', json={
    'message': '这条线强度大吗？我没走过高原。', 'name': '小山',
    'phone': '13900000001', 'activityId': AID})
check('领队咨询建单 200', r.status_code == 200, str(r.status_code))
LI = r.json()
LQID = int(LI.get('inquiryId') or 0)
LTK = str(LI.get('accessToken') or '')
check('返回 accessToken', len(LTK) >= 20 and LI.get('kind') == 'leader', LTK[:12])
check('返回 inquiryId', LQID > 0, str(LQID))

r = client.post(f'/api/public/activities/{AID}/inquiries', json={
    'message': '几月去最漂亮？小孩能去吗？', 'name': '小山', 'phone': '13900000001'})
check('线路咨询建单 200', r.status_code == 200, str(r.status_code))
RI = r.json()
RQID = int(RI.get('inquiryId') or 0)
RTK = str(RI.get('accessToken') or '')
check('线路咨询也返回 token', len(RTK) >= 20 and RI.get('kind') == 'route', RTK[:12])

with db.conn() as c:
    n1 = c.execute("SELECT COUNT(*) FROM inquiry_messages WHERE kind='leader' AND inquiry_id=?",
                   (LQID,)).fetchone()[0]
    n2 = c.execute("SELECT COUNT(*) FROM inquiry_messages WHERE kind='route' AND inquiry_id=?",
                   (RQID,)).fetchone()[0]
check('两条咨询各写入首条消息', n1 == 1 and n2 == 1, f'leader={n1} route={n2}')
with db.conn() as c:
    senders = [x['sender'] for x in c.execute(
        'SELECT sender FROM inquiry_messages WHERE inquiry_id=? AND kind=?', (LQID, 'leader')).fetchall()]
check('首条消息 sender=customer', senders == ['customer'], str(senders))

print('\n── C 端凭证闸门 ──')
r = client.get(f'/api/public/inquiries/leader/{LQID}')
check('无 token 403', r.status_code == 403, str(r.status_code))
r = client.get(f'/api/public/inquiries/leader/{LQID}?token=wrong-token')
check('错 token 404', r.status_code == 404, str(r.status_code))
r = client.get(f'/api/public/inquiries/route/{LQID}?token={LTK}')
check('跨 kind 404', r.status_code == 404, str(r.status_code))
r = client.get(f'/api/public/inquiries/leader/{LQID}?token={LTK}')
check('正确 token 200', r.status_code == 200, str(r.status_code))
d = r.json()
check('时间线含原始留言', len(d.get('messages') or []) == 1 and '强度' in d['messages'][0]['body'],
      json.dumps(d.get('messages'), ensure_ascii=False)[:120])
check('初始 replyCount=0', int(d.get('replyCount') or 0) == 0, str(d.get('replyCount')))
check('不下发成本/内部字段', 'source_json' not in d and 'cost' not in d)

print('\n── 领队回复 → 顾客可见 ──')
r = client.get(f'/api/leader/inquiries?club_id={CLUB}&leader_id={LID}')
check('领队端列表 200', r.status_code == 200, str(r.status_code))
li = [x for x in r.json().get('inquiries', []) if int(x['id']) == LQID]
check('领队能看到这条咨询', len(li) == 1, str(len(li)))
if li:
    check('领队列表下发 replyCount', 'replyCount' in li[0], str(li[0].keys()))
r = client.post(f'/api/leader/inquiries/{LQID}/messages?club_id={CLUB}&leader_id={LID}',
                json={'message': '整体中等强度，第一天爬升约 600 米，有徒步经验没问题。'})
check('领队回复 200', r.status_code == 200, str(r.status_code))
check('回复写入时间线', len(r.json().get('messages') or []) == 2, str(len(r.json().get('messages') or [])))
with db.conn() as c:
    st = c.execute('SELECT status,unread_customer FROM leader_inquiries WHERE id=?', (LQID,)).fetchone()
check('回复后 status=handled', st['status'] == 'handled', str(st['status']))
check('回复后顾客未读=1', int(st['unread_customer']) == 1, str(st['unread_customer']))

r = client.get(f'/api/public/inquiries/leader/{LQID}?token={LTK}')
d = r.json()
check('C 端看到领队回复', len(d['messages']) == 2 and d['messages'][1]['sender'] == 'leader',
      json.dumps(d['messages'], ensure_ascii=False)[:160])
check('C 端 replyCount=1', int(d.get('replyCount') or 0) == 1, str(d.get('replyCount')))
with db.conn() as c:
    unread = int(c.execute('SELECT unread_customer FROM leader_inquiries WHERE id=?',
                           (LQID,)).fetchone()[0])
check('读后未读清零', unread == 0, str(unread))

print('\n── 顾客追问 → 后台红点重现 ──')
r = client.post(f'/api/public/inquiries/leader/{LQID}/messages?token={LTK}',
                json={'message': '那需要带登山杖吗？'})
check('顾客追问 200', r.status_code == 200, str(r.status_code))
check('追问后 3 条消息', len(r.json().get('messages') or []) == 3, str(len(r.json().get('messages') or [])))
with db.conn() as c:
    st = c.execute('SELECT status FROM leader_inquiries WHERE id=?', (LQID,)).fetchone()
check('追问后 status 回 new', st['status'] == 'new', str(st['status']))
r = client.get(f'/api/club/{CLUB}/inquiries?kind=all&status=all')
check('后台统一收件箱 200', r.status_code == 200, str(r.status_code))
items = r.json().get('items') or []
tgt = [x for x in items if x['kind'] == 'leader' and int(x['id']) == LQID]
check('后台列表含该咨询', len(tgt) == 1, str(len(tgt)))
if tgt:
    check('后台下发 replyCount=1', int(tgt[0].get('replyCount') or 0) == 1, str(tgt[0].get('replyCount')))
    check('后台下发 lastMessageAt', 'lastMessageAt' in tgt[0], str(tgt[0].keys()))

print('\n── 后台代回复 / thread ──')
r = client.get(f'/api/club/{CLUB}/inquiries/leader/{LQID}/thread')
check('后台 thread 200', r.status_code == 200, str(r.status_code))
check('后台 thread 3 条', len(r.json().get('messages') or []) == 3, str(len(r.json().get('messages') or [])))
r = client.post(f'/api/club/{CLUB}/inquiries/route/{RQID}/messages',
                json={'message': '5 月和 10 月最漂亮，10 岁以上可以跟。'})
check('后台代回复线路咨询 200', r.status_code == 200, str(r.status_code))
r = client.get(f'/api/public/inquiries/route/{RQID}?token={RTK}')
check('C 端看到后台回复', r.status_code == 200 and len(r.json().get('messages') or []) == 2
      and r.json()['messages'][1]['sender'] == 'club', str(r.status_code))
r = client.get(f'/api/leader/inquiries?club_id={CLUB}&leader_id={LID}')
# 两张咨询表各自 AUTOINCREMENT，id 会撞号（都是 1）——不能按 id 判隔离，
# 按内容判：线路咨询的原文绝不能出现在领队端的「问我的」列表里。
_leader_inqs = r.json().get('inquiries', [])
check('线路咨询不串到领队端', all('几月去最漂亮' not in str(x.get('message') or '')
                                 for x in _leader_inqs), str(len(_leader_inqs)))

print('\n── 老数据无 token / 校验 ──')
with db.conn() as c:
    old = int(c.execute("INSERT INTO route_inquiries(club_id,activity_id,name,phone,message) "
                        "VALUES(?,?,?,?,?)", (CLUB, AID, '老顾客', '13700000001', '历史问题')).lastrowid)
r = client.get(f'/api/public/inquiries/route/{old}?token=anything')
check('老咨询无 token 404（防猜 id）', r.status_code == 404, str(r.status_code))
r = client.post(f'/api/club/{CLUB}/inquiries/route/{old}/messages', json={'message': '历史问题已收到'})
check('后台仍可回复老咨询', r.status_code == 200, str(r.status_code))
r = client.post(f'/api/public/inquiries/leader/{LQID}/messages?token={LTK}', json={'message': 'x'})
check('追问过短 400', r.status_code == 400, str(r.status_code))
r = client.post(f'/api/club/{CLUB}/inquiries/leader/{LQID}/messages', json={'message': 'x'})
check('回复过短 400', r.status_code == 400, str(r.status_code))
r = client.get(f'/api/club/{CLUB}/inquiries/nope/{LQID}/thread')
check('未知 kind 400', r.status_code == 400, str(r.status_code))

print('\n── 前端静态守门 ──')


def read(p):
    return (ROOT / p).read_text(encoding='utf-8')


shared_css = read('static/shared.css')
cap = shared_css.split('.ed-cap-pill{', 1)[1].split('}', 1)[0] if '.ed-cap-pill{' in shared_css else ''
check('图注强制 grid-column 1/-1', 'grid-column:1/-1' in cap, cap[:120])
check('图注不再用 999px 圆角', '999px' not in cap and 'border-radius:0' in cap, cap[:120])
check('图注 display:block 横排', 'display:block' in cap)
hl = shared_css.split('.ed-highlight{', 1)[1].split('}', 1)[0] if '.ed-highlight{' in shared_css else ''
check('高亮块去掉浅绿底与圆角', 'background:none' in hl and 'border-radius:0' in hl, hl[:120])
inf = shared_css.split('.info-note__i{', 1)[1].split('}', 1)[0] if '.info-note__i{' in shared_css else ''
check('编号不再用圆形徽章', 'border-radius:0' in inf and 'background:none' in inf, inf[:120])

web_js = read('static/web/web.js')
leaders = web_js.split('function leadersHtml(a){', 1)[1][:1400] if 'function leadersHtml(a){' in web_js else ''
check('单领队走 solo 专版', 'if(list.length===1)' in leaders and 'w-lead__solo' in leaders)
check('多领队走 rail 横滑', 'w-lead__rail' in leaders and '左右滑动查看' in leaders)
check('保留旧 grid 给领队团队专区', '.w-lead__grid' in read('static/web/web.js'))
check('C 端保存 accessToken', 'myInquirySave(' in web_js and 'accessToken' in web_js)
check('C 端可追问', 'myInquirySend' in web_js and 'myInquiryRefresh' in web_js)
check('对话抽屉导出到 window', 'window.myInquiryOpen=myInquiryOpen' in web_js)

web_css = read('static/web/web.css')
check('横滑轨道样式', '.web-shell .w-lead__rail' in web_css and 'scroll-snap-type:x mandatory' in web_css)
check('单人专版样式', '.w-lead__solo .w-lc' in web_css and 'grid-template-columns:92px' in web_css)
check('C 端图注同步根治', '.web-shell .ed-cap-pill' in web_css and 'grid-column:1/-1' in web_css)
check('C 端高亮同步去装饰', '.web-shell .ed-highlight' in web_css and 'background:none' in web_css)

check('「我的」有咨询入口', "pushMe('inquiries')" in read('static/web/index.html'))
me_js = read('static/web/me.js')
check('me.js 注册咨询子页', 'SUB_ME.inquiries=function' in me_js)
check('咨询列表用 data 属性匹配（不拼 id 选择器）',
      'data-kind=' in me_js and "getElementById('meIq-'" not in me_js)
check('后台可站内回复', 'infoReply' in read('static/club/club.js') and 'infoThreadHtml' in read('static/club/club.js'))
check('领队端可站内回复', 'replyInquiry' in read('static/leader/leader.js')
      and 'viewInquiryThread' in read('static/leader/leader.js'))
check('AI 契约禁止装饰性容器', '禁止装饰性文字容器' in read('ai_engine.py'))
check('iframe 兜底存在', 'figcaption{display:block' in read('static/shared.js'))
check('schema 有消息表', 'CREATE TABLE IF NOT EXISTS inquiry_messages' in read('schema.sql'))
check('老库补列存在', "_ensure_column(c,_t,'access_token'" in read('db.py'))
for f in ['static/web/index.html', 'static/club/index.html', 'static/leader/index.html']:
    check(f'{f} 缓存版本 20261105d', '20261105d' in read(f))

print('\n' + ('ALL PASS' if not FAILS else f'FAILED: {len(FAILS)} -> ' + ', '.join(FAILS)))
sys.exit(1 if FAILS else 0)

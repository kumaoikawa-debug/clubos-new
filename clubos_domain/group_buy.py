"""拼团 / 团购成团（v0.39，P1-3）。

参考「远拓旅游 SaaS · 营销管理 - 拼团」：俱乐部为某场活动配置「N 人成团、拼团价」，
会员开团后邀请好友参团，满员即成团，超时未成团自动失效（惰性判定，无需定时任务）。

与既有体系的关系（刻意保持最小爆炸半径）：
- marketing_campaigns（P0-1）虽预留 GROUP 类型，但那是**报价期折扣引擎**；拼团是
  「先组队、后结算」的状态机，独立建表 / 独立端点，**不改动 evaluate()**。
- 拼团价只通过 price_quote 的 groupBuy 字段**只读下发**供 C 端展示与对账，
  **绝不改 payable、绝不碰 checkout 确认流程** —— 否则会出现「报价是拼团价、
  实收却是原价」的口径不一致。真正的收款优惠由后续结算接入，本模块留 priceCents 快照。
- 成团后联动 P0-4 通知中枢发站内信（模板未配置时静默跳过）。

金额一律「分」INTEGER（字段带 Cents 后缀），对外同时给「分 / 元」两套字段。
设计纪律：未开启/过期/满员/重复参团 → 静默拒绝返回 reason，不阻断任何既有下单链路；
写操作吞异常返回 {ok,reason}，由路由决定是否 raise 触发事务回滚。
本模块 stdlib-only，可被 importlib 独立加载测试。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

CAMPAIGN_STATUSES = ('DRAFT', 'ACTIVE', 'PAUSED', 'ENDED')
TEAM_STATUSES = ('OPEN', 'SUCCESS', 'EXPIRED', 'CANCELLED')
MEMBER_STATUSES = ('JOINED', 'SUCCESS', 'CANCELLED')
MIN_PEOPLE = 2
MAX_PEOPLE = 100
MAX_DURATION_HOURS = 720          # 最长 30 天
MAX_TEAMS = 10000
MAX_TITLE = 80


# ──────────────────────────── 工具 ────────────────────────────
def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def loads(raw):
    if not raw:
        return {}
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except (TypeError, ValueError):
        return {}


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _int(v, default=0, lo=0, hi=10**9) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _text(v, limit=80) -> str:
    return str(v or '').strip()[:limit]


def to_cents(amount) -> int:
    """元 → 分。非法 / 负数 / NaN / inf 一律归 0。"""
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(v) or v <= 0:
        return 0
    return int(round(v * 100))


def from_cents(cents) -> float:
    try:
        return round(int(cents or 0) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _yuan_from(payload, base, key) -> float:
    """payload 用「元」；base 优先读同名元字段，仅有 Cents 字段才 /100 换算。

    ★ 绝不把 base[key+'Cents'] 直接喂给 to_cents()（P1-1 的坑：会把分当元放大 100 倍）。
    """
    v = payload.get(key)
    if v is None:
        v = (base or {}).get(key)
        if v is None:
            try:
                v = int((base or {}).get(key + 'Cents') or 0) / 100.0
            except (TypeError, ValueError):
                v = 0
    return v


# ──────────────────────────── 配置归一化 ────────────────────────────
def normalize_campaign(payload: dict, current: dict | None = None) -> dict:
    """校验并归一化拼团活动配置；非法抛 ValueError（后台配置应显式报错）。"""
    p = payload if isinstance(payload, dict) else {}
    base = current if isinstance(current, dict) else {}
    title = _text(p.get('title', base.get('title', '')), MAX_TITLE)
    if not title:
        raise ValueError('拼团活动标题不能为空')
    # 显式校验而非 _int 夹逼：夹逼会把「1 人成团」这种非法配置洗成 2 人（P1-2 的教训）。
    try:
        required = int(p.get('requiredPeople', base.get('requiredPeople', 3)))
    except (TypeError, ValueError):
        raise ValueError('成团人数必须是整数')
    if not (MIN_PEOPLE <= required <= MAX_PEOPLE):
        raise ValueError('成团人数需在 %d~%d 之间' % (MIN_PEOPLE, MAX_PEOPLE))
    gpc = to_cents(_yuan_from(p, base, 'groupPrice'))
    if gpc <= 0:
        raise ValueError('拼团价必须大于 0')
    opc = to_cents(_yuan_from(p, base, 'originalPrice'))
    if opc and opc < gpc:
        raise ValueError('原价不能低于拼团价')
    status = str(p.get('status', base.get('status', 'DRAFT')) or 'DRAFT').strip().upper()
    if status not in CAMPAIGN_STATUSES:
        raise ValueError('非法状态: %s' % status)
    start = p.get('startAt', base.get('startAt')) or None
    end = p.get('endAt', base.get('endAt')) or None
    try:
        start = int(start) if start else None
        end = int(end) if end else None
    except (TypeError, ValueError):
        raise ValueError('startAt/endAt 必须是秒级时间戳')
    if start and end and end <= start:
        raise ValueError('结束时间必须晚于开始时间')
    return {
        'title': title,
        'activityId': _int(p.get('activityId', base.get('activityId', 0)), 0, 0, 10**9),
        'requiredPeople': required,
        'groupPriceCents': gpc, 'groupPrice': from_cents(gpc),
        'originalPriceCents': opc, 'originalPrice': from_cents(opc),
        'durationHours': _int(p.get('durationHours', base.get('durationHours', 24)), 24,
                              1, MAX_DURATION_HOURS),
        'maxTeams': _int(p.get('maxTeams', base.get('maxTeams', 0)), 0, 0, MAX_TEAMS),
        'status': status, 'startAt': start, 'endAt': end,
    }


def campaign_view(row) -> dict:
    d = dict(row) if row is not None else {}
    gpc = int(d.get('group_price_cents') or 0)
    opc = int(d.get('original_price_cents') or 0)
    return {
        'id': d.get('id'), 'clubId': d.get('club_id'),
        'activityId': d.get('activity_id'), 'title': d.get('title'),
        'requiredPeople': int(d.get('required_people') or 3),
        'groupPriceCents': gpc, 'groupPrice': from_cents(gpc),
        'originalPriceCents': opc, 'originalPrice': from_cents(opc),
        'durationHours': int(d.get('duration_hours') or 24),
        'maxTeams': int(d.get('max_teams') or 0),
        'status': d.get('status') or 'DRAFT',
        'startAt': d.get('start_at'), 'endAt': d.get('end_at'),
        'createdAt': d.get('created_at'), 'updatedAt': d.get('updated_at'),
        'teamCount': int(d.get('team_count') or 0) if 'team_count' in d else None,
    }


def team_view(row) -> dict:
    d = dict(row) if row is not None else {}
    pc = int(d.get('price_cents') or 0)
    req = int(d.get('required_people') or 0)
    cnt = int(d.get('member_count') or 0)
    return {
        'id': d.get('id'), 'campaignId': d.get('campaign_id'),
        'clubId': d.get('club_id'), 'activityId': d.get('activity_id'),
        'leaderUserId': d.get('leader_user_id'),
        'requiredPeople': req, 'memberCount': cnt, 'remaining': max(0, req - cnt),
        'priceCents': pc, 'price': from_cents(pc),
        'status': d.get('status') or 'OPEN',
        'expireAt': d.get('expire_at'), 'createdAt': d.get('created_at'),
        'updatedAt': d.get('updated_at'),
        'campaignTitle': d.get('campaign_title'),
    }


def member_view(row) -> dict:
    d = dict(row) if row is not None else {}
    pc = int(d.get('price_cents') or 0)
    return {
        'id': d.get('id'), 'teamId': d.get('team_id'), 'clubId': d.get('club_id'),
        'userId': d.get('user_id'), 'role': d.get('role'),
        'priceCents': pc, 'price': from_cents(pc),
        'status': d.get('status') or 'JOINED', 'joinedAt': d.get('joined_at'),
        'userName': d.get('user_name'), 'userPhone': d.get('user_phone'),
    }


def window_ok(camp: dict, ts: int | None = None) -> bool:
    """活动是否在可开团时间窗内。"""
    now = int(ts if ts is not None else now_ts())
    st, ed = camp.get('startAt'), camp.get('endAt')
    try:
        if st and now < int(st):
            return False
        if ed and now > int(ed):
            return False
    except (TypeError, ValueError):
        pass
    return True


def team_active(row, ts: int | None = None) -> bool:
    """团是否仍可参团（只读推断，不落库）。"""
    d = dict(row) if row is not None else {}
    if (d.get('status') or 'OPEN') != 'OPEN':
        return False
    exp = d.get('expire_at')
    try:
        if exp and int(ts if ts is not None else now_ts()) >= int(exp):
            return False
    except (TypeError, ValueError):
        pass
    return True


# ──────────────────────────── DB：配置 CRUD ────────────────────────────
def list_campaigns(c, club_id: int, only_active=False, activity_id=None, limit=200) -> list:
    sql = """SELECT cp.*,
                    (SELECT COUNT(*) FROM group_buy_teams t WHERE t.campaign_id=cp.id) AS team_count
             FROM group_buy_campaigns cp WHERE cp.club_id=?"""
    args = [int(club_id)]
    if only_active:
        sql += " AND cp.status='ACTIVE'"
    else:
        sql += " AND cp.status!='ENDED'"
    if activity_id:
        sql += ' AND cp.activity_id=?'
        args.append(int(activity_id))
    sql += ' ORDER BY cp.id DESC LIMIT ?'
    args.append(max(1, min(500, int(limit or 200))))
    return [campaign_view(r) for r in c.execute(sql, args).fetchall()]


def get_campaign(c, club_id: int, campaign_id: int):
    r = c.execute('SELECT * FROM group_buy_campaigns WHERE id=? AND club_id=?',
                  (int(campaign_id), int(club_id))).fetchone()
    if not r:
        raise LookupError('拼团活动不存在')
    return campaign_view(r)


def create_campaign(c, club_id: int, payload: dict) -> dict:
    cfg = normalize_campaign(payload)
    c.execute("""INSERT INTO group_buy_campaigns
                 (club_id,activity_id,title,required_people,group_price_cents,original_price_cents,
                  duration_hours,max_teams,status,start_at,end_at,created_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
              (int(club_id), cfg['activityId'], cfg['title'], cfg['requiredPeople'],
               cfg['groupPriceCents'], cfg['originalPriceCents'], cfg['durationHours'],
               cfg['maxTeams'], cfg['status'], cfg['startAt'], cfg['endAt']))
    return get_campaign(c, club_id, int(c.execute('SELECT last_insert_rowid()').fetchone()[0]))


def update_campaign(c, club_id: int, campaign_id: int, payload: dict) -> dict:
    cur = get_campaign(c, club_id, campaign_id)
    cfg = normalize_campaign(payload, cur)
    c.execute("""UPDATE group_buy_campaigns SET
                   activity_id=?,title=?,required_people=?,group_price_cents=?,original_price_cents=?,
                   duration_hours=?,max_teams=?,status=?,start_at=?,end_at=?,updated_at=datetime('now')
                 WHERE id=? AND club_id=?""",
              (cfg['activityId'], cfg['title'], cfg['requiredPeople'], cfg['groupPriceCents'],
               cfg['originalPriceCents'], cfg['durationHours'], cfg['maxTeams'], cfg['status'],
               cfg['startAt'], cfg['endAt'], int(campaign_id), int(club_id)))
    return get_campaign(c, club_id, campaign_id)


def delete_campaign(c, club_id: int, campaign_id: int) -> dict:
    get_campaign(c, club_id, campaign_id)
    c.execute("""UPDATE group_buy_campaigns SET status='ENDED',updated_at=datetime('now')
                 WHERE id=? AND club_id=?""", (int(campaign_id), int(club_id)))
    return {'ok': True}


# ──────────────────────────── DB：团状态 ────────────────────────────
def expire_due(c, club_id: int | None = None, ts: int | None = None) -> int:
    """惰性过期：把已到点仍为 OPEN 的团置 EXPIRED。无需定时任务。"""
    try:
        now = int(ts if ts is not None else now_ts())
        sql = "UPDATE group_buy_teams SET status='EXPIRED',updated_at=datetime('now') WHERE status='OPEN' AND expire_at IS NOT NULL AND expire_at<=?"
        args = [now]
        if club_id is not None:
            sql += ' AND club_id=?'
            args.append(int(club_id))
        cur = c.execute(sql, args)
        return int(cur.rowcount or 0)
    except Exception:
        return 0


def _has_open_team(c, club_id, campaign_id, user_id, exclude_team_id=None) -> bool:
    """同一活动、同一用户只允许在一个进行中的团里（防一人多团刷位）。"""
    sql = """SELECT 1 FROM group_buy_teams t
             WHERE t.club_id=? AND t.campaign_id=? AND t.status='OPEN'
               AND (t.leader_user_id=? OR EXISTS(
                     SELECT 1 FROM group_buy_members m WHERE m.team_id=t.id AND m.user_id=?
                       AND m.status IN ('JOINED','SUCCESS')))
               AND t.expire_at>?"""
    args = [int(club_id), int(campaign_id), int(user_id), int(user_id), now_ts()]
    if exclude_team_id:
        sql += ' AND t.id!=?'
        args.append(int(exclude_team_id))
    return bool(c.execute(sql, args).fetchone())


def open_team(c, *, club_id: int, campaign_id: int, user_id: int, ts: int | None = None) -> dict:
    """开团：团长自动成为第一位成员。"""
    res = {'ok': False, 'reason': '', 'teamId': None}
    try:
        club_id, campaign_id, user_id = int(club_id), int(campaign_id), int(user_id)
        now = int(ts if ts is not None else now_ts())
        camp = get_campaign(c, club_id, campaign_id)
        if camp['status'] != 'ACTIVE':
            res['reason'] = 'campaign-not-active'
            return res
        if not window_ok(camp, now):
            res['reason'] = 'out-of-window'
            return res
        if camp['maxTeams'] and int(c.execute(
                'SELECT COUNT(*) FROM group_buy_teams WHERE campaign_id=? AND status IN (\'OPEN\',\'SUCCESS\')',
                (campaign_id,)).fetchone()[0] or 0) >= camp['maxTeams']:
            res['reason'] = 'max-teams'
            return res
        if _has_open_team(c, club_id, campaign_id, user_id):
            res['reason'] = 'already-in-team'
            return res
        expire = now + int(camp['durationHours']) * 3600
        c.execute("""INSERT INTO group_buy_teams
                     (campaign_id,club_id,activity_id,leader_user_id,required_people,price_cents,
                      status,member_count,expire_at,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,'OPEN',1,?,datetime('now'),datetime('now'))""",
                  (campaign_id, club_id, camp['activityId'], user_id,
                   camp['requiredPeople'], camp['groupPriceCents'], expire))
        tid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        c.execute("""INSERT INTO group_buy_members
                     (team_id,club_id,user_id,role,price_cents,status,joined_at)
                     VALUES(?,?,?,'LEADER',?,'JOINED',datetime('now'))""",
                  (tid, club_id, user_id, camp['groupPriceCents']))
        res.update(ok=True, reason='ok', teamId=tid,
                   team=team_view(c.execute('SELECT * FROM group_buy_teams WHERE id=?', (tid,)).fetchone()))
        return res
    except LookupError:
        res['reason'] = 'campaign-not-found'
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def join_team(c, *, club_id: int, team_id: int, user_id: int, ts: int | None = None) -> dict:
    """参团；满员自动成团。返回 isSuccess + 全团成员（供路由发通知）。"""
    res = {'ok': False, 'reason': '', 'isSuccess': False, 'team': None, 'members': []}
    try:
        club_id, team_id, user_id = int(club_id), int(team_id), int(user_id)
        now = int(ts if ts is not None else now_ts())
        expire_due(c, club_id, now)
        t = c.execute('SELECT * FROM group_buy_teams WHERE id=? AND club_id=?',
                      (team_id, club_id)).fetchone()
        if not t:
            res['reason'] = 'team-not-found'
            return res
        if t['status'] == 'SUCCESS':
            res['reason'] = 'already-success'
            return res
        if not team_active(t, now):
            # 已被 expire_due 惰性落库为 EXPIRED 的团，也要明确回报「已过期」，
            # 而不是笼统的 team-closed（C 端需要区分「过期」与「取消 / 已成团」）。
            res['reason'] = 'team-expired' if t['status'] in ('OPEN', 'EXPIRED') else 'team-closed'
            return res
        camp = get_campaign(c, club_id, int(t['campaign_id']))
        if camp['status'] != 'ACTIVE':
            res['reason'] = 'campaign-not-active'
            return res
        req = int(t['required_people'] or 0)
        if int(t['member_count'] or 0) >= req:
            res['reason'] = 'team-full'
            return res
        if c.execute('SELECT 1 FROM group_buy_members WHERE team_id=? AND user_id=?',
                     (team_id, user_id)).fetchone():
            res['reason'] = 'already-joined'
            return res
        if _has_open_team(c, club_id, int(t['campaign_id']), user_id, exclude_team_id=team_id):
            res['reason'] = 'already-in-team'
            return res
        c.execute("""INSERT INTO group_buy_members
                     (team_id,club_id,user_id,role,price_cents,status,joined_at)
                     VALUES(?,?,?,'MEMBER',?,'JOINED',datetime('now'))""",
                  (team_id, club_id, user_id, int(t['price_cents'] or 0)))
        cnt = int(c.execute('SELECT COUNT(*) FROM group_buy_members WHERE team_id=? AND status IN (\'JOINED\',\'SUCCESS\')',
                            (team_id,)).fetchone()[0] or 0)
        success = cnt >= req
        if success:
            c.execute("""UPDATE group_buy_teams SET status='SUCCESS',member_count=?,
                         updated_at=datetime('now') WHERE id=?""", (cnt, team_id))
            c.execute("UPDATE group_buy_members SET status='SUCCESS' WHERE team_id=?", (team_id,))
        else:
            c.execute("UPDATE group_buy_teams SET member_count=?,updated_at=datetime('now') WHERE id=?",
                      (cnt, team_id))
        tr = c.execute('SELECT * FROM group_buy_teams WHERE id=?', (team_id,)).fetchone()
        mem = c.execute("""SELECT m.*,u.name AS user_name,u.phone AS user_phone
                           FROM group_buy_members m LEFT JOIN users u ON u.id=m.user_id
                           WHERE m.team_id=? ORDER BY m.id""", (team_id,)).fetchall()
        res.update(ok=True, reason='ok', isSuccess=success, team=team_view(tr),
                   members=[member_view(m) for m in mem])
        return res
    except LookupError:
        res['reason'] = 'campaign-not-found'
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def cancel_team(c, *, club_id: int, team_id: int, user_id: int | None = None,
                by_admin=False) -> dict:
    """取消团：仅团长本人或后台可操作，只有 OPEN 团可取消。"""
    res = {'ok': False, 'reason': ''}
    try:
        t = c.execute('SELECT * FROM group_buy_teams WHERE id=? AND club_id=?',
                      (int(team_id), int(club_id))).fetchone()
        if not t:
            res['reason'] = 'team-not-found'
            return res
        if not by_admin and int(t['leader_user_id']) != int(user_id or 0):
            res['reason'] = 'not-leader'
            return res
        if t['status'] != 'OPEN':
            res['reason'] = 'team-not-open'
            return res
        c.execute("""UPDATE group_buy_teams SET status='CANCELLED',updated_at=datetime('now')
                     WHERE id=?""", (int(team_id),))
        c.execute("UPDATE group_buy_members SET status='CANCELLED' WHERE team_id=?", (int(team_id),))
        res.update(ok=True, reason='ok')
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def team_detail(c, club_id: int, team_id: int) -> dict | None:
    expire_due(c, int(club_id))
    t = c.execute("""SELECT t.*,cp.title AS campaign_title FROM group_buy_teams t
                     LEFT JOIN group_buy_campaigns cp ON cp.id=t.campaign_id
                     WHERE t.id=? AND t.club_id=?""", (int(team_id), int(club_id))).fetchone()
    if not t:
        return None
    mem = c.execute("""SELECT m.*,u.name AS user_name,u.phone AS user_phone
                       FROM group_buy_members m LEFT JOIN users u ON u.id=m.user_id
                       WHERE m.team_id=? ORDER BY m.id""", (int(team_id),)).fetchall()
    out = team_view(t)
    out['members'] = [member_view(m) for m in mem]
    return out


def list_teams(c, club_id: int, campaign_id=None, status=None, limit=100) -> list:
    expire_due(c, int(club_id))
    sql = """SELECT t.*,cp.title AS campaign_title FROM group_buy_teams t
             LEFT JOIN group_buy_campaigns cp ON cp.id=t.campaign_id WHERE t.club_id=?"""
    args = [int(club_id)]
    if campaign_id:
        sql += ' AND t.campaign_id=?'
        args.append(int(campaign_id))
    if status:
        sql += ' AND t.status=?'
        args.append(str(status).upper())
    sql += ' ORDER BY t.id DESC LIMIT ?'
    args.append(max(1, min(500, int(limit or 100))))
    return [team_view(r) for r in c.execute(sql, args).fetchall()]


def user_teams(c, club_id: int, user_id: int, limit=50) -> list:
    """我参与 / 发起的团（供 C 端「我的拼团」）。"""
    expire_due(c, int(club_id))
    sql = """SELECT DISTINCT t.*,cp.title AS campaign_title FROM group_buy_teams t
             LEFT JOIN group_buy_campaigns cp ON cp.id=t.campaign_id
             LEFT JOIN group_buy_members m ON m.team_id=t.id
             WHERE t.club_id=? AND (t.leader_user_id=? OR m.user_id=?)
             ORDER BY t.id DESC LIMIT ?"""
    args = [int(club_id), int(user_id), int(user_id), max(1, min(200, int(limit or 50)))]
    return [team_view(r) for r in c.execute(sql, args).fetchall()]


def summary(c, club_id: int) -> dict:
    expire_due(c, int(club_id))
    out = {'campaigns': 0, 'teams': 0, 'open': 0, 'success': 0,
           'expired': 0, 'cancelled': 0, 'members': 0}
    try:
        out['campaigns'] = int(c.execute(
            "SELECT COUNT(*) FROM group_buy_campaigns WHERE club_id=? AND status!='ENDED'",
            (int(club_id),)).fetchone()[0] or 0)
        for r in c.execute('SELECT status,COUNT(*) n FROM group_buy_teams WHERE club_id=? GROUP BY status',
                           (int(club_id),)).fetchall():
            k = str(r['status'] or '').lower()
            n = int(r['n'] or 0)
            out['teams'] += n
            if k in out:
                out[k] = n
        out['members'] = int(c.execute(
            "SELECT COUNT(*) FROM group_buy_members WHERE club_id=? AND status IN ('JOINED','SUCCESS')",
            (int(club_id),)).fetchone()[0] or 0)
    except Exception:
        pass
    return out


def quote_for_team(c, *, club_id: int, activity_id: int, team_id: int, user_id: int) -> dict | None:
    """price_quote 的只读接入：团不属于本活动 / 用户不在团里 → None。

    **纯读不写库**（报价端点绝不能产生副作用）；过期只作逻辑推断，由 expire_due
    在团列表 / 详情等写路径惰性落库。绝不修改 payable。
    """
    try:
        t = c.execute("""SELECT * FROM group_buy_teams
                         WHERE id=? AND club_id=? AND activity_id=?""",
                      (int(team_id), int(club_id), int(activity_id))).fetchone()
        if not t:
            return None
        if not c.execute('SELECT 1 FROM group_buy_members WHERE team_id=? AND user_id=?',
                         (int(team_id), int(user_id))).fetchone():
            return None
        status = t['status']
        if status == 'OPEN' and not team_active(t):
            status = 'EXPIRED'
        pc = int(t['price_cents'] or 0)
        opc = 0
        camp = c.execute('SELECT original_price_cents FROM group_buy_campaigns WHERE id=?',
                         (int(t['campaign_id']),)).fetchone()
        if camp:
            opc = int(camp['original_price_cents'] or 0)
        return {
            'teamId': t['id'], 'campaignId': t['campaign_id'], 'status': status,
            'priceCents': pc, 'price': from_cents(pc),
            'originalPriceCents': opc, 'originalPrice': from_cents(opc),
            'saving': from_cents(max(0, opc - pc)),
            'requiredPeople': int(t['required_people'] or 0),
            'memberCount': int(t['member_count'] or 0),
            'remaining': max(0, int(t['required_people'] or 0) - int(t['member_count'] or 0)),
            'expireAt': t['expire_at'],
            'appliedToPayable': False,   # 明示：拼团价仅展示，未参与应付金额计算
        }
    except Exception:
        return None

"""互动玩法：签到 / 抽奖 / 集章（v0.38，P1-2）。

参考「远拓旅游 SaaS · 营销管理 - 互动营销」：俱乐部可配置每日签到（连签加码）、
幸运抽奖（概率奖品池 + 每日次数上限）、集章任务（完成动作集章、集满兑换奖励）。

与既有体系的关系（刻意保持最小爆炸半径）：
- campaign_engine（P0-1）在 CAMPAIGN_TYPES 里预留了 CHECKIN/LOTTERY/STAMP，但那是
  **报价期折扣引擎**。互动玩法是「用户主动参与 → 发奖」，时机与语义都不同，
  因此独立建表 / 独立端点，**绝不改动 evaluate() 与 price_quote 的应付金额**。
- 奖励发放复用真实积分账户：club_members.club_points_balance + club_point_ledger
  （type='earn', source_type='engagement'），不新增第二套积分口径。
- 实物 / 自定义奖品只落 engagement_rewards 待后台核销（PENDING → FULFILLED）。

设计纪律（沿用三铁律）：
1. 俱乐部未开启某玩法 / 配置非法 → 一律不可用，绝不阻断既有下单 / 报价链路；
2. 所有参与日志带 (club,kind,user,biz_key) 唯一约束，重复点击幂等不重复发奖；
3. 所有写操作吞异常并返回 {ok,reason,...}，由调用方在 `with conn() as c` 事务内决定
   是否 raise 触发回滚；纯函数部分 stdlib-only，可被 importlib 独立加载测试。

日期口径：固定 UTC+8 自然日（国内俱乐部业务日），避免服务器时区漂移导致跨日错乱。
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta, timezone

KINDS = ('CHECKIN', 'LOTTERY', 'STAMP')
REWARD_TYPES = ('POINTS', 'PRIZE')
REWARD_STATUSES = ('GRANTED', 'PENDING', 'FULFILLED', 'CANCELLED')
MAX_PRIZES = 30
MAX_STREAK_RULES = 20
MAX_ACTIONS = 20
MAX_DAILY_LIMIT = 50
MAX_POINTS = 100_000
CN_TZ = timezone(timedelta(hours=8))

DEFAULT_STAMP_ACTIONS = (
    {'code': 'signup', 'label': '报名活动'},
    {'code': 'share', 'label': '分享活动'},
    {'code': 'review', 'label': '发布回顾'},
)


# ──────────────────────────── 基础工具 ────────────────────────────
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


def _int(v, default=0, lo=0, hi=10**9) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(float(n)):
        return default
    return max(lo, min(hi, n))


def _text(v, limit=60) -> str:
    return str(v or '').strip()[:limit]


def day_key(ts=None) -> str:
    """UTC+8 业务自然日，格式 YYYY-MM-DD。"""
    try:
        dt = datetime.fromtimestamp(float(ts), CN_TZ) if ts is not None else datetime.now(CN_TZ)
    except (TypeError, ValueError, OSError, OverflowError):
        dt = datetime.now(CN_TZ)
    return dt.date().isoformat()


def compute_streak(day_keys, today=None) -> int:
    """连续签到天数。若今天尚未签到，则统计截至昨天的连续段（用于「今天签可续上」）。"""
    days = {str(x)[:10] for x in (day_keys or []) if x}
    today = today or day_key()
    try:
        cur = date.fromisoformat(today)
    except (TypeError, ValueError):
        return 0
    if today not in days:
        cur -= timedelta(days=1)
    n = 0
    while cur.isoformat() in days and n < 3660:
        n += 1
        cur -= timedelta(days=1)
    return n


def cycle_day(streak: int, cycle_days: int) -> int:
    """连签第几天（按周期回绕）：第 8 天在 7 天周期里算第 1 天。"""
    try:
        cyc = max(1, int(cycle_days))
    except (TypeError, ValueError):
        cyc = 7
    if streak <= 0:
        return 0
    return ((streak - 1) % cyc) + 1


def checkin_reward(streak: int, cfg: dict) -> dict:
    """计算一次签到应得奖励：每日基础分 + 命中的最高连签档位加码。"""
    cfg = cfg if isinstance(cfg, dict) else {}
    base = _int(cfg.get('dailyPoints'), 0, 0, MAX_POINTS)
    bonus, prize = 0, ''
    best = -1
    for r in cfg.get('streakRules') or []:
        if not isinstance(r, dict) or not r.get('enabled', True):
            continue
        d = _int(r.get('days'), 0, 1, 3660)
        if d > 0 and cycle_day(streak, cfg.get('cycleDays', 7)) >= d and d > best:
            best = d
            bonus = _int(r.get('points'), 0, 0, MAX_POINTS)
            prize = _text(r.get('prizeName'), 60)
    total = min(MAX_POINTS, base + bonus)
    return {'streak': max(0, int(streak)), 'cycleDay': cycle_day(streak, cfg.get('cycleDays', 7)),
            'basePoints': base, 'bonusPoints': bonus, 'totalPoints': total,
            'prizeName': prize, 'hitRuleDays': max(0, best)}


def draw_prize(prizes, remaining=None, rnd=None) -> dict | None:
    """按权重抽一个奖品；无可用奖品（库存耗尽 / 权重为 0）→ None（谢谢参与）。

    remaining: {prize_id: 已发出数量}；stock<0 表示不限量。rnd 可注入以便单测确定性。
    """
    pool = []
    for p in prizes or []:
        if not isinstance(p, dict) or not p.get('enabled', True):
            continue
        w = _int(p.get('weight'), 0, 0, 10**6)
        if w <= 0:
            continue
        pid = _text(p.get('id'), 40)
        stock = _int(p.get('stock'), -1, -1, 10**7)
        used = _int((remaining or {}).get(pid), 0, 0, 10**7)
        if stock >= 0 and used >= stock:
            continue
        pool.append((p, w))
    if not pool:
        return None
    total = sum(w for _, w in pool)
    r = (rnd or random.random)() * total
    acc = 0.0
    for p, w in pool:
        acc += w
        if r < acc:
            return p
    return pool[-1][0]


# ──────────────────────────── 配置归一化 ────────────────────────────
def normalize_streak_rule(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None
    # lo 必须为 0：若用 lo=1，传入 days=0 会被夹成 1 而悄悄变成一条合法规则。
    days = _int(payload.get('days'), 0, 0, 3660)
    if days <= 0:
        return None
    return {'days': days, 'points': _int(payload.get('points'), 0, 0, MAX_POINTS),
            'prizeName': _text(payload.get('prizeName'), 60),
            'enabled': bool(payload.get('enabled', True))}


def normalize_prize(payload, idx=0) -> dict | None:
    if not isinstance(payload, dict):
        return None
    name = _text(payload.get('name'), 60)
    if not name:
        return None
    typ = str(payload.get('type') or 'POINTS').strip().upper()
    if typ not in REWARD_TYPES:
        typ = 'POINTS'
    return {
        'id': _text(payload.get('id'), 40) or ('p%d' % (idx + 1)),
        'name': name,
        'type': typ,
        'points': _int(payload.get('points'), 0, 0, MAX_POINTS),
        'stock': _int(payload.get('stock'), -1, -1, 10**7),
        'weight': _int(payload.get('weight'), 1, 0, 10**6),
        'enabled': bool(payload.get('enabled', True)),
    }


def normalize_action(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None
    code = _text(payload.get('code'), 40).lower()
    if not code or not code.replace('_', '').isalnum():
        return None
    return {'code': code, 'label': _text(payload.get('label'), 60) or code,
            'enabled': bool(payload.get('enabled', True))}


def _list_field(payload, current, key, normalizer, limit):
    """PATCH 合并语义：字段没传 → 原样保留 current；传了才归一化（非法项静默丢弃）。"""
    src = payload.get(key)
    if src is None:
        src = (current or {}).get(key)
    if isinstance(src, str):
        try:
            src = json.loads(src)
        except (TypeError, ValueError):
            src = []
    if not isinstance(src, list):
        return []
    out = []
    for i, item in enumerate(src[:limit]):
        n = normalizer(item, i) if normalizer is normalize_prize else normalizer(item)
        if n:
            out.append(n)
    return out


def default_config(kind: str) -> dict:
    kind = str(kind or '').strip().upper()
    if kind == 'CHECKIN':
        return {'enabled': False, 'dailyPoints': 5, 'cycleDays': 7, 'streakRules': []}
    if kind == 'LOTTERY':
        return {'enabled': False, 'dailyLimit': 3, 'prizes': []}
    if kind == 'STAMP':
        return {'enabled': False, 'required': 3, 'rewardPoints': 50, 'rewardName': '',
                'actions': [dict(a, enabled=True) for a in DEFAULT_STAMP_ACTIONS]}
    return {'enabled': False}


def normalize_config(kind: str, payload: dict, current: dict | None = None) -> dict:
    """归一化一种玩法的配置；current 为既有配置（PATCH 合并用）。"""
    kind = str(kind or '').strip().upper()
    if kind not in KINDS:
        raise ValueError('不支持的玩法类型: %s' % kind)
    p = payload if isinstance(payload, dict) else {}
    base = current if isinstance(current, dict) else default_config(kind)
    merged = dict(default_config(kind))
    merged.update(base)
    out = {'enabled': bool(p.get('enabled', merged.get('enabled', False)))}
    if kind == 'CHECKIN':
        out['dailyPoints'] = _int(p.get('dailyPoints', merged.get('dailyPoints', 5)), 5, 0, MAX_POINTS)
        out['cycleDays'] = _int(p.get('cycleDays', merged.get('cycleDays', 7)), 7, 1, 366)
        out['streakRules'] = _list_field(p, merged, 'streakRules', normalize_streak_rule, MAX_STREAK_RULES)
        out['streakRules'].sort(key=lambda x: x['days'])
    elif kind == 'LOTTERY':
        out['dailyLimit'] = _int(p.get('dailyLimit', merged.get('dailyLimit', 3)), 3, 1, MAX_DAILY_LIMIT)
        out['prizes'] = _list_field(p, merged, 'prizes', normalize_prize, MAX_PRIZES)
    else:
        out['required'] = _int(p.get('required', merged.get('required', 3)), 3, 1, 999)
        out['rewardPoints'] = _int(p.get('rewardPoints', merged.get('rewardPoints', 50)), 50, 0, MAX_POINTS)
        out['rewardName'] = _text(p.get('rewardName', merged.get('rewardName', '')), 60)
        acts = _list_field(p, merged, 'actions', normalize_action, MAX_ACTIONS)
        out['actions'] = acts or [dict(a, enabled=True) for a in DEFAULT_STAMP_ACTIONS]
    return out


def config_from_row(row, kind: str) -> dict:
    d = dict(row) if row is not None else {}
    cfg = loads(d.get('config_json'))
    cfg['enabled'] = bool(d.get('enabled'))
    return normalize_config(kind, {}, cfg)


# ──────────────────────────── 视图 ────────────────────────────
def log_view(row) -> dict:
    d = dict(row) if row is not None else {}
    return {'id': d.get('id'), 'clubId': d.get('club_id'), 'kind': d.get('kind'),
            'userId': d.get('user_id'), 'bizKey': d.get('biz_key'), 'dayKey': d.get('day_key'),
            'action': d.get('action'), 'payload': loads(d.get('payload_json')),
            'createdAt': d.get('created_at'), 'userName': d.get('user_name'),
            'userPhone': d.get('user_phone')}


def reward_view(row) -> dict:
    d = dict(row) if row is not None else {}
    return {'id': d.get('id'), 'clubId': d.get('club_id'), 'kind': d.get('kind'),
            'userId': d.get('user_id'), 'logId': d.get('log_id'),
            'rewardType': d.get('reward_type'), 'points': int(d.get('points') or 0),
            'prizeId': d.get('prize_id'), 'prizeName': d.get('prize_name'),
            'status': d.get('status'), 'sourceKey': d.get('source_key'),
            'createdAt': d.get('created_at'), 'fulfilledAt': d.get('fulfilled_at'),
            'userName': d.get('user_name'), 'userPhone': d.get('user_phone')}


# ──────────────────────────── DB：配置 ────────────────────────────
def get_config(c, club_id: int, kind: str) -> dict:
    r = c.execute('SELECT * FROM engagement_configs WHERE club_id=? AND kind=?',
                  (int(club_id), str(kind).upper())).fetchone()
    return config_from_row(r, kind)


def save_config(c, club_id: int, kind: str, cfg: dict) -> dict:
    c.execute("""INSERT INTO engagement_configs(club_id,kind,enabled,config_json,updated_at)
                 VALUES(?,?,?,?,datetime('now'))
                 ON CONFLICT(club_id,kind) DO UPDATE SET
                   enabled=excluded.enabled, config_json=excluded.config_json,
                   updated_at=excluded.updated_at""",
              (int(club_id), str(kind).upper(), 1 if cfg.get('enabled') else 0, dumps(cfg)))
    return cfg


# ──────────────────────────── DB：发奖 ────────────────────────────
def grant(c, *, club_id: int, user_id: int, kind: str, log_id=None,
          reward_type='POINTS', points=0, prize_id=None, prize_name='',
          source_key=None) -> dict:
    """记一条发奖。POINTS 同步加到真实活动积分账户 + 写 club_point_ledger 流水；
    PRIZE 只落待核销记录。source_key 命中唯一索引 → 幂等返回 duplicated。"""
    res = {'ok': False, 'reason': '', 'rewardId': None, 'points': 0}
    try:
        club_id, user_id = int(club_id), int(user_id)
        pts = _int(points, 0, 0, MAX_POINTS)
        typ = str(reward_type or 'POINTS').upper()
        if typ not in REWARD_TYPES:
            typ = 'POINTS'
        sk = _text(source_key, 80) or None
        if sk:
            dup = c.execute("""SELECT id FROM engagement_rewards
                               WHERE club_id=? AND kind=? AND user_id=? AND source_key=? LIMIT 1""",
                            (club_id, str(kind).upper(), user_id, sk)).fetchone()
            if dup:
                res['reason'] = 'duplicated'
                return res
        status = 'GRANTED' if typ == 'POINTS' else 'PENDING'
        if typ == 'POINTS' and pts > 0:
            # 惰性建会员行（用户可能还没在俱乐部下过单），与既有手工调整口径一致
            c.execute("""INSERT OR IGNORE INTO club_members(club_id,user_id,level,club_points_balance)
                         VALUES(?,?,'普通会员',0)""", (club_id, user_id))
            c.execute("""UPDATE club_members SET club_points_balance=club_points_balance+?
                         WHERE club_id=? AND user_id=?""", (pts, club_id, user_id))
            c.execute("""INSERT INTO club_point_ledger
                         (club_id,user_id,type,amount,source_type,source_id,note)
                         VALUES(?,?,'earn',?,'engagement',?,?)""",
                      (club_id, user_id, pts, sk or str(log_id or ''),
                       '%s发奖：%s' % (str(kind).upper(), _text(prize_name, 40) or '积分')))
        c.execute("""INSERT INTO engagement_rewards
                     (club_id,kind,user_id,log_id,reward_type,points,prize_id,prize_name,
                      status,source_key,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                  (club_id, str(kind).upper(), user_id, log_id, typ, pts,
                   _text(prize_id, 40) or None, _text(prize_name, 60) or None,
                   status, sk))
        res.update(ok=True, reason='ok', points=pts,
                   rewardId=int(c.execute('SELECT last_insert_rowid()').fetchone()[0]))
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def _add_log(c, *, club_id, user_id, kind, biz_key, day, action, payload) -> int | None:
    """写参与日志；biz_key 撞唯一约束 → None（调用方据此判定重复）。"""
    try:
        cur = c.execute("""INSERT OR IGNORE INTO engagement_logs
                           (club_id,kind,user_id,biz_key,day_key,action,payload_json,created_at)
                           VALUES(?,?,?,?,?,?,?,datetime('now'))""",
                        (int(club_id), str(kind).upper(), int(user_id), str(biz_key)[:120],
                         str(day)[:10], str(action)[:40], dumps(payload or {})))
        # OR IGNORE 撞唯一约束时 rowcount=0；此时返回 None 让调用方判定「重复参与」，
        # 避免把已存在的 log_id 当成新参与并再次触发发奖。
        if cur.rowcount <= 0:
            return None
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    except Exception:
        return None


def _log_exists(c, club_id, user_id, kind, biz_key) -> bool:
    return bool(c.execute("""SELECT 1 FROM engagement_logs
                             WHERE club_id=? AND kind=? AND user_id=? AND biz_key=? LIMIT 1""",
                          (int(club_id), str(kind).upper(), int(user_id), str(biz_key)[:120])).fetchone())


# ──────────────────────────── DB：签到 ────────────────────────────
def checkin(c, *, club_id: int, user_id: int, day=None) -> dict:
    """每日签到（UTC+8 自然日幂等）。返回 {ok,reason,streak,reward,...}。"""
    res = {'ok': False, 'reason': '', 'day': None, 'streak': 0, 'reward': None}
    try:
        club_id, user_id = int(club_id), int(user_id)
        d = str(day or day_key())[:10]
        res['day'] = d
        cfg = get_config(c, club_id, 'CHECKIN')
        if not cfg['enabled']:
            res['reason'] = 'disabled'
            return res
        bk = 'CHECKIN:%s' % d
        if _log_exists(c, club_id, user_id, 'CHECKIN', bk):
            res['reason'] = 'already'
            return res
        hist = [x[0] for x in c.execute("""SELECT day_key FROM engagement_logs
                                           WHERE club_id=? AND kind='CHECKIN' AND user_id=?
                                           ORDER BY day_key DESC LIMIT 400""",
                                        (club_id, user_id)).fetchall()]
        streak = compute_streak(hist + [d], d)
        reward = checkin_reward(streak, cfg)
        lid = _add_log(c, club_id=club_id, user_id=user_id, kind='CHECKIN', biz_key=bk,
                       day=d, action='CHECKIN', payload={'streak': streak, 'points': reward['totalPoints']})
        if lid is None:
            res['reason'] = 'exception'
            return res
        g = grant(c, club_id=club_id, user_id=user_id, kind='CHECKIN', log_id=lid,
                  reward_type='POINTS', points=reward['totalPoints'],
                  prize_name=reward['prizeName'] or '每日签到', source_key=bk)
        if not g['ok'] and g['reason'] not in ('duplicated',):
            res['reason'] = g['reason']
            return res
        res.update(ok=True, reason='ok', streak=streak, reward=reward,
                   pointsGranted=g['points'], rewardId=g['rewardId'])
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


# ──────────────────────────── DB：抽奖 ────────────────────────────
def _remaining_counts(c, club_id, prizes) -> dict:
    out = {}
    for p in prizes:
        pid = p.get('id')
        if not pid:
            continue
        n = c.execute("""SELECT COUNT(*) FROM engagement_rewards
                         WHERE club_id=? AND kind='LOTTERY' AND prize_id=?
                           AND status!='CANCELLED'""",
                      (int(club_id), pid)).fetchone()[0]
        out[pid] = int(n or 0)
    return out


def draw(c, *, club_id: int, user_id: int, day=None, rnd=None) -> dict:
    """抽奖一次；每日次数上限、奖品库存、幂等由同事务保证。"""
    res = {'ok': False, 'reason': '', 'prize': None, 'pointsGranted': 0, 'drawsToday': 0, 'remaining': 0}
    try:
        club_id, user_id = int(club_id), int(user_id)
        d = str(day or day_key())[:10]
        cfg = get_config(c, club_id, 'LOTTERY')
        if not cfg['enabled']:
            res['reason'] = 'disabled'
            return res
        used = int(c.execute("""SELECT COUNT(*) FROM engagement_logs
                                WHERE club_id=? AND kind='LOTTERY' AND user_id=? AND day_key=?""",
                             (club_id, user_id, d)).fetchone()[0] or 0)
        limit = _int(cfg.get('dailyLimit'), 3, 1, MAX_DAILY_LIMIT)
        res['drawsToday'] = used
        res['remaining'] = max(0, limit - used)
        if used >= limit:
            res['reason'] = 'daily-limit'
            return res
        rem = _remaining_counts(c, club_id, cfg.get('prizes') or [])
        prize = draw_prize(cfg.get('prizes'), rem, rnd)
        seq = used + 1
        bk = 'DRAW:%s:%d' % (d, seq)
        if _log_exists(c, club_id, user_id, 'LOTTERY', bk):
            res['reason'] = 'already'
            return res
        pid = prize.get('id') if prize else None
        pname = prize.get('name') if prize else '谢谢参与'
        rtype = (prize.get('type') if prize else 'POINTS') or 'POINTS'
        pts = _int(prize.get('points'), 0, 0, MAX_POINTS) if prize else 0
        lid = _add_log(c, club_id=club_id, user_id=user_id, kind='LOTTERY', biz_key=bk,
                       day=d, action='DRAW',
                       payload={'prizeId': pid, 'prizeName': pname, 'points': pts})
        if lid is None:
            res['reason'] = 'exception'
            return res
        g = None
        if prize:
            g = grant(c, club_id=club_id, user_id=user_id, kind='LOTTERY', log_id=lid,
                      reward_type=rtype, points=pts, prize_id=pid, prize_name=pname,
                      source_key=bk)
            if not g['ok'] and g['reason'] not in ('duplicated',):
                res['reason'] = g['reason']
                return res
        res.update(ok=True, reason='ok', prize=prize, pointsGranted=(g or {}).get('points', 0),
                   rewardId=(g or {}).get('rewardId'), drawsToday=seq,
                   remaining=max(0, limit - seq))
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


# ──────────────────────────── DB：集章 ────────────────────────────
def add_stamp(c, *, club_id: int, user_id: int, action: str, biz_key=None, day=None) -> dict:
    """上报一个动作集章。同一 (动作,自然日) 默认只记一枚；集满 required 枚自动发奖。"""
    res = {'ok': False, 'reason': '', 'collected': 0, 'required': 0, 'redeemed': False}
    try:
        club_id, user_id = int(club_id), int(user_id)
        d = str(day or day_key())[:10]
        cfg = get_config(c, club_id, 'STAMP')
        if not cfg['enabled']:
            res['reason'] = 'disabled'
            return res
        act = _text(action, 40).lower()
        allowed = {a['code'] for a in (cfg.get('actions') or []) if a.get('enabled', True)}
        if act not in allowed or not allowed:
            res['reason'] = 'bad-action'
            return res
        required = _int(cfg.get('required'), 3, 1, 999)
        bk = _text(biz_key, 120) or ('STAMP:%s:%s' % (act, d))
        if _log_exists(c, club_id, user_id, 'STAMP', bk):
            res['reason'] = 'already'
            return res
        lid = _add_log(c, club_id=club_id, user_id=user_id, kind='STAMP', biz_key=bk,
                       day=d, action=act, payload={'label': act})
        if lid is None:
            res['reason'] = 'exception'
            return res
        total = int(c.execute("""SELECT COUNT(*) FROM engagement_logs
                                 WHERE club_id=? AND kind='STAMP' AND user_id=?""",
                              (club_id, user_id)).fetchone()[0] or 0)
        redeemed = int(c.execute("""SELECT COUNT(*) FROM engagement_rewards
                                    WHERE club_id=? AND kind='STAMP' AND user_id=?
                                      AND status!='CANCELLED'""",
                                 (club_id, user_id)).fetchone()[0] or 0)
        collected = max(0, total - redeemed * required)
        res.update(ok=True, reason='ok', collected=collected, required=required)
        if collected >= required:
            cycle = redeemed + 1
            g = grant(c, club_id=club_id, user_id=user_id, kind='STAMP', log_id=lid,
                      reward_type='POINTS', points=cfg.get('rewardPoints', 0),
                      prize_name=cfg.get('rewardName') or '集章达标奖励',
                      source_key='REDEEM:%d' % cycle)
            res['redeemed'] = bool(g['ok'])
            res['rewardId'] = g.get('rewardId')
            res['pointsGranted'] = g.get('points', 0)
            res['collected'] = 0
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


# ──────────────────────────── DB：状态 / 汇总 ────────────────────────────
def user_status(c, *, club_id: int, user_id: int, day=None) -> dict:
    """C 端一屏状态：今日是否已签 / 连签 / 抽奖剩余次数 / 集章进度。"""
    club_id, user_id = int(club_id), int(user_id)
    d = str(day or day_key())[:10]
    out = {'userId': user_id, 'day': d, 'CHECKIN': {'enabled': False},
           'LOTTERY': {'enabled': False}, 'STAMP': {'enabled': False}}
    try:
        ck = get_config(c, club_id, 'CHECKIN')
        hist = [x[0] for x in c.execute("""SELECT day_key FROM engagement_logs
                                           WHERE club_id=? AND kind='CHECKIN' AND user_id=?
                                           ORDER BY day_key DESC LIMIT 400""",
                                        (club_id, user_id)).fetchall()]
        signed = d in hist
        streak = compute_streak(hist, d)
        out['CHECKIN'] = {'enabled': bool(ck['enabled']), 'signedToday': signed,
                          'streak': streak, 'cycleDay': cycle_day(streak, ck.get('cycleDays', 7)),
                          'todayReward': checkin_reward(streak + (0 if signed else 1), ck),
                          'dailyPoints': ck.get('dailyPoints', 0),
                          'streakRules': ck.get('streakRules', [])}
        lt = get_config(c, club_id, 'LOTTERY')
        used = int(c.execute("""SELECT COUNT(*) FROM engagement_logs
                                WHERE club_id=? AND kind='LOTTERY' AND user_id=? AND day_key=?""",
                             (club_id, user_id, d)).fetchone()[0] or 0)
        rem = _remaining_counts(c, club_id, lt.get('prizes') or [])
        prizes = [{'id': p['id'], 'name': p['name'],
                   'remaining': (None if p.get('stock', -1) < 0
                                 else max(0, p['stock'] - rem.get(p['id'], 0)))}
                  for p in (lt.get('prizes') or []) if p.get('enabled', True)]
        out['LOTTERY'] = {'enabled': bool(lt['enabled']), 'drawsToday': used,
                          'dailyLimit': lt.get('dailyLimit', 3),
                          'remaining': max(0, lt.get('dailyLimit', 3) - used), 'prizes': prizes}
        st = get_config(c, club_id, 'STAMP')
        total = int(c.execute("""SELECT COUNT(*) FROM engagement_logs
                                 WHERE club_id=? AND kind='STAMP' AND user_id=?""",
                              (club_id, user_id)).fetchone()[0] or 0)
        redeemed = int(c.execute("""SELECT COUNT(*) FROM engagement_rewards
                                    WHERE club_id=? AND kind='STAMP' AND user_id=?
                                      AND status!='CANCELLED'""",
                                 (club_id, user_id)).fetchone()[0] or 0)
        required = st.get('required', 3)
        out['STAMP'] = {'enabled': bool(st['enabled']), 'required': required,
                        'collected': max(0, total - redeemed * required),
                        'redeemedCount': redeemed, 'actions': st.get('actions', []),
                        'rewardPoints': st.get('rewardPoints', 0),
                        'rewardName': st.get('rewardName', '')}
    except Exception:
        pass
    return out


def summary(c, club_id: int) -> dict:
    """后台汇总：参与人数 / 次数 / 已发积分 / 待核销奖品。"""
    club_id = int(club_id)
    out = {'checkins': 0, 'draws': 0, 'stamps': 0, 'participants': 0,
           'pointsGranted': 0, 'pendingPrizes': 0, 'byKind': {}}
    try:
        for k in KINDS:
            out['byKind'][k] = int(c.execute(
                'SELECT COUNT(*) FROM engagement_logs WHERE club_id=? AND kind=?',
                (club_id, k)).fetchone()[0] or 0)
        out['checkins'] = out['byKind']['CHECKIN']
        out['draws'] = out['byKind']['LOTTERY']
        out['stamps'] = out['byKind']['STAMP']
        out['participants'] = int(c.execute(
            'SELECT COUNT(DISTINCT user_id) FROM engagement_logs WHERE club_id=?',
            (club_id,)).fetchone()[0] or 0)
        out['pointsGranted'] = int(c.execute("""SELECT COALESCE(SUM(points),0) FROM engagement_rewards
                                                WHERE club_id=? AND status='GRANTED'""",
                                             (club_id,)).fetchone()[0] or 0)
        out['pendingPrizes'] = int(c.execute("""SELECT COUNT(*) FROM engagement_rewards
                                                WHERE club_id=? AND status='PENDING'""",
                                             (club_id,)).fetchone()[0] or 0)
    except Exception:
        pass
    return out

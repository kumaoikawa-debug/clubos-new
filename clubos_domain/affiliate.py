"""分销员 / 推广员佣金（v0.42，P2-1）。

参考「远拓旅游 SaaS · 营销管理」的「分销员」形态：会员成为推广员 → 持专属码拉新
→ 被邀请人下单 → 按档位费率计佣 → 冻结期后结算打款。

**与既有体系的边界（很重要，勿混口径）**
- `commission_ledger` / `commission_settlements`：平台 ↔ 俱乐部 的装备订单佣金，
  主体是 club、表里没有 user_id。本模块完全不碰它。
- `invite_records` / `invite_reward_settings`：一次性「邀请奖励」（发券 / 加积分），
  主体是会员拉新。本模块也不写它，`club_members.referred_by` 保持原义。
- 本模块是第三套：**俱乐部 ↔ 推广员（user）** 的持续返佣，独立 `affiliate_*` 表。

**三铁律（沿用 club_home.py）**
1. 未配置 / 表不存在 / SQL 异常 / 值损坏 → 静默回落，绝不因佣金问题阻断主流程。
2. 金额一律整数「分」，费率一律 basis points（1bp = 0.01%），杜绝浮点误差。
3. 所有对外写操作幂等；非法输入静默丢弃或返回 reason，不抛异常到调用方。

**不侵入结算**：不改 checkout / payable / registrations / gear_orders。佣金入账由
显式端点触发（订单完成后调用），并记录 orderType + orderId 做幂等与对账。
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone

# ── 常量 ────────────────────────────────────────────────────────────────────
MEMBER_STATUSES = ('PENDING', 'ACTIVE', 'FROZEN')
BINDING_STATUSES = ('ACTIVE', 'REVOKED')
COMMISSION_STATUSES = ('PENDING', 'SETTLED', 'REVERSED')
SETTLEMENT_STATUSES = ('GENERATED', 'PAID')
# 营销适用范围硬边界（2026-11-13）：只允许俱乐部活动 / 营地订单。
# 总平台装备销售（gear_order / mall / points-mall / Medusa）一律拒绝，
# 完整口径见 commerce_scope.py；三处白名单由 tests/test_commerce_scope.py 断言一致。
ORDER_TYPES = ('registration', 'camp_booking', 'camp_order', 'custom')

MAX_RATE_BP = 5000            # 单笔佣金率上限 50%
MAX_AMOUNT_CENTS = 100000000  # 单笔订单金额上限 ¥1,000,000
MAX_FREEZE_DAYS = 365
MAX_LEVELS = 12
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # 去掉易混淆的 0/O/1/I

DEFAULT_LEVELS = (
    {'key': 'L1', 'label': '初级推广员', 'minAmountCents': 0, 'minOrders': 0, 'rateBp': 500},
    {'key': 'L2', 'label': '银牌推广员', 'minAmountCents': 100000, 'minOrders': 5, 'rateBp': 700},
    {'key': 'L3', 'label': '金牌推广员', 'minAmountCents': 500000, 'minOrders': 20, 'rateBp': 1000},
)


def dumps(o) -> str:
    try:
        return json.dumps(o if o is not None else {}, ensure_ascii=False, separators=(',', ':'))
    except Exception:
        return '{}'


def loads(s, fallback=None):
    try:
        v = json.loads(s) if s else None
        return v if v is not None else (fallback if fallback is not None else {})
    except Exception:
        return fallback if fallback is not None else {}


def to_cents(v) -> int:
    """把「元」安全地转成整数分。接受数字与字符串，越界直接 0（不夹逼成最大值）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if f != f or f in (float('inf'), float('-inf')) or f <= 0:
        return 0
    n = int(round(f * 100))
    return n if 0 < n <= MAX_AMOUNT_CENTS else 0


def from_cents(n) -> float:
    try:
        return round(int(n or 0) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _int(v, default=0, lo=0, hi=10 ** 9):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def _now() -> int:
    return int(time.time())


TS_MAX = 10 ** 12


def _ts(v=None) -> int:
    """秒级时间戳归一化。

    注意：2026 年的 Unix 时间戳约 1.78e9，已超过 _int() 默认的 1e9 上限；
    若沿用 _int(now, ...) 会把显式传入的时间静默洗成「当前真实时间」，
    导致冻结期 / 有效期判定错误（P1-2 教训：夹逼函数不能替代合法性校验）。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return _now()
    return n if 1 <= n <= TS_MAX else _now()


def _ts_to_str(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).isoformat(timespec='seconds')
    except Exception:
        return ''


def gen_code(prefix: str = 'A', length: int = 8) -> str:
    """生成推广码。纯随机但字符集收敛，全库唯一由 DB UNIQUE + 路由重试保证。"""
    n = max(4, min(16, _int(length, 8)))
    p = ''.join(ch for ch in str(prefix or '').upper() if ch.isalnum())[:4] or 'A'
    return p + ''.join(secrets.choice(CODE_ALPHABET) for _ in range(n))


# ── 配置 ────────────────────────────────────────────────────────────────────
def normalize_levels(raw) -> list:
    """等级档位归一化。非法档位整条丢弃（不夹逼成合法值，延续 P1-2 教训）。"""
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw[:MAX_LEVELS]:
        if not isinstance(it, dict):
            continue
        key = str(it.get('key') or '').strip()[:24]
        rate = _int(it.get('rateBp'), 0, 0, MAX_RATE_BP)
        if not key or rate <= 0:
            continue
        out.append({
            'key': key,
            'label': str(it.get('label') or key)[:40],
            'minAmountCents': _int(it.get('minAmountCents'), 0, 0, MAX_AMOUNT_CENTS),
            'minOrders': _int(it.get('minOrders'), 0, 0, 10 ** 6),
            'rateBp': rate,
        })
    return out


def normalize_config(payload, current=None) -> dict:
    """俱乐部分销配置归一化。支持 PATCH 合并；金额字段入参一律「元」，内部一律「分」。"""
    p = payload if isinstance(payload, dict) else {}
    cur = dict(current or {})
    base = config_from_row(cur) if cur else default_config()

    if 'levels' in p:
        levels = normalize_levels(p.get('levels'))
    else:
        levels = base['levels']

    rate_bp = _int(p.get('defaultRateBp', base['defaultRateBp']), base['defaultRateBp'], 0, MAX_RATE_BP)
    freeze = _int(p.get('freezeDays', base['freezeDays']), base['freezeDays'], 0, MAX_FREEZE_DAYS)

    # minSettleAmount 入参是「元」；合并时若未传，直接沿用 base 的分（不能再 to_cents 一次）
    if 'minSettleAmount' in p:
        min_cents = to_cents(p.get('minSettleAmount'))
    else:
        min_cents = base['minSettleCents']

    return {
        'enabled': bool(p.get('enabled', base['enabled'])),
        'autoApprove': bool(p.get('autoApprove', base['autoApprove'])),
        'allowSelfPurchase': bool(p.get('allowSelfPurchase', base['allowSelfPurchase'])),
        'defaultRateBp': rate_bp,
        'freezeDays': freeze,
        'minSettleCents': min_cents,
        'levels': levels or [dict(x) for x in DEFAULT_LEVELS],
    }


def default_config() -> dict:
    return {
        'enabled': False,
        'autoApprove': True,
        'allowSelfPurchase': False,
        'defaultRateBp': 500,
        'freezeDays': 7,
        'minSettleCents': 1000,
        'levels': [dict(x) for x in DEFAULT_LEVELS],
    }


def config_from_row(row) -> dict:
    """把 DB 行 / 已存 JSON 还原成配置 dict。任何异常都回落到默认。"""
    d = dict(row or {})
    raw = d.get('config_json')
    cfg = loads(raw, {}) if isinstance(raw, str) else (d if d else {})
    if not isinstance(cfg, dict):
        cfg = {}
    base = default_config()
    try:
        base.update({
            'enabled': bool(cfg.get('enabled', d.get('enabled', False))),
            'autoApprove': bool(cfg.get('autoApprove', True)),
            'allowSelfPurchase': bool(cfg.get('allowSelfPurchase', False)),
            'defaultRateBp': _int(cfg.get('defaultRateBp', d.get('default_rate_bp')), 500, 0, MAX_RATE_BP),
            'freezeDays': _int(cfg.get('freezeDays', d.get('freeze_days')), 7, 0, MAX_FREEZE_DAYS),
            'minSettleCents': _int(cfg.get('minSettleCents', d.get('min_settle_cents')), 1000, 0, MAX_AMOUNT_CENTS),
            'levels': normalize_levels(cfg.get('levels')) or [dict(x) for x in DEFAULT_LEVELS],
        })
    except Exception:
        return default_config()
    return base


def public_config(cfg: dict) -> dict:
    """C 端可见配置：只给是否开放与准入说明，不下发费率 / 档位 / 冻结天数。"""
    return {
        'enabled': bool((cfg or {}).get('enabled')),
        'autoApprove': bool((cfg or {}).get('autoApprove')),
    }


# ── 分销员 ──────────────────────────────────────────────────────────────────
def normalize_member(payload, current=None) -> dict:
    p = payload if isinstance(payload, dict) else {}
    cur = dict(current or {})
    status = str(p.get('status', cur.get('status', 'PENDING')) or 'PENDING').upper()
    if status not in MEMBER_STATUSES:
        status = 'PENDING'
    rate_raw = p.get('rateBp', cur.get('rate_bp_override'))
    rate = None
    if rate_raw not in (None, '', 0, '0'):
        v = _int(rate_raw, -1, -1, MAX_RATE_BP)
        rate = v if v > 0 else None
    return {
        'userId': _int(p.get('userId', cur.get('user_id')), 0, 1, 10 ** 9),
        'level': str(p.get('level', cur.get('level', '')) or '').strip()[:24],
        'rateBpOverride': rate,
        'status': status,
        'note': str(p.get('note', cur.get('note', '')) or '').strip()[:200],
    }


def resolve_level(cfg: dict, member: dict) -> dict:
    """按分销员累计成交 / 订单数挑命中的最高费率档（纯函数，不查库）。"""
    levels = (cfg or {}).get('levels') or []
    amount = _int((member or {}).get('total_amount_cents'), 0, 0, MAX_AMOUNT_CENTS)
    orders = _int((member or {}).get('total_orders'), 0, 0, 10 ** 9)
    best = None
    for lv in levels:
        if amount >= _int(lv.get('minAmountCents'), 0, 0) and orders >= _int(lv.get('minOrders'), 0, 0):
            if best is None or _int(lv.get('rateBp'), 0, 0) > _int(best.get('rateBp'), 0, 0):
                best = lv
    return dict(best) if best else {'key': 'BASE', 'label': '基础费率', 'rateBp': _int((cfg or {}).get('defaultRateBp'), 0, 0, MAX_RATE_BP)}


def compute_commission(amount_cents: int, rate_bp: int) -> int:
    """整数运算：佣金分 = 订单分 × bp // 10000。"""
    a = _int(amount_cents, 0, 0, MAX_AMOUNT_CENTS)
    r = _int(rate_bp, 0, 0, MAX_RATE_BP)
    return (a * r) // 10000


def effective_rate_bp(cfg: dict, member: dict) -> tuple:
    """返回 (rate_bp, level_key, level_label, source)。分销员单独覆盖优先。"""
    ov = (member or {}).get('rate_bp_override')
    if ov not in (None, '', 0):
        r = _int(ov, 0, 0, MAX_RATE_BP)
        if r > 0:
            return r, 'CUSTOM', '单独设定费率', 'override'
    lv = resolve_level(cfg, member)
    return _int(lv.get('rateBp'), 0, 0, MAX_RATE_BP), str(lv.get('key') or 'BASE'), str(lv.get('label') or ''), 'level'


def member_view(r, cfg=None) -> dict:
    d = dict(r or {})
    rate, key, label, src = effective_rate_bp(cfg or default_config(), d)
    return {
        'id': d.get('id'), 'clubId': d.get('club_id'), 'userId': d.get('user_id'),
        'code': d.get('code'), 'level': d.get('level') or '', 'status': d.get('status'),
        'note': d.get('note') or '', 'rateBp': rate, 'levelKey': key, 'levelLabel': label,
        'rateSource': src,
        'totalOrders': _int(d.get('total_orders'), 0, 0),
        'totalAmountCents': _int(d.get('total_amount_cents'), 0, 0),
        'totalAmount': from_cents(d.get('total_amount_cents')),
        'totalCommissionCents': _int(d.get('total_commission_cents'), 0, 0),
        'totalCommission': from_cents(d.get('total_commission_cents')),
        'settledCents': _int(d.get('settled_cents'), 0, 0),
        'settledAmount': from_cents(d.get('settled_cents')),
        'pendingCents': max(0, _int(d.get('total_commission_cents'), 0, 0) - _int(d.get('settled_cents'), 0, 0)),
        'createdAt': d.get('created_at'),
    }


# ── 推广关系绑定 ────────────────────────────────────────────────────────────
def bind(c, *, club_id, affiliate_user_id, invitee_user_id, code='', channel='code',
         expires_at=None, now=None) -> dict:
    """绑定推广关系。返回 {ok, bindingId, reason}；不抛异常，路由决定 4xx。

    规则：防自购、防重复绑定（一个被邀请人只绑一个）、防互相绑定成环、
    分销员必须 ACTIVE、被邀请人不能已是分销员（避免层级套利）。
    """
    res = {'ok': False, 'bindingId': None, 'reason': 'invalid'}
    try:
        a = _int(affiliate_user_id, 0, 1)
        i = _int(invitee_user_id, 0, 1)
        if not a or not i:
            res['reason'] = 'invalid-user'
            return res
        if a == i:
            res['reason'] = 'self-bind'
            return res
        ts = _ts(now)
        m = c.execute('SELECT id,status FROM affiliate_members WHERE club_id=? AND user_id=?',
                      (club_id, a)).fetchone()
        if not m or str(m['status']).upper() != 'ACTIVE':
            res['reason'] = 'affiliate-not-active'
            return res
        dup = c.execute('SELECT id FROM affiliate_bindings WHERE club_id=? AND invitee_user_id=?',
                        (club_id, i)).fetchone()
        if dup:
            res['reason'] = 'already-bound'
            return res
        # 防环：被邀请人若已是分销员，且当前推广员已被绑为其下线 → 拒绝
        ring = c.execute("""SELECT id FROM affiliate_bindings
                            WHERE club_id=? AND affiliate_user_id=? AND invitee_user_id=?
                              AND status='ACTIVE'""", (club_id, i, a)).fetchone()
        if ring:
            res['reason'] = 'cycle'
            return res
        own = c.execute('SELECT id FROM affiliate_members WHERE club_id=? AND user_id=?',
                        (club_id, i)).fetchone()
        if own:
            res['reason'] = 'invitee-is-affiliate'
            return res
        cur = c.execute("""INSERT INTO affiliate_bindings
              (club_id,affiliate_user_id,invitee_user_id,code,channel,status,created_at,expires_at)
              VALUES(?,?,?,?,?,'ACTIVE',?,?)""",
                        (club_id, a, i, str(code or '')[:40], str(channel or 'code')[:24],
                         _ts_to_str(ts), expires_at))
        res.update(ok=True, bindingId=int(cur.lastrowid))
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def active_binding(c, *, club_id, invitee_user_id, now=None):
    """查有效推广关系（含分销员状态、绑定有效期）。无有效关系返回 None。"""
    try:
        ts = _ts(now)
        r = c.execute("""SELECT b.id AS binding_id, b.affiliate_user_id, b.expires_at,
                                m.id AS member_id, m.status AS member_status,
                                m.rate_bp_override, m.level, m.total_amount_cents,
                                m.total_orders, m.code
                           FROM affiliate_bindings b
                           JOIN affiliate_members m
                             ON m.club_id=b.club_id AND m.user_id=b.affiliate_user_id
                          WHERE b.club_id=? AND b.invitee_user_id=? AND b.status='ACTIVE'""",
                      (club_id, _int(invitee_user_id, 0, 1))).fetchone()
        if not r or str(r['member_status']).upper() != 'ACTIVE':
            return None
        exp = r['expires_at']
        if exp:
            try:
                if int(exp) < ts:
                    return None
            except (TypeError, ValueError):
                # 非秒级时间戳的字符串按 ISO 处理，解析不了就当作未过期（保守不误伤）
                try:
                    if datetime.fromisoformat(str(exp)).timestamp() < ts:
                        return None
                except Exception:
                    pass
        return dict(r)
    except Exception:
        return None


# ── 佣金入账 / 冲回 / 结算 ──────────────────────────────────────────────────
def preview(c, *, club_id, buyer_user_id, amount_cents, cfg, module_enabled=True,
            now=None) -> dict:
    """只读试算：不写库、不产生任何副作用。"""
    out = {'eligible': False, 'reason': 'not-configured', 'commissionCents': 0,
           'rateBp': 0, 'levelKey': '', 'affiliateUserId': None, 'orderAmountCents': 0}
    try:
        cfg = cfg or default_config()
        amt = _int(amount_cents, 0, 0, MAX_AMOUNT_CENTS)
        out['orderAmountCents'] = amt
        if not module_enabled:
            out['reason'] = 'module-disabled'
            return out
        if not cfg.get('enabled'):
            out['reason'] = 'disabled'
            return out
        if amt <= 0:
            out['reason'] = 'invalid-amount'
            return out
        b = active_binding(c, club_id=club_id, invitee_user_id=buyer_user_id, now=now)
        if not b:
            out['reason'] = 'no-binding'
            return out
        if int(b['affiliate_user_id']) == _int(buyer_user_id, 0, 1) and not cfg.get('allowSelfPurchase'):
            out['reason'] = 'self-purchase'
            return out
        rate, key, _label, _src = effective_rate_bp(cfg, b)
        out.update(eligible=True, reason='ok', rateBp=rate, levelKey=key,
                   affiliateUserId=int(b['affiliate_user_id']),
                   commissionCents=compute_commission(amt, rate))
        return out
    except Exception:
        out['reason'] = 'exception'
        return out


def accrue(c, *, club_id, order_type, order_id, buyer_user_id, amount_cents, cfg,
           module_enabled=True, now=None) -> dict:
    """佣金入账。幂等键 (club_id, order_type, order_id, affiliate_user_id)。

    全程吞异常 —— 佣金绝不能阻断订单主流程；失败时返回 reason 供对账排查。
    """
    res = {'accrued': False, 'reason': 'not-configured', 'commissionCents': 0,
           'rateBp': 0, 'levelKey': '', 'affiliateUserId': None, 'commissionId': None}
    try:
        ot = str(order_type or '').strip()[:24]
        oid = str(order_id or '').strip()[:64]
        if ot not in ORDER_TYPES or not oid:
            res['reason'] = 'invalid-order'
            return res
        q = preview(c, club_id=club_id, buyer_user_id=buyer_user_id,
                    amount_cents=amount_cents, cfg=cfg,
                    module_enabled=module_enabled, now=now)
        res.update(reason=q['reason'], commissionCents=q['commissionCents'], rateBp=q['rateBp'],
                   levelKey=q['levelKey'], affiliateUserId=q['affiliateUserId'])
        if not q['eligible']:
            return res
        ts = _ts(now)
        freeze = _int((cfg or {}).get('freezeDays'), 0, 0, MAX_FREEZE_DAYS)
        b = active_binding(c, club_id=club_id, invitee_user_id=buyer_user_id, now=now)
        cur = c.execute("""INSERT OR IGNORE INTO affiliate_commissions
             (club_id,affiliate_user_id,invitee_user_id,binding_id,order_type,order_id,
              order_amount_cents,rate_bp,commission_cents,level_key,status,
              created_at,available_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)""",
                         (club_id, q['affiliateUserId'], _int(buyer_user_id, 0, 1),
                          int(b['binding_id']), ot, oid, q['orderAmountCents'],
                          q['rateBp'], q['commissionCents'], q['levelKey'],
                          _ts_to_str(ts), _ts_to_str(ts + freeze * 86400)))
        # INSERT OR IGNORE 命中唯一键时 rowcount=0，天然幂等
        if not getattr(cur, 'rowcount', 0):
            res['reason'] = 'duplicate'
            res['commissionCents'] = 0
            return res
        c.execute("""UPDATE affiliate_members
                        SET total_orders=total_orders+1,
                            total_amount_cents=total_amount_cents+?,
                            total_commission_cents=total_commission_cents+?
                      WHERE club_id=? AND user_id=?""",
                  (q['orderAmountCents'], q['commissionCents'], club_id, q['affiliateUserId']))
        res.update(accrued=True, reason='ok', commissionId=int(cur.lastrowid))
        return res
    except Exception:
        res.update(accrued=False, reason='exception', commissionCents=0)
        return res


def reverse(c, *, club_id, order_type, order_id, now=None) -> dict:
    """退款冲回：只冲未结算的 PENDING 佣金；已结算的明确拒绝，提示人工线下追回（不算错账）。"""
    res = {'reversed': 0, 'skippedSettled': 0, 'cents': 0, 'reason': 'ok'}
    try:
        ts = _ts(now)
        ot = str(order_type or '').strip()[:24]
        oid = str(order_id or '').strip()[:64]
        if ot not in ORDER_TYPES or not oid:
            res['reason'] = 'invalid-order'
            return res
        rs = c.execute("""SELECT id,affiliate_user_id,commission_cents,status
                            FROM affiliate_commissions
                           WHERE club_id=? AND order_type=? AND order_id=?""",
                       (club_id, ot, oid)).fetchall()
        total = 0
        n = 0
        for r in rs:
            if str(r['status']).upper() == 'SETTLED':
                res['skippedSettled'] += 1
                continue
            if str(r['status']).upper() != 'PENDING':
                continue
            cur = c.execute("""UPDATE affiliate_commissions SET status='REVERSED', reversed_at=?
                                WHERE id=? AND status='PENDING'""", (_ts_to_str(ts), r['id']))
            if getattr(cur, 'rowcount', 0):
                cents = _int(r['commission_cents'], 0, 0)
                total += cents
                n += 1
                c.execute("""UPDATE affiliate_members
                                SET total_commission_cents=MAX(0,total_commission_cents-?),
                                    total_orders=MAX(0,total_orders-1)
                              WHERE club_id=? AND user_id=?""",
                          (cents, club_id, r['affiliate_user_id']))
        res.update(reversed=n, cents=total)
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def settle(c, *, club_id, affiliate_user_id, cfg, now=None) -> dict:
    """结算：把冻结期满的 PENDING 佣金汇总成一张结算单（GENERATED，待打款）。"""
    res = {'settled': False, 'reason': 'ok', 'settlementId': None,
           'grossCents': 0, 'entryCount': 0}
    try:
        ts = _ts(now)
        uid = _int(affiliate_user_id, 0, 1)
        if not uid:
            res['reason'] = 'invalid-user'
            return res
        m = c.execute('SELECT id,status FROM affiliate_members WHERE club_id=? AND user_id=?',
                      (club_id, uid)).fetchone()
        if not m:
            res['reason'] = 'not-affiliate'
            return res
        if str(m['status']).upper() == 'FROZEN':
            res['reason'] = 'member-frozen'
            return res
        # available_at 存 ISO 字符串，SQLite 字符串比较对同格式 UTC ISO 有效；
        # 为稳妥这里用 Python 逐行判定，避免时区/格式差异导致误结算。
        rs = c.execute("""SELECT id,commission_cents,available_at FROM affiliate_commissions
                           WHERE club_id=? AND affiliate_user_id=? AND status='PENDING'""",
                       (club_id, uid)).fetchall()
        due = []
        for r in rs:
            av = r['available_at']
            try:
                av_ts = int(av)
            except (TypeError, ValueError):
                try:
                    av_ts = int(datetime.fromisoformat(str(av)).timestamp())
                except Exception:
                    continue
            if av_ts <= ts:
                due.append(r)
        gross = sum(_int(x['commission_cents'], 0, 0) for x in due)
        min_cents = _int((cfg or {}).get('minSettleCents'), 0, 0, MAX_AMOUNT_CENTS)
        if not due or gross <= 0:
            res['reason'] = 'nothing-due'
            return res
        if gross < min_cents:
            res['reason'] = 'below-minimum'
            res['grossCents'] = gross
            return res
        sid = 'AFS-%d-%d-%s' % (club_id, uid, secrets.token_hex(4).upper())
        c.execute("""INSERT INTO affiliate_settlements
             (id,club_id,affiliate_user_id,gross_cents,entry_count,status,created_at)
             VALUES(?,?,?,?,?,'GENERATED',?)""",
                  (sid, club_id, uid, gross, len(due), _ts_to_str(ts)))
        for r in due:
            c.execute("""UPDATE affiliate_commissions
                            SET status='SETTLED', settlement_id=?, settled_at=?
                          WHERE id=? AND status='PENDING'""",
                      (sid, _ts_to_str(ts), r['id']))
        c.execute("""UPDATE affiliate_members
                        SET settled_cents=settled_cents+?
                      WHERE club_id=? AND user_id=?""", (gross, club_id, uid))
        res.update(settled=True, settlementId=sid, grossCents=gross, entryCount=len(due))
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def mark_settlement_paid(c, *, club_id, settlement_id, payment_ref='', now=None) -> dict:
    """结算单标记已打款，幂等。"""
    res = {'ok': False, 'reason': 'not-found'}
    try:
        r = c.execute('SELECT id,status FROM affiliate_settlements WHERE id=? AND club_id=?',
                      (str(settlement_id or '')[:80], club_id)).fetchone()
        if not r:
            return res
        if str(r['status']).upper() == 'PAID':
            res.update(ok=True, reason='already-paid')
            return res
        cur = c.execute("""UPDATE affiliate_settlements
                              SET status='PAID', payment_ref=?, paid_at=?
                            WHERE id=? AND club_id=? AND status='GENERATED'""",
                        (str(payment_ref or '')[:120], _ts_to_str(_ts(now)),
                         str(settlement_id)[:80], club_id))
        if not getattr(cur, 'rowcount', 0):
            res['reason'] = 'invalid-status'
            return res
        res.update(ok=True, reason='ok')
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def commission_view(r) -> dict:
    d = dict(r or {})
    return {
        'id': d.get('id'), 'clubId': d.get('club_id'),
        'affiliateUserId': d.get('affiliate_user_id'),
        'inviteeUserId': d.get('invitee_user_id'),
        'orderType': d.get('order_type'), 'orderId': d.get('order_id'),
        'orderAmountCents': _int(d.get('order_amount_cents'), 0, 0),
        'orderAmount': from_cents(d.get('order_amount_cents')),
        'rateBp': _int(d.get('rate_bp'), 0, 0),
        'commissionCents': _int(d.get('commission_cents'), 0, 0),
        'commission': from_cents(d.get('commission_cents')),
        'levelKey': d.get('level_key'), 'status': d.get('status'),
        'createdAt': d.get('created_at'), 'availableAt': d.get('available_at'),
        'settledAt': d.get('settled_at'), 'settlementId': d.get('settlement_id'),
        'reversedAt': d.get('reversed_at'),
    }


def settlement_view(r) -> dict:
    d = dict(r or {})
    return {
        'id': d.get('id'), 'clubId': d.get('club_id'),
        'affiliateUserId': d.get('affiliate_user_id'),
        'grossCents': _int(d.get('gross_cents'), 0, 0),
        'grossAmount': from_cents(d.get('gross_cents')),
        'entryCount': _int(d.get('entry_count'), 0, 0),
        'status': d.get('status'), 'paymentRef': d.get('payment_ref'),
        'createdAt': d.get('created_at'), 'paidAt': d.get('paid_at'),
    }

"""礼品卡（v0.43，P2-2）。

参考「远拓旅游 SaaS · 营销管理」的礼品卡形态：俱乐部按批次发行固定面额的卡密
→ 会员用卡密兑换 → 面额进入会员「礼品卡余额」→ 下单时核销余额。

**与既有体系的边界（很重要，勿混口径）**
- `stored_value_accounts` / `stored_value_ledger`：真金白银的「充值储值」（含充值赠送），
  `club_members.club_points_balance` / `gear_point_accounts` / `ai_credit_accounts` 是积分点数。
  礼品卡是**预付凭证兑换出来的独立余额**，既不写储值账户也不写积分，独立 `gift_card_*` 表。
- 本模块**不接入 checkout / payable**：兑换只增加会员礼品卡余额，核销由显式端点触发
  （订单完成后调用），`quote_consume()` 只做只读试算供 price-quote 下发对账，
  `appliedToPayable` 恒为 False，与 P1-3 拼团价同纪律。

**三铁律（沿用 club_home.py / P0~P2 各模块）**
1. 未配置 / 表不存在 / SQL 异常 / 值损坏 → 静默回落，绝不因礼品卡阻断既有下单链路。
2. 金额一律整数「分」；有效期一律 UTC+8 业务日字符串 YYYY-MM-DD，纯比较不落库。
3. 所有对外写操作幂等；非法输入静默丢弃或返回 reason，不抛异常到调用方。

DAO 函数只吃 cursor，由调用方在 `with conn() as c` 事务内调用，不自行 commit。
"""
from __future__ import annotations

import json
import math
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

# ── 常量 ────────────────────────────────────────────────────────────────────
BATCH_STATUSES = ('ACTIVE', 'VOID')
CARD_STATUSES = ('ACTIVE', 'REDEEMED', 'FROZEN', 'VOID', 'EXPIRED')
CARD_EDITABLE_STATUSES = ('ACTIVE', 'FROZEN', 'VOID')
ACCOUNT_STATUSES = ('ACTIVE', 'FROZEN')
LEDGER_TYPES = ('REDEEM', 'CONSUME', 'REFUND', 'ADJUST_UP', 'ADJUST_DOWN')
# 营销适用范围硬边界（2026-11-13）：只允许俱乐部活动 / 营地订单。
# 总平台装备销售（gear_order / mall / points-mall / Medusa）一律拒绝，
# 完整口径见 commerce_scope.py；三处白名单由 tests/test_commerce_scope.py 断言一致。
ORDER_TYPES = ('registration', 'camp_booking', 'camp_order', 'custom')

MAX_FACE_CENTS = 1_000_000       # 单卡面额上限 ¥10,000
MAX_AMOUNT_CENTS = 100_000_000   # 单笔核销上限 ¥1,000,000
MAX_ISSUE = 500                  # 单次发卡上限
MAX_REDEEMS_PER_USER = 100       # 每人可兑换卡数上限（0 = 不限）
MAX_BATCH_NAME = 60
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # 去掉易混淆的 0/O/1/I
CODE_PREFIX = 'GC'
CODE_LENGTH = 12
CODE_RE = re.compile(r'^[A-Z0-9]{4,32}$')

TS_MAX = 10 ** 12
_INVALID = object()


# ── 基础工具 ────────────────────────────────────────────────────────────────
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
    """元 → 整数分。非法 / NaN / inf / 非正 / 越界一律 0（不夹逼成最大值）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(f) or f <= 0:
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


def _ts(v=None) -> int:
    """秒级时间戳归一化。2026 年时间戳约 1.78e9，绝不能沿用 _int 默认 1e9 上界
    （P2-1 教训：夹逼函数不能替代合法性校验，默认上界要匹配数据域）。"""
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


def business_today(today=None) -> str:
    """UTC+8 业务日（YYYY-MM-DD）。today 可注入便于测试。"""
    if today is not None:
        return str(today)[:10]
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')


def parse_date(v):
    """严格 YYYY-MM-DD。空 → None；非法 → _INVALID（调用方据此整条丢弃）。"""
    if v is None or not str(v).strip():
        return None
    try:
        return datetime.strptime(str(v).strip()[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
    except Exception:
        return _INVALID


def normalize_code(raw) -> str:
    """卡密归一化：去掉空格/连字符、转大写；形状不对返回 ''（不猜测、不夹逼）。"""
    s = re.sub(r'[\s\-_]', '', str(raw or '')).upper()
    return s if CODE_RE.fullmatch(s) else ''


def gen_code(prefix: str = CODE_PREFIX, length: int = CODE_LENGTH) -> str:
    """密码学随机卡密。全库唯一由 DB UNIQUE(code) + 发卡重试保证。"""
    n = max(6, min(20, _int(length, CODE_LENGTH)))
    p = ''.join(ch for ch in str(prefix or '').upper() if ch.isalnum())[:4] or CODE_PREFIX
    return p + ''.join(secrets.choice(CODE_ALPHABET) for _ in range(n))


# ── 配置 ────────────────────────────────────────────────────────────────────
def default_config() -> dict:
    return {'enabled': False, 'maxRedeemsPerUser': 0, 'notice': ''}


def normalize_config(payload: dict, current: dict | None = None) -> dict:
    """PATCH 合并。current 是既有配置；越界值由路由层显式 400，这里只做兜底回落。"""
    p = payload if isinstance(payload, dict) else {}
    base = current if isinstance(current, dict) else default_config()
    raw_limit = p.get('maxRedeemsPerUser', base.get('maxRedeemsPerUser', 0))
    return {
        'enabled': bool(p.get('enabled', base.get('enabled', False))),
        'maxRedeemsPerUser': _int(raw_limit, 0, 0, MAX_REDEEMS_PER_USER),
        'notice': str(p.get('notice', base.get('notice', '')) or '').strip()[:120],
    }


def config_from_row(r) -> dict:
    if r is None:
        return default_config()
    d = dict(r)
    cfg = loads(d.get('config_json'), {})
    return {
        'enabled': bool(d.get('enabled')),
        'maxRedeemsPerUser': _int(cfg.get('maxRedeemsPerUser'), 0, 0, MAX_REDEEMS_PER_USER),
        'notice': str(cfg.get('notice') or '').strip()[:120],
    }


def config_view(cfg: dict) -> dict:
    cfg = cfg or default_config()
    return {
        'enabled': bool(cfg.get('enabled')),
        'maxRedeemsPerUser': int(cfg.get('maxRedeemsPerUser') or 0),
        'notice': str(cfg.get('notice') or ''),
    }


def public_config(cfg: dict) -> dict:
    """C 端只下发需要展示的字段，不泄露后台运营参数。"""
    cfg = cfg or default_config()
    return {'enabled': bool(cfg.get('enabled')),
            'notice': str(cfg.get('notice') or ''),
            'maxRedeemsPerUser': int(cfg.get('maxRedeemsPerUser') or 0)}


# ── 批次 / 卡密归一化 ───────────────────────────────────────────────────────
def normalize_batch(payload: dict, current: dict | None = None) -> dict | None:
    """新建批次。面额严格正整数分，数量 1~MAX_ISSUE，日期严格，非法整条丢弃。"""
    if not isinstance(payload, dict):
        return None
    name = str(payload.get('name') or '').strip()[:MAX_BATCH_NAME]
    if not name:
        return None
    face = to_cents(payload.get('faceAmount'))
    if not 0 < face <= MAX_FACE_CENTS:
        return None
    try:
        count = int(payload.get('count') or 0)
    except (TypeError, ValueError):
        return None
    if not 1 <= count <= MAX_ISSUE:
        return None
    vf = parse_date(payload.get('validFrom'))
    vu = parse_date(payload.get('validUntil'))
    if vf is _INVALID or vu is _INVALID:
        return None
    if vf and vu and vf > vu:
        return None
    return {
        'name': name,
        'faceCents': face,
        'faceAmount': from_cents(face),
        'count': count,
        'validFrom': vf,
        'validUntil': vu,
        'note': str(payload.get('note') or '').strip()[:200],
    }


def normalize_batch_update(payload: dict, current: dict | None = None) -> dict | None:
    """批次 PATCH：**不允许改面额**（已发出的卡会账实不符），可改名/备注/有效期/停用。"""
    if not isinstance(payload, dict):
        return None
    base = current if isinstance(current, dict) else {}
    out = {}
    if 'name' in payload:
        name = str(payload.get('name') or '').strip()[:MAX_BATCH_NAME]
        if not name:
            return None
        out['name'] = name
    if 'note' in payload:
        out['note'] = str(payload.get('note') or '').strip()[:200]
    if 'status' in payload:
        st = str(payload.get('status') or '').upper()
        if st not in BATCH_STATUSES:
            return None
        out['status'] = st
    vf = parse_date(payload.get('validFrom')) if 'validFrom' in payload else None
    vu = parse_date(payload.get('validUntil')) if 'validUntil' in payload else None
    if vf is _INVALID or vu is _INVALID:
        return None
    if 'validFrom' in payload:
        out['validFrom'] = vf
    if 'validUntil' in payload:
        out['validUntil'] = vu
    left = out.get('validFrom', base.get('validFrom'))
    right = out.get('validUntil', base.get('validUntil'))
    if left and right and left > right:
        return None
    return out


def normalize_card_update(payload: dict, current: dict | None = None) -> dict | None:
    """单卡 PATCH：已兑换的卡**不允许改状态**（否则可重复兑换），只允许 ACTIVE/FROZEN/VOID。"""
    if not isinstance(payload, dict):
        return None
    base = current if isinstance(current, dict) else {}
    out = {}
    if 'status' in payload:
        if (base.get('status') or '') == 'REDEEMED':
            return None
        st = str(payload.get('status') or '').upper()
        if st not in CARD_EDITABLE_STATUSES:
            return None
        out['status'] = st
    if 'note' in payload:
        out['note'] = str(payload.get('note') or '').strip()[:200]
    return out


def effective_status(card, today=None) -> str:
    """惰性有效期判定（纯函数，不落库）。已终态的卡直接返回其落库状态。"""
    d = dict(card) if not isinstance(card, dict) else card
    st = d.get('status') or 'ACTIVE'
    if st != 'ACTIVE':
        return st
    t = business_today(today)
    vf, vu = d.get('valid_from'), d.get('valid_until')
    if vf and t < vf:
        return 'NOT_STARTED'
    if vu and t > vu:
        return 'EXPIRED'
    return 'ACTIVE'


def batch_view(r, issued: int = 0, redeemed: int = 0) -> dict:
    d = dict(r) if r is not None else {}
    face = int(d.get('face_cents') or 0)
    return {
        'id': d.get('id'),
        'clubId': d.get('club_id'),
        'name': d.get('name'),
        'faceCents': face, 'faceAmount': from_cents(face),
        'planCount': int(d.get('plan_count') or 0),
        'issued': int(issued or 0), 'redeemed': int(redeemed or 0),
        'validFrom': d.get('valid_from'), 'validUntil': d.get('valid_until'),
        'status': d.get('status') or 'ACTIVE',
        'note': d.get('note'),
        'createdAt': d.get('created_at'), 'createdBy': d.get('created_by'),
    }


def card_view(r, today=None) -> dict:
    d = dict(r) if r is not None else {}
    face = int(d.get('face_cents') or 0)
    return {
        'id': d.get('id'), 'clubId': d.get('club_id'), 'batchId': d.get('batch_id'),
        'code': d.get('code'),
        'faceCents': face, 'faceAmount': from_cents(face),
        'status': d.get('status') or 'ACTIVE',
        'effectiveStatus': effective_status(d, today),
        'userId': d.get('user_id'), 'redeemedAt': d.get('redeemed_at'),
        'validFrom': d.get('valid_from'), 'validUntil': d.get('valid_until'),
        'note': d.get('note'), 'createdAt': d.get('created_at'),
    }


def account_view(r) -> dict:
    d = dict(r) if r is not None else {}
    bal = int(d.get('balance_cents') or 0)
    return {
        'id': d.get('id'), 'clubId': d.get('club_id'), 'userId': d.get('user_id'),
        'balanceCents': bal, 'balanceAmount': from_cents(bal),
        'totalRedeemedCents': int(d.get('total_redeemed_cents') or 0),
        'totalRedeemedAmount': from_cents(d.get('total_redeemed_cents')),
        'totalConsumedCents': int(d.get('total_consumed_cents') or 0),
        'totalConsumedAmount': from_cents(d.get('total_consumed_cents')),
        'status': d.get('status') or 'ACTIVE',
        'createdAt': d.get('created_at'), 'updatedAt': d.get('updated_at'),
    }


def ledger_view(r) -> dict:
    d = dict(r) if r is not None else {}
    amt = int(d.get('amount_cents') or 0)
    return {
        'id': d.get('id'), 'type': d.get('type'),
        'amountCents': amt, 'amountAmount': from_cents(abs(amt)),
        'balanceAfterCents': int(d.get('balance_after_cents') or 0),
        'balanceAfterAmount': from_cents(d.get('balance_after_cents')),
        'sourceType': d.get('source_type'), 'sourceId': d.get('source_id'),
        'cardId': d.get('card_id'), 'operator': d.get('operator'),
        'note': d.get('note'), 'createdAt': d.get('created_at'),
    }


# ── 只读试算 ────────────────────────────────────────────────────────────────
def quote_consume(payable_cents: int, balance_cents: int, max_ratio: int = 100) -> dict:
    """本单最多可用礼品卡余额（纯读，不写库、不改 payable）。"""
    try:
        payable = max(0, int(payable_cents))
        balance = max(0, int(balance_cents))
        ratio = max(0, min(100, int(max_ratio)))
    except (TypeError, ValueError):
        payable, balance, ratio = 0, 0, 0
    usable = min(balance, payable, int(payable * ratio / 100))
    return {
        'payableCents': payable, 'payableAmount': from_cents(payable),
        'balanceCents': balance, 'balanceAmount': from_cents(balance),
        'usableCents': usable, 'usableAmount': from_cents(usable),
        'remainingCents': max(0, payable - usable),
        'remainingAmount': from_cents(max(0, payable - usable)),
        'maxPayRatio': ratio,
    }


# ── 记账（cursor-only，调用方负责事务）──────────────────────────────────────
def apply_ledger(c, *, club_id: int, user_id: int, delta_cents: int, ledger_type: str,
                 source_type: str | None = None, source_id: str | None = None,
                 card_id: int | None = None, operator: str | None = None,
                 note: str | None = None, now: int | None = None) -> dict:
    """同事务内改余额 + 写流水（带 balance_after_cents 快照）。

    - 幂等：同一 (club_id,user_id,source_type,source_id) 已记过 → reason='duplicated'；
    - 余额不足 / 账户冻结 / 零额 / 非法类型 → 明确 reason，不写任何数据；
    - 累计字段只统计真实业务流水（REDEEM/CONSUME/REFUND），后台手工调整不计入；
    - 所有异常内部吞掉 → reason='exception'，绝不外抛阻断主流程。
    """
    res = {'ok': False, 'reason': '', 'balanceCents': None}
    try:
        club_id, user_id, delta_cents = int(club_id), int(user_id), int(delta_cents)
        if delta_cents == 0:
            res['reason'] = 'zero-delta'
            return res
        if ledger_type not in LEDGER_TYPES:
            res['reason'] = 'bad-type'
            return res
        sid = str(source_id) if source_id is not None else None
        created = _ts_to_str(_ts(now))
        if sid is not None:
            dup = c.execute(
                """SELECT id FROM gift_card_ledger
                    WHERE club_id=? AND user_id=? AND source_type=? AND source_id=? LIMIT 1""",
                (club_id, user_id, source_type, sid)).fetchone()
            if dup:
                res['reason'] = 'duplicated'
                return res
        c.execute(
            """INSERT OR IGNORE INTO gift_card_accounts
               (club_id,user_id,balance_cents,total_redeemed_cents,total_consumed_cents,
                status,created_at,updated_at)
               VALUES(?,?,0,0,0,'ACTIVE',?,?)""",
            (club_id, user_id, created, created))
        acc = c.execute(
            'SELECT * FROM gift_card_accounts WHERE club_id=? AND user_id=?',
            (club_id, user_id)).fetchone()
        if not acc:
            res['reason'] = 'no-account'
            return res
        if (acc['status'] or 'ACTIVE') == 'FROZEN':
            res['reason'] = 'frozen'
            return res
        bal = int(acc['balance_cents'] or 0)
        new_bal = bal + delta_cents
        if new_bal < 0:
            res['reason'] = 'insufficient'
            res['balanceCents'] = bal
            return res
        # 累计消费是「净核销额」：CONSUME 增加、REFUND 冲减（下限 0）；
        # 后台手工调整（ADJUST_*）一律不计入，避免人工纠错污染对账口径（同 P1-1）。
        d_redeem = delta_cents if ledger_type == 'REDEEM' else 0
        d_consume = -delta_cents if ledger_type == 'CONSUME' else 0
        d_refund = delta_cents if ledger_type == 'REFUND' else 0
        c.execute(
            """UPDATE gift_card_accounts
                  SET balance_cents=?,
                      total_redeemed_cents=total_redeemed_cents+?,
                      total_consumed_cents=MAX(0, total_consumed_cents+?),
                      updated_at=?
                WHERE id=?""",
            (new_bal, max(0, d_redeem), d_consume - d_refund, created, acc['id']))
        cur = c.execute(
            """INSERT INTO gift_card_ledger
               (club_id,user_id,type,amount_cents,balance_after_cents,source_type,source_id,
                card_id,operator,note,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (club_id, user_id, ledger_type, delta_cents, new_bal, source_type, sid,
             card_id, str(operator or '')[:60] or None, str(note or '')[:200] or None, created))
        res.update(ok=True, reason='ok', balanceCents=new_bal, ledgerId=cur.lastrowid)
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def issue_cards(c, *, club_id: int, batch_id: int, face_cents: int, count: int,
                valid_from=None, valid_until=None, prefix: str = CODE_PREFIX,
                length: int = CODE_LENGTH, now: int | None = None) -> dict:
    """按批次发卡。卡密全局唯一，撞码重试；返回实际发出数量与卡密列表。"""
    res = {'ok': False, 'reason': '', 'issued': 0, 'codes': []}
    try:
        club_id, batch_id, face_cents = int(club_id), int(batch_id), int(face_cents)
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 0
        if not 1 <= count <= MAX_ISSUE or not 0 < face_cents <= MAX_FACE_CENTS:
            res['reason'] = 'invalid-input'
            return res
        created = _ts_to_str(_ts(now))
        made = []
        for _ in range(count):
            for _try in range(8):
                code = gen_code(prefix, length)
                try:
                    c.execute(
                        """INSERT INTO gift_card_cards
                           (club_id,batch_id,code,face_cents,status,valid_from,valid_until,created_at)
                           VALUES(?,?,?,?,'ACTIVE',?,?,?)""",
                        (club_id, batch_id, code, face_cents, valid_from, valid_until, created))
                    made.append(code)
                    break
                except Exception as e:
                    # 只重试唯一键冲突；其它异常（表缺失等）立即放弃整批。
                    if 'UNIQUE' not in str(e).upper():
                        res['reason'] = 'exception'
                        return res
            else:
                res['reason'] = 'code-collision'
                return res
        res.update(ok=True, reason='ok', issued=len(made), codes=made)
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def redeem(c, *, club_id: int, code: str, user_id: int,
           cfg: dict | None = None, module_enabled: bool = True,
           today=None, now: int | None = None, operator: str | None = None) -> dict:
    """卡密兑换：卡置 REDEEMED + 面额进会员礼品卡余额（同事务，失败由调用方回滚）。"""
    res = {'ok': False, 'reason': '', 'cardId': None, 'faceCents': 0,
           'faceAmount': 0.0, 'balanceCents': None, 'balanceAmount': 0.0}
    cfg = cfg or default_config()
    try:
        if not module_enabled or not cfg.get('enabled'):
            res['reason'] = 'disabled'
            return res
        norm = normalize_code(code)
        if not norm:
            res['reason'] = 'bad-code'
            return res
        try:
            club_id, user_id = int(club_id), int(user_id)
        except (TypeError, ValueError):
            res['reason'] = 'invalid-user'
            return res
        if user_id <= 0:
            res['reason'] = 'invalid-user'
            return res
        # 按 club_id + code 查，别家俱乐部的卡一律视作不存在（不泄露存在性）。
        card = c.execute(
            'SELECT * FROM gift_card_cards WHERE club_id=? AND code=?',
            (club_id, norm)).fetchone()
        if not card:
            res['reason'] = 'not-found'
            return res
        d = dict(card)
        st = effective_status(d, today)
        if st != 'ACTIVE':
            res['reason'] = 'card-' + st.lower().replace('_', '-')
            return res
        batch = c.execute(
            'SELECT * FROM gift_card_batches WHERE id=? AND club_id=?',
            (d['batch_id'], club_id)).fetchone()
        if not batch or (batch['status'] or 'ACTIVE') != 'ACTIVE':
            res['reason'] = 'batch-void'
            return res
        # 批次有效期也是硬闸门：后台后来收紧/停用批次时，已经发出的旧卡不能绕过。
        # 卡上自带的 valid_* 只用于列表展示与导出，绝不作为唯一真相。
        bst = effective_status({'status': 'ACTIVE',
                                'valid_from': batch['valid_from'],
                                'valid_until': batch['valid_until']}, today)
        if bst != 'ACTIVE':
            res['reason'] = 'card-' + bst.lower().replace('_', '-')
            return res
        limit = _int(cfg.get('maxRedeemsPerUser'), 0, 0, MAX_REDEEMS_PER_USER)
        if limit:
            used = c.execute(
                """SELECT COUNT(*) FROM gift_card_cards
                    WHERE club_id=? AND user_id=? AND status='REDEEMED'""",
                (club_id, user_id)).fetchone()[0] or 0
            if used >= limit:
                res['reason'] = 'redeem-limit'
                return res
        created = _ts_to_str(_ts(now))
        # 条件更新防并发重复兑换：谁先把 ACTIVE 改掉谁生效。
        cur = c.execute(
            """UPDATE gift_card_cards
                  SET status='REDEEMED', user_id=?, redeemed_at=?
                WHERE id=? AND club_id=? AND status='ACTIVE'""",
            (user_id, created, d['id'], club_id))
        if not cur.rowcount:
            res['reason'] = 'card-redeemed'
            return res
        face = int(d.get('face_cents') or 0)
        led = apply_ledger(c, club_id=club_id, user_id=user_id, delta_cents=face,
                           ledger_type='REDEEM', source_type='GIFT_CARD',
                           source_id=norm, card_id=d['id'], operator=operator,
                           note='礼品卡兑换', now=now)
        if not led['ok']:
            res['reason'] = led['reason']
            return res
        res.update(ok=True, reason='ok', cardId=d['id'], faceCents=face,
                   faceAmount=from_cents(face), balanceCents=led['balanceCents'],
                   balanceAmount=from_cents(led['balanceCents']))
        return res
    except Exception:
        res['reason'] = 'exception'
        return res


def consume(c, *, club_id: int, user_id: int, cents: int,
            order_type: str, order_id: str, operator: str | None = None,
            note: str | None = None, now: int | None = None) -> dict:
    """余额核销：金额分，orderType+orderId 幂等。绝不改 payable / checkout。"""
    res = {'ok': False, 'reason': '', 'usedCents': 0,
           'usedAmount': 0.0, 'balanceCents': None, 'balanceAmount': 0.0}
    try:
        club_id, user_id = int(club_id), int(user_id)
        amount = int(cents)
        ot = str(order_type or '')
        oid = str(order_id or '').strip()[:80]
    except (TypeError, ValueError):
        res['reason'] = 'invalid-input'
        return res
    if not 0 < amount <= MAX_AMOUNT_CENTS or ot not in ORDER_TYPES or not oid:
        res['reason'] = 'invalid-input'
        return res
    led = apply_ledger(c, club_id=club_id, user_id=user_id, delta_cents=-amount,
                       ledger_type='CONSUME', source_type='ORDER',
                       source_id=ot + ':' + oid, operator=operator,
                       note=note or '礼品卡余额核销', now=now)
    if not led['ok']:
        res['reason'] = led['reason']
        return res
    res.update(ok=True, reason='ok', usedCents=amount, usedAmount=from_cents(amount),
               balanceCents=led['balanceCents'], balanceAmount=from_cents(led['balanceCents']))
    return res


def reverse_consume(c, *, club_id: int, user_id: int, order_type: str, order_id: str,
                    operator: str | None = None, now: int | None = None) -> dict:
    """退款冲回核销：只冲真实存在的 CONSUME，且同一订单只冲一次。"""
    res = {'ok': False, 'reason': '', 'cents': 0, 'amount': 0.0, 'balanceCents': None}
    try:
        club_id, user_id = int(club_id), int(user_id)
    except (TypeError, ValueError):
        res['reason'] = 'invalid-input'
        return res
    src = str(order_type or '') + ':' + str(order_id or '').strip()[:80]
    refund_src = 'REFUND:' + src
    try:
        origin = c.execute(
            """SELECT amount_cents FROM gift_card_ledger
                WHERE club_id=? AND user_id=? AND type='CONSUME'
                  AND source_type='ORDER' AND source_id=? LIMIT 1""",
            (club_id, user_id, src)).fetchone()
        if not origin:
            res['reason'] = 'not-found'
            return res
        if c.execute(
            """SELECT id FROM gift_card_ledger
                WHERE club_id=? AND user_id=? AND type='REFUND'
                  AND source_type='ORDER' AND source_id=? LIMIT 1""",
                (club_id, user_id, refund_src)).fetchone():
            res['reason'] = 'duplicated'
            return res
        back = abs(int(origin['amount_cents'] or 0))
        led = apply_ledger(c, club_id=club_id, user_id=user_id, delta_cents=back,
                           ledger_type='REFUND', source_type='ORDER',
                           source_id=refund_src, operator=operator,
                           note='核销冲回', now=now)
        if not led['ok']:
            res['reason'] = led['reason']
            return res
        res.update(ok=True, reason='ok', cents=back, amount=from_cents(back),
                   balanceCents=led['balanceCents'])
        return res
    except Exception:
        res['reason'] = 'exception'
        return res

"""会员储值账户（v0.37，P1-1）。

参考「远拓旅游 SaaS · 会员储值/充值赠额」：会员先充值（可配置「充 X 送 Y」档位），
余额可在报名/商城支付时抵扣，全程留流水可审计。

与既有体系的关系（刻意保持最小爆炸半径）：
- 既有 club_members.club_points_balance / gear_point_accounts / ai_credit_accounts 都是
  「积分 / 点数」，本模块是**真金白银的预存余额**，独立账户表，绝不改动 wallet_snapshot。
- 金额一律以「分」(INTEGER) 存储与运算，杜绝浮点误差；对外同时给「分」和「元」两套字段。
- 记账沿用 ai_credit_ledger 的「账户 + 流水」范式，额外写入 balance_after_cents 余额快照，
  并用 (club_id,user_id,source_type,source_id) 唯一索引保证幂等（重复确认充值不会重复到账）。

设计要点（沿用三铁律哲学）：
1. 俱乐部未开启储值 → 一律不可用，绝不阻断既有下单/报价链路；
2. 非法档位 / 非法金额 / 余额不足 → 静默拒绝或丢弃，不抛错到主流程；
3. 所有对外纯函数 stdlib-only，可被 importlib 独立加载测试；记账函数只吃 cursor，
   由调用方在 `with conn() as c` 事务内调用，不自行 commit。
"""
from __future__ import annotations

import json
import math

MAX_RECHARGE_CENTS = 1_000_000   # 单笔充值上限 ¥10,000
MAX_ADJUST_CENTS = 100_000       # 后台单次手工调整上限 ¥1,000
RULE_MAX = 20
ACCOUNT_STATUSES = ('ACTIVE', 'FROZEN')
RECHARGE_STATUSES = ('PENDING', 'PAID', 'CANCELLED')
LEDGER_TYPES = ('RECHARGE', 'BONUS', 'ADJUST_UP', 'ADJUST_DOWN', 'PAY', 'REFUND', 'CANCEL')


def to_cents(amount) -> int:
    """元 → 分（四舍五入）。非法 / NaN / inf / 负数 一律归 0。"""
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(v) or v <= 0:
        return 0
    return int(round(v * 100))


def from_cents(cents) -> float:
    """分 → 元（2 位小数）。"""
    try:
        return round(int(cents or 0) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def loads(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def normalize_rule(payload) -> dict | None:
    """归一化一条「充 X 送 Y」档位。输入用「元」(minAmount/bonusAmount)，内部转分。

    形状不对 → 返回 None（静默丢弃，不抛错）；门槛必须 > 0。
    """
    if not isinstance(payload, dict):
        return None
    min_c = to_cents(payload.get('minAmount'))
    bonus_c = to_cents(payload.get('bonusAmount', payload.get('bonusAmountYuan')))
    # bonus 允许为 0（纯充值不赠送），但要能区分「没传」和「传了 0」
    if min_c <= 0 or min_c > MAX_RECHARGE_CENTS:
        return None
    if bonus_c < 0 or bonus_c > MAX_RECHARGE_CENTS:
        return None
    return {
        'minCents': min_c,
        'minAmount': from_cents(min_c),
        'bonusCents': bonus_c,
        'bonusAmount': from_cents(bonus_c),
        'label': str(payload.get('label') or '').strip()[:40],
        'enabled': bool(payload.get('enabled', True)),
    }


def normalize_rules(raw) -> list:
    """把任意输入（list / JSON 字符串）归一化成规则数组，按门槛从高到低排序。"""
    src = loads(raw) if isinstance(raw, str) else raw
    if not isinstance(src, list):
        return []
    out = []
    for item in src[:RULE_MAX]:
        r = normalize_rule(item)
        if r:
            out.append(r)
    out.sort(key=lambda x: x['minCents'], reverse=True)
    return out


def normalize_config(payload: dict, current: dict | None = None) -> dict:
    """俱乐部储值配置归一化。current 为既有配置（PATCH 合并用）。"""
    base = current if isinstance(current, dict) else {}
    cfg_present = payload.get('rules')
    if cfg_present is None:
        rules = base.get('rules') or []
        if not isinstance(rules, list):
            rules = normalize_rules(rules)
    else:
        rules = normalize_rules(cfg_present)
    try:
        ratio = int(payload.get('maxPayRatio', base.get('maxPayRatio', 100)))
    except (TypeError, ValueError):
        ratio = 100
    # 最低充值门槛：payload 用「元」；base 优先读同样以元为单位的 minRechargeAmount，
    # 仅当 base 只有「分」字段（minRechargeCents）时才 /100 换算。
    # ★ 绝不把 base['minRechargeCents'] 直接喂给 to_cents()——那会把「分」当「元」
    #   放大 100 倍（只 PATCH enabled 也会把 ¥100 悄悄变成 ¥10000）。
    min_yuan = payload.get('minRechargeAmount')
    if min_yuan is None:
        min_yuan = base.get('minRechargeAmount')
        if min_yuan is None:
            try: min_yuan = (int(base.get('minRechargeCents') or 0)) / 100.0
            except (TypeError, ValueError): min_yuan = 0
    return {
        'enabled': bool(payload.get('enabled', base.get('enabled', False))),
        'allowActivityPay': bool(payload.get('allowActivityPay', base.get('allowActivityPay', True))),
        'maxPayRatio': max(0, min(100, ratio)),
        'minRechargeCents': max(0, min(MAX_RECHARGE_CENTS, to_cents(min_yuan))),
        'rules': rules,
    }


def config_from_row(row) -> dict:
    """DB 行 → 前端配置 dict（元 + 分双份字段）。"""
    d = dict(row) if row is not None else {}
    out = {
        'enabled': bool(d.get('enabled')),
        'allowActivityPay': bool(d.get('allow_activity_pay', 1)),
        'maxPayRatio': int(d.get('max_pay_ratio') or 100),
        'minRechargeCents': int(d.get('min_recharge_cents') or 0),
        'minRechargeAmount': from_cents(d.get('min_recharge_cents') or 0),
        'rules': normalize_rules(d.get('rules_json')),
        'updatedAt': d.get('updated_at'),
    }
    return out


def compute_bonus(amount_cents: int, rules: list) -> int:
    """按「满足门槛中门槛最高者」选档，返回赠送额（分）。未命中 / 未启用 → 0。"""
    try:
        amount_cents = int(amount_cents)
    except (TypeError, ValueError):
        return 0
    if amount_cents <= 0:
        return 0
    best_min, best_bonus = -1, 0
    for r in rules or []:
        if not isinstance(r, dict) or not r.get('enabled', True):
            continue
        mn = int(r.get('minCents') or 0)
        if amount_cents >= mn and mn > best_min:
            best_min = mn
            best_bonus = max(0, int(r.get('bonusCents') or 0))
    return best_bonus


def quote_pay(payable_cents: int, balance_cents: int, max_ratio: int = 100) -> dict:
    """本次用储值最多能抵多少分（可配置封顶比例）。纯函数，不改任何数据。"""
    try:
        payable = max(0, int(payable_cents))
        balance = max(0, int(balance_cents))
        ratio = max(0, min(100, int(max_ratio)))
    except (TypeError, ValueError):
        payable, balance, ratio = 0, 0, 0
    usable = min(balance, payable, int(payable * ratio / 100))
    remaining = max(0, payable - usable)
    return {
        'payableCents': payable, 'payableAmount': from_cents(payable),
        'balanceCents': balance, 'balanceAmount': from_cents(balance),
        'usableCents': usable, 'usableAmount': from_cents(usable),
        'remainingCents': remaining, 'remainingAmount': from_cents(remaining),
        'maxPayRatio': ratio,
    }


def account_view(row) -> dict:
    """DB 行 → 前端账户 dict（元 + 分双份字段）。"""
    d = dict(row) if row is not None else {}
    bal = int(d.get('balance_cents') or 0)
    return {
        'id': d.get('id'),
        'clubId': d.get('club_id'),
        'userId': d.get('user_id'),
        'userName': d.get('user_name'),
        'userPhone': d.get('user_phone'),
        'balanceCents': bal, 'balanceAmount': from_cents(bal),
        'totalRechargedCents': int(d.get('total_recharged_cents') or 0),
        'totalBonusCents': int(d.get('total_bonus_cents') or 0),
        'totalSpentCents': int(d.get('total_spent_cents') or 0),
        'status': d.get('status') or 'ACTIVE',
        'createdAt': d.get('created_at'),
        'updatedAt': d.get('updated_at'),
    }


def ledger_view(row) -> dict:
    d = dict(row) if row is not None else {}
    amt = int(d.get('amount_cents') or 0)
    return {
        'id': d.get('id'),
        'type': d.get('type'),
        'amountCents': amt, 'amountAmount': from_cents(abs(amt)),
        'balanceAfterCents': int(d.get('balance_after_cents') or 0),
        'balanceAfterAmount': from_cents(d.get('balance_after_cents') or 0),
        'sourceType': d.get('source_type'),
        'sourceId': d.get('source_id'),
        'operator': d.get('operator'),
        'note': d.get('note'),
        'createdAt': d.get('created_at'),
    }


def apply_ledger(c, *, club_id: int, user_id: int, delta_cents: int, ledger_type: str,
                 source_type: str | None = None, source_id: str | None = None,
                 operator: str | None = None, note: str | None = None) -> dict:
    """在同事务内改余额 + 写流水。返回 {ok, reason, balanceCents}。

    - 幂等：同一 (club_id,user_id,source_type,source_id) 已记过 → ok=False, reason='duplicated'；
    - 余额不足（扣减后为负）→ ok=False, reason='insufficient'，不写任何数据；
    - 账户不存在 → 自动建 0 余额账户（仅当 delta > 0 或账户已存在时才有意义）。
    所有异常内部吞掉，返回 ok=False, reason='exception'，绝不外抛阻断主流程。
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
        if sid is not None:
            dup = c.execute(
                """SELECT id FROM stored_value_ledger
                   WHERE club_id=? AND user_id=? AND source_type=? AND source_id=? LIMIT 1""",
                (club_id, user_id, source_type, sid)).fetchone()
            if dup:
                res['reason'] = 'duplicated'
                return res
        c.execute(
            """INSERT OR IGNORE INTO stored_value_accounts
               (club_id,user_id,balance_cents,total_recharged_cents,total_bonus_cents,total_spent_cents,status,created_at,updated_at)
               VALUES(?,?,0,0,0,0,'ACTIVE',datetime('now'),datetime('now'))""",
            (club_id, user_id))
        acc = c.execute(
            'SELECT * FROM stored_value_accounts WHERE club_id=? AND user_id=?',
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

        # 累计字段只统计真实业务流水：后台手工调整（ADJUST_UP/DOWN）是纠错/补偿，
        # 不计入「累计充值 / 累计消费」，否则后台对账口径会被人工操作污染。
        d_recharge = delta_cents if ledger_type == 'RECHARGE' else 0
        d_bonus = delta_cents if ledger_type == 'BONUS' else 0
        d_spent = -delta_cents if ledger_type == 'PAY' else 0
        c.execute(
            """UPDATE stored_value_accounts
               SET balance_cents=?,
                   total_recharged_cents=total_recharged_cents+?,
                   total_bonus_cents=total_bonus_cents+?,
                   total_spent_cents=total_spent_cents+?,
                   updated_at=datetime('now')
               WHERE id=?""",
            (new_bal, max(0, d_recharge), max(0, d_bonus), max(0, d_spent), acc['id']))
        c.execute(
            """INSERT INTO stored_value_ledger
               (club_id,user_id,type,amount_cents,balance_after_cents,source_type,source_id,operator,note,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (club_id, user_id, ledger_type, delta_cents, new_bal, source_type, sid,
             str(operator or '')[:60] or None, str(note or '')[:200] or None))
        res['ok'] = True
        res['reason'] = 'ok'
        res['balanceCents'] = new_bal
        return res
    except Exception:
        res['reason'] = 'exception'
        return res

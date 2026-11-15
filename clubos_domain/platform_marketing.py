"""总平台营销能力（2026-11-15）。

对齐俱乐部端「俱乐部营销」页的消费送券 / 邀请奖励，但数据严格隔离：
* 平台规则只发 ``owner_type='PLATFORM'`` 的券，成本总平台承担（funding=PLATFORM）；
* 平台活动复用 ``marketing_campaigns(owner_type='PLATFORM')``，报价引擎已统一求值；
* 邀请奖励是「俱乐部配置优先、平台配置兜底」，不会覆盖俱乐部自己设的奖励。

所有函数只拿调用方传入的 sqlite cursor，模块本身不 import db，便于单测。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

_log = logging.getLogger(__name__)

INVITE_REWARD_KEY = 'platform.inviteReward'

INVITE_DEFAULTS = {
    'enabled': False,
    'inviteeBenefitId': None,
    'inviterBenefitId': None,
    'inviterPoints': 0,
    'shareTitle': '',
    'shareDesc': '',
    'shareImageKey': 'sunrise',
}


class PlatformMarketingError(ValueError):
    """配置 / 规则校验失败（路由层转 400）。"""


def _dict(r):
    return dict(r) if r is not None else None


# ── 消费送券规则 ──────────────────────────────────────────────────────────
def gift_rules(c, include_disabled: bool = True) -> list[dict]:
    q = '''SELECT g.*, b.title benefit_title, b.mode, b.threshold_amount benefit_threshold,
                  b.discount_percent, b.cash_value benefit_cash_value, b.stock benefit_stock,
                  b.status benefit_status, b.benefit_type, b.claimed_count benefit_claimed
           FROM platform_coupon_gift_rules g JOIN member_benefits b ON b.id=g.benefit_id'''
    if not include_disabled:
        q += ' WHERE g.enabled=1'
    q += ' ORDER BY g.threshold_amount ASC, g.id DESC'
    return [dict(r) for r in c.execute(q).fetchall()]


def _assert_platform_coupon(c, benefit_id: int) -> dict:
    r = c.execute('SELECT * FROM member_benefits WHERE id=?', (int(benefit_id),)).fetchone()
    if not r:
        raise PlatformMarketingError('要赠送的优惠券不存在')
    b = dict(r)
    if b.get('owner_type') != 'PLATFORM' or b.get('benefit_type') not in ('activity_coupon', 'gear_coupon'):
        raise PlatformMarketingError('只能赠送总平台自己创建的活动券 / 装备券')
    if str(b.get('status') or 'active') != 'active':
        raise PlatformMarketingError('该券已下架，不能用于赠送')
    if b.get('stock') is not None and int(b['stock']) <= 0:
        raise PlatformMarketingError('该券库存为 0，请先补库存再设置赠送')
    return b


def create_gift_rule(c, payload: dict) -> int:
    threshold = max(0.0, float(payload.get('thresholdAmount') or 0))
    if threshold <= 0:
        raise PlatformMarketingError('赠送门槛必须大于 0 元')
    bid = int(payload.get('benefitId') or 0)
    _assert_platform_coupon(c, bid)
    per_user_limit = max(1, int(payload.get('perUserLimit') or 1))
    enabled = 0 if payload.get('enabled') is False else 1
    c.execute('''INSERT INTO platform_coupon_gift_rules
                 (threshold_amount,benefit_id,per_user_limit,enabled,created_at,updated_at)
                 VALUES(?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)''',
              (threshold, bid, per_user_limit, enabled))
    return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])


def update_gift_rule(c, rule_id: int, payload: dict) -> None:
    r = c.execute('SELECT * FROM platform_coupon_gift_rules WHERE id=?', (int(rule_id),)).fetchone()
    if not r:
        raise PlatformMarketingError('赠送规则不存在')
    sets, vals = {}, []
    if 'thresholdAmount' in payload:
        threshold = max(0.0, float(payload.get('thresholdAmount') or 0))
        if threshold <= 0:
            raise PlatformMarketingError('赠送门槛必须大于 0 元')
        sets['threshold_amount=?'] = threshold
    if 'benefitId' in payload:
        bid = int(payload.get('benefitId') or 0)
        _assert_platform_coupon(c, bid)
        sets['benefit_id=?'] = bid
    if 'perUserLimit' in payload:
        sets['per_user_limit=?'] = max(1, int(payload.get('perUserLimit') or 1))
    if 'enabled' in payload:
        sets['enabled=?'] = 1 if payload.get('enabled') else 0
    if not sets:
        return
    vals = list(sets.values()) + [int(rule_id)]
    c.execute('UPDATE platform_coupon_gift_rules SET %s,updated_at=CURRENT_TIMESTAMP WHERE id=?'
              % ','.join(sets.keys()), vals)


def delete_gift_rule(c, rule_id: int) -> None:
    c.execute('DELETE FROM platform_coupon_gift_rules WHERE id=?', (int(rule_id),))


def process_coupon_gifts(c, *, benefits, user_id: int, cash_paid: float,
                         club_id: int | None = None) -> list[dict]:
    """成交回调：单笔实付达门槛自动送平台券。异常由调用方吞掉，绝不阻断支付。"""
    if cash_paid <= 0:
        return []
    out = []
    try:
        rules = gift_rules(c, include_disabled=False)
    except Exception:
        return []
    for g in rules:
        if cash_paid < float(g['threshold_amount'] or 0):
            continue
        b = benefits._coupon_row(c, int(g['benefit_id']))
        if not b or str(b.get('status') or 'active') != 'active':
            continue
        if b.get('stock') is not None and int(b['stock']) <= 0:
            continue
        gifted = int(c.execute(
            """SELECT COUNT(*) FROM benefit_redemptions
               WHERE benefit_id=? AND user_id=? AND points_spent=0 AND gift_note LIKE '消费满%'""",
            (int(g['benefit_id']), int(user_id))).fetchone()[0] or 0)
        if gifted >= int(g['per_user_limit'] or 1):
            continue
        try:
            issued = benefits._issue(
                c, b, int(user_id), club_id, funding='PLATFORM',
                note='消费满 ¥%g 自动赠送（总平台）' % float(g['threshold_amount']))
            issued['ruleId'] = int(g['id'])
            out.append(issued)
        except Exception as e:
            # 绝不阻断支付，但要留痕：否则「规则配了却没发券」会变成静默故障。
            _log.warning('platform coupon gift failed rule=%s benefit=%s: %s',
                         g.get('id'), g.get('benefit_id'), type(e).__name__)
            continue
    return out


# ── 平台级邀请奖励（俱乐部配置优先，平台兜底）──────────────────────────────
def invite_reward(c) -> dict:
    cfg = dict(INVITE_DEFAULTS)
    try:
        r = c.execute('SELECT value FROM platform_settings WHERE key=?', (INVITE_REWARD_KEY,)).fetchone()
    except Exception:
        return cfg
    if not r:
        return cfg
    raw = r['value'] if isinstance(r, dict) else r[0]
    try:
        saved = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if isinstance(saved, dict):
            for k in cfg:
                if k in saved:
                    cfg[k] = saved[k]
    except Exception:
        pass
    return cfg


def save_invite_reward(c, payload: dict) -> dict:
    cfg = dict(INVITE_DEFAULTS)
    cfg['enabled'] = bool(payload.get('enabled'))
    for k in ('inviteeBenefitId', 'inviterBenefitId'):
        v = int(payload.get(k) or 0) or None
        if v:
            b = c.execute('SELECT owner_type,benefit_type FROM member_benefits WHERE id=?', (v,)).fetchone()
            b = _dict(b)
            if not b or b.get('owner_type') != 'PLATFORM' or b.get('benefit_type') not in ('activity_coupon', 'gear_coupon'):
                raise PlatformMarketingError('邀请奖励只能选择总平台自己创建的券')
        cfg[k] = v
    cfg['inviterPoints'] = max(0, int(payload.get('inviterPoints') or 0))
    cfg['shareTitle'] = str(payload.get('shareTitle') or '').strip()[:120]
    cfg['shareDesc'] = str(payload.get('shareDesc') or '').strip()[:240]
    cfg['shareImageKey'] = str(payload.get('shareImageKey') or 'sunrise').strip()[:40] or 'sunrise'
    stored = dict(cfg)
    stored['_updatedAt'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO platform_settings(key,value) VALUES(?,?) '
              'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (INVITE_REWARD_KEY, json.dumps(stored, ensure_ascii=False)))
    return cfg


def invite_reward_for_club(c, club_id: int):
    """C 端邀请奖励判据：俱乐部自己启用就用俱乐部的，否则回落平台级配置。

    返回 (配置字典, 'CLUB' | 'PLATFORM') 或 (None, None)。
    配置字典统一成 invite_reward_settings 的列名，调用方无需分支取字段。
    """
    r = c.execute('SELECT * FROM invite_reward_settings WHERE club_id=? AND enabled=1',
                  (int(club_id),)).fetchone()
    if r:
        return dict(r), 'CLUB'
    # 俱乐部还在用旧版「邀请券」（clubs.referral_coupon_benefit_id）时也以俱乐部为准，
    # 平台配置不能覆盖俱乐部已经设好的任何邀请奖励，只在完全没配时兜底。
    try:
        old = c.execute('SELECT referral_coupon_benefit_id FROM clubs WHERE id=?',
                        (int(club_id),)).fetchone()
        if old is not None and int((dict(old).get('referral_coupon_benefit_id') or 0)) > 0:
            return None, None
    except Exception:
        pass
    p = invite_reward(c)
    if not p.get('enabled'):
        return None, None
    return {
        'invitee_benefit_id': p.get('inviteeBenefitId'),
        'inviter_benefit_id': p.get('inviterBenefitId'),
        'inviter_points': p.get('inviterPoints') or 0,
        'share_title': p.get('shareTitle'),
        'share_desc': p.get('shareDesc'),
        'share_image_key': p.get('shareImageKey') or 'sunrise',
    }, 'PLATFORM'


# ── 发放记录 / 概览 ───────────────────────────────────────────────────────
def campaign_grant_log(c, limit: int = 100) -> list[dict]:
    """平台活动（owner_type='PLATFORM'）成交回调自动发放的赠品留痕。"""
    limit = max(1, min(int(limit or 100), 300))
    return [dict(r) for r in c.execute(
        '''SELECT g.id,g.campaign_id,g.order_id,g.gift_type,g.copies,g.user_id,g.created_at,
                  mc.title campaign_title,mc.type campaign_type,u.name user_name
           FROM campaign_grant_log g
           JOIN marketing_campaigns mc ON mc.id=g.campaign_id
           LEFT JOIN users u ON u.id=g.user_id
           WHERE mc.owner_type='PLATFORM'
           ORDER BY g.id DESC LIMIT ?''', (limit,)).fetchall()]


def coupon_issue_log(c, limit: int = 100) -> list[dict]:
    """平台券的发放记录（含后台定向发放与消费送券自动发放）。"""
    limit = max(1, min(int(limit or 100), 300))
    return [dict(r) for r in c.execute(
        '''SELECT r.id,r.benefit_id,r.user_id,r.voucher_code,r.gift_note,r.status,r.created_at,
                  b.title benefit_title,b.benefit_type,u.name user_name
           FROM benefit_redemptions r
           JOIN member_benefits b ON b.id=r.benefit_id
           LEFT JOIN users u ON u.id=r.user_id
           WHERE b.owner_type='PLATFORM'
           ORDER BY r.id DESC LIMIT ?''', (limit,)).fetchall()]


def overview(c) -> dict:
    """页面顶部的统计条：口径与列表一致，避免「统计说有、列表为空」。"""
    def n(sql, args=()):
        try:
            return int(c.execute(sql, args).fetchone()[0] or 0)
        except Exception:
            return 0
    return {
        'coupons': n("SELECT COUNT(*) FROM member_benefits WHERE owner_type='PLATFORM' AND benefit_type IN ('activity_coupon','gear_coupon')"),
        'couponsActive': n("SELECT COUNT(*) FROM member_benefits WHERE owner_type='PLATFORM' AND status='active' AND benefit_type IN ('activity_coupon','gear_coupon')"),
        'giftRules': n('SELECT COUNT(*) FROM platform_coupon_gift_rules'),
        'giftRulesEnabled': n('SELECT COUNT(*) FROM platform_coupon_gift_rules WHERE enabled=1'),
        'campaigns': n("SELECT COUNT(*) FROM marketing_campaigns WHERE owner_type='PLATFORM' AND status!='ENDED'"),
        'campaignsActive': n("SELECT COUNT(*) FROM marketing_campaigns WHERE owner_type='PLATFORM' AND status='ACTIVE'"),
        'campaignGrants': n("SELECT COUNT(*) FROM campaign_grant_log g JOIN marketing_campaigns mc ON mc.id=g.campaign_id WHERE mc.owner_type='PLATFORM'"),
        'couponIssues': n("SELECT COUNT(*) FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id WHERE b.owner_type='PLATFORM'"),
        'inviteEnabled': bool(invite_reward(c).get('enabled')),
    }

from __future__ import annotations
"""统一营销活动引擎（P0-1，2026-11-13）。

把分散的营销玩法收敛为一张 marketing_campaigns 通用表 + 类型专属 rules_json，
新增玩法只填配置、不新建表、不重写报价链。当前已接入引擎规则的类型：
  - NEWCOMER   首单立减（用户在本俱乐部无已支付订单时生效，直接减 payable）
  - TIME_LIMIT 限时折扣（在 schedule 窗口内、命中 scope 时生效，直接减 payable）
  - BUY_GIFT   满额赠送（满 threshold 元送 points / gift_card / coupon，不改 payable；
               引擎返回 gifts 列表，实际发放待接入成交回调）
其余类型（GROUP / CHECKIN / LOTTERY / STAMP / GIFT_CARD / INVITE）由各自独立模块实现，
或以 type 实例接入复用同一 evaluate() 骨架。

设计纪律（对齐既有报价链「宁可不打折不能付不了款」）：
  - 只读、永不对外抛错：任何异常都退回「无折扣」，绝不阻断下单。
  - 折扣只作用于传入的 payable（已含积分/会员/券折后金额），叠加在既有三层之后。
  - club 隔离：CLUB 活动只对本俱乐部生效；PLATFORM 活动对所有俱乐部生效。
  - 本模块仅依赖标准库，可被单元测试直接 importlib 加载，不触发 clubos_domain 包重导入。
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

CAMPAIGN_TYPES = (
    'NEWCOMER', 'TIME_LIMIT', 'GROUP', 'BUY_GIFT', 'CHECKIN',
    'LOTTERY', 'STAMP', 'GIFT_CARD', 'INVITE',
)
STATUSES = ('DRAFT', 'ACTIVE', 'PAUSED', 'ENDED')


# ──────────────────────────── 工具 ────────────────────────────
def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _jload(v, default=None):
    try:
        return json.loads(v) if v is not None else default
    except Exception:
        return default


def _to_ints(vals):
    out = set()
    for x in (vals or []):
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            pass
    return out


# ──────────────────────────── 校验 ────────────────────────────
def validate_campaign_payload(p: dict) -> dict:
    """校验并归一化创建/更新载荷；非法抛 ValueError。"""
    if not isinstance(p, dict):
        raise ValueError('payload 必须为对象')
    t = str(p.get('type') or '').strip().upper()
    if t not in CAMPAIGN_TYPES:
        raise ValueError('不支持的活动类型: %s' % t)
    title = str(p.get('title') or '').strip()
    if not title:
        raise ValueError('活动标题不能为空')
    status = str(p.get('status') or 'DRAFT').strip().upper()
    if status not in STATUSES:
        raise ValueError('非法状态: %s' % status)
    schedule = p.get('schedule') or {}
    scope = p.get('scope') or {}
    rules = p.get('rules') or {}
    if not isinstance(schedule, dict) or not isinstance(scope, dict) or not isinstance(rules, dict):
        raise ValueError('schedule/scope/rules 必须为对象')
    if t in ('NEWCOMER', 'TIME_LIMIT'):
        _validate_rules_discount(rules)
    elif t == 'BUY_GIFT':
        _validate_rules_buy_gift(rules)
    return {
        'type': t, 'title': title, 'status': status,
        'schedule': schedule, 'scope': scope, 'rules': rules,
    }


def _validate_rules_discount(rules: dict):
    mode = str(rules.get('mode') or 'fixed').strip().lower()
    if mode not in ('fixed', 'percent', 'threshold'):
        raise ValueError('rules.mode 必须为 fixed/percent/threshold')
    if mode in ('fixed', 'threshold'):
        amt = rules.get('amount')
        if amt is None or float(amt) < 0:
            raise ValueError('fixed/threshold 模式需要非负 amount')
    if mode == 'percent':
        pct = rules.get('percent')
        if pct is None or not (0 < float(pct) <= 100):
            raise ValueError('percent 需在 (0,100]')
    if 'maxDiscount' in rules and float(rules['maxDiscount']) < 0:
        raise ValueError('maxDiscount 不能为负')


def _validate_rules_buy_gift(rules: dict):
    """BUY_GIFT 买赠规则：满额(threshold 元)送券/积分/礼品卡额度，不影响 payable。"""
    th = rules.get('threshold')
    if th is None or float(th) <= 0:
        raise ValueError('BUY_GIFT 需要正数 threshold（满额门槛，元）')
    gtype = str(rules.get('giftType') or 'points').strip().lower()
    if gtype not in ('points', 'gift_card', 'coupon'):
        raise ValueError('giftType 必须为 points / gift_card / coupon')
    gval = rules.get('giftValue')
    if gval is None or float(gval) <= 0:
        raise ValueError('BUY_GIFT 需要正数 giftValue（赠送值）')
    try:
        if int(rules.get('maxGifts', 1)) < 1:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError('maxGifts 必须为 >=1 的整数')


# ──────────────────────────── 引擎 ────────────────────────────
class CampaignEngine:
    """统一营销活动引擎。所有方法接收 sqlite3 连接 cursor，只读或显式写。"""

    # ---- 报价期求值（只读）----
    def evaluate(self, c, *, club_id: int, user_id: int | None, payable: float,
                 activity_ids: list | None = None, product_ids: list | None = None,
                 store_ids: list | None = None, category_ids: list | None = None,
                 order_time: int | None = None) -> dict:
        """对一笔应付金额求所有生效活动的总折扣与赠送。返回 {discount, applied, gifts}。

        - discount / applied：只会含「直接减价」类玩法（NEWCOMER / TIME_LIMIT）。
        - gifts：含「满额赠送」类玩法（BUY_GIFT），不影响 payable，作为赠品发放依据。
        """
        out: dict[str, Any] = {'discount': 0.0, 'applied': [], 'gifts': []}
        try:
            if payable is None or float(payable) <= 0:
                return out
            club_id = int(club_id)
            uid = int(user_id) if user_id else None
            base = float(payable)
            now = int(order_time) if order_time else _now_ts()
            rows = c.execute(
                """SELECT * FROM marketing_campaigns
                   WHERE status='ACTIVE'
                     AND (owner_type='PLATFORM' OR (owner_type='CLUB' AND owner_club_id=?))""",
                (club_id,)).fetchall()
            total = 0.0
            applied = []
            gifts = []
            for r in rows:
                d = self._eval_one(
                    c, row=dict(r), club_id=club_id, user_id=uid, payable=base, now=now,
                    activity_ids=_to_ints(activity_ids), product_ids=_to_ints(product_ids),
                    store_ids=_to_ints(store_ids), category_ids=_to_ints(category_ids))
                if not d:
                    continue
                if d['amount'] > 0:
                    applied.append(d)
                    total += d['amount']
                if d.get('gift'):
                    gifts.append(d)
            out['discount'] = round(min(total, base), 2)
            out['applied'] = applied
            out['gifts'] = gifts
        except Exception:
            logger.exception('campaign evaluate failed (club=%s user=%s)', club_id, user_id)
        return out

    def _eval_one(self, c, *, row, club_id, user_id, payable, now,
                  activity_ids, product_ids, store_ids, category_ids):
        typ = row.get('type')
        scope = _jload(row.get('scope_json'), {}) or {}
        schedule = _jload(row.get('schedule_json'), {}) or {}
        rules = _jload(row.get('rules_json'), {}) or {}
        if not self._scope_match(scope, activity_ids, product_ids, store_ids, category_ids):
            return None
        if not self._schedule_ok(schedule, now):
            return None
        if typ == 'NEWCOMER' and not (user_id and self._is_first_order(c, club_id, user_id)):
            return None
        if typ == 'BUY_GIFT':
            gift = self._compute_buy_gift(rules, payable)
            if not gift:
                return None
            return {'id': row.get('id'), 'type': typ, 'title': row.get('title'),
                    'amount': 0.0, 'gift': gift}
        amt = self._compute_discount(rules, payable)
        if amt <= 0:
            return None
        return {'id': row.get('id'), 'type': typ, 'title': row.get('title'), 'amount': round(amt, 2)}

    @staticmethod
    def _scope_match(scope, activity_ids, product_ids, store_ids, category_ids):
        for key, vals in (('activityIds', activity_ids), ('productIds', product_ids),
                          ('storeIds', store_ids), ('categoryIds', category_ids)):
            want = scope.get(key)
            if want:
                if not (_to_ints(want) & set(vals)):
                    return False
        return True

    @staticmethod
    def _schedule_ok(schedule, now):
        if not schedule:
            return True
        # startAt: 活动在 startAt 之后生效 → now >= startAt 才通过
        # endAt:   活动在 endAt 之前生效 → now <= endAt 才通过
        for fld, ok in (('startAt', lambda n: now >= n), ('endAt', lambda n: now <= n)):
            v = schedule.get(fld)
            if v:
                try:
                    if not ok(int(v)):
                        return False
                except (TypeError, ValueError):
                    pass
        wd = schedule.get('weekdays')
        if wd:
            try:
                today = datetime.fromtimestamp(now, tz=timezone.utc).weekday()
                if int(today) not in [int(x) for x in wd]:
                    return False
            except Exception:
                pass
        tf, tt = schedule.get('timeFrom'), schedule.get('timeTo')
        if tf or tt:
            try:
                cur = datetime.fromtimestamp(now, tz=timezone.utc)
                cur_min = cur.hour * 60 + cur.minute
                if tf and cur_min < int(tf):
                    return False
                if tt and cur_min > int(tt):
                    return False
            except Exception:
                pass
        return True

    @staticmethod
    def _compute_discount(rules, payable):
        mode = str(rules.get('mode') or 'fixed').lower()
        cap = float(rules.get('maxDiscount') or 0)
        amt = 0.0
        if mode == 'fixed':
            amt = float(rules.get('amount') or 0)
        elif mode == 'percent':
            amt = round(payable * float(rules.get('percent') or 0) / 100.0, 2)
        elif mode == 'threshold':
            th = float(rules.get('threshold') or 0)
            if payable >= th:
                amt = float(rules.get('amount') or 0)
        if cap > 0:
            amt = min(amt, cap)
        return round(max(0.0, min(amt, payable)), 2)

    @staticmethod
    def _compute_buy_gift(rules, payable) -> dict | None:
        """BUY_GIFT 满额赠送：payable >= threshold 时按 floor(payable/threshold) 计算份数，封顶 maxGifts。

        返回赠送明细；不返回任何减价金额（赠送物在成交后发放，不影响 payable）。
        赠送类型：points（积分）/ gift_card（礼品卡额度，元）/ coupon（券面额，元）。
        """
        try:
            th = float(rules.get('threshold') or 0)
            if th <= 0 or float(payable) < th:
                return None
            gtype = str(rules.get('giftType') or 'points').strip().lower()
            if gtype not in ('points', 'gift_card', 'coupon'):
                return None
            gval = float(rules.get('giftValue') or 0)
            if gval <= 0:
                return None
            try:
                max_g = int(rules.get('maxGifts', 1))
            except (TypeError, ValueError):
                max_g = 1
            if max_g < 1:
                max_g = 1
            copies = min(max_g, int(float(payable) // th))
            if copies < 1:
                return None
            return {
                'giftType': gtype,
                'giftValue': round(gval, 2),
                'copies': copies,
                'totalValue': round(gval * copies, 2),
                'threshold': round(th, 2),
            }
        except Exception:
            return None

    @staticmethod
    def _is_first_order(c, club_id, user_id):
        club_id, user_id = int(club_id), int(user_id)
        # 营销适用范围硬边界：首单资格只看俱乐部活动 / 营地成交记录。
        # 绝不查 gear_orders / member_orders —— 装备销售不能消耗或影响活动营销资格。
        # 与 commerce_scope.FIRST_ORDER_PROBES 同源（本模块单文件加载，不跨模块 import）。
        probes = [
            "SELECT 1 FROM registrations WHERE club_id=? AND user_id=? AND status IN ('paid','completed','confirmed') LIMIT 1",
            "SELECT 1 FROM camp_orders WHERE club_id=? AND user_id=? AND status IN ('paid','completed') LIMIT 1",
        ]
        for sql in probes:
            try:
                if c.execute(sql, (club_id, user_id)).fetchone():
                    return False
            except Exception:
                continue
        return True

    # ---- CRUD（供 app.py 路由调用）----
    def list_campaigns(self, c, *, owner_club_id=None, owner_type='CLUB'):
        if owner_type == 'PLATFORM':
            rows = c.execute(
                "SELECT * FROM marketing_campaigns WHERE owner_type='PLATFORM' ORDER BY id DESC").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM marketing_campaigns WHERE owner_type='CLUB' AND owner_club_id=? ORDER BY id DESC",
                (int(owner_club_id),)).fetchall()
        return [self._serialize(r) for r in rows]

    def create_campaign(self, c, *, owner_type='CLUB', owner_club_id=None, payload: dict, created_by=None):
        p = validate_campaign_payload(payload)
        c.execute(
            """INSERT INTO marketing_campaigns
               (owner_type, owner_club_id, type, title, status, schedule_json, scope_json, rules_json, created_by, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?, datetime('now'), datetime('now'))""",
            (owner_type, int(owner_club_id) if owner_club_id else None, p['type'], p['title'], p['status'],
             json.dumps(p['schedule'], ensure_ascii=False), json.dumps(p['scope'], ensure_ascii=False),
             json.dumps(p['rules'], ensure_ascii=False), int(created_by) if created_by else None))
        return self.get_campaign(c, int(c.execute('SELECT last_insert_rowid()').fetchone()[0]))

    def update_campaign(self, c, *, campaign_id, payload: dict, owner_club_id=None):
        r = c.execute("SELECT * FROM marketing_campaigns WHERE id=?", (int(campaign_id),)).fetchone()
        if not r:
            raise LookupError('活动不存在')
        if owner_club_id is not None and int(r['owner_club_id']) != int(owner_club_id):
            raise PermissionError('无权修改该活动')
        merged = self._serialize(r)
        merged.update({k: v for k, v in payload.items() if k in ('title', 'status', 'schedule', 'scope', 'rules')})
        if 'type' in payload and payload['type'] != r['type']:
            merged['type'] = payload['type']
        p = validate_campaign_payload(merged)
        sets, vals = [], []
        mapping = (('title', 'title', False), ('status', 'status', False),
                   ('schedule_json', 'schedule', True), ('scope_json', 'scope', True), ('rules_json', 'rules', True))
        for col, key, js in mapping:
            if key in payload:
                sets.append(f"{col}=?")
                vals.append(json.dumps(payload[key], ensure_ascii=False) if js else payload[key])
        if sets:
            sets.append("updated_at=datetime('now')")
            vals.append(int(campaign_id))
            c.execute("UPDATE marketing_campaigns SET %s WHERE id=?" % ','.join(sets), vals)
        return self.get_campaign(c, int(campaign_id))

    def delete_campaign(self, c, *, campaign_id, owner_club_id=None):
        r = c.execute("SELECT * FROM marketing_campaigns WHERE id=?", (int(campaign_id),)).fetchone()
        if not r:
            raise LookupError('活动不存在')
        if owner_club_id is not None and r['owner_club_id'] is not None and int(r['owner_club_id']) != int(owner_club_id):
            raise PermissionError('无权删除该活动')
        c.execute("UPDATE marketing_campaigns SET status='ENDED', updated_at=datetime('now') WHERE id=?",
                  (int(campaign_id),))
        return {'ok': True}

    def get_campaign(self, c, campaign_id):
        r = c.execute("SELECT * FROM marketing_campaigns WHERE id=?", (int(campaign_id),)).fetchone()
        if not r:
            raise LookupError('活动不存在')
        return self._serialize(r)

    @staticmethod
    def _serialize(r):
        r = dict(r)
        r['schedule'] = _jload(r.get('schedule_json'), {})
        r['scope'] = _jload(r.get('scope_json'), {})
        r['rules'] = _jload(r.get('rules_json'), {})
        r.pop('schedule_json', None)
        r.pop('scope_json', None)
        r.pop('rules_json', None)
        return r

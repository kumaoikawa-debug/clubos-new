from __future__ import annotations
"""统一营销活动引擎（P0-1，2026-11-13）。

把分散的营销玩法收敛为一张 marketing_campaigns 通用表 + 类型专属 rules_json，
新增玩法只填配置、不新建表、不重写报价链。当前已接入引擎规则的类型：
  - NEWCOMER   首单立减（用户在本俱乐部无已支付订单时生效，直接减 payable）
  - TIME_LIMIT 限时折扣（在 schedule 窗口内、命中 scope 时生效，直接减 payable）
  - BUY_GIFT   满额赠送（满 threshold 元送 points / gift_card / coupon，不改 payable；
               引擎返回 gifts 列表，实际发放由成交回调触发，见 app.py _grant_campaign_gifts）
  - INVITE     邀请有礼（被邀请且为本俱乐部首单的买家，满 threshold 元送 points / gift_card /
               coupon；复用 BUY_GIFT 规则与 _is_invited 校验，发放同源）
其余类型（GROUP / CHECKIN / LOTTERY / STAMP / GIFT_CARD）由各自独立模块实现，
或以 type 实例接入复用同一 evaluate() 骨架。

设计纪律（对齐既有报价链「宁可不打折不能付不了款」）：
  - 只读、永不对外抛错：任何异常都退回「无折扣」，绝不阻断下单。
  - 折扣只作用于传入的 payable（已含积分/会员/券折后金额），叠加在既有三层之后。
  - club 隔离：CLUB 活动只对本俱乐部生效；PLATFORM 活动对所有俱乐部生效。
  - 本模块仅依赖标准库，可被单元测试直接 importlib 加载，不触发 clubos_domain 包重导入。
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# P1-3（2026-11-14）：俱乐部按本地日历配置活动时段。旧代码用 timezone.utc
# 判 weekdays/timeFrom/timeTo，与北京时间差 8 小时 —— 配「每天 20:00–22:00」
# 会在次日凌晨才生效。统一用 UTC+8 固定偏移（不依赖 tzdata，标准库即可）。
CLUB_LOCAL_TZ = timezone(timedelta(hours=8))


def _schedule_boundary(v, *, is_end=False):
    """把 schedule 的起止值归一成 Unix 秒。

    兼容三种写法：Unix 秒（int/数字字符串）、'YYYY-MM-DD HH:mm'、'YYYY-MM-DD'。
    只有日期时 startAt 取 00:00:00、endAt 取 23:59:59（当天仍算在窗口内）。
    所有本地时间都按 CLUB_LOCAL_TZ 解析，不再受宿主机器时区影响。
    """
    if v in (None, '', 0, '0'):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    s = s.replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            d = datetime.strptime(s, fmt)
            if fmt == '%Y-%m-%d':
                d = d.replace(hour=23, minute=59, second=59) if is_end else d.replace(hour=0, minute=0, second=0)
            return int(d.replace(tzinfo=CLUB_LOCAL_TZ).timestamp())
        except ValueError:
            continue
    return None

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


def _clean_ids(vals):
    """快照里只存可序列化的整数列表（去掉空值 / 0 / 非数字）。"""
    out = []
    for x in (vals or []):
        try:
            i = int(x)
            if i > 0:
                out.append(i)
        except (TypeError, ValueError):
            pass
    return sorted(set(out))


# ── 报价 / 成交发放共用的营销上下文快照（2026-11-14 · V3 / P0-3）────────────
def build_campaign_context(*, club_id, user_id, payable, kind='activity',
                           activity_ids=None, product_ids=None, store_ids=None,
                           category_ids=None, camp_ids=None, pitch_ids=None,
                           discount=0.0, applied=None, gifts=None,
                           evaluated_at=None) -> dict:
    """把「本单适用哪些活动、按哪个金额算份数」冻结成一份可 JSON 化的快照。

    落点：checkout_intents.payload_json.campaignContext。报价期（price-quote）与
    成交发放期（_grant_campaign_gifts_for_intent）都读同一份 context / payable，
    杜绝「报价显示赠品、成交不发放」以及 payable 口径漂移导致的份数不一致。
    """
    return {
        'version': 2,
        'clubId': int(club_id),
        'userId': int(user_id) if user_id else None,
        'kind': str(kind or 'activity'),
        'payable': round(float(payable or 0), 2),
        'activityIds': _clean_ids(activity_ids),
        'productIds': _clean_ids(product_ids),
        'storeIds': _clean_ids(store_ids),
        'categoryIds': _clean_ids(category_ids),
        'campIds': _clean_ids(camp_ids),
        'pitchIds': _clean_ids(pitch_ids),
        'discount': round(float(discount or 0), 2),
        'applied': list(applied or []),
        'gifts': list(gifts or []),
        # 显式传 0 表示「这不是一份下单快照，成交侧必须重新求值」（单测/老单兜底）。
        'evaluatedAt': int(evaluated_at) if evaluated_at is not None else _now_ts(),
    }


def context_from_intent(intent: dict | None) -> dict | None:
    """从 checkout_intents 行还原快照；老单（无快照）按订单字段兜底重建。

    兜底路径只是为了让本次上线前已存在的 pending 结算单仍能成交，绝不重新
    猜测活动资格：gifts 为空时成交侧会重新 evaluate（并排除本笔订单）。
    """
    if not intent:
        return None
    meta = _jload(intent.get('payload_json'), {}) or {}
    snap = meta.get('campaignContext')
    if isinstance(snap, dict) and snap.get('version') == 2:
        ctx = dict(snap)
    else:
        ctx = {
            'version': 2,
            'clubId': intent.get('club_id'),
            'userId': intent.get('user_id'),
            'kind': intent.get('kind') or 'activity',
            'payable': round(float(intent.get('cash_amount') or 0), 2),
            'activityIds': [], 'productIds': [], 'storeIds': [],
            'categoryIds': [], 'campIds': [], 'pitchIds': [],
            'discount': 0.0, 'applied': [], 'gifts': [], 'evaluatedAt': 0,
            'legacy': True,
        }
        aid = meta.get('activityId') or meta.get('activity_id')
        if aid:
            ctx['activityIds'] = _clean_ids([aid])
        if meta.get('campId'):
            ctx['campIds'] = _clean_ids([meta.get('campId')])
        if meta.get('pitchId'):
            ctx['pitchIds'] = _clean_ids([meta.get('pitchId')])
    ctx['clubId'] = int(ctx.get('clubId') or intent.get('club_id') or 0)
    ctx['userId'] = int(ctx.get('userId') or intent.get('user_id') or 0)
    return ctx


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
    elif t in ('BUY_GIFT', 'INVITE'):
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
                 camp_ids: list | None = None, pitch_ids: list | None = None,
                 order_time: int | None = None, exclude_order: dict | None = None) -> dict:
        """对一笔应付金额求所有生效活动的总折扣与赠送。返回 {discount, applied, gifts}。

        - discount / applied：只会含「直接减价」类玩法（NEWCOMER / TIME_LIMIT）。
        - gifts：含「满额赠送」类玩法（BUY_GIFT / INVITE），不影响 payable，作为赠品发放依据。
        - exclude_order：成交回调专用，形如 {'kind':'activity','id': 123}；首单资格
          判定时排除「本笔订单自己」（支付成功先落 paid 订单、后发赠品）。
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
                    store_ids=_to_ints(store_ids), category_ids=_to_ints(category_ids),
                    camp_ids=_to_ints(camp_ids), pitch_ids=_to_ints(pitch_ids),
                    exclude_order=exclude_order)
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

    def evaluate_context(self, c, ctx: dict, *, exclude_order: dict | None = None) -> dict:
        """按 build_campaign_context 快照求值（报价 / 成交发放共用同一入口）。"""
        return self.evaluate(
            c, club_id=ctx.get('clubId'), user_id=ctx.get('userId'),
            payable=ctx.get('payable'),
            activity_ids=ctx.get('activityIds'), product_ids=ctx.get('productIds'),
            store_ids=ctx.get('storeIds'), category_ids=ctx.get('categoryIds'),
            camp_ids=ctx.get('campIds'), pitch_ids=ctx.get('pitchIds'),
            exclude_order=exclude_order)

    def _eval_one(self, c, *, row, club_id, user_id, payable, now,
                  activity_ids, product_ids, store_ids, category_ids,
                  camp_ids=None, pitch_ids=None, exclude_order=None):
        typ = row.get('type')
        scope = _jload(row.get('scope_json'), {}) or {}
        schedule = _jload(row.get('schedule_json'), {}) or {}
        rules = _jload(row.get('rules_json'), {}) or {}
        if not self._scope_match(scope, activity_ids, product_ids, store_ids,
                                 category_ids, camp_ids, pitch_ids):
            return None
        if not self._schedule_ok(schedule, now):
            return None
        if typ == 'NEWCOMER' and not (user_id and self._is_first_order(c, club_id, user_id, exclude_order)):
            return None
        if typ in ('BUY_GIFT', 'INVITE'):
            # INVITE：仅被邀请且为本俱乐部首单的买家可领（邀请有礼，与 JOIN 时的邀请奖励解耦）；
            # BUY_GIFT：不限身份，满额即送。
            if typ == 'INVITE' and not (
                user_id and self._is_invited(c, club_id, user_id)
                and self._is_first_order(c, club_id, user_id, exclude_order)
            ):
                return None
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
    def _scope_match(scope, activity_ids, product_ids, store_ids, category_ids,
                     camp_ids=None, pitch_ids=None):
        # 与 camp_pricing._scope_context 产出的 ID 集合一一对应：报价与成交发放
        # 必须能用同一组对象 ID 判定命中，不能「报价命中、发放落空」。
        for key, vals in (('activityIds', activity_ids), ('productIds', product_ids),
                          ('storeIds', store_ids), ('categoryIds', category_ids),
                          ('campIds', camp_ids), ('pitchIds', pitch_ids)):
            want = scope.get(key)
            if want:
                if not (_to_ints(want) & set(vals or ())):
                    return False
        return True

    @staticmethod
    def _schedule_ok(schedule, now):
        # 空 schedule 的语义是「长期生效」（后台表单会明确提示，不再叫限时却不限时）。
        if not schedule:
            return True
        # startAt: 活动在 startAt 之后生效 → now >= startAt 才通过
        # endAt:   活动在 endAt 之前生效 → now <= endAt 才通过
        start = _schedule_boundary(schedule.get('startAt'))
        end = _schedule_boundary(schedule.get('endAt'), is_end=True)
        if start is not None and now < start:
            return False
        if end is not None and now > end:
            return False
        local = datetime.fromtimestamp(now, tz=CLUB_LOCAL_TZ)
        wd = schedule.get('weekdays')
        if wd:
            try:
                if int(local.weekday()) not in [int(x) for x in wd]:
                    return False
            except Exception:
                pass
        tf, tt = schedule.get('timeFrom'), schedule.get('timeTo')
        if tf or tt:
            try:
                cur_min = local.hour * 60 + local.minute
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
    def _is_first_order(c, club_id, user_id, exclude_order=None):
        """本俱乐部是否尚无历史成交订单。

        P0-1（2026-11-14）：支付成功是「先写 paid 订单、后发赠品」，若不排除本笔订单，
        INVITE / NEWCOMER 的资格永远被本单自己否决。exclude_order 由成交回调传入，
        形如 {'kind':'activity'|'camp_booking'|'camp_order','id': 本笔订单主键}。
        """
        club_id, user_id = int(club_id), int(user_id)
        # 营销适用范围硬边界：首单资格只看俱乐部活动 / 营地成交记录。
        # 绝不查 gear_orders / member_orders —— 装备销售不能消耗或影响活动营销资格。
        # 与 commerce_scope.FIRST_ORDER_PROBES 同源（本模块单文件加载，不跨模块 import）。
        probes = [
            ('activity', "SELECT 1 FROM registrations WHERE club_id=? AND user_id=? AND status IN ('paid','completed','confirmed')"),
            ('camp_order', "SELECT 1 FROM camp_orders WHERE club_id=? AND user_id=? AND status IN ('paid','completed')"),
        ]
        ex = exclude_order if isinstance(exclude_order, dict) else {}
        ex_kind = str(ex.get('kind') or '')
        if ex_kind == 'camp_booking':
            ex_kind = 'camp_order'
        try:
            ex_id = int(ex.get('id') or 0)
        except (TypeError, ValueError):
            ex_id = 0
        for kind, sql in probes:
            args = [club_id, user_id]
            if ex_id and ex_kind == kind:
                sql += ' AND id<>?'
                args.append(ex_id)
            sql += ' LIMIT 1'
            try:
                if c.execute(sql, args).fetchone():
                    return False
            except Exception:
                continue
        return True

    @staticmethod
    def _is_invited(c, club_id, user_id):
        """该用户是否被邀请加入本俱乐部（邀请有礼资格判定）。

        与 commerce_scope 同源：只看俱乐部活动 / 营地维度的邀请关系，不查装备域。
        - club_members.referred_by 指向邀请人（入会时写入）；或
        - invite_records 中存在该用户作为被邀请人的记录。
        任一命中即视为「被邀请」。
        """
        club_id, user_id = int(club_id), int(user_id)
        try:
            m = c.execute(
                "SELECT referred_by FROM club_members WHERE club_id=? AND user_id=?",
                (club_id, user_id)).fetchone()
            if m and m['referred_by']:
                return True
            if c.execute(
                "SELECT 1 FROM invite_records WHERE club_id=? AND invitee_user_id=? LIMIT 1",
                (club_id, user_id)).fetchone():
                return True
        except Exception:
            return False
        return False

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

    @staticmethod
    def _assert_club_owner(row, owner_club_id, action='修改'):
        """P1-1：PLATFORM 活动 owner_club_id 为 NULL，俱乐部路由一律拒绝。

        旧代码 int(None) 抛 TypeError → 500；DELETE 又因 NULL 跳过权限判断 →
        俱乐部能停用平台活动。两种越权在这里统一收敛成 403。
        """
        if owner_club_id is None:
            return
        if str(row['owner_type'] or '').upper() == 'PLATFORM':
            raise PermissionError('平台活动只能由总平台%s' % action)
        if row['owner_club_id'] is None or int(row['owner_club_id']) != int(owner_club_id):
            raise PermissionError('无权%s该活动' % action)

    def update_campaign(self, c, *, campaign_id, payload: dict, owner_club_id=None):
        r = c.execute("SELECT * FROM marketing_campaigns WHERE id=?", (int(campaign_id),)).fetchone()
        if not r:
            raise LookupError('活动不存在')
        self._assert_club_owner(r, owner_club_id, '修改')
        merged = self._serialize(r)
        merged.update({k: v for k, v in payload.items() if k in ('title', 'status', 'schedule', 'scope', 'rules')})
        if 'type' in payload:
            merged['type'] = payload['type']
        # 改类型会连带校验新类型的规则；不合法直接 400，绝不「200 但没改」。
        p = validate_campaign_payload(merged)
        sets, vals = [], []
        mapping = (('title', 'title', False), ('status', 'status', False),
                   ('schedule_json', 'schedule', True), ('scope_json', 'scope', True), ('rules_json', 'rules', True))
        for col, key, js in mapping:
            if key in payload:
                sets.append(f"{col}=?")
                vals.append(json.dumps(payload[key], ensure_ascii=False) if js else payload[key])
        if 'type' in payload:
            sets.append('type=?')
            vals.append(p['type'])
        if sets:
            sets.append("updated_at=datetime('now')")
            vals.append(int(campaign_id))
            c.execute("UPDATE marketing_campaigns SET %s WHERE id=?" % ','.join(sets), vals)
        return self.get_campaign(c, int(campaign_id))

    def delete_campaign(self, c, *, campaign_id, owner_club_id=None):
        r = c.execute("SELECT * FROM marketing_campaigns WHERE id=?", (int(campaign_id),)).fetchone()
        if not r:
            raise LookupError('活动不存在')
        self._assert_club_owner(r, owner_club_id, '删除')
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

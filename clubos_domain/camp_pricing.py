from __future__ import annotations
"""营地「会员权益定价层」（2026-11-11）。

用户要求：营地里的产品 —— 房间/营位、租用装备、小站商品 —— 必须**沿用原活动会员体系**，
即同一套会员等级折扣、同一套积分抵用比例、同一套福利券、同一套消费送积分。

设计原则（与既有架构的关系）：
  * 本模块**不新建**任何会员/积分/券规则，只做「编排」：把营地商品金额送进
    BookingEngine._quote_with_policy()（活动报名同一条报价链）、
    CheckoutEngine._member_activity_discount()（活动会员等级折扣同一函数）、
    BenefitEngine.quote_vouchers(kind='camp')（营地专属适用范围，applies_to∈{camp,all} 的活动券）、
    ClubOSPointsEngine（同一积分账本）。这样活动侧口径一变，营地自动跟随，
    绝不会出现「活动 9 折、营地原价」的两套会员。
  * 不触碰 CheckoutEngine.confirm() / BookingEngine.quote() 等既有支付核心，
    营地订单走独立的 camp_orders 状态机，但结算单仍复用 checkout_intents，
    积分 hold/consume/release 完全复用 points_engine 的账本原语，保证五不变量。
  * 押金不参与任何折扣与积分抵扣，只在最后加到应付上（离营退还，不能打折）。

金额链（与 /api/public/activities/{id}/price-quote 完全同序）：
    goodsAmount                         商品原价合计（服务端从 DB 重算）
      → 积分抵用  club/gearPointDiscount
      → 会员等级立减 memberDiscount（基于积分折后金额，与活动一致）
      → 福利券    voucherDiscount
    = payable
    + depositAmount（押金）
    = total
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 营地会员权益开关（存 clubs.camp_benefits_json）。缺省全开 —— 老俱乐部升级后
# 立刻享受「营地产品也用会员体系」，不需要后台额外配置。
CAMP_BENEFITS_DEFAULT: dict[str, Any] = {
    'memberDiscountEnabled': True,
    'pointsEnabled': True,
    'voucherEnabled': True,
    'earnPointsEnabled': True,
    'clubPointsMaxDiscountPercent': 100.0,
}
CAMP_BENEFITS_WRITABLE = (
    'memberDiscountEnabled', 'pointsEnabled', 'voucherEnabled',
    'earnPointsEnabled', 'clubPointsMaxDiscountPercent',
)


def read_config(c, club_id: int) -> dict[str, Any]:
    """读俱乐部营地会员权益开关；任何脏数据都退回缺省，绝不让配置异常卡住下单。"""
    cfg = dict(CAMP_BENEFITS_DEFAULT)
    try:
        r = c.execute('SELECT camp_benefits_json FROM clubs WHERE id=?', (int(club_id),)).fetchone()
        raw = (r[0] if r else None)
        if not raw:
            return cfg
        import json
        saved = json.loads(raw)
        if not isinstance(saved, dict):
            return cfg
        for k in ('memberDiscountEnabled', 'pointsEnabled', 'voucherEnabled', 'earnPointsEnabled'):
            if k in saved:
                cfg[k] = bool(saved[k])
        if 'clubPointsMaxDiscountPercent' in saved:
            try:
                cfg['clubPointsMaxDiscountPercent'] = max(0.0, min(100.0, float(saved['clubPointsMaxDiscountPercent'])))
            except (TypeError, ValueError):
                pass
    except Exception:
        logger.exception('camp benefits config read failed (club=%s)', club_id)
    return cfg


def member_discount_percent(c, club_id: int, user_id: int | None) -> float:
    """当前会员在营地商品上的「立减百分数」（10 = 减 10%）；无等级返回 0。

    刻意复用活动侧同一张 club_member_tiers.activity_discount_percent（90=9折），
    这样后台「会员等级」页改一次折扣，活动与营地同时生效。
    """
    if user_id is None:
        return 0.0
    try:
        m = c.execute('SELECT current_tier_id FROM club_members WHERE club_id=? AND user_id=?',
                      (int(club_id), int(user_id))).fetchone()
        if not m or not m[0]:
            return 0.0
        t = c.execute('SELECT activity_discount_percent FROM club_member_tiers WHERE id=? AND club_id=?',
                      (int(m[0]), int(club_id))).fetchone()
        if not t:
            return 0.0
        rate = float(t[0] or 0)
        return round(100.0 - rate, 2) if 1.0 <= rate < 100.0 else 0.0
    except Exception:
        logger.exception('camp member discount percent lookup failed (club=%s user=%s)', club_id, user_id)
        return 0.0


class CampPricingEngine:
    """把营地商品金额编排进既有会员/积分/券引擎。所有依赖由 app.py 注入，
    域模块不反向 import app，避免循环依赖。"""

    def __init__(self, *, booking_engine, checkout_engine, benefit_engine,
                 points_engine, wallet_snapshot, setting, gear_points_policy=None):
        self.booking = booking_engine
        self.checkout = checkout_engine
        self.benefits = benefit_engine
        self.points = points_engine
        self.wallet_snapshot = wallet_snapshot
        self.setting = setting
        self.gear_points_policy = gear_points_policy

    def _synthetic_activity(self, club_id: int, cfg: dict) -> dict:
        """给活动报价链一个「虚拟活动」：把营地开关映射成活动积分策略的同名字段。
        这样 from_activity() 解析出的规则与真实活动完全同理，而不是另写一套。"""
        return {
            'club_id': int(club_id),
            'member_discount_enabled': 1 if cfg['memberDiscountEnabled'] else 0,
            'points_enabled': 1 if cfg['pointsEnabled'] else 0,
            'earn_club_points': 1 if cfg['earnPointsEnabled'] else 0,
            'accept_club_points': 1,
            'club_points_max_discount_percent': cfg['clubPointsMaxDiscountPercent'],
            'accept_gear_points': 1,
            'gear_points_max_discount_amount': None,
            'club_points_earn_rate_override': None,
        }

    def overview(self, c, *, club_id: int, user_id: int | None) -> dict:
        """GET /camp-benefits：C 端进营地页先拿会员概览（等级/折扣率/积分比例/钱包）。"""
        cfg = read_config(c, club_id)
        uid = int(user_id or 0) or None
        wallet = self.wallet_snapshot(c, uid or 0, club_id) if uid else {
            'clubPoints': 0, 'gearPoints': 0, 'memberLevel': '非会员'}
        from .points_redeem_rate import resolve as resolve_rate
        try:
            rate = resolve_rate(c, club_id=int(club_id), user_id=uid, setting=self.setting)
        except Exception:
            rate = 100
        return {
            'memberDiscountEnabled': bool(cfg['memberDiscountEnabled']),
            'pointsEnabled': bool(cfg['pointsEnabled']),
            'voucherEnabled': bool(cfg['voucherEnabled']),
            'earnPointsEnabled': bool(cfg['earnPointsEnabled']),
            'clubPointsMaxDiscountPercent': float(cfg['clubPointsMaxDiscountPercent']),
            'memberLevel': str(wallet.get('memberLevel') or '非会员'),
            'memberDiscountPercent': member_discount_percent(c, club_id, uid) if cfg['memberDiscountEnabled'] else 0.0,
            'pointsRedeemRate': int(rate),
            'clubPoints': int(wallet.get('clubPoints') or 0),
            'gearPoints': int(wallet.get('gearPoints') or 0),
        }

    @staticmethod
    def _scope_context(lines: list[dict]) -> dict:
        """#860 从服务端重算过的订单规范行里抽出「真实对象 ID」。
        报价与下单共用这一个函数，保证「券在报价页能抵、下单时也能抵」不会漂移。

        注意：营地商品（小站/租赁）只有文本 category 字段、没有分类 ID，所以
        规范行里通常不带 categoryIds；「指定装备商品分类」的券因此不适用于营地订单
        —— 这是数据模型事实，不是漏传。C 端券列表现在会带同一份 context，所以这类券
        在营地结算页就不会出现（列表与 hold 一致），不会「显示可抵、下单被拒」。
        """
        camps, pitches, products, categories, activities = set(), set(), set(), set(), set()
        for x in lines or []:
            try:
                cid = x.get('campId')
                if cid: camps.add(int(cid))
            except (TypeError, ValueError):
                pass
            # 规范行将来若带分类/活动归属，直接并入，报价与 hold 自动同时生效。
            for key, bucket in (('categoryId', categories), ('categoryIds', categories),
                                ('activityId', activities), ('activityIds', activities)):
                v = x.get(key)
                if v is None: continue
                if not isinstance(v, (list, tuple, set)): v = [v]
                for i in v:
                    try:
                        if i not in (None, '', 0, '0'): bucket.add(int(i))
                    except (TypeError, ValueError): pass
            try: rid = int(x.get('id') or 0)
            except (TypeError, ValueError): rid = 0
            if not rid: continue
            if str(x.get('kind') or '') == 'pitch': pitches.add(rid)
            else: products.add(rid)
        return {'campIds': sorted(camps), 'pitchIds': sorted(pitches),
                'productIds': sorted(products), 'categoryIds': sorted(categories),
                'activityIds': sorted(activities)}

    def quote(self, c, *, club_id: int, user_id: int | None, items: list[dict],
              club_points: int = 0, gear_points: int = 0,
              voucher_codes: list[str] | None = None) -> dict:
        """营地统一报价（**只读**，不落库、不 hold）。items 必须是服务端重算过价的规范行。

        v4.1 #810：行级权益开关（promoEnabled/memberEnabled/pointsEnabled，缺省=开）。
        三个「合格基数」分别统计各开关打开的行小计；每层折扣只作用于该层合格金额，
        上一层折扣按金额占比分摊到下层基数。**全部行开关全开时三个基数都等于 goods，
        计算路径与 #810 之前完全一致（零漂移）**。

        返回键固定，见 /tmp/camp-api-contract.md §2。任何一处异常都退回「无权益原价」，
        绝不让会员体系把下单链路卡死（宁可不打折，不能付不了款）。
        """
        cfg = read_config(c, club_id)
        uid = int(user_id or 0) or None
        codes = [str(x).strip() for x in (voucher_codes or []) if str(x).strip()]
        lines = [dict(x) for x in items]
        goods = round(sum(float(x.get('subtotal') or 0) for x in lines), 2)
        deposit = round(sum(float(x.get('deposit') or 0) for x in lines), 2)

        def _on(line, key):
            v = line.get(key)
            if v is None:
                return True
            try:
                return int(v) != 0
            except (TypeError, ValueError):
                return True

        # 三个合格基数：积分抵用 / 会员折扣 / 优惠活动(福利券)。关掉的行不进该层折扣基数。
        points_base = round(sum(float(x.get('subtotal') or 0) for x in lines if _on(x, 'pointsEnabled')), 2)
        member_base = round(sum(float(x.get('subtotal') or 0) for x in lines if _on(x, 'memberEnabled')), 2)
        promo_base = round(sum(float(x.get('subtotal') or 0) for x in lines if _on(x, 'promoEnabled')), 2)

        wallet = self.wallet_snapshot(c, uid or 0, club_id) if uid else {
            'clubPoints': 0, 'gearPoints': 0, 'memberLevel': '非会员'}

        out: dict[str, Any] = {
            'ok': True, 'items': lines,
            'goodsAmount': goods,
            'memberDiscount': 0.0, 'memberDiscountRate': 0.0,
            'memberLevel': str(wallet.get('memberLevel') or '非会员'),
            'clubPointsUsed': 0, 'gearPointsUsed': 0,
            'clubPointDiscount': 0.0, 'gearPointDiscount': 0.0,
            'pointsRedeemRate': 100,
            'maxRedeemable': {'club': {'points': 0, 'cash': 0.0}, 'gear': {'points': 0, 'cash': 0.0}},
            'voucherDiscount': 0.0, 'vouchersApplied': [],
            'payable': goods, 'depositAmount': deposit,
            'total': round(goods + deposit, 2), 'pointsEarned': 0,
            'wallet': wallet,
            'benefits': {k: bool(cfg[k]) for k in
                         ('memberDiscountEnabled', 'pointsEnabled', 'voucherEnabled')},
            'eligibility': {'pointsBase': points_base, 'memberBase': member_base, 'promoBase': promo_base},
        }
        if goods <= 0 or not uid:
            return out

        try:
            synth = self._synthetic_activity(club_id, cfg)
            policy = None
            # 第一层：积分抵用 —— 只对 pointsEnabled=开 的行生效。
            after_points = goods
            if points_base > 0:
                req_cp = max(0, int(club_points or 0))
                req_gp = max(0, int(gear_points or 0))
                q, policy, max_redeemable, rate = self.booking._quote_with_policy(
                    c=c, activity=synth, amount=points_base, wallet=wallet,
                    requested_club_points=req_cp, requested_gear_points=req_gp, user_id=uid)
                point_discount = round(points_base - float(q.payable), 2)
                after_points = round(goods - point_discount, 2)
                out['clubPointsUsed'] = int(q.club_points_used)
                out['gearPointsUsed'] = int(q.gear_points_used)
                out['clubPointDiscount'] = round(float(q.club_point_discount), 2)
                out['gearPointDiscount'] = round(float(q.platform_point_subsidy), 2)
                out['pointsRedeemRate'] = int(rate)
                mr = max_redeemable or {}
                out['maxRedeemable'] = {
                    'club': mr.get('club') or {'points': 0, 'cash': 0.0},
                    'gear': mr.get('gear') or {'points': 0, 'cash': 0.0},
                }
                out['_policy'] = policy.as_dict() if hasattr(policy, 'as_dict') else {}

            # 第二层：会员等级立减 —— 只对 memberEnabled=开 的行生效；
            # 折扣基数 = 积分折后总额 × (会员合格金额 / 商品总额)，全开时=积分折后总额（与活动一致）。
            after_member = after_points
            member_share = (member_base / goods) if goods > 0 else 0.0
            if cfg['memberDiscountEnabled'] and member_base > 0 and member_share > 0:
                m_amount = round(after_points * member_share, 2)
                md, mrate = self.checkout._member_activity_discount(
                    c, activity=synth, user_id=uid, amount=m_amount)
                if md > 0:
                    out['memberDiscount'] = round(float(md), 2)
                    out['memberDiscountRate'] = float(mrate)
                    after_member = round(after_points - float(md), 2)

            # 第三层：福利券 —— 只对 promoEnabled=开 的行生效；
            # 可用金额 = 会员折后总额 × (优惠合格金额 / 商品总额)，全开时=会员折后总额（与活动一致）。
            promo_share = (promo_base / goods) if goods > 0 else 0.0
            if cfg['voucherEnabled'] and codes and promo_base > 0 and promo_share > 0:
                v_avail = round(max(0.0, (after_points - out['memberDiscount']) * promo_share), 2)
                vb = self.benefits.quote_vouchers(
                    c, voucher_codes=codes, user_id=uid, club_id=int(club_id),
                    kind='camp', amount_available=v_avail,
                    context=self._scope_context(lines))
                out['voucherDiscount'] = round(float(vb.get('totalDiscount') or 0), 2)
                out['vouchersApplied'] = vb.get('applied') or []
                out['_voucherClubDiscount'] = float(vb.get('clubDiscount') or 0)
                out['_voucherPlatformSubsidy'] = float(vb.get('platformSubsidy') or 0)

            payable = round(max(0.0, after_member - out['voucherDiscount']), 2)
            out['payable'] = payable
            out['total'] = round(payable + deposit, 2)
            if cfg['earnPointsEnabled'] and points_base > 0 and policy is not None \
                    and getattr(policy, 'effective_earn_club_points', False):
                out['pointsEarned'] = int(self.points.club_points_earned_from_activity(
                    payable, rate_override=policy.club_points_earn_rate_override))
        except Exception:
            # 券码非法 / 券已被占用等是「可预期业务错误」，由调用方转成 409；
            # 其余异常退回原价，保证下单不被会员体系阻断。
            logger.exception('camp pricing quote failed (club=%s user=%s)', club_id, uid)
            raise
        return out

    # ---- 下单 / 支付 / 取消：账本原语全部复用 points_engine 与 benefit_engine ----

    def hold(self, c, *, checkout_id: str, user_id: int, club_id: int,
             quote: dict, voucher_codes: list[str]) -> None:
        """下单时锁定积分与券。point_holds 有 (intent_id,point_type) 唯一索引，天然幂等。"""
        uid = int(user_id or 0)
        if not uid:
            return
        c.execute('INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)', (club_id, uid))
        c.execute('INSERT OR IGNORE INTO gear_point_accounts(user_id,balance) VALUES(?,0)', (uid,))
        cp = int(quote.get('clubPointsUsed') or 0)
        gp = int(quote.get('gearPointsUsed') or 0)
        if cp or gp:
            self.points.hold_points(c, intent_id=checkout_id, user_id=uid,
                                    club_id=club_id, club_points=cp, gear_points=gp)
        if quote.get('voucherDiscount', 0) > 0 and voucher_codes:
            self.benefits.hold_vouchers(
                c, intent_id=checkout_id, voucher_codes=voucher_codes,
                user_id=uid, club_id=club_id, kind='camp',
                amount_available=float(quote.get('payable') or 0) + float(quote.get('voucherDiscount') or 0),
                context=self._scope_context(quote.get('items') or []))

    def finalize(self, c, *, checkout_id: str, order_id: int, club_id: int,
                 user_id: int, quote: dict) -> int:
        """支付成功：consume holds → 生成正式抵扣流水 → 消费送积分。返回实际赠送积分。"""
        uid = int(user_id or 0)
        earned = 0
        if not uid:
            return 0
        self.points.consume_holds(c, intent_id=checkout_id)
        self.points.materialize_redemptions(
            c, intent_id=checkout_id, order_kind='camp_order', order_id=order_id, club_id=club_id)
        self.benefits.consume_vouchers(
            c, intent_id=checkout_id, order_kind='camp_order', order_id=order_id)
        want = int(quote.get('pointsEarned') or 0)
        if want > 0:
            # 与活动报名**同一个比例解析函数**（club_points_rate / 等级覆盖），
            # 但流水 source_type 标成 camp_order —— 否则未来的退款 reversal 会把营地订单
            # 误当成活动报名去反查 registration，账本对不上。
            earned = int(self.points.club_points_earned_from_activity(
                float(quote.get('payable') or 0),
                rate_override=quote.get('_earnRateOverride')))
            earned = max(0, min(earned, want))
            if earned:
                c.execute('UPDATE club_members SET club_points_balance=club_points_balance+? WHERE club_id=? AND user_id=?',
                          (earned, club_id, uid))
                c.execute('''INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note)
                             VALUES(?,?,?,?,?,?,?)''',
                          (club_id, uid, 'earn', earned, 'camp_order', str(order_id),
                           '营地商品消费获得活动积分；成本由俱乐部承担'))
        return earned

    def release(self, c, *, checkout_id: str) -> None:
        """取消/超时：释放积分 hold 与券 hold，账本守恒。"""
        self.points.release_holds(c, intent_id=checkout_id)
        self.benefits.release_vouchers(c, intent_id=checkout_id)

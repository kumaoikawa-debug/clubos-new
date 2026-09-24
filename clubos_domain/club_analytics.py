from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any


class ClubBusinessIntelligence:
    """Read-only, club-scoped BI derived exclusively from ClubOS source records.

    Period activity cash flow: successfully paid bookings CREATED in period less confirmed
    cash refunds COMPLETED in period, including refunds for older bookings. A separate
    cohort metric deducts all confirmed refunds for bookings created in the period.
    It is not a profit/loss statement: no complete activity operating cost ledger exists.
    """

    WINDOWS = (7, 30, 90, 365)

    @staticmethod
    def _num(v: Any) -> float:
        return round(float(v or 0), 2)

    @staticmethod
    def _sum(rows: list[dict], field: str) -> float:
        return round(sum(float(x.get(field) or 0) for x in rows), 2)

    def _period(self, window_days: int) -> tuple[int, str, str]:
        try:
            days = int(window_days)
        except (TypeError, ValueError):
            raise ValueError('windowDays 必须是 7、30、90 或 365')
        if days not in self.WINDOWS:
            raise ValueError('windowDays 必须是 7、30、90 或 365')
        end = datetime.utcnow().replace(microsecond=0) + timedelta(seconds=1)
        start = end - timedelta(days=days)
        return days, start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')

    def dashboard(self, c, *, club_id: int, window_days: int = 30) -> dict[str, Any]:
        days, start, end = self._period(window_days)
        club = c.execute('SELECT id,name FROM clubs WHERE id=?', (club_id,)).fetchone()
        if not club:
            raise LookupError('俱乐部不存在')
        r = lambda sql, args=(): [dict(x) for x in c.execute(sql, args).fetchall()]
        period = (club_id, start, end)
        bookings = r("""SELECT id,activity_id,occurrence_id,user_id,status,amount,original_amount,
                                  club_point_discount,club_benefit_discount,platform_point_subsidy,
                                  platform_benefit_subsidy,created_at
                            FROM registrations WHERE club_id=? AND created_at>=? AND created_at<?
                              AND payment_status='succeeded' AND status IN ('paid','refunded')""", period)
        booking_ids = {int(x['id']) for x in bookings}
        booking_cash = self._sum(bookings, 'amount')
        # A cash refund is counted only when the provider-confirmed refund succeeded.
        refunds = r("""SELECT id,registration_id,cash_amount,refunded_at FROM refund_requests
                       WHERE club_id=? AND kind='activity' AND status='succeeded'
                         AND registration_id IS NOT NULL""", (club_id,))
        cohort_refund = round(sum(float(x['cash_amount'] or 0) for x in refunds
                                  if int(x['registration_id']) in booking_ids), 2)
        period_refunds = [x for x in refunds if x['refunded_at'] and start <= str(x['refunded_at']) < end]
        refund_cash_period = self._sum(period_refunds, 'cash_amount')
        active_participants = int(c.execute("""SELECT COUNT(*) FROM registration_participants p
                          JOIN registrations b ON b.id=p.registration_id
                          WHERE b.club_id=? AND b.created_at>=? AND b.created_at<?
                            AND b.payment_status='succeeded' AND p.status='active'""", period).fetchone()[0])
        fully_refunded_bookings = sum(1 for x in bookings if x['status']=='refunded')
        partial_refund_bookings = int(c.execute("""SELECT COUNT(DISTINCT b.id) FROM registrations b
             JOIN refund_requests rr ON rr.registration_id=b.id AND rr.kind='activity' AND rr.status='succeeded'
             WHERE b.club_id=? AND b.created_at>=? AND b.created_at<? AND b.status<>'refunded'""", period).fetchone()[0])
        buyers = {int(x['user_id']) for x in bookings}
        # Repeat purchase is a *paid-order proxy*, not a general retention rate.
        history = r("""SELECT user_id, COUNT(*) n FROM registrations
                       WHERE club_id=? AND payment_status='succeeded' AND status IN ('paid','refunded')
                         AND created_at<? GROUP BY user_id""", (club_id, end))
        repeat_buyers = sum(1 for x in history if int(x['user_id']) in buyers and int(x['n']) >= 2)

        activity_ids = sorted({int(x['activity_id']) for x in bookings})
        activity_names = {int(x['id']): str(x['title']) for x in r('SELECT id,title FROM activities WHERE club_id=?', (club_id,))}
        activity_agg: dict[int, dict] = {}
        refund_by_reg = defaultdict(float)
        for x in refunds:
            refund_by_reg[int(x['registration_id'])] += float(x['cash_amount'] or 0)
        for x in bookings:
            aid = int(x['activity_id'])
            a = activity_agg.setdefault(aid, dict(activityId=aid, title=activity_names.get(aid, ''),
                               orders=0, paidCash=0., confirmedRefundCash=0., netCash=0.,
                               platformSubsidy=0., clubDiscount=0., paidBuyerIds=set()))
            a['orders'] += 1
            a['paidCash'] += float(x['amount'] or 0)
            a['confirmedRefundCash'] += refund_by_reg[int(x['id'])]
            a['platformSubsidy'] += float(x['platform_point_subsidy'] or 0) + float(x['platform_benefit_subsidy'] or 0)
            a['clubDiscount'] += float(x['club_point_discount'] or 0) + float(x['club_benefit_discount'] or 0)
            a['paidBuyerIds'].add(int(x['user_id']))
        activities = []
        for x in activity_agg.values():
            x['netCash'] = round(x['paidCash'] - x['confirmedRefundCash'], 2)
            x['paidBuyers'] = len(x.pop('paidBuyerIds'))
            for k in ('paidCash', 'confirmedRefundCash', 'platformSubsidy', 'clubDiscount'):
                x[k] = round(x[k], 2)
            activities.append(x)
        activities.sort(key=lambda x: (-x['netCash'], x['activityId']))

        members = r('SELECT id,user_id,created_at,level,club_points_balance FROM club_members WHERE club_id=?', (club_id,))
        new_members = sum(1 for x in members if start <= str(x['created_at']) < end)
        tiers = defaultdict(int)
        for x in members:
            tiers[str(x['level'] or '普通会员')] += 1
        club_points = r('SELECT type,amount,created_at FROM club_point_ledger WHERE club_id=? AND created_at>=? AND created_at<?', period)
        club_points_issued = sum(max(0, int(x['amount'] or 0)) for x in club_points)
        club_points_reduced = abs(sum(min(0, int(x['amount'] or 0)) for x in club_points))
        club_points_balance = sum(int(x['club_points_balance'] or 0) for x in members)

        gear = r("""SELECT id,total,cash_paid,refunded_cash_total,source_club_id,user_id,created_at
                    FROM gear_orders WHERE source_club_id=? AND created_at>=? AND created_at<?
                      AND payment_status='succeeded'""", period)
        gear_refunds = r("""SELECT cash_amount,refunded_at FROM refund_requests
                       WHERE club_id=? AND kind='gear' AND status='succeeded'""", (club_id,))
        gear_period_refunds = [x for x in gear_refunds if x['refunded_at'] and start <= str(x['refunded_at']) < end]
        commissions = r('SELECT amount,ledger_type,status,created_at FROM commission_ledger WHERE club_id=? AND created_at>=? AND created_at<?', period)
        settlements = r("""SELECT net_amount,paid_at FROM commission_settlements WHERE club_id=? AND status='paid'
                          AND paid_at>=? AND paid_at<?""", period)
        earned = round(sum(float(x['amount'] or 0) for x in commissions if x['ledger_type']=='earn'), 2)
        reversed_commission = round(sum(float(x['amount'] or 0) for x in commissions if x['ledger_type'] in ('refund_reverse','after_sales_reverse')), 2)

        ai_usage = r('SELECT status,credits_charged,task_type,created_at FROM ai_usage_records WHERE club_id=? AND created_at>=? AND created_at<?', period)
        credit_ledgers = r('SELECT type,amount FROM ai_credit_ledger WHERE club_id=? AND created_at>=? AND created_at<?', period)
        credit_account = c.execute('SELECT balance FROM ai_credit_accounts WHERE club_id=?', (club_id,)).fetchone()
        debt = float(c.execute('SELECT COALESCE(SUM(amount-resolved_amount),0) FROM ai_credit_adjustment_debt WHERE club_id=? AND resolved=0', (club_id,)).fetchone()[0] or 0)
        credit_consumed = abs(sum(min(0, int(x['amount'] or 0)) for x in credit_ledgers if x['type']=='consume'))
        reward_credits = sum(max(0, int(x['amount'] or 0)) for x in credit_ledgers if x['type']=='mall_reward')
        ai_success = sum(1 for x in ai_usage if x['status']=='success')
        ai_failure = sum(1 for x in ai_usage if x['status']=='failed')
        task_counts = defaultdict(int)
        for x in ai_usage:
            if x['status']=='success':
                task_counts[str(x['task_type'])] += 1

        trend = {}
        cursor = datetime.fromisoformat(start).date()
        last = datetime.fromisoformat(end).date()
        while cursor <= last:
            trend[cursor.isoformat()] = {'date': cursor.isoformat(), 'activityPaidCash': 0., 'activityRefundCash': 0., 'gearAttributedGMV': 0.}
            cursor += timedelta(days=1)
        for x in bookings:
            k = str(x['created_at'])[:10]
            if k in trend: trend[k]['activityPaidCash'] += float(x['amount'] or 0)
        for x in period_refunds:
            k = str(x['refunded_at'])[:10]
            if k in trend: trend[k]['activityRefundCash'] += float(x['cash_amount'] or 0)
        for x in gear:
            k = str(x['created_at'])[:10]
            if k in trend: trend[k]['gearAttributedGMV'] += float(x['total'] or 0)
        for x in trend.values():
            for k in ('activityPaidCash','activityRefundCash','gearAttributedGMV'):
                x[k] = round(x[k], 2)
            x['activityNetCashFlow'] = round(x['activityPaidCash'] - x['activityRefundCash'], 2)

        return {
            'clubId': int(club_id), 'clubName': str(club['name']),
            'period': {'windowDays':days, 'start':start, 'endExclusive':end, 'timezone':'UTC'},
            'activity': {
                'publishedCount': int(c.execute("SELECT COUNT(*) FROM activities WHERE club_id=? AND status='published'", (club_id,)).fetchone()[0]),
                'bookingOrders':len(bookings), 'fullRefundOrders':fully_refunded_bookings,
                'partialRefundOrders':partial_refund_bookings, 'activeParticipants':active_participants,
                'paidCashInWindow':booking_cash, 'confirmedRefundCashInWindow':refund_cash_period,
                'netCashFlowInWindow':round(booking_cash - refund_cash_period, 2),
                'bookingCohortRefundCash':cohort_refund, 'bookingCohortNetCash':round(booking_cash-cohort_refund, 2),
                'clubFundedDiscount':round(self._sum(bookings,'club_point_discount') + self._sum(bookings,'club_benefit_discount'),2),
                'platformFundedSubsidy':round(self._sum(bookings,'platform_point_subsidy') + self._sum(bookings,'platform_benefit_subsidy'),2),
                'payingUsers':len(buyers), 'repeatPayingUsers':repeat_buyers,
                'repeatPayingUserRatioPct':round(repeat_buyers/len(buyers)*100,2) if buyers else 0.
            },
            'membership': {'totalMembers':len(members),'newMembersInWindow':new_members,
                           'tierCounts':dict(sorted(tiers.items())),'clubPointsBalance':club_points_balance,
                           'clubPointsIssuedInWindow':club_points_issued,'clubPointsDebitedInWindow':club_points_reduced},
            'commerce': {'attributedOrderCount':len(gear),'attributedGMV':self._sum(gear,'total'),
                         'attributedCashPaid':self._sum(gear,'cash_paid'),
                         'cohortRefundCash':self._sum(gear,'refunded_cash_total'),
                         'cashRefundedInWindow':self._sum(gear_period_refunds,'cash_amount'),
                         'commissionEarnedInWindow':earned,'commissionReversedInWindow':reversed_commission,
                         'commissionNetMovement':round(earned+reversed_commission,2),
                         'settlementPaidInWindow':self._sum(settlements,'net_amount'),
                         'aiRewardCreditsInWindow':reward_credits},
            'ai': {'successfulCalls':ai_success,'failedCalls':ai_failure,
                   'creditsConsumedInWindow':credit_consumed,
                   'currentBalance':int(credit_account['balance']) if credit_account else 0,
                   'unresolvedDebt':int(debt), 'tasks':dict(sorted(task_counts.items()))},
            'activities':activities, 'trend':list(trend.values()),
            'definitions': {
                'activityCashFlow':'本期已支付报名实收 - 本期已成功现金退款；退款可对应往期订单。',
                'bookingCohortNetCash':'本期新建并已支付报名的实收 - 这些报名截至查询时已成功退款的现金。',
                'repeatPayingUsers':'本期付费用户中，在本俱乐部截至期末累计有至少2笔成功付费报名的用户，不是完整留存率。',
                'gearGMV':'按 sourceClubId 归因本期商城订单的交易总额；商城履约和成本仍归平台。',
                'commission':'本期佣金流水与本期实际打款分别统计，不能简单相加。',
                'profit':'未建立完整活动成本/费用账本，本版本不计算或展示活动净利润。',
                'privacy':'BI 仅按 club_id 聚合，不输出其他俱乐部经营数据；正式生产鉴权/RBAC仍需单独验收。'
            }
        }

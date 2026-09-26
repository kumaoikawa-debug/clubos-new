from __future__ import annotations
from typing import Any
from clubos_domain.product_stock import variant_of, write_variant_stock


class CommerceRefundEngine:
    """ClubOS financial reversals after the commerce/payment layer confirms a full refund.

    Medusa/payment provider owns the actual money refund. ClubOS reverses only its own
    domain side effects: points, commission, AI-credit reward, local inventory mirror.
    """

    def __init__(self, points_engine, benefit_engine=None, commission_engine=None, inventory_engine=None):
        self.points = points_engine
        self.benefits = benefit_engine
        self.commissions = commission_engine
        self.inventory = inventory_engine


    def refund_gear_after_sales(self, c, *, case_id:str, reason:str='after_sales_refund') -> dict[str,Any]:
        case_row=c.execute('SELECT * FROM after_sales_cases WHERE id=?',(case_id,)).fetchone()
        if not case_row: raise LookupError('售后单不存在')
        case=dict(case_row)
        order_row=c.execute('SELECT * FROM gear_orders WHERE id=?',(case['order_id'],)).fetchone()
        if not order_row: raise LookupError('装备订单不存在')
        order=dict(order_row)
        marker=c.execute("SELECT 1 FROM gear_point_ledger WHERE type='after_sales_marker' AND source_type='gear_after_sales' AND source_id=? LIMIT 1",(case_id,)).fetchone()
        if marker:
            return {'ok':True,'orderId':order['id'],'afterSalesCaseId':case_id,'status':'partial_refunded','idempotent':True}
        items=[dict(x) for x in c.execute('''SELECT ai.*,goi.product_id,goi.unit_price,p.commission_rate
                   FROM after_sales_items ai JOIN gear_order_items goi ON goi.id=ai.order_item_id
                   JOIN products p ON p.id=goi.product_id WHERE ai.case_id=?''',(case_id,)).fetchall()]
        goods=round(sum(float(x.get('requested_amount') or 0) for x in items),2)
        refund_cash=round(float(case.get('approved_refund_amount') or case.get('requested_refund_amount') or 0),2)
        total=max(0.0,float(order.get('total') or 0)); cash_paid=max(0.0,float(order.get('cash_paid') or 0))
        goods_ratio=min(1.0, goods/total) if total else 0.0
        user_id=int(order['user_id']); club_id=int(order['source_club_id']); oid=int(order['id'])

        redeemed=int(c.execute('SELECT COALESCE(SUM(points_used),0) FROM point_redemptions WHERE order_kind="gear" AND order_id=? AND point_type="gear"',(oid,)).fetchone()[0] or 0)
        already_returned=int(c.execute("SELECT COALESCE(SUM(amount),0) FROM gear_point_ledger WHERE type='after_sales_redeem_return' AND source_type='gear_after_sales' AND source_id IN (SELECT id FROM after_sales_cases WHERE order_id=?)",(oid,)).fetchone()[0] or 0)
        target_return=min(redeemed, int(round(redeemed*min(1.0,(float(order.get('refunded_goods_total') or 0)+goods)/total))) if total else 0)
        return_pts=max(0,target_return-already_returned)
        if return_pts:
            c.execute('UPDATE gear_point_accounts SET balance=balance+? WHERE user_id=?',(return_pts,user_id))
            c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                      (user_id,'after_sales_redeem_return',return_pts,'gear_after_sales',case_id,'部分装备退款按商品占比返还原订单使用的装备积分'))

        earned=int(c.execute('SELECT COALESCE(SUM(amount),0) FROM gear_point_ledger WHERE source_type="gear_order" AND source_id=? AND type="earn"',(str(oid),)).fetchone()[0] or 0)
        already_rev=abs(int(c.execute("SELECT COALESCE(SUM(amount),0) FROM gear_point_ledger WHERE type='after_sales_earn_reverse' AND source_type='gear_after_sales' AND source_id IN (SELECT id FROM after_sales_cases WHERE order_id=?)",(oid,)).fetchone()[0] or 0))
        target_rev=min(earned,int(round(earned*min(1.0,(float(order.get('refunded_cash_total') or 0)+refund_cash)/cash_paid))) if cash_paid else 0)
        earn_rev=max(0,target_rev-already_rev); gear_debt=0
        if earn_rev:
            balrow=c.execute('SELECT balance FROM gear_point_accounts WHERE user_id=?',(user_id,)).fetchone()
            bal=int(balrow[0] or 0) if balrow else 0
            deduct=min(bal,earn_rev)
            if deduct:
                c.execute('UPDATE gear_point_accounts SET balance=balance-? WHERE user_id=?',(deduct,user_id))
                c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                          (user_id,'after_sales_earn_reverse',-deduct,'gear_after_sales',case_id,'部分装备退款按现金退款比例冲回已赚装备积分'))
            gear_debt=earn_rev-deduct
            if gear_debt:
                c.execute('INSERT INTO point_adjustment_debt(user_id,club_id,point_type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                          (user_id,None,'gear',gear_debt,'gear_after_sales',case_id,'部分装备退款时已赚积分不足以冲回，形成积分欠账'))

        comm=round(sum(float(x['unit_price'] or 0)*int(x['quantity'] or 0)*float(x['commission_rate'] or 0) for x in items),2)
        settled=float(c.execute("SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE order_id=? AND ledger_type='earn' AND status='settled'",(oid,)).fetchone()[0] or 0)
        total_earn=float(c.execute("SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE order_id=? AND ledger_type='earn'",(oid,)).fetchone()[0] or 0)
        prev_reverse=abs(float(c.execute("SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE order_id=? AND ledger_type IN ('refund_reverse','after_sales_reverse')",(oid,)).fetchone()[0] or 0))
        comm=max(0.0,min(comm,max(0.0,total_earn-prev_reverse)))
        future_offset=0.0
        if comm:
            unsettled=max(0.0,total_earn-settled-prev_reverse)
            immediate=min(comm,unsettled); future_offset=max(0.0,comm-immediate)
            if immediate:
                c.execute("INSERT INTO commission_ledger(club_id,order_id,amount,status,ledger_type,note) VALUES(?,?,?,?,?,?)",
                          (club_id,oid,-immediate,'available','after_sales_reverse',f'售后单 {case_id} 部分退款冲回佣金'))
            if future_offset:
                c.execute("INSERT INTO commission_ledger(club_id,order_id,amount,status,ledger_type,note) VALUES(?,?,?,?,?,?)",
                          (club_id,oid,-future_offset,'available','after_sales_reverse',f'售后单 {case_id} 对已结算佣金形成下期冲抵'))

        reward=int(c.execute("SELECT COALESCE(SUM(amount),0) FROM ai_credit_ledger WHERE club_id=? AND source_type='gear_order' AND source_id=? AND type='mall_reward'",(club_id,str(oid))).fetchone()[0] or 0)
        already_ai=abs(int(c.execute("SELECT COALESCE(SUM(amount),0) FROM ai_credit_ledger WHERE club_id=? AND source_type='gear_after_sales' AND type='mall_reward_reverse' AND source_id IN (SELECT id FROM after_sales_cases WHERE order_id=?)",(club_id,oid)).fetchone()[0] or 0))
        target_ai=min(reward,int(round(reward*min(1.0,(float(order.get('refunded_goods_total') or 0)+goods)/total))) if total else 0)
        ai_reverse=max(0,target_ai-already_ai); ai_debt=0
        if ai_reverse:
            balrow=c.execute('SELECT balance FROM ai_credit_accounts WHERE club_id=?',(club_id,)).fetchone()
            bal=int(balrow[0] or 0) if balrow else 0
            deduct=min(bal,ai_reverse)
            if deduct:
                c.execute('UPDATE ai_credit_accounts SET balance=balance-? WHERE club_id=?',(deduct,club_id))
                c.execute('INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                          (club_id,'mall_reward_reverse',-deduct,'gear_after_sales',case_id,'部分装备退款按退款商品GMV冲回商城奖励'))
            ai_debt=ai_reverse-deduct
            if ai_debt:
                c.execute('INSERT INTO ai_credit_adjustment_debt(club_id,amount,source_type,source_id,note) VALUES(?,?,?,?,?)',
                          (club_id,ai_debt,'gear_after_sales',case_id,'部分装备退款时商城奖励已使用，形成AI Credits欠账'))

        new_cash=round(float(order.get('refunded_cash_total') or 0)+refund_cash,2)
        new_goods=round(float(order.get('refunded_goods_total') or 0)+goods,2)
        fully=(new_goods>=total-0.005)
        restored=0
        if fully and self.benefits:
            restored=self.benefits.restore_order_vouchers(c,order_kind='gear',order_id=oid)
        c.execute("INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)",
                  (user_id,'after_sales_marker',0,'gear_after_sales',case_id,'售后退款资产冲正完成标记'))
        if fully:
            c.execute("UPDATE gear_orders SET status='refunded',refund_status='succeeded',after_sales_status='refunded',refunded_at=CURRENT_TIMESTAMP,refunded_cash_total=?,refunded_goods_total=? WHERE id=?",(new_cash,new_goods,oid))
        else:
            c.execute("UPDATE gear_orders SET refund_status='partial',after_sales_status='completed',refunded_cash_total=?,refunded_goods_total=? WHERE id=?",(new_cash,new_goods,oid))
        return {'ok':True,'orderId':oid,'afterSalesCaseId':case_id,'status':'refunded' if fully else 'partial_refunded',
                'cashRefundAmount':refund_cash,'goodsRefundAmount':goods,'orderFullyRefunded':fully,
                'gearPointsReturned':return_pts,'gearPointsEarnedReversed':earn_rev,'gearPointsDebt':gear_debt,
                'commissionReversed':round(comm,2),'commissionFutureOffset':round(future_offset,2),
                'aiCreditsRewardReversed':ai_reverse-ai_debt,'aiCreditsDebt':ai_debt,'benefitVouchersRestored':restored}

    def refund_gear_order(self, c, *, order_id:int, reason:str='full_refund') -> dict[str,Any]:
        r=c.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)).fetchone()
        if not r: raise LookupError('装备订单不存在')
        order=dict(r)
        if order['status']=='refunded':
            return {'ok':True,'orderId':order_id,'status':'refunded','idempotent':True}
        if order['status'] not in ('paid','processing','shipped','delivered','completed','after_sales'):
            raise ValueError(f"当前订单状态不能退款: {order['status']}")

        point_result=self.points.reverse_gear_order(c,order=order)
        restored_vouchers = self.benefits.restore_order_vouchers(c,order_kind='gear',order_id=order_id) if self.benefits else 0

        # Restore local inventory mirror. In Medusa production mode Medusa remains authoritative;
        # this keeps the ClubOS projection consistent after a confirmed refund.
        items=c.execute('SELECT product_id,variant_id,quantity FROM gear_order_items WHERE order_id=?',(order_id,)).fetchall()
        for it in items:
            if self.inventory:
                self.inventory.refund_restock(c,product_id=int(it['product_id']),quantity=int(it['quantity']),order_id=order_id,actor_type='refund',variant_id=it['variant_id'] if 'variant_id' in it.keys() else None)
            else:
                _v=variant_of(c,product_id=int(it['product_id']),variant_id=(it['variant_id'] if 'variant_id' in it.keys() else None))
                if _v: write_variant_stock(c,product_id=int(it['product_id']),variant_id=int(_v['id']),new_stock=int(_v['stock'] or 0)+int(it['quantity']))
                else: c.execute('UPDATE products SET stock=stock+? WHERE id=?',(int(it['quantity']),int(it['product_id'])))

        # Reverse commission once. Unsettled commission becomes reversed; if it was already
        # paid to the club, create a negative available entry so the next settlement offsets it.
        if self.commissions:
            commission_result=self.commissions.reverse_order(c,order_id=order_id,club_id=int(order['source_club_id']))
            to_reverse=float(commission_result.get('reversed') or 0)
        else:
            comm=c.execute("SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE order_id=? AND ledger_type='earn'",(order_id,)).fetchone()
            earned_comm=float(comm[0] or 0)
            reversed_comm=c.execute("SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE order_id=? AND ledger_type='refund_reverse'",(order_id,)).fetchone()
            reversed_total=abs(float(reversed_comm[0] or 0))
            to_reverse=max(0.0,earned_comm-reversed_total)
            commission_result={'reversed':to_reverse,'futureOffset':0.0}
            if to_reverse:
                c.execute("""INSERT INTO commission_ledger(club_id,order_id,amount,status,ledger_type,note)
                             VALUES(?,?,?,?,?,?)""",
                          (order['source_club_id'],order_id,-to_reverse,'reversed','refund_reverse','装备订单全额退款，冲回俱乐部佣金'))

        # Reverse AI Credits reward. If already spent, leave a debt instead of making the wallet silently negative.
        reward_row=c.execute('''SELECT COALESCE(SUM(amount),0) FROM ai_credit_ledger
                                WHERE club_id=? AND source_type='gear_order' AND source_id=? AND type='mall_reward' ''',
                             (order['source_club_id'],str(order_id))).fetchone()
        reward=int(reward_row[0] or 0)
        already=c.execute('''SELECT COALESCE(SUM(-amount),0) FROM ai_credit_ledger
                             WHERE club_id=? AND source_type='gear_refund' AND source_id=? AND type='mall_reward_reverse' ''',
                          (order['source_club_id'],str(order_id))).fetchone()
        already_reversed=int(already[0] or 0)
        reward_to_reverse=max(0,reward-already_reversed)
        ai_debt=0
        if reward_to_reverse:
            balrow=c.execute('SELECT balance FROM ai_credit_accounts WHERE club_id=?',(order['source_club_id'],)).fetchone()
            balance=int(balrow[0] or 0) if balrow else 0
            deduct=min(balance,reward_to_reverse)
            if deduct:
                c.execute('UPDATE ai_credit_accounts SET balance=balance-? WHERE club_id=?',(deduct,order['source_club_id']))
                c.execute('''INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,source_id,note)
                             VALUES(?,?,?,?,?,?)''',
                          (order['source_club_id'],'mall_reward_reverse',-deduct,'gear_refund',str(order_id),'装备退款冲回商城销售AI Credits奖励'))
            ai_debt=reward_to_reverse-deduct
            if ai_debt:
                c.execute('''INSERT INTO ai_credit_adjustment_debt(club_id,amount,source_type,source_id,note)
                             VALUES(?,?,?,?,?)''',
                          (order['source_club_id'],ai_debt,'gear_refund',str(order_id),'商城奖励已使用，退款形成AI Credits欠账'))

        c.execute("UPDATE gear_orders SET status='refunded',payment_status='succeeded',refund_status='succeeded',after_sales_status='refunded',refunded_at=CURRENT_TIMESTAMP WHERE id=?",(order_id,))
        return {'ok':True,'orderId':order_id,'status':'refunded','reason':reason,
                'points':point_result,'benefitVouchersRestored':restored_vouchers,
                'commissionReversed':round(to_reverse,2),'commissionFutureOffset':round(float(commission_result.get('futureOffset') or 0),2),
                'aiCreditsRewardReversed':reward_to_reverse-ai_debt,'aiCreditsDebt':ai_debt}

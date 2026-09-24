from __future__ import annotations
import json, secrets
from typing import Any


class BenefitEngine:
    """ClubOS benefit/voucher domain rules.

    - CLUB benefits spend Club Points and are funded by that club.
    - PLATFORM benefits spend Gear Points and are funded by ClubOS Platform.
    - Point redemption creates a voucher first; checkout later reserves/uses that voucher.
    - Open-source commerce handles cart/payment/order; ClubOS keeps funding ownership here.
    """

    def __init__(self, wallet_snapshot):
        self.wallet_snapshot = wallet_snapshot

    def create_club_benefit(self, c, *, club_id:int, payload:dict[str,Any]) -> int:
        if str(payload.get('pointsType') or 'club').lower() != 'club':
            raise ValueError('俱乐部福利只能使用活动积分兑换')
        benefit_type=str(payload.get('benefitType') or 'gift')
        if benefit_type == 'gear_coupon':
            raise ValueError('俱乐部不能创建装备商城抵扣券；装备商城由总平台统一经营')
        return self._create(c, owner_type='CLUB', owner_club_id=club_id, target_club_id=club_id,
                            points_type='club', payload=payload)

    def create_platform_benefit(self, c, *, payload:dict[str,Any]) -> int:
        if str(payload.get('pointsType') or 'gear').lower() != 'gear':
            raise ValueError('平台福利只能使用装备积分兑换')
        target = payload.get('targetClubId')
        return self._create(c, owner_type='PLATFORM', owner_club_id=None,
                            target_club_id=(int(target) if target not in (None,'') else None),
                            points_type='gear', payload=payload)

    def _create(self,c,*,owner_type:str,owner_club_id:int|None,target_club_id:int|None,points_type:str,payload:dict[str,Any])->int:
        title=str(payload.get('title') or '').strip()
        if not title: raise ValueError('福利名称不能为空')
        cost=max(0,int(payload.get('pointsCost') or 0))
        if cost<=0: raise ValueError('兑换积分必须大于0')
        stock=payload.get('stock')
        stock=None if stock in (None,'') else max(0,int(stock))
        benefit_type=str(payload.get('benefitType') or 'gift')
        cash_value=max(0,float(payload.get('cashValue') or 0))
        if benefit_type in {'activity_coupon','gear_coupon'} and cash_value <= 0:
            raise ValueError('抵扣券的权益价值必须大于0')
        c.execute('''INSERT INTO member_benefits(owner_type,owner_club_id,target_club_id,title,description,
                     benefit_type,points_type,points_cost,cash_value,stock,status,rules_json)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(
            owner_type,owner_club_id,target_club_id,title,payload.get('description'),benefit_type,
            points_type,cost,cash_value,stock,payload.get('status','active'),
            json.dumps(payload.get('rules') or {},ensure_ascii=False)))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])

    def list_for_club(self,c,*,club_id:int,include_inactive:bool=False)->list[dict[str,Any]]:
        status_clause='' if include_inactive else "AND status='active'"
        q=f'''SELECT * FROM member_benefits
              WHERE ((owner_type='CLUB' AND owner_club_id=?) OR (owner_type='PLATFORM' AND (target_club_id IS NULL OR target_club_id=?)))
              {status_clause} ORDER BY owner_type,id DESC'''
        return [dict(r) for r in c.execute(q,(club_id,club_id)).fetchall()]

    def redeem(self,c,*,benefit_id:int,user_id:int,display_club_id:int)->dict[str,Any]:
        benefit=c.execute('SELECT * FROM member_benefits WHERE id=? AND status="active"',(benefit_id,)).fetchone()
        if not benefit: raise LookupError('福利不存在或已下架')
        b=dict(benefit)
        if b['owner_type']=='CLUB':
            if int(b['owner_club_id']) != int(display_club_id):
                raise ValueError('该俱乐部不可兑换此福利')
            if b['points_type']!='club': raise ValueError('福利积分类型配置错误')
        else:
            if b['target_club_id'] is not None and int(b['target_club_id']) != int(display_club_id):
                raise ValueError('该平台福利未向当前俱乐部开放')
            if b['points_type']!='gear': raise ValueError('福利积分类型配置错误')
        if b['stock'] is not None and int(b['stock'])<=0:
            raise OverflowError('福利已兑完')
        points=int(b['points_cost'])
        if b['points_type']=='club':
            wallet=c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',(display_club_id,user_id)).fetchone()
            if not wallet or int(wallet[0] or 0)<points: raise ValueError('活动积分不足')
            c.execute('UPDATE club_members SET club_points_balance=club_points_balance-? WHERE club_id=? AND user_id=?',(points,display_club_id,user_id))
            c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                      (display_club_id,user_id,'benefit_redeem',-points,'member_benefit',str(benefit_id),'会员福利兑换；成本由俱乐部承担'))
            funding='CLUB'
        else:
            wallet=c.execute('SELECT balance FROM gear_point_accounts WHERE user_id=?',(user_id,)).fetchone()
            if not wallet or int(wallet[0] or 0)<points: raise ValueError('装备积分不足')
            c.execute('UPDATE gear_point_accounts SET balance=balance-? WHERE user_id=?',(points,user_id))
            c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                      (user_id,'benefit_redeem',-points,'member_benefit',str(benefit_id),'平台福利兑换；成本由总平台承担'))
            funding='PLATFORM'
        if b['stock'] is not None:
            c.execute('UPDATE member_benefits SET stock=stock-1,updated_at=CURRENT_TIMESTAMP WHERE id=?',(benefit_id,))
        code='BEN-'+secrets.token_hex(4).upper()
        c.execute('''INSERT INTO benefit_redemptions(benefit_id,user_id,display_club_id,point_type,points_spent,
                     funding_owner,cash_value,status,voucher_code) VALUES(?,?,?,?,?,?,?,?,?)''',
                  (benefit_id,user_id,display_club_id,b['points_type'],points,funding,float(b['cash_value'] or 0),'issued',code))
        rid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        return {'redemptionId':rid,'voucherCode':code,'title':b['title'],'pointsType':b['points_type'],
                'pointsSpent':points,'fundingOwner':funding,'benefitType':b['benefit_type'],'cashValue':float(b['cash_value'] or 0)}

    def user_redemptions(self,c,*,user_id:int,club_id:int)->list[dict[str,Any]]:
        return [dict(r) for r in c.execute('''SELECT r.*,b.title,b.description,b.benefit_type,b.owner_type,b.target_club_id
                    FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                    WHERE r.user_id=? AND r.display_club_id=? ORDER BY r.id DESC''',(user_id,club_id)).fetchall()]

    def eligible_vouchers(self,c,*,user_id:int,club_id:int,kind:str)->list[dict[str,Any]]:
        kind=str(kind).lower()
        if kind not in {'activity','gear'}: raise ValueError('kind must be activity or gear')
        benefit_type='activity_coupon' if kind=='activity' else 'gear_coupon'
        rows=c.execute('''SELECT r.*,b.title,b.description,b.benefit_type,b.owner_type,b.owner_club_id,b.target_club_id
                          FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                          WHERE r.user_id=? AND r.status='issued' AND b.status='active' AND b.benefit_type=?
                          ORDER BY r.id DESC''',(user_id,benefit_type)).fetchall()
        out=[]
        for rr in rows:
            x=dict(rr)
            if kind=='activity':
                # Activity coupons are redeemed inside a club member center and stay scoped to that club.
                if int(x['display_club_id']) != int(club_id):
                    continue
                if x['owner_type']=='CLUB' and int(x['owner_club_id']) != int(club_id):
                    continue
                if x['owner_type']=='PLATFORM' and x['target_club_id'] is not None and int(x['target_club_id']) != int(club_id):
                    continue
            else:
                # Gear storefront is platform-owned. Only platform-funded gear coupons can reduce gear orders.
                if x['owner_type']!='PLATFORM':
                    continue
                if x['target_club_id'] is not None and int(x['target_club_id']) != int(club_id):
                    continue
            out.append(x)
        return out

    def quote_vouchers(self,c,*,voucher_codes:list[str],user_id:int,club_id:int,kind:str,amount_available:float)->dict[str,Any]:
        codes=[]
        for code in voucher_codes or []:
            code=str(code or '').strip()
            if code and code not in codes: codes.append(code)
        if not codes:
            return {'clubDiscount':0.0,'platformSubsidy':0.0,'totalDiscount':0.0,'applied':[]}
        eligible={x['voucher_code']:x for x in self.eligible_vouchers(c,user_id=user_id,club_id=club_id,kind=kind)}
        remaining=max(0.0,float(amount_available or 0))
        club_discount=0.0; platform_subsidy=0.0; applied=[]
        for code in codes:
            v=eligible.get(code)
            if not v:
                raise ValueError(f'福利券不可用或已被使用: {code}')
            value=min(remaining,max(0.0,float(v['cash_value'] or 0)))
            if value<=0: continue
            funding=str(v['funding_owner'])
            if funding=='CLUB': club_discount += value
            else: platform_subsidy += value
            remaining=max(0.0,remaining-value)
            applied.append({'redemptionId':int(v['id']),'voucherCode':code,'title':v['title'],
                            'benefitType':v['benefit_type'],'fundingOwner':funding,'cashValue':round(value,2)})
            if remaining<=0: break
        return {'clubDiscount':round(club_discount,2),'platformSubsidy':round(platform_subsidy,2),
                'totalDiscount':round(club_discount+platform_subsidy,2),'applied':applied}

    def hold_vouchers(self,c,*,intent_id:str,voucher_codes:list[str],user_id:int,club_id:int,kind:str,amount_available:float)->dict[str,Any]:
        quoted=self.quote_vouchers(c,voucher_codes=voucher_codes,user_id=user_id,club_id=club_id,kind=kind,amount_available=amount_available)
        for a in quoted['applied']:
            cur=c.execute('''UPDATE benefit_redemptions SET status='held',held_checkout_id=?
                             WHERE id=? AND status='issued' AND user_id=?''',(intent_id,a['redemptionId'],user_id))
            if cur.rowcount != 1:
                raise ValueError(f"福利券刚刚已被占用: {a['voucherCode']}")
        return quoted

    def release_vouchers(self,c,*,intent_id:str):
        c.execute('''UPDATE benefit_redemptions SET status='issued',held_checkout_id=NULL
                     WHERE held_checkout_id=? AND status='held' ''',(intent_id,))

    def consume_vouchers(self,c,*,intent_id:str,order_kind:str,order_id:int):
        c.execute('''UPDATE benefit_redemptions SET status='used',used_at=CURRENT_TIMESTAMP,
                     used_order_kind=?,used_order_id=?,held_checkout_id=NULL
                     WHERE held_checkout_id=? AND status='held' ''',(order_kind,str(order_id),intent_id))

    def restore_order_vouchers(self,c,*,order_kind:str,order_id:int)->int:
        # Full refund restores the voucher itself. Original points remain spent because the user still owns the voucher.
        cur=c.execute('''UPDATE benefit_redemptions SET status='issued',used_at=NULL,used_order_kind=NULL,used_order_id=NULL
                         WHERE used_order_kind=? AND used_order_id=? AND status='used' ''',(order_kind,str(order_id)))
        return int(cur.rowcount or 0)

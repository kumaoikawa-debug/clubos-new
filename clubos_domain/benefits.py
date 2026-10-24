from __future__ import annotations
import json, secrets, datetime
from typing import Any


class BenefitEngine:
    """ClubOS benefit/voucher domain rules.

    - CLUB benefits spend Club Points and are funded by that club.
    - PLATFORM benefits spend Gear Points and are funded by ClubOS Platform.
    - Point redemption creates a voucher first; checkout later reserves/uses that voucher.
    - Open-source commerce handles cart/payment/order; ClubOS keeps funding ownership here.
    """

    # 通用券图模板（2026-10-24）：不做图片上传，后台建券时挑一款内置模板，
    # 平台端 / C 端 / 分享卡用同一套渲染，保证视觉一致且分享出去也好看。
    COUPON_IMAGE_KEYS = ('sunrise', 'forest', 'alpine', 'dusk', 'sand', 'mono')
    # 投放范围：全部俱乐部 / 全部 C 端用户 / 指定俱乐部 / 指定城市
    TARGET_TYPES = ('all_clubs', 'all_users', 'club', 'city')
    # 发放方式：后台直接发放 / 用户自己领取
    CLAIM_MODES = ('grant', 'claim')
    # 券形态：直接抵（无门槛）/ 满多少减多少
    COUPON_MODES = ('fixed', 'threshold')

    def __init__(self, wallet_snapshot):
        self.wallet_snapshot = wallet_snapshot

    @staticmethod
    def _now():
        return datetime.datetime.now()

    @staticmethod
    def _parse_dt(value):
        """把 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM' 解析成 datetime，解析不了返回 None。"""
        text = str(value or '').strip()
        if not text:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def _expired(self, benefit, now=None):
        """券是否已过期 / 未生效（valid_to / valid_from 都可空）。"""
        now = now or self._now()
        end = self._parse_dt((benefit or {}).get('valid_to'))
        if end and end < now:
            return True
        begin = self._parse_dt((benefit or {}).get('valid_from'))
        if begin and begin > now:
            return True
        return False

    def create_club_benefit(self, c, *, club_id:int, payload:dict[str,Any]) -> int:
        if str(payload.get('pointsType') or 'club').lower() != 'club':
            raise ValueError('俱乐部福利只能使用活动积分兑换')
        benefit_type=str(payload.get('benefitType') or 'gift')
        if benefit_type == 'gear_coupon':
            raise ValueError('俱乐部不能创建装备商城抵扣券；装备商城由总平台统一经营')
        return self._create(c, owner_type='CLUB', owner_club_id=club_id, target_club_id=club_id,
                            points_type='club', payload=payload)

    def create_platform_benefit(self, c, *, payload:dict[str,Any]) -> int:
        """总平台发券（2026-10-24 优惠券体系）。

        两种用途：活动报名券（activity_coupon）、装备购买券（gear_coupon）。
        两种形态：直接抵（无门槛，抵扣基数由后台自由设定）/ 满多少减多少。
        投放范围：全部俱乐部 / 全部 C 端用户 / 指定俱乐部 / 指定城市。
        成本一律由总平台承担（funding_owner=PLATFORM，结算时进平台补贴账）。
        """
        benefit_type = str(payload.get('benefitType') or '').strip()
        points_type = str(payload.get('pointsType') or '').strip().lower()
        if not benefit_type:
            benefit_type = 'activity_coupon' if points_type in ('activity', 'club') else 'gear_coupon'
        if points_type in ('activity', 'club'):
            points_type = 'club'
        elif points_type in ('gear', 'equipment', ''):
            points_type = 'gear'
        else:
            raise ValueError('券用途只能是活动报名或装备购买')
        if benefit_type not in ('activity_coupon', 'gear_coupon'):
            raise ValueError('平台只能发活动券或装备券')
        if benefit_type == 'activity_coupon' and points_type != 'club':
            points_type = 'club'
        if benefit_type == 'gear_coupon' and points_type != 'gear':
            points_type = 'gear'
        target = payload.get('targetClubId')
        return self._create(c, owner_type='PLATFORM', owner_club_id=None,
                            target_club_id=(int(target) if target not in (None, '') else None),
                            points_type=points_type, payload=dict(payload, benefitType=benefit_type))

    def _create(self,c,*,owner_type:str,owner_club_id:int|None,target_club_id:int|None,points_type:str,payload:dict[str,Any])->int:
        title=str(payload.get('title') or '').strip()
        if not title: raise ValueError('福利名称不能为空')
        cost=max(0,int(payload.get('pointsCost') or 0))
        # 平台券允许 0 积分门槛（真优惠券：直接抵或满减，成本平台承担）；
        # 俱乐部福利仍然是「用活动积分兑换」，必须有积分成本。
        if cost<=0 and owner_type!='PLATFORM':
            raise ValueError('兑换积分必须大于0')
        stock=payload.get('stock')
        stock=None if stock in (None,'') else max(0,int(stock))
        benefit_type=str(payload.get('benefitType') or 'gift')
        cash_value=max(0,float(payload.get('cashValue') or 0))
        if benefit_type in {'activity_coupon','gear_coupon'} and cash_value <= 0:
            raise ValueError('抵扣券的权益价值必须大于0')
        # 券形态：直接抵 / 满减（满减必须给门槛）
        mode=str(payload.get('mode') or 'fixed').strip()
        if mode not in self.COUPON_MODES: raise ValueError('券形态只能是直接抵或满减')
        threshold=max(0,float(payload.get('thresholdAmount') or 0))
        if mode=='threshold' and threshold<=0:
            raise ValueError('满减券必须设置使用门槛')
        if mode=='fixed': threshold=0
        # 投放范围
        target_type=str(payload.get('targetType') or ('club' if owner_type=='CLUB' else 'all_clubs')).strip()
        if target_type not in self.TARGET_TYPES: raise ValueError('投放范围不合法')
        target_city=str(payload.get('targetCity') or '').strip() or None
        if target_type=='city' and not target_city:
            raise ValueError('按城市投放必须选择城市')
        if target_type=='club' and target_club_id is None:
            raise ValueError('定向俱乐部投放必须选择俱乐部')
        claim_mode=str(payload.get('claimMode') or 'grant').strip()
        if claim_mode not in self.CLAIM_MODES: raise ValueError('发放方式不合法')
        image_key=str(payload.get('imageKey') or 'sunrise').strip()
        if image_key not in self.COUPON_IMAGE_KEYS: image_key='sunrise'
        per_user_limit=max(1,int(payload.get('perUserLimit') or 1))
        valid_from=str(payload.get('validFrom') or '').strip() or None
        valid_to=str(payload.get('validTo') or '').strip() or None
        share_enabled=0 if payload.get('shareEnabled') is False else 1
        c.execute('''INSERT INTO member_benefits(owner_type,owner_club_id,target_club_id,title,description,
                     benefit_type,points_type,points_cost,cash_value,stock,status,rules_json,
                     mode,threshold_amount,image_key,valid_from,valid_to,claim_mode,
                     target_type,target_city,per_user_limit,claimed_count,share_enabled)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            owner_type,owner_club_id,target_club_id,title,payload.get('description'),benefit_type,
            points_type,cost,cash_value,stock,payload.get('status','active'),
            json.dumps(payload.get('rules') or {},ensure_ascii=False),
            mode,threshold,image_key,valid_from,valid_to,claim_mode,
            target_type,target_city,per_user_limit,0,share_enabled))
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
            # 平台券既能用装备积分换（装备券），也能用活动积分换（活动券，
            # 2026-10-24 起总平台可以发活动报名券）—— 这里不能只认 gear。
            if b['points_type'] not in ('gear', 'club'): raise ValueError('福利积分类型配置错误')
        if b['stock'] is not None and int(b['stock'])<=0:
            raise OverflowError('福利已兑完')
        points=int(b['points_cost'])
        if b['points_type']=='club':
            wallet=c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',(display_club_id,user_id)).fetchone()
            if not wallet or int(wallet[0] or 0)<points: raise ValueError('活动积分不足')
            c.execute('UPDATE club_members SET club_points_balance=club_points_balance-? WHERE club_id=? AND user_id=?',(points,display_club_id,user_id))
            c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                      (display_club_id,user_id,'benefit_redeem',-points,'member_benefit',str(benefit_id),
                       '会员福利兑换；成本由俱乐部承担' if b['owner_type']=='CLUB' else '活动积分兑换平台券；券面值成本由总平台承担'))
            # 平台券的面值成本仍归总平台（funding=PLATFORM），但活动积分是俱乐部的资产，
            # 兑换时扣俱乐部的积分 —— 两件事各记各的账。
            funding='CLUB' if b['owner_type']=='CLUB' else 'PLATFORM'
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
        # 券包是「用户维度的钱包」，不再按俱乐部切：平台券可以跨俱乐部用，
        # 定向发到某俱乐部的券也该在用户自己的券包里看到。
        return [dict(r) for r in c.execute('''SELECT r.*,b.title,b.description,b.benefit_type,b.owner_type,b.target_club_id,
                     b.mode,b.threshold_amount,b.image_key,b.valid_from,b.valid_to,b.share_enabled,b.target_type,b.target_city
                    FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                    WHERE r.user_id=? ORDER BY r.id DESC''',(user_id,)).fetchall()]

    def eligible_vouchers(self,c,*,user_id:int,club_id:int,kind:str)->list[dict[str,Any]]:
        kind=str(kind).lower()
        if kind not in {'activity','gear'}: raise ValueError('kind must be activity or gear')
        benefit_type='activity_coupon' if kind=='activity' else 'gear_coupon'
        rows=c.execute('''SELECT r.*,b.title,b.description,b.benefit_type,b.owner_type,b.owner_club_id,b.target_club_id,
                                 b.mode,b.threshold_amount,b.image_key,b.valid_from,b.valid_to,
                                 b.target_type,b.target_city,b.share_enabled
                          FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                          WHERE r.user_id=? AND r.status='issued' AND b.status='active' AND b.benefit_type=?
                          ORDER BY r.id DESC''',(user_id,benefit_type)).fetchall()
        out=[]
        for rr in rows:
            x=dict(rr)
            if self._expired(x):
                continue  # 过期 / 未生效的券不参与报价
            if kind=='activity':
                if x['owner_type']=='CLUB':
                    # 俱乐部券仍然只在本俱乐部报名时能用（成本由该俱乐部承担）。
                    if int(x['owner_club_id']) != int(club_id):
                        continue
                    if int(x['display_club_id']) != int(club_id):
                        continue
                else:
                    # 平台活动券：默认全平台通用（成都区域各俱乐部都能用）。
                    # 指定了俱乐部的券只在该俱乐部有效；定向发到某俱乐部的券同理。
                    if x['target_club_id'] is not None and int(x['target_club_id']) != int(club_id):
                        continue
                    if int(x['display_club_id'] or 0) > 0 and int(x['display_club_id']) != int(club_id):
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
            threshold=max(0.0,float(v.get('threshold_amount') or 0))
            if threshold>0 and remaining < threshold:
                raise ValueError(f'该券需订单满 ¥{threshold:.0f} 才能使用')
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

    # ── 优惠券：定向投放 / 领取 / 群发 / 统计（2026-10-24）─────────────────
    # 用户身份只认 user_id + 注册城市；俱乐部归属在「定向某俱乐部」时才参与判定。

    def coupon_scope_ok(self, benefit, *, city=None, club_id=None) -> bool:
        """投放范围判定：圈人用（领取时校验一次，发到手里的券不再复查城市）。"""
        target_type = str((benefit or {}).get('target_type') or 'all_clubs')
        if target_type == 'club':
            tc = (benefit or {}).get('target_club_id')
            return tc is not None and club_id is not None and int(tc) == int(club_id)
        if target_type == 'city':
            tc = str((benefit or {}).get('target_city') or '').strip()
            return bool(tc) and bool(city) and tc == str(city).strip()
        # all_users / all_clubs：不做城市限制
        return True

    def _user_claimed_count(self, c, benefit_id: int, user_id: int) -> int:
        return int(c.execute("SELECT COUNT(*) FROM benefit_redemptions WHERE benefit_id=? AND user_id=?",
                             (benefit_id, user_id)).fetchone()[0] or 0)

    def _coupon_row(self, c, benefit_id: int):
        r = c.execute('SELECT * FROM member_benefits WHERE id=?', (benefit_id,)).fetchone()
        return dict(r) if r else None

    def claimable_coupons(self, c, *, user_id: int, city=None, club_id=None) -> list[dict[str, Any]]:
        """C 端「领券中心」：当前用户能领的券。"""
        rows = c.execute("""SELECT * FROM member_benefits
                            WHERE owner_type='PLATFORM' AND status='active' AND claim_mode='claim'
                              AND benefit_type IN ('activity_coupon','gear_coupon')
                            ORDER BY id DESC""").fetchall()
        out = []
        for r in rows:
            b = dict(r)
            if self._expired(b):
                continue
            if not self.coupon_scope_ok(b, city=city, club_id=club_id):
                continue
            stock = b['stock']
            if stock is not None and int(stock) <= 0:
                continue
            if self._user_claimed_count(c, b['id'], user_id) >= int(b['per_user_limit'] or 1):
                continue
            b['remain'] = None if stock is None else int(stock)
            b['claimedCount'] = int(b['claimed_count'] or 0)
            out.append(b)
        return out

    def _issue(self, c, benefit: dict[str, Any], user_id: int, display_club_id: int) -> dict[str, Any]:
        """发一张券到用户券包（积分成本为 0，成本由总平台承担）。"""
        code = 'CPN-' + secrets.token_hex(4).upper()
        c.execute("""INSERT INTO benefit_redemptions(benefit_id,user_id,display_club_id,point_type,points_spent,
                     funding_owner,cash_value,status,voucher_code,gift_note) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (int(benefit['id']), int(user_id), (int(display_club_id) if display_club_id else None),
                    str(benefit.get('points_type') or 'gear'), 0, 'PLATFORM',
                    float(benefit.get('cash_value') or 0), 'issued', code,
                    '总平台发放；成本由总平台承担'))
        rid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        c.execute('UPDATE member_benefits SET claimed_count=claimed_count+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                  (int(benefit['id']),))
        if benefit.get('stock') is not None:
            c.execute('UPDATE member_benefits SET stock=stock-1 WHERE id=?', (int(benefit['id']),))
        return {'redemptionId': rid, 'voucherCode': code, 'title': benefit.get('title'),
                'cashValue': float(benefit.get('cash_value') or 0), 'fundingOwner': 'PLATFORM',
                'benefitType': benefit.get('benefit_type'), 'imageKey': benefit.get('image_key')}

    def claim_coupon(self, c, *, benefit_id: int, user_id: int, city=None, club_id=None) -> dict[str, Any]:
        """用户自己领券（城市 / 俱乐部定向投放都走这里）。"""
        b = self._coupon_row(c, benefit_id)
        if not b or b['status'] != 'active':
            raise ValueError('优惠券不存在或已下架')
        if str(b.get('claim_mode') or 'grant') != 'claim':
            raise ValueError('该券由总平台定向发放，不可自行领取')
        if b['owner_type'] != 'PLATFORM':
            raise ValueError('只有总平台发的券可以公开领取')
        if self._expired(b):
            raise ValueError('该券已过期或尚未开始')
        if not self.coupon_scope_ok(b, city=city, club_id=club_id):
            raise ValueError('该券不在你的领取范围内')
        if b['stock'] is not None and int(b['stock']) <= 0:
            raise ValueError('该券已领完')
        if self._user_claimed_count(c, b['id'], user_id) >= int(b['per_user_limit'] or 1):
            raise ValueError('每人限领 %d 张' % int(b['per_user_limit'] or 1))
        # 定向俱乐部的券记在该俱乐部名下，其余（城市 / 全平台）记 0 = 平台直发，
        # display_club_id=0 的平台券在所有俱乐部都能用。
        return self._issue(c, b, user_id, int(b.get('target_club_id') or 0))

    def grant_coupon(self, c, *, benefit_id: int, user_ids: list[int], club_id: int = 0) -> dict[str, Any]:
        """后台群发：给一批用户各发一张（成本总平台承担）。"""
        b = self._coupon_row(c, benefit_id)
        if not b or b['status'] != 'active':
            raise ValueError('优惠券不存在或已下架')
        if b['owner_type'] != 'PLATFORM':
            raise ValueError('只有总平台可以定向发券')
        if self._expired(b):
            raise ValueError('该券已过期或尚未开始')
        issued = 0
        skipped = 0
        display = int(b.get('target_club_id') or club_id or 0)
        for uid in user_ids or []:
            uid = int(uid)
            if self._user_claimed_count(c, b['id'], uid) >= int(b['per_user_limit'] or 1):
                skipped += 1
                continue
            if b['stock'] is not None and int(b['stock']) <= 0:
                skipped += 1
                continue
            self._issue(c, b, uid, display)
            issued += 1
        return {'issued': issued, 'skipped': skipped}

    def coupon_stats(self, c, benefit_id: int) -> dict[str, Any]:
        b = self._coupon_row(c, benefit_id)
        if not b:
            return {}
        total = None if b['stock'] is None else int(b['stock'])
        used = int(c.execute("SELECT COUNT(*) FROM benefit_redemptions WHERE benefit_id=? AND status='used'",
                             (benefit_id,)).fetchone()[0] or 0)
        held = int(c.execute("SELECT COUNT(*) FROM benefit_redemptions WHERE benefit_id=? AND status='held'",
                             (benefit_id,)).fetchone()[0] or 0)
        issued = int(c.execute("SELECT COUNT(*) FROM benefit_redemptions WHERE benefit_id=?",
                               (benefit_id,)).fetchone()[0] or 0)
        return {'stock': total, 'issued': issued, 'used': used, 'held': held,
                'remaining': (None if total is None else max(0, total - issued)),
                'claimedCount': int(b['claimed_count'] or 0),
                'perUserLimit': int(b['per_user_limit'] or 1)}

    def coupon_by_code(self, c, code: str):
        """分享用：按券码查券（只暴露展示信息，不带领取者隐私）。"""
        r = c.execute("""SELECT r.voucher_code,r.status,r.cash_value,r.created_at,r.used_at,
                                b.title,b.description,b.benefit_type,b.mode,b.threshold_amount,b.image_key,
                                b.valid_from,b.valid_to,b.share_enabled,b.target_type,b.target_city,b.owner_type
                         FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                         WHERE r.voucher_code=?""", (str(code or '').strip(),)).fetchone()
        return dict(r) if r else None

    def restore_order_vouchers(self,c,*,order_kind:str,order_id:int)->int:
        # Full refund restores the voucher itself. Original points remain spent because the user still owns the voucher.
        cur=c.execute('''UPDATE benefit_redemptions SET status='issued',used_at=NULL,used_order_kind=NULL,used_order_id=NULL
                         WHERE used_order_kind=? AND used_order_id=? AND status='used' ''',(order_kind,str(order_id)))
        return int(cur.rowcount or 0)

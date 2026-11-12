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
    # 券形态：直接抵（无门槛）/ 满多少减多少 / 折扣券（2026-10-26，订单打 N 折）
    COUPON_MODES = ('fixed', 'threshold', 'discount')

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
        # #860 券与积分解耦：券就是券，建券不再要求「用积分兑换」。
        # points_cost 列保留只是为了兼容存量数据与历史发券记录（存量测试仍可传值），
        # 新建券不传即 0；「用积分换券」统一走积分商城，积分价由老板在
        # points_mall_items.points_cost 自定义，与券本身无关。
        stock=payload.get('stock')
        stock=None if stock in (None,'') else max(0,int(stock))
        benefit_type=str(payload.get('benefitType') or 'gift')
        # 适用品类（2026-11-11 营地 v4 S5）：activity=活动报名 / camp=营地消费 / all=通用。
        # 营地商品复用「活动券」这一券种（benefit_type='activity_coupon'），靠 applies_to 区分适用范围，
        # 这样俱乐部既能发「活动专用券」，也能发「营地专用券」或「通用券」，互不串味。
        applies_to=str(payload.get('appliesTo') or 'activity').strip().lower()
        # #860 扩展：product=指定单个商品 / category=指定商品分类。
        # 这两个值只说明「大类」，具体指定到哪一个对象由 scope_ref_kind + scope_ref_id 决定。
        if applies_to not in ('activity', 'camp', 'all', 'product', 'category'):
            raise ValueError('适用范围只能是全平台通用、活动报名、营地消费、指定商品或指定分类')
        # 券形态：直接抵 / 满减 / 折扣（2026-10-26）
        # 折扣券只允许活动报名券 —— 券的适用边界就是俱乐部活动，不涉及装备商城。
        mode=str(payload.get('mode') or 'fixed').strip()
        if mode not in self.COUPON_MODES: raise ValueError('券形态只能是直接抵、满减或折扣')
        threshold=max(0,float(payload.get('thresholdAmount') or 0))
        discount_percent=0.0
        if mode=='discount':
            if benefit_type!='activity_coupon':
                raise ValueError('折扣券只支持活动报名使用，不能涉及装备')
            discount_percent=float(payload.get('discountPercent') or 0)
            if not (1 <= discount_percent < 100):
                raise ValueError('折扣力度必须在 1~99 之间（如 90 表示 9 折）')
        if mode=='threshold' and threshold<=0:
            raise ValueError('满减券必须设置使用门槛')
        if mode=='fixed': threshold=0
        cash_value=max(0,float(payload.get('cashValue') or 0))
        if benefit_type in {'activity_coupon','gear_coupon'} and mode!='discount' and cash_value <= 0:
            raise ValueError('抵扣券的权益价值必须大于0')
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
        # 券面自定义（2026-10-26）：{c1,c2,ink} 手填 #RRGGBB，空 = 跟随内置模板配色。
        face=payload.get('face') if isinstance(payload.get('face'),dict) else None
        face_json=None
        if face:
            import re as _re
            def _hex(v):
                v=str(v or '').strip()
                return v.upper() if _re.fullmatch(r'#[0-9a-fA-F]{6}', v) else None
            face_json=json.dumps({k:_hex(face.get(k)) for k in ('c1','c2','ink') if _hex(face.get(k))},
                                 ensure_ascii=False) if any(_hex(face.get(k)) for k in ('c1','c2','ink')) else None
        # ── #856 Word 设计稿字段（2026-11-12，严格照搬展示字段，纯存储/展示，不参与计费逻辑）──
        store_scope=str(payload.get('storeScope') or 'this_club').strip().lower()
        if store_scope not in ('this_club','all_clubs','specified'):
            store_scope='this_club'
        _dm=payload.get('diningModes')
        if isinstance(_dm,list):
            dining_modes_json=json.dumps(_dm,ensure_ascii=False)
        elif isinstance(_dm,str) and _dm.strip():
            dining_modes_json=_dm.strip()
        else:
            dining_modes_json=None
        wechat_delivery=1 if payload.get('wechatDelivery') else 0
        discount_coupon_type=str(payload.get('discountCouponType') or 'single').strip().lower()
        if discount_coupon_type not in ('single','stored'):
            discount_coupon_type='single'
        _mda=payload.get('maxDiscountAmount')
        max_discount_amount=None if _mda in (None,'') else float(_mda)
        threshold_scope=str(payload.get('thresholdScope') or 'all').strip().lower()
        if threshold_scope not in ('all','category','product'):
            threshold_scope='all'
        deduction_scope=str(payload.get('deductionScope') or 'all').strip().lower()
        if deduction_scope not in ('all','category','product'):
            deduction_scope='all'
        redeemable_product_type=str(payload.get('redeemableProductType') or 'all').strip().lower()
        if redeemable_product_type not in ('all','category','specified'):
            redeemable_product_type='all'
        selected_product=str(payload.get('selectedProduct') or '').strip() or None
        # ── #860 真实适用范围：下拉选出来的必须是库里真实存在的对象 ID ──────────────
        scope_ref_kind=str(payload.get('scopeRefKind') or '').strip().lower()
        if scope_ref_kind not in ('','activity','camp','pitch','product','category'):
            scope_ref_kind=''
        _srid=payload.get('scopeRefId')
        scope_ref_id=None
        try:
            if _srid not in (None,'',0,'0'): scope_ref_id=int(_srid)
        except (TypeError,ValueError):
            scope_ref_id=None
        if scope_ref_id and not scope_ref_kind:
            scope_ref_kind={'activity':'activity','camp':'camp','product':'product',
                            'category':'category'}.get(applies_to,'')
        if not scope_ref_id: scope_ref_kind=''
        # 指定商品/分类时，applies_to 必须与对象类型对齐，避免「选了商品但大类写通用」。
        if scope_ref_kind in ('product','category') and applies_to not in ('product','category','all'):
            applies_to=scope_ref_kind
        # 多营地适用：只有老板名下有多家营地时才会有 specified，存 JSON 数组。
        camp_scope=str(payload.get('campScope') or 'all').strip().lower()
        if camp_scope not in ('all','specified'): camp_scope='all'
        _cids=payload.get('campScopeIds')
        if isinstance(_cids,str):
            try: _cids=json.loads(_cids)
            except Exception: _cids=[]
        if not isinstance(_cids,list): _cids=[]
        _camp_ids=[]
        for _x in _cids:
            try: _camp_ids.append(int(_x))
            except (TypeError,ValueError): pass
        camp_scope_ids=json.dumps(_camp_ids,ensure_ascii=False) if _camp_ids else None
        if camp_scope=='specified' and not camp_scope_ids: camp_scope='all'
        # 有效期：fixed 沿用模板上的 valid_from/valid_to；relative 在发券时按领券日折算。
        validity_type=str(payload.get('validityType') or 'fixed').strip().lower()
        if validity_type not in ('fixed','relative'): validity_type='fixed'
        valid_days=max(0,int(payload.get('validDays') or 0))
        if validity_type=='relative' and valid_days<=0:
            raise ValueError('「领券后 N 天内可用」必须填写有效天数')
        if validity_type=='relative': valid_from=None; valid_to=None
        # 领用限制
        claim_audience=str(payload.get('claimAudience') or 'all').strip().lower()
        if claim_audience not in ('all','member','non_member','new_user'): claim_audience='all'
        per_user_daily_limit=max(0,int(payload.get('perUserDailyLimit') or 0))
        _nu=payload.get('newUserOnly')
        new_user_only=1 if (_nu in (True,1,'1','true','是','yes') or claim_audience=='new_user') else 0
        c.execute('''INSERT INTO member_benefits(owner_type,owner_club_id,target_club_id,title,description,
                     benefit_type,applies_to,points_type,points_cost,cash_value,stock,status,rules_json,
                     mode,discount_percent,threshold_amount,image_key,valid_from,valid_to,claim_mode,
                     target_type,target_city,per_user_limit,claimed_count,share_enabled,face_json,
                     store_scope,dining_modes,wechat_delivery,discount_coupon_type,max_discount_amount,
                     threshold_scope,deduction_scope,redeemable_product_type,selected_product,
                     scope_ref_kind,scope_ref_id,camp_scope,camp_scope_ids,validity_type,valid_days,
                     claim_audience,per_user_daily_limit,new_user_only)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                            ?,?,?,?,?,?,?,?,?)''',(
            owner_type,owner_club_id,target_club_id,title,payload.get('description'),benefit_type,
            applies_to,
            points_type,cost,cash_value,stock,payload.get('status','active'),
            json.dumps(payload.get('rules') or {},ensure_ascii=False),
            mode,discount_percent,threshold,image_key,valid_from,valid_to,claim_mode,
            target_type,target_city,per_user_limit,0,share_enabled,face_json,
            store_scope,dining_modes_json,wechat_delivery,discount_coupon_type,max_discount_amount,
            threshold_scope,deduction_scope,redeemable_product_type,selected_product,
            scope_ref_kind,scope_ref_id,camp_scope,camp_scope_ids,validity_type,valid_days,
            claim_audience,per_user_daily_limit,new_user_only))
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
        # #860 券与积分解耦：新券 points_cost 固定为 0，绝不能从这个老「积分兑换福利」
        # 入口被零成本反复兑换（只受 stock 限制会变成无限刷券）。存量带积分价的
        # 福利/券保持兼容；新券请用「领券中心」或「积分商城」上架后兑换。
        if (str(b.get('benefit_type') or '') in ('activity_coupon','gear_coupon')
                and int(b.get('points_cost') or 0)==0):
            raise ValueError('该优惠券不支持积分直接兑换，请在领券中心领取或到积分商城兑换')
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
        # #860：老「积分兑换会员福利」入口也要写相对效期实发日期。
        # 不写的话模板日期被清空（_create 对 relative 券置 None），这张券会永不过期。
        ivf,ivt=self.relative_validity_window(b)
        c.execute('''INSERT INTO benefit_redemptions(benefit_id,user_id,display_club_id,point_type,points_spent,
                     funding_owner,cash_value,status,voucher_code,issued_valid_from,issued_valid_to)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (benefit_id,user_id,display_club_id,b['points_type'],points,funding,float(b['cash_value'] or 0),'issued',code,ivf,ivt))
        rid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        return {'redemptionId':rid,'voucherCode':code,'title':b['title'],'pointsType':b['points_type'],
                'pointsSpent':points,'fundingOwner':funding,'benefitType':b['benefit_type'],'cashValue':float(b['cash_value'] or 0)}

    def user_redemptions(self,c,*,user_id:int,club_id:int)->list[dict[str,Any]]:
        # 券包是「用户维度的钱包」，不再按俱乐部切：平台券可以跨俱乐部用，
        # 定向发到某俱乐部的券也该在用户自己的券包里看到。
        # #860：券包展示要用「这张券实发时算出来的日期」，相对效期券的模板日期是空的，
        # 直接展示 b.valid_from/valid_to 会显示成无有效期；同时下发适用范围字段供 C 端标注。
        return [dict(r) for r in c.execute('''SELECT r.*,b.title,b.description,b.benefit_type,b.owner_type,b.target_club_id,
                     b.mode,b.discount_percent,b.threshold_amount,b.image_key,
                     COALESCE(r.issued_valid_from,b.valid_from) AS valid_from,
                     COALESCE(r.issued_valid_to,b.valid_to) AS valid_to,
                     b.share_enabled,b.target_type,b.target_city,b.face_json,
                     b.applies_to,b.scope_ref_kind,b.scope_ref_id,b.camp_scope,b.camp_scope_ids,
                     b.validity_type,b.valid_days,b.claim_audience,b.per_user_daily_limit,b.new_user_only
                    FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                    WHERE r.user_id=? ORDER BY r.id DESC''',(user_id,)).fetchall()]

    @staticmethod
    def _scope_ids(ctx,key):
        """把 context 里的单个 ID 或 ID 列表统一成 set[int]（脏值直接丢弃）。"""
        v=(ctx or {}).get(key)
        if v is None: return set()
        if not isinstance(v,(list,tuple,set)): v=[v]
        out=set()
        for i in v:
            try:
                if i not in (None,'',0,'0'): out.add(int(i))
            except (TypeError,ValueError): pass
        return out

    def coupon_scope_matches(self,x,*,kind:str,context:dict|None)->bool:
        """#860：券的「适用范围」是否覆盖当前订单。

        context 形如 {'activityId','campId','pitchIds','productIds','categoryIds'}。
        ★ 这是纯函数，报价与核销共用同一份判据；调用方不传 context 时只按大类过滤
        （保持 /vouchers 列表等旧调用的行为完全不变），传了才做「指定对象」严格匹配。
        """
        ctx=context or {}
        at=str(x.get('applies_to') or 'activity').lower()
        if at not in ('activity','camp','all','product','category'): at='activity'
        # 1) 大类：保持旧语义（指定商品/分类的券允许跨 activity/gear/camp，靠下面的对象匹配收口）
        if at=='activity' and kind=='camp': return False
        if at=='camp' and kind=='activity': return False
        # 2) 多营地适用：只在「确实带了营地维度」时才收口。
        #    活动报名 / 装备订单不下发 campIds，此时不能因为券配了多营地就把它否掉，
        #    否则「全平台通用 + 多营地」的券在活动/装备结算里会莫名不可用。
        if str(x.get('camp_scope') or 'all').lower()=='specified' and kind=='camp':
            try: allowed=set(int(i) for i in json.loads(x.get('camp_scope_ids') or '[]'))
            except Exception: allowed=set()
            camps=self._scope_ids(ctx,'campIds')|self._scope_ids(ctx,'campId')
            if not allowed or not (allowed & camps): return False
        # 3) 指定到真实对象：必须有 context 才判；没传 context 时只按大类（向后兼容）
        rk=str(x.get('scope_ref_kind') or '').lower()
        rid=x.get('scope_ref_id')
        if not rk or not rid or not context: return True
        try: rid=int(rid)
        except (TypeError,ValueError): return True
        if rk=='activity':  return rid in (self._scope_ids(ctx,'activityIds')|self._scope_ids(ctx,'activityId'))
        if rk=='camp':      return rid in (self._scope_ids(ctx,'campIds')|self._scope_ids(ctx,'campId'))
        if rk=='pitch':     return rid in self._scope_ids(ctx,'pitchIds')
        if rk=='product':   return rid in self._scope_ids(ctx,'productIds')
        if rk=='category':  return rid in self._scope_ids(ctx,'categoryIds')
        return True

    def eligible_vouchers(self,c,*,user_id:int,club_id:int,kind:str,context:dict|None=None)->list[dict[str,Any]]:
        kind=str(kind).lower()
        if kind not in {'activity','gear','camp'}: raise ValueError('kind must be activity, gear or camp')
        # 营地商品复用「活动券」这一券种（benefit_type='activity_coupon'），
        # 靠 applies_to 区分适用范围：camp 订单只能用 applies_to∈{camp,all} 的券。
        benefit_type='gear_coupon' if kind=='gear' else 'activity_coupon'
        rows=c.execute('''SELECT r.*,b.title,b.description,b.benefit_type,b.owner_type,b.owner_club_id,b.target_club_id,
                                 b.mode,b.discount_percent,b.threshold_amount,b.image_key,
                                 b.valid_from AS benefit_valid_from,b.valid_to AS benefit_valid_to,
                                 b.target_type,b.target_city,b.share_enabled,b.face_json,b.applies_to,
                                 b.scope_ref_kind,b.scope_ref_id,b.camp_scope,b.camp_scope_ids,b.validity_type,
                                 b.max_discount_amount
                          FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                          WHERE r.user_id=? AND r.status='issued' AND b.status='active' AND b.benefit_type=?
                          ORDER BY r.id DESC''',(user_id,benefit_type)).fetchall()
        out=[]
        for rr in rows:
            x=dict(rr)
            # #860 相对效期：优先用「这张券实发时算出来的日期」，没有才回落到模板日期。
            x['valid_from']=x.get('issued_valid_from') or x.get('benefit_valid_from')
            x['valid_to']=x.get('issued_valid_to') or x.get('benefit_valid_to')
            if self._expired(x):
                continue  # 过期 / 未生效的券不参与报价
            # 适用范围过滤（2026-11-11 营地 v4 S5 + #860 指定对象）
            at=str(x.get('applies_to') or 'activity').lower()
            if at not in ('activity','camp','all','product','category'):
                at='activity'
            if at in ('product','category'):
                # 指定商品/分类的券跨品类有效，但必须命中订单里的真实对象
                if context and not self.coupon_scope_matches(x,kind=kind,context=context):
                    continue
            else:
                if kind=='camp' and at not in ('camp','all'):
                    continue
                if kind=='activity' and at not in ('activity','all'):
                    continue
                if not self.coupon_scope_matches(x,kind=kind,context=context):
                    continue
            if kind=='gear':
                # Gear storefront is platform-owned. Only platform-funded gear coupons can reduce gear orders.
                if x['owner_type']!='PLATFORM':
                    continue
                if x['target_club_id'] is not None and int(x['target_club_id']) != int(club_id):
                    continue
                out.append(x)
                continue
            # activity / camp 共用同一套活动券，沿用原有俱乐部归属限制
            if x['owner_type']=='CLUB':
                # 俱乐部券仍然只在本俱乐部报名 / 营地消费时能用（成本由该俱乐部承担）。
                if int(x['owner_club_id']) != int(club_id):
                    continue
                if int(x.get('display_club_id') or 0) != int(club_id):
                    continue
            else:
                # 平台活动券：默认全平台通用（成都区域各俱乐部都能用）。
                # 指定了俱乐部的券只在该俱乐部有效；定向发到某俱乐部的券同理。
                if x['target_club_id'] is not None and int(x['target_club_id']) != int(club_id):
                    continue
                if int(x.get('display_club_id') or 0) > 0 and int(x.get('display_club_id')) != int(club_id):
                    continue
            out.append(x)
        return out

    def quote_vouchers(self,c,*,voucher_codes:list[str],user_id:int,club_id:int,kind:str,amount_available:float,context:dict|None=None)->dict[str,Any]:
        codes=[]
        for code in voucher_codes or []:
            code=str(code or '').strip()
            if code and code not in codes: codes.append(code)
        if not codes:
            return {'clubDiscount':0.0,'platformSubsidy':0.0,'totalDiscount':0.0,'applied':[]}
        eligible={x['voucher_code']:x for x in self.eligible_vouchers(c,user_id=user_id,club_id=club_id,kind=kind,context=context)}
        remaining=max(0.0,float(amount_available or 0))
        club_discount=0.0; platform_subsidy=0.0; applied=[]
        for code in codes:
            v=eligible.get(code)
            if not v:
                raise ValueError(f'福利券不可用或已被使用: {code}')
            threshold=max(0.0,float(v.get('threshold_amount') or 0))
            if threshold>0 and remaining < threshold:
                raise ValueError(f'该券需订单满 ¥{threshold:.0f} 才能使用')
            if str(v.get('mode') or 'fixed')=='discount':
                # 折扣券（2026-10-26）：按当前未抵扣余额打 N 折，立减 10% 的那部分。
                # 一张折扣券只对剩余金额生效一次，剩余金额继续参与后续券的满减判断。
                dp=max(1.0,min(99.0,float(v.get('discount_percent') or 0)))
                value=min(remaining,round(remaining*(100.0-dp)/100.0,2))
                # #860：后台可给折扣券设「最高抵扣金额」，0 / 空 / NULL = 不封顶。
                # 之前 SELECT 了该列却没用，导致设置了上限的券实际按全额折扣计算。
                mda=v.get('max_discount_amount')
                if mda not in (None,'',0,'0'):
                    try: value=min(value,max(0.0,float(mda)))
                    except (TypeError,ValueError): pass
            else:
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

    def hold_vouchers(self,c,*,intent_id:str,voucher_codes:list[str],user_id:int,club_id:int,kind:str,amount_available:float,context:dict|None=None)->dict[str,Any]:
        quoted=self.quote_vouchers(c,voucher_codes=voucher_codes,user_id=user_id,club_id=club_id,kind=kind,amount_available=amount_available,context=context)
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

    @staticmethod
    def _claim_audience_state(c, user_id:int):
        """#860 领取人群判定。新用户 = 活动报名 / 装备订单 / 营地订单三张表都没有记录。
        用「有没有下过单」而不是注册天数，口径确定、可离线复现，也不会因时间流逝漂移。"""
        uid=int(user_id)
        is_member=bool(c.execute('SELECT 1 FROM club_members WHERE user_id=? LIMIT 1',(uid,)).fetchone())
        has_order=any(bool(c.execute('SELECT 1 FROM %s WHERE user_id=? LIMIT 1'%t,(uid,)).fetchone())
                      for t in ('registrations','gear_orders','camp_orders'))
        return is_member, not has_order

    def _claim_limit_error(self, c, b, user_id:int):
        """#860 领用限制统一判据：返回 None=可领，否则是给顾客的拒绝原因。
        claimable_coupons（列表）与 claim_coupon（提交）共用，避免「列表能看见、点进去被拒」。"""
        aud=str(b.get('claim_audience') or 'all').lower()
        new_only=int(b.get('new_user_only') or 0)
        if aud!='all' or new_only:
            is_member,is_new=self._claim_audience_state(c,user_id)
            if new_only and not is_new: return '该券仅限新用户领取'
            if aud=='member' and not is_member: return '该券仅限会员领取'
            if aud=='non_member' and is_member: return '该券仅限非会员领取'
            if aud=='new_user' and not is_new: return '该券仅限新用户领取'
        daily=int(b.get('per_user_daily_limit') or 0)
        if daily>0:
            n=int(c.execute("""SELECT COUNT(*) FROM benefit_redemptions
                               WHERE benefit_id=? AND user_id=? AND date(created_at)=date('now')""",
                            (int(b['id']),int(user_id))).fetchone()[0] or 0)
            if n>=daily: return '每人每日限领 %d 张'%daily
        return None

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
            # #860 领取人群 / 每人每日限领 / 仅新用户：列表与提交同一判据
            if self._claim_limit_error(c, b, user_id):
                continue
            b['remain'] = None if stock is None else int(stock)
            b['claimedCount'] = int(b['claimed_count'] or 0)
            out.append(b)
        return out

    @staticmethod
    def relative_validity_window(benefit: dict[str, Any]):
        """#860「领券后 N 天内可用」：把相对效期折算成这张券实发当天的绝对起止时刻。

        返回 (issued_valid_from, issued_valid_to)；固定效期券返回 (None, None)。
        失效时刻固定到当天 23:59:59，否则只写 YYYY-MM-DD 会被 _parse_dt 当成 00:00
        而当天即过期。★ 所有往 benefit_redemptions 写行的入口都必须调用本函数，
        漏掉一处就会让相对效期券变成永不过期券（模板日期被清空 + 回落拿不到）。
        """
        if str((benefit or {}).get('validity_type') or 'fixed').lower()!='relative':
            return None, None
        try: days=max(1,int((benefit or {}).get('valid_days') or 0))
        except (TypeError,ValueError): days=1
        d0=datetime.date.today()
        return (d0.strftime('%Y-%m-%d 00:00:00'),
                (d0+datetime.timedelta(days=days-1)).strftime('%Y-%m-%d 23:59:59'))

    def _issue(self, c, benefit: dict[str, Any], user_id: int, display_club_id: int,
               *, funding: str = 'PLATFORM', note: str | None = None) -> dict[str, Any]:
        """发一张券到用户券包（积分成本为 0；平台券成本总平台承担，俱乐部券成本俱乐部承担）。"""
        funding = 'CLUB' if funding == 'CLUB' else 'PLATFORM'
        code = 'CPN-' + secrets.token_hex(4).upper()
        ivf,ivt=self.relative_validity_window(benefit)
        c.execute("""INSERT INTO benefit_redemptions(benefit_id,user_id,display_club_id,point_type,points_spent,
                     funding_owner,cash_value,status,voucher_code,gift_note,issued_valid_from,issued_valid_to)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (int(benefit['id']), int(user_id), (int(display_club_id) if display_club_id else None),
                    str(benefit.get('points_type') or 'gear'), 0, funding,
                    float(benefit.get('cash_value') or 0), 'issued', code,
                    note or ('总平台发放；成本由总平台承担' if funding == 'PLATFORM' else '俱乐部发放；成本由俱乐部承担'),
                    ivf, ivt))
        rid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        c.execute('UPDATE member_benefits SET claimed_count=claimed_count+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                  (int(benefit['id']),))
        if benefit.get('stock') is not None:
            c.execute('UPDATE member_benefits SET stock=stock-1 WHERE id=?', (int(benefit['id']),))
        return {'redemptionId': rid, 'voucherCode': code, 'title': benefit.get('title'),
                'cashValue': float(benefit.get('cash_value') or 0), 'fundingOwner': funding,
                'benefitType': benefit.get('benefit_type'), 'imageKey': benefit.get('image_key'),
                'mode': benefit.get('mode'), 'discountPercent': float(benefit.get('discount_percent') or 0)}

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
        _lim=self._claim_limit_error(c, b, user_id)
        if _lim: raise ValueError(_lim)
        # 定向俱乐部的券记在该俱乐部名下，其余（城市 / 全平台）记 0 = 平台直发，
        # display_club_id=0 的平台券在所有俱乐部都能用。
        return self._issue(c, b, user_id, int(b.get('target_club_id') or 0))

    def grant_coupon(self, c, *, benefit_id: int, user_ids: list[int], club_id: int = 0) -> dict[str, Any]:
        """后台群发：给一批用户各发一张（成本总平台承担）。

        #860 边界（复查结论）：claim_audience / new_user_only / per_user_daily_limit
        约束的是「用户自己领取」这一条通道，由 _claim_limit_error 在
        claimable_coupons 与 claim_coupon 两处共用。后台定向发放是管理员显式指定
        收件人、积分商城兑换是花积分购买，两者都不是「领」，故不走该限制 ——
        否则老板把券上架积分商城后，老用户会被「仅新用户可领」误挡。
        """
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
                                b.valid_from,b.valid_to,b.share_enabled,b.target_type,b.target_city,b.owner_type,b.face_json
                         FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                         WHERE r.voucher_code=?""", (str(code or '').strip(),)).fetchone()
        return dict(r) if r else None

    def restore_order_vouchers(self,c,*,order_kind:str,order_id:int)->int:
        # Full refund restores the voucher itself. Original points remain spent because the user still owns the voucher.
        cur=c.execute('''UPDATE benefit_redemptions SET status='issued',used_at=NULL,used_order_kind=NULL,used_order_id=NULL
                         WHERE used_order_kind=? AND used_order_id=? AND status='used' ''',(order_kind,str(order_id)))
        return int(cur.rowcount or 0)

    # ── 消费送券（2026-10-26）────────────────────────────────────────────
    # 俱乐部设定「单笔活动消费满 X 元自动赠送某张券」。规则约束（按文档）：
    #   1. 只能赠送 满减券 / 折扣券 / 直抵券 —— 且必须是本俱乐部自己的活动券（不涉及装备）；
    #   2. 前提：被赠送的券「已有库存」（stock 不为 NULL 且 >0），无库存规则不触发；
    #   3. 每位用户受 per_user_limit 限制，达到上限后不再自动赠送。

    def gift_rules(self, c, *, club_id: int, include_disabled: bool = True) -> list[dict[str, Any]]:
        q = '''SELECT g.*, b.title benefit_title, b.mode, b.threshold_amount benefit_threshold,
                      b.discount_percent, b.cash_value benefit_cash_value, b.stock benefit_stock,
                      b.status benefit_status, b.claimed_count benefit_claimed
               FROM coupon_gift_rules g JOIN member_benefits b ON b.id=g.benefit_id
               WHERE g.club_id=?'''
        if not include_disabled:
            q += ' AND g.enabled=1'
        q += ' ORDER BY g.threshold_amount ASC, g.id DESC'
        return [dict(r) for r in c.execute(q, (club_id,)).fetchall()]

    def create_gift_rule(self, c, *, club_id: int, payload: dict[str, Any]) -> int:
        threshold = max(0.0, float(payload.get('thresholdAmount') or 0))
        if threshold <= 0:
            raise ValueError('赠送门槛必须大于 0 元')
        benefit_id = int(payload.get('benefitId') or 0)
        b = c.execute('SELECT * FROM member_benefits WHERE id=?', (benefit_id,)).fetchone()
        if not b:
            raise LookupError('要赠送的优惠券不存在')
        b = dict(b)
        if b['owner_type'] != 'CLUB' or int(b['owner_club_id']) != int(club_id) or b['benefit_type'] != 'activity_coupon':
            raise ValueError('只能赠送本俱乐部创建的活动优惠券（不能涉及装备）')
        if str(b.get('status') or 'active') != 'active':
            raise ValueError('该券已下架，不能用于赠送')
        # 文档硬约束：赠送的券必须已有库存 —— 不限库存（NULL）视作永远有货。
        if b['stock'] is not None and int(b['stock']) <= 0:
            raise ValueError('该券库存为 0，必须先备库存才能设置赠送')
        per_user_limit = max(1, int(payload.get('perUserLimit') or 1))
        enabled = 0 if payload.get('enabled') is False else 1
        c.execute('''INSERT INTO coupon_gift_rules(club_id,threshold_amount,benefit_id,per_user_limit,enabled)
                     VALUES(?,?,?,?,?)''', (club_id, threshold, benefit_id, per_user_limit, enabled))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])

    def update_gift_rule(self, c, *, club_id: int, rule_id: int, payload: dict[str, Any]) -> None:
        row = c.execute('SELECT * FROM coupon_gift_rules WHERE id=? AND club_id=?', (int(rule_id), club_id)).fetchone()
        if not row:
            raise LookupError('赠送规则不存在')
        sets = {}
        if 'thresholdAmount' in payload:
            threshold = max(0.0, float(payload.get('thresholdAmount') or 0))
            if threshold <= 0:
                raise ValueError('赠送门槛必须大于 0 元')
            sets['threshold_amount'] = threshold
        if 'perUserLimit' in payload:
            sets['per_user_limit'] = max(1, int(payload.get('perUserLimit') or 1))
        if 'enabled' in payload:
            sets['enabled'] = 1 if payload.get('enabled') else 0
        if not sets:
            return
        cols = ','.join(f'{k}=?' for k in sets)
        c.execute(f'UPDATE coupon_gift_rules SET {cols} WHERE id=? AND club_id=?', (*sets.values(), int(rule_id), club_id))

    def delete_gift_rule(self, c, *, club_id: int, rule_id: int) -> None:
        c.execute('DELETE FROM coupon_gift_rules WHERE id=? AND club_id=?', (int(rule_id), club_id))

    def process_coupon_gifts(self, c, *, club_id: int, user_id: int, cash_paid: float) -> list[dict[str, Any]]:
        """活动订单支付成功后触发：满足门槛的规则自动送券。异常不阻断支付主流程。"""
        if cash_paid <= 0:
            return []
        out = []
        try:
            rules = self.gift_rules(c, club_id=club_id, include_disabled=False)
        except Exception:
            return []
        for g in rules:
            if cash_paid < float(g['threshold_amount'] or 0):
                continue
            b = self._coupon_row(c, int(g['benefit_id']))
            if not b or str(b.get('status') or 'active') != 'active':
                continue
            if b['stock'] is not None and int(b['stock']) <= 0:
                continue  # 券没库存了：规则静默跳过，不报错不打断顾客
            gifted = int(c.execute("SELECT COUNT(*) FROM benefit_redemptions WHERE benefit_id=? AND user_id=? AND points_spent=0 AND gift_note LIKE '消费满%'",
                                   (int(g['benefit_id']), user_id)).fetchone()[0] or 0)
            if gifted >= int(g['per_user_limit'] or 1):
                continue
            try:
                issued = self._issue(c, b, user_id, club_id, funding='CLUB',
                                     note=f"消费满 ¥{float(g['threshold_amount']):g} 自动赠送")
                issued['ruleId'] = int(g['id'])
                out.append(issued)
            except Exception:
                continue
        return out

    # ── 升级赠券（2026-10-26）────────────────────────────────────────────
    # 俱乐部为某个会员等级绑定一张券；会员升级到该等级时系统自动赠送。
    # tier_gift_log 以 (user_id, tier_id) 唯一防重 —— refresh_all 读触发的重算不会重复发。

    def tier_gift_rules(self, c, *, club_id: int, include_disabled: bool = True) -> list[dict[str, Any]]:
        q = '''SELECT g.*, t.name tier_name, t.rank tier_rank,
                      b.title benefit_title, b.mode, b.threshold_amount benefit_threshold,
                      b.discount_percent, b.cash_value benefit_cash_value, b.stock benefit_stock, b.status benefit_status
               FROM tier_gift_rules g
               JOIN club_member_tiers t ON t.id=g.tier_id
               JOIN member_benefits b ON b.id=g.benefit_id
               WHERE g.club_id=?'''
        if not include_disabled:
            q += ' AND g.enabled=1'
        q += ' ORDER BY t.rank ASC, g.id DESC'
        return [dict(r) for r in c.execute(q, (club_id,)).fetchall()]

    def create_tier_gift_rule(self, c, *, club_id: int, payload: dict[str, Any]) -> int:
        tier_id = int(payload.get('tierId') or 0)
        benefit_id = int(payload.get('benefitId') or 0)
        t = c.execute('SELECT * FROM club_member_tiers WHERE id=? AND club_id=?', (tier_id, club_id)).fetchone()
        if not t:
            raise LookupError('会员等级不存在')
        b = self._coupon_row(c, benefit_id)
        if not b:
            raise LookupError('要赠送的优惠券不存在')
        if b['owner_type'] != 'CLUB' or int(b['owner_club_id']) != int(club_id) or b['benefit_type'] != 'activity_coupon':
            raise ValueError('只能赠送本俱乐部创建的活动优惠券（不能涉及装备）')
        if str(b.get('status') or 'active') != 'active':
            raise ValueError('该券已下架，不能用于赠送')
        if b['stock'] is not None and int(b['stock']) <= 0:
            raise ValueError('该券库存为 0，必须先备库存才能设置赠送')
        enabled = 0 if payload.get('enabled') is False else 1
        c.execute('''INSERT INTO tier_gift_rules(club_id,tier_id,benefit_id,enabled) VALUES(?,?,?,?)
                     ON CONFLICT(club_id,tier_id) DO UPDATE SET benefit_id=excluded.benefit_id,
                     enabled=excluded.enabled''', (club_id, tier_id, benefit_id, enabled))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])

    def update_tier_gift_rule(self, c, *, club_id: int, rule_id: int, payload: dict[str, Any]) -> None:
        row = c.execute('SELECT * FROM tier_gift_rules WHERE id=? AND club_id=?', (int(rule_id), club_id)).fetchone()
        if not row:
            raise LookupError('升级赠券规则不存在')
        if 'enabled' in payload:
            c.execute('UPDATE tier_gift_rules SET enabled=? WHERE id=? AND club_id=?',
                      (1 if payload.get('enabled') else 0, int(rule_id), club_id))

    def delete_tier_gift_rule(self, c, *, club_id: int, rule_id: int) -> None:
        c.execute('DELETE FROM tier_gift_rules WHERE id=? AND club_id=?', (int(rule_id), club_id))

    def process_tier_gifts(self, c, *, club_id: int, user_id: int, tier_id: int) -> list[dict[str, Any]]:
        """兼容入口（2026-10-27 起真身在 process_tier_rewards）。"""
        return self.process_tier_rewards(c, club_id=club_id, user_id=user_id, tier_id=tier_id)

    def _legacy_process_tier_gifts(self, c, *, club_id: int, user_id: int, tier_id: int) -> list[dict[str, Any]]:
        """membership.on_upgrade 回调：会员升级到 tier_id 时自动送券（幂等）。"""
        out = []
        rules = [dict(r) for r in c.execute(
            'SELECT * FROM tier_gift_rules WHERE club_id=? AND tier_id=? AND enabled=1',
            (club_id, int(tier_id))).fetchall()]
        for g in rules:
            # 先抢防重位：INSERT OR IGNORE 成功（changes=1）才发券，失败说明已发过。
            cur = c.execute('''INSERT OR IGNORE INTO tier_gift_log(club_id,user_id,tier_id,benefit_id)
                               VALUES(?,?,?,?)''', (club_id, user_id, int(tier_id), int(g['benefit_id'])))
            if cur.rowcount != 1:
                continue
            try:
                b = self._coupon_row(c, int(g['benefit_id']))
                if not b or str(b.get('status') or 'active') != 'active':
                    c.execute('DELETE FROM tier_gift_log WHERE club_id=? AND user_id=? AND tier_id=?',
                              (club_id, user_id, int(tier_id)))
                    continue
                if b['stock'] is not None and int(b['stock']) <= 0:
                    # 没库存：释放防重位，等下次有库存时补发。
                    c.execute('DELETE FROM tier_gift_log WHERE club_id=? AND user_id=? AND tier_id=?',
                              (club_id, user_id, int(tier_id)))
                    continue
                tier = c.execute('SELECT name FROM club_member_tiers WHERE id=?', (int(tier_id),)).fetchone()
                issued = self._issue(c, b, user_id, club_id, funding='CLUB',
                                     note=f"达到「{tier['name'] if tier else '会员'}」等级自动赠送")
                issued['ruleId'] = int(g['id'])
                out.append(issued)
            except Exception:
                c.execute('DELETE FROM tier_gift_log WHERE club_id=? AND user_id=? AND tier_id=?',
                          (club_id, user_id, int(tier_id)))
        return out

    # ── 升级奖励 v2（2026-10-27）──────────────────────────────────────────
    # 用户要求「达到会员等级条件之后可以获得优惠券或某种奖励，也可以赠送积分」：
    # 同一等级一条规则，可只送券、只送积分、或两者都给。
    # 防重仍按 (user_id,tier_id) 唯一 —— 同一等级终身只发一次，读触发的重算不会重复发。

    def tier_reward_rules(self, c, *, club_id: int, include_disabled: bool = True) -> list[dict[str, Any]]:
        q = '''SELECT g.*, t.name tier_name, t.rank tier_rank, t.access_mode, t.status tier_status,
                      b.title benefit_title, b.mode, b.threshold_amount benefit_threshold,
                      b.discount_percent, b.cash_value benefit_cash_value,
                      b.stock benefit_stock, b.status benefit_status
               FROM tier_reward_rules g
               JOIN club_member_tiers t ON t.id=g.tier_id
               LEFT JOIN member_benefits b ON b.id=g.benefit_id
               WHERE g.club_id=?'''
        if not include_disabled:
            q += ' AND g.enabled=1'
        q += ' ORDER BY t.rank ASC, g.id DESC'
        return [dict(r) for r in c.execute(q, (club_id,)).fetchall()]

    def _validate_reward_coupon(self, c, club_id: int, benefit_id: int) -> dict[str, Any]:
        b = self._coupon_row(c, benefit_id)
        if not b:
            raise LookupError('要赠送的优惠券不存在')
        if b['owner_type'] != 'CLUB' or int(b['owner_club_id']) != int(club_id) or b['benefit_type'] != 'activity_coupon':
            raise ValueError('只能赠送本俱乐部创建的活动优惠券（不能涉及装备）')
        if str(b.get('status') or 'active') != 'active':
            raise ValueError('该券已下架，不能用于赠送')
        if b['stock'] is not None and int(b['stock']) <= 0:
            raise ValueError('该券库存为 0，必须先备库存才能设置赠送')
        return b

    def create_tier_reward_rule(self, c, *, club_id: int, payload: dict[str, Any]) -> int:
        tier_id = int(payload.get('tierId') or 0)
        benefit_id = int(payload.get('benefitId') or 0)
        points = int(payload.get('pointsAward') or 0)
        t = c.execute('SELECT * FROM club_member_tiers WHERE id=? AND club_id=?', (tier_id, club_id)).fetchone()
        if not t:
            raise LookupError('会员等级不存在')
        if points < 0:
            raise ValueError('赠送积分不能为负数')
        if benefit_id:
            self._validate_reward_coupon(c, club_id, benefit_id)
        if not benefit_id and points <= 0:
            raise ValueError('请至少设置一项奖励：送一张券，或送活动积分')
        enabled = 0 if payload.get('enabled') is False else 1
        c.execute('''INSERT INTO tier_reward_rules(club_id,tier_id,benefit_id,points_award,enabled)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(club_id,tier_id) DO UPDATE SET benefit_id=excluded.benefit_id,
                     points_award=excluded.points_award, enabled=excluded.enabled''',
                  (club_id, tier_id, benefit_id or None, points, enabled))
        row = c.execute('SELECT id FROM tier_reward_rules WHERE club_id=? AND tier_id=?', (club_id, tier_id)).fetchone()
        return int(row['id']) if row else 0

    def update_tier_reward_rule(self, c, *, club_id: int, rule_id: int, payload: dict[str, Any]) -> None:
        row = c.execute('SELECT * FROM tier_reward_rules WHERE id=? AND club_id=?', (int(rule_id), club_id)).fetchone()
        if not row:
            raise LookupError('升级奖励规则不存在')
        sets, args = [], []
        if 'enabled' in payload:
            sets.append('enabled=?'); args.append(1 if payload.get('enabled') else 0)
        if 'pointsAward' in payload:
            pts = int(payload.get('pointsAward') or 0)
            if pts < 0:
                raise ValueError('赠送积分不能为负数')
            sets.append('points_award=?'); args.append(pts)
        if 'benefitId' in payload:
            bid = int(payload.get('benefitId') or 0)
            if bid:
                self._validate_reward_coupon(c, club_id, bid)
            sets.append('benefit_id=?'); args.append(bid or None)
        if not sets:
            return
        args += [int(rule_id), club_id]
        c.execute(f"UPDATE tier_reward_rules SET {','.join(sets)} WHERE id=? AND club_id=?", args)

    def delete_tier_reward_rule(self, c, *, club_id: int, rule_id: int) -> None:
        c.execute('DELETE FROM tier_reward_rules WHERE id=? AND club_id=?', (int(rule_id), club_id))

    def process_tier_rewards(self, c, *, club_id: int, user_id: int, tier_id: int) -> list[dict[str, Any]]:
        """membership.on_upgrade 回调：升到 tier_id 时按规则发券 / 发活动积分（幂等）。"""
        out: list[dict[str, Any]] = []
        rules = [dict(r) for r in c.execute(
            'SELECT * FROM tier_reward_rules WHERE club_id=? AND tier_id=? AND enabled=1',
            (club_id, int(tier_id))).fetchall()]
        for g in rules:
            # 先抢防重位：INSERT OR IGNORE 命中唯一索引说明已经发过。
            cur = c.execute('''INSERT OR IGNORE INTO tier_reward_log(club_id,user_id,tier_id,benefit_id,points_award)
                               VALUES(?,?,?,?,?)''',
                            (club_id, user_id, int(tier_id), g.get('benefit_id'), int(g.get('points_award') or 0)))
            if cur.rowcount != 1:
                continue
            tier = c.execute('SELECT name FROM club_member_tiers WHERE id=?', (int(tier_id),)).fetchone()
            tname = tier['name'] if tier else '会员'
            got: list[dict[str, Any]] = []
            try:
                bid = int(g.get('benefit_id') or 0)
                if bid:
                    b = self._coupon_row(c, bid)
                    if b and str(b.get('status') or 'active') == 'active' and (
                            b['stock'] is None or int(b['stock']) > 0):
                        issued = self._issue(c, b, user_id, club_id, funding='CLUB',
                                             note=f"达到「{tname}」等级自动赠送")
                        issued['ruleId'] = int(g['id'])
                        got.append(issued)
                pts = int(g.get('points_award') or 0)
                if pts > 0:
                    c.execute('UPDATE club_members SET club_points_balance=club_points_balance+? '
                              'WHERE club_id=? AND user_id=?', (pts, club_id, user_id))
                    c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) '
                              'VALUES(?,?,?,?,?,?,?)',
                              (club_id, user_id, 'earn', pts, 'tier_reward', str(tier_id),
                               f"达到「{tname}」等级奖励活动积分；成本由俱乐部承担"))
                    got.append({'pointsAwarded': pts, 'ruleId': int(g['id'])})
            except Exception:
                c.execute('DELETE FROM tier_reward_log WHERE club_id=? AND user_id=? AND tier_id=?',
                          (club_id, user_id, int(tier_id)))
                continue
            if not got:
                # 券不可用（下架/没库存）且没配积分 —— 释放防重位，等条件恢复后补发。
                c.execute('DELETE FROM tier_reward_log WHERE club_id=? AND user_id=? AND tier_id=?',
                          (club_id, user_id, int(tier_id)))
                continue
            out.extend(got)
        return out

    # ── 拉新邀请（2026-10-26）────────────────────────────────────────────
    def welcome_new_member(self, c, *, club_id: int, user_id: int) -> dict[str, Any] | None:
        """membership.on_member_created 回调：新会员注册即得一张「邀请优惠券」。

        旧逻辑：发 clubs.referral_coupon_benefit_id 给所有新会员（含被邀请人）。
        #858：俱乐部开启新版「邀请奖励」模块后，被邀请人的券改由邀请链路（public_join_with_invite）
        统一发放，这里跳过，避免同一人被发两张；未开启新模块的俱乐部仍走旧逻辑，向后兼容。"""
        inv = c.execute('SELECT 1 FROM invite_reward_settings WHERE club_id=? AND enabled=1', (club_id,)).fetchone()
        if inv:
            return None
        # 被邀请人已绑定邀请关系 → 券由邀请链路（public_join_with_invite）统一发放，这里不发，避免重复。
        ref = c.execute('SELECT referred_by FROM club_members WHERE club_id=? AND user_id=?', (club_id, user_id)).fetchone()
        if ref and ref['referred_by']:
            return None
        row = c.execute('SELECT referral_coupon_benefit_id FROM clubs WHERE id=?', (club_id,)).fetchone()
        if not row or not row['referral_coupon_benefit_id']:
            return None
        b = self._coupon_row(c, int(row['referral_coupon_benefit_id']))
        if not b or str(b.get('status') or 'active') != 'active' or b['benefit_type'] != 'activity_coupon':
            return None
        try:
            return self._issue(c, b, user_id, club_id, funding='CLUB', note='注册俱乐部会员自动赠送')
        except Exception:
            return None

    def transfer_coupon(self, c, *, club_id: int, code: str, to_user_id: int) -> dict[str, Any]:
        """券转赠：好友把券码输入领取 —— 校验券可转、接收方是本俱乐部会员，然后转移归属。"""
        code = str(code or '').strip()
        r = c.execute('''SELECT r.*,b.title,b.benefit_type,b.owner_type,b.owner_club_id,b.share_enabled,
                                b.valid_from,b.valid_to,b.mode,b.discount_percent,b.threshold_amount,b.image_key,b.face_json
                         FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                         WHERE r.voucher_code=?''', (code,)).fetchone()
        if not r:
            raise LookupError('券码不存在，请核对后重试')
        v = dict(r)
        if not int(v.get('share_enabled') or 0):
            raise ValueError('该券不允许转赠')
        if v['status'] != 'issued':
            raise ValueError('该券已被使用或被锁定，无法转赠')
        if self._expired(v):
            raise ValueError('该券已过期，无法转赠')
        if v['owner_type'] == 'CLUB' and int(v['owner_club_id'] or 0) != int(club_id):
            raise ValueError('该券不属于本俱乐部')
        to_user_id = int(to_user_id)
        is_member = c.execute('SELECT 1 FROM club_members WHERE club_id=? AND user_id=?',
                              (club_id, to_user_id)).fetchone()
        if not is_member:
            raise ValueError('好友需要先注册本俱乐部会员才能领取')
        cur = c.execute('''UPDATE benefit_redemptions SET user_id=?, display_club_id=?, gift_note='好友转赠'
                           WHERE voucher_code=? AND status='issued' ''', (to_user_id, club_id, code))
        if cur.rowcount != 1:
            raise ValueError('该券刚刚已被转出或使用')
        return {'voucherCode': code, 'title': v['title'], 'mode': v.get('mode'),
                'cashValue': float(v.get('cash_value') or 0),
                'discountPercent': float(v.get('discount_percent') or 0)}

from __future__ import annotations
import secrets
from typing import Any

# 积分商城（2026-10-26）：俱乐部用「活动积分」上架可兑换商品。
# 文档口径：积分兑换商品可以是活动 / 一次服务 / 实物，也可以是优惠券
# （优惠券需提前在优惠券中设计好）。100 积分 = ¥1。
ITEM_TYPES = ('coupon', 'activity', 'service', 'goods')
ITEM_TYPE_CN = {'coupon': '优惠券', 'activity': '活动', 'service': '服务', 'goods': '实物'}


class PointsMallEngine:
    """Points mall: club-scoped items redeemable with Club Points.

    - coupon 类：兑换即把关联的俱乐部活动券发进用户券包（复用 BenefitEngine._issue）；
    - activity / service / goods 类：生成核销码，俱乐部扫码或输码核销履约。
    扣积分与余额判定同 BenefitEngine.redeem：只动 club_members.club_points_balance。
    """

    def __init__(self, benefit_engine=None):
        self.benefits = benefit_engine

    # ── 俱乐部端：商品管理 ────────────────────────────────────────────────
    def list_items(self, c, *, club_id: int, include_inactive: bool = True) -> list[dict[str, Any]]:
        q = 'SELECT * FROM points_mall_items WHERE club_id=?'
        if not include_inactive:
            q += " AND status='active'"
        q += ' ORDER BY sort ASC, id DESC'
        items = [dict(r) for r in c.execute(q, (club_id,)).fetchall()]
        for it in items:
            it['typeName'] = ITEM_TYPE_CN.get(it['item_type'], it['item_type'])
            if it['item_type'] == 'coupon' and it['ref_id']:
                b = c.execute('SELECT title,cash_value,discount_percent,mode,threshold_amount,stock FROM member_benefits WHERE id=?',
                              (int(it['ref_id']),)).fetchone()
                if b:
                    it['refTitle'] = b['title']
                    it['refMode'] = b['mode']
                    it['refCashValue'] = float(b['cash_value'] or 0)
                    it['refDiscountPercent'] = float(b['discount_percent'] or 0)
        return items

    def create_item(self, c, *, club_id: int, payload: dict[str, Any]) -> int:
        title = str(payload.get('title') or '').strip()
        if not title:
            raise ValueError('兑换商品名称不能为空')
        item_type = str(payload.get('itemType') or 'goods').strip().lower()
        if item_type not in ITEM_TYPES:
            raise ValueError('兑换类型只能是优惠券/活动/服务/实物')
        points = int(payload.get('pointsCost') or 0)
        if points <= 0:
            raise ValueError('所需积分必须大于 0')
        stock = payload.get('stock')
        stock = None if stock in (None, '') else max(0, int(stock))
        ref_id = payload.get('refId')
        ref_id = int(ref_id) if ref_id not in (None, '') else None
        if item_type == 'coupon':
            if not ref_id:
                raise ValueError('优惠券兑换必须关联一张本俱乐部已设计好的活动券')
            b = c.execute('SELECT * FROM member_benefits WHERE id=?', (ref_id,)).fetchone()
            if not b:
                raise LookupError('关联的优惠券不存在')
            b = dict(b)
            if b['owner_type'] != 'CLUB' or int(b['owner_club_id']) != int(club_id) or b['benefit_type'] != 'activity_coupon':
                raise ValueError('只能关联本俱乐部创建的活动优惠券')
            if str(b.get('status') or 'active') != 'active':
                raise ValueError('关联的优惠券已下架')
        c.execute('''INSERT INTO points_mall_items(club_id,item_type,title,description,image_url,points_cost,stock,ref_type,ref_id,status,sort)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (club_id, item_type, title, payload.get('description'), payload.get('imageUrl'),
                   points, stock, (item_type if item_type == 'coupon' else None), ref_id,
                   payload.get('status') or 'active', max(0, int(payload.get('sort') or 0))))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])

    def update_item(self, c, *, club_id: int, item_id: int, payload: dict[str, Any]) -> None:
        row = c.execute('SELECT * FROM points_mall_items WHERE id=? AND club_id=?', (int(item_id), club_id)).fetchone()
        if not row:
            raise LookupError('兑换商品不存在')
        sets: dict[str, Any] = {}
        if 'title' in payload:
            title = str(payload.get('title') or '').strip()
            if not title:
                raise ValueError('兑换商品名称不能为空')
            sets['title'] = title
        if 'description' in payload: sets['description'] = payload.get('description')
        if 'imageUrl' in payload: sets['image_url'] = payload.get('imageUrl')
        if 'pointsCost' in payload:
            points = int(payload.get('pointsCost') or 0)
            if points <= 0:
                raise ValueError('所需积分必须大于 0')
            sets['points_cost'] = points
        if 'stock' in payload:
            stock = payload.get('stock')
            sets['stock'] = None if stock in (None, '') else max(0, int(stock))
        if 'sort' in payload: sets['sort'] = max(0, int(payload.get('sort') or 0))
        if 'status' in payload:
            st = str(payload.get('status') or 'active')
            if st not in ('active', 'inactive'):
                raise ValueError('状态只能是 active / inactive')
            sets['status'] = st
        if not sets:
            return
        cols = ','.join(f'{k}=?' for k in sets)
        c.execute(f'UPDATE points_mall_items SET {cols},updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',
                  (*sets.values(), int(item_id), club_id))

    def delete_item(self, c, *, club_id: int, item_id: int) -> None:
        c.execute('DELETE FROM points_mall_items WHERE id=? AND club_id=?', (int(item_id), club_id))

    # ── C 端：兑换 ────────────────────────────────────────────────────────
    def redeem(self, c, *, club_id: int, item_id: int, user_id: int) -> dict[str, Any]:
        it = c.execute('SELECT * FROM points_mall_items WHERE id=? AND club_id=? AND status="active"',
                       (int(item_id), club_id)).fetchone()
        if not it:
            raise LookupError('兑换商品不存在或已下架')
        it = dict(it)
        if it['stock'] is not None and int(it['stock']) <= 0:
            raise OverflowError('该兑换商品已被兑完')
        points = int(it['points_cost'] or 0)
        wallet = c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',
                           (club_id, user_id)).fetchone()
        if not wallet or int(wallet[0] or 0) < points:
            raise ValueError('活动积分不足')
        c.execute('UPDATE club_members SET club_points_balance=club_points_balance-? WHERE club_id=? AND user_id=?',
                  (points, club_id, user_id))
        c.execute('''INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note)
                     VALUES(?,?,?,?,?,?,?)''',
                  (club_id, user_id, 'mall_redeem', -points, 'points_mall_item', str(it['id']),
                   f"积分商城兑换「{it['title']}」"))
        code = None
        coupon = None
        if it['item_type'] == 'coupon' and it['ref_id'] and self.benefits is not None:
            b = c.execute('SELECT * FROM member_benefits WHERE id=?', (int(it['ref_id']),)).fetchone()
            if not b:
                raise LookupError('关联的优惠券不存在')
            coupon = self.benefits._issue(c, dict(b), user_id, club_id, funding='CLUB',
                                          note=f"积分商城兑换（{points} 积分）")
        else:
            code = 'PM-' + secrets.token_hex(4).upper()
        if it['stock'] is not None:
            c.execute('UPDATE points_mall_items SET stock=stock-1,updated_at=CURRENT_TIMESTAMP WHERE id=?', (it['id'],))
        c.execute('''INSERT INTO points_mall_redemptions(club_id,item_id,item_title,item_type,user_id,points_spent,code,status)
                     VALUES(?,?,?,?,?,?,?,?)''',
                  (club_id, it['id'], it['title'], it['item_type'], user_id, points, code, 'issued'))
        rid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        return {'redemptionId': rid, 'title': it['title'], 'itemType': it['item_type'],
                'pointsSpent': points, 'code': code, 'coupon': coupon}

    def my_redemptions(self, c, *, club_id: int, user_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in c.execute(
            '''SELECT * FROM points_mall_redemptions WHERE club_id=? AND user_id=? ORDER BY id DESC''',
            (club_id, user_id)).fetchall()]

    # ── 俱乐部端：核销 / 兑换记录 ─────────────────────────────────────────
    def verify(self, c, *, club_id: int, code: str, operator: str = 'club') -> dict[str, Any]:
        code = str(code or '').strip().upper()
        if not code:
            raise ValueError('请输入核销码')
        row = c.execute('SELECT * FROM points_mall_redemptions WHERE code=? AND club_id=?', (code, club_id)).fetchone()
        if not row:
            raise LookupError('核销码不存在或不属于本俱乐部')
        r = dict(row)
        if r['status'] == 'verified':
            raise ValueError(f'该核销码已于 {str(r["verified_at"] or "")[:16]} 核销')
        c.execute('''UPDATE points_mall_redemptions SET status='verified',verified_at=CURRENT_TIMESTAMP,verified_by=?
                     WHERE id=?''', (operator, r['id']))
        r['status'] = 'verified'
        return r

    def redemption_records(self, c, *, club_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in c.execute(
            '''SELECT m.*, u.name user_name, u.phone user_phone
               FROM points_mall_redemptions m JOIN users u ON u.id=m.user_id
               WHERE m.club_id=? ORDER BY m.id DESC LIMIT 200''', (club_id,)).fetchall()]

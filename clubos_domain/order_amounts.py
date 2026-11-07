"""售后金额口径统一（OBS-02，2026-11-07）。

背景
----
`gear_orders.total` / `gear_orders.cash_paid` 是「会员装备折扣 + 积分抵扣 +
平台补贴」之后的**成交口径**；而 `gear_order_items.unit_price` 与
`after_sales_items.requested_amount` 是**商品原价口径**。

历史实现把原价当分子、折后金额当分母，造成两类真实偏差：

1. 退款现金算多：`requested_refund = gross * (cash_paid / total)`，在 9.5 折订单里
   `cash_paid == total`，于是退 1 件原价 100 元的商品会申请退 ¥100，而顾客实际
   只为这件付了 ¥95 —— **每单多退一个折扣差**。
2. 商品占比被放大：`goods / total = 100 / 285 ≈ 0.351`，实际应为 `95 / 285 = 1/3`。
   它同时驱动已抵扣积分返还、已赚积分冲回、AI Credits 冲回与「是否全单退完」
   判定；极端情况下会把「退部分」误判成「全单退完」，触发整单退款终态与福利券恢复。

统一规则
--------
以订单明细原价合计 `gross_all = SUM(unit_price * quantity)` 作为桥：

    goods_factor = total     / gross_all   （原价 → 折后商品口径，与 total 同源）
    cash_factor  = cash_paid / gross_all   （原价 → 实付现金口径，与 cash_paid 同源）

于是 `goods_basis / total == cash_basis / cash_paid == 原价 / gross_all`，
分子分母永远同源；当累计售后覆盖全部商品时占比恰好为 1，不会提前溢出。

老库缺订单明细时安全退回旧口径（不折算、按现金比例），绝不自己造折扣。
"""
from __future__ import annotations


def order_gross(c, order_id: int) -> float:
    """订单商品原价合计 SUM(gear_order_items.unit_price * quantity)。"""
    r = c.execute('SELECT COALESCE(SUM(unit_price*quantity),0) FROM gear_order_items WHERE order_id=?',
                  (int(order_id),)).fetchone()
    return round(float((r[0] if r else 0) or 0), 2)


def amount_basis(c, order_id: int, *, total: float, cash_paid: float) -> dict:
    """返回订单级金额换算基准，供售后建单与退款冲正共用（唯一真实来源）。"""
    gross = order_gross(c, order_id)
    total = max(0.0, float(total or 0))
    cash = max(0.0, float(cash_paid or 0))
    if gross <= 0:
        # 老库 / 异常订单没有明细：不折算商品口径，现金按原逻辑比例回退。
        goods_factor = 1.0
        cash_factor = (cash / total) if total > 0 else 0.0
    else:
        # 折扣不会让成交金额高于原价；异常数据（如把运费算进 total）也不放大分子。
        goods_factor = min(1.0, max(0.0, total / gross)) if total > 0 else 1.0
        cash_factor = max(0.0, cash / gross)
    return {'gross': gross, 'total': total, 'cashPaid': cash,
            'goodsFactor': goods_factor, 'cashFactor': cash_factor}

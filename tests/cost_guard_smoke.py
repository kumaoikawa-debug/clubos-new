"""成本数据闸门离线回归（不 import app → 不碰数据库）。

2026-10-06 用户铁律：方案里的成本/报价数据，任何前端都不得显示。
三道闸门都要锁住：解析期 redact、出口 sanitize、语境判定（人均费用看语境）。

运行：/Users/jckuma/.workbuddy/binaries/python/envs/default/bin/python tests/cost_guard_smoke.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cost_guard import (
    redact_cost_text, sanitize_for_frontend, is_cost_row, is_cost_key,
    has_cost_context, master_has_cost_evidence, scrub_cost_text,
)

FAILED = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        FAILED.append(name)


print("== 1. 解析期 redact_cost_text 剔成本、留服务项名 ==")
# 真实方案的成本页是「单元格散行」（每一项单独成行），与表格行不同形状。
sheet = ("[Slide 15]\n14 — COST 活动费用明细\n按 20 人报价 · 成本预估\n"
         "车费\n门票\n午餐\n合计（未含税） ¥76,131.00\n"
         "人均费用 ¥ 3,806.55\n策划执行（10%）¥ 6,921.00")
clean, stats = redact_cost_text(sheet)
check("成本页被识别为 1 个成本块", stats["costBlocks"] == 1, stats)
check("成本行被删除（数量>0）", stats["costLines"] > 0, stats)
for tok in ("76,131", "3,806.55", "6,921", "未含税", "单价", "小计", "人均费用", "策划执行"):
    check(f"清洗后不含成本 token: {tok}", tok not in clean, clean)
check("服务项名保留（车费）", "车费" in clean, clean)
check("服务项名保留（门票）", "门票" in clean, clean)

print("== 2. 普通招募文案：人均288=公开价，不当成本 ==")
note = "11月14日带12个会员去什邡蓥华山，人均288元，含领队与保险。"
check("招募文案无成本语境", has_cost_context(note) is False)
check("招募文案人均288不是成本行", is_cost_row("人均288元", False) is False)
check("招募文案人均288是公开价提取位", is_cost_row("人均288元", False) is False)

print("== 3. 成本语境：人均费用=内部单价，必须按成本 ==")
ctx = has_cost_context("合计 未含税 ¥38,400")
check("未含税→成本语境", ctx is True)
check("人均费用在成本语境=成本", is_cost_row("人均费用 ¥1,280", ctx) is True)
check("人均费用键在成本语境=成本键", is_cost_key("人均费用", ctx) is True)
check("人均费用在非成本语境≠成本键", is_cost_key("人均费用", False) is False)
check("合计（未含税）键=成本键", is_cost_key("合计（未含税）", None) is True)
check("费用包含键≠成本键", is_cost_key("费用包含", None) is False)

print("== 4. sanitize_for_frontend：清老数据 + 价格归零 ==")
old_master = {
    "title": "新都桥鱼子西", "price": 3806.55, "date": "2026-11-01", "location": "新都桥",
    "fees": {"人均费用": "¥3,806.55", "合计（未含税）": "¥76,131.00", "按人数报价": "20人",
             "备注": "成本预估，未含税", "费用包含": "车费、门票、氧气、摄影"},
    "itinerary": [{"time": "D1", "text": "抵达新都桥"}],
}
old_detail = {"blocks": [{"type": "facts", "items": [
    {"label": "人均费用", "value": "¥3,806.55"},
    {"label": "主题", "value": "高客定制"}]}]}
m, d, changed, cd = sanitize_for_frontend(dict(old_master), dict(old_detail))
check("识别为成本来源", cd is True)
check("master 价格归零", float(m.get("price") or 0) == 0, m.get("price"))
check("master 打 priceFrom=pending", m.get("priceFrom") == "pending", m.get("priceFrom"))
check("fees 不含人均费用", "人均费用" not in (m.get("fees") or {}))
check("fees 不含合计（未含税）", "合计（未含税）" not in (m.get("fees") or {}))
check("fees 保留费用包含", "费用包含" in (m.get("fees") or {}))
dblob = __import__("json").dumps(d, ensure_ascii=False)
for tok in ("3,806.55", "76,131", "未含税"):
    check(f"detail 不含成本 token: {tok}", tok not in dblob, dblob)
check("facts 人均费用行移除", all(i.get("label") != "人均费用" for i in d["blocks"][0]["items"]))
check("facts 主题行保留", any(i.get("label") == "主题" for i in d["blocks"][0]["items"]))

print("== 5. 公开招募文案：绝不误伤 ==")
pub = {"title": "蓥华山", "price": 288.0, "fees": {"人均费用": "¥288", "费用包含": "领队、保险"}}
mp, dp2, chg, cd2 = sanitize_for_frontend(dict(pub), None)
check("公开文案未改动", chg is False)
check("公开价 288 保留", float(mp.get("price") or 0) == 288.0)
check("公开文案人均费用保留", "人均费用" in (mp.get("fees") or {}))

print("== 6. scrub_cost_text：成本句清空、保留公开句 ==")
check("成本句清空", scrub_cost_text("人均费用 ¥3,806.55", True) == "")
check("公开句保留", "每人 288 元" in scrub_cost_text("本次每人 288 元，含午餐", False))

print("== 7. 成本标签的 value 不得变成孤儿（真实泄漏回归）==")
# 线上抓到过的泄漏：{'label':'策划执行','value':'10%'} 删掉 label 后剩下 {'value':'10%'}，
# 前端会渲染出一个没有标签的「10%」，策划执行费率照样漏出去。
m7, d7, chg7, cd7 = sanitize_for_frontend(
    {"title": "T", "fees": {"合计（未含税）": "¥76,131.00", "费用包含": "车费"}},
    {"blocks": [{"type": "facts", "items": [
        {"label": "策划执行", "value": "10%"},
        {"label": "人均费用", "value": "¥3,806.55"},
        {"label": "主题", "value": "高客定制"}]}]})
blob7 = __import__("json").dumps(d7, ensure_ascii=False)
check("识别为成本来源", cd7 is True)
check("孤儿 '10%' 已消失", '"10%"' not in blob7, blob7)
check("策划执行标签已消失", "策划执行" not in blob7, blob7)
check("facts 主题行仍在", '"高客定制"' in blob7, blob7)
items7 = (d7.get("blocks") or [{}])[0].get("items") or []
check("只剩 1 条 facts item", len(items7) == 1, items7)
check("仅存的那条是主题", items7[0].get("label") == "主题", items7)

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    sys.exit(1)
print("ALL COST-GUARD SMOKE PASSED")

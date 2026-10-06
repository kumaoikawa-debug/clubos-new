"""`ai_engine._extract_structured` 离线回归（不 import app → 不碰数据库）。

为什么单独有这个文件：
  抽取器最初是围绕「新都桥鱼子西方案」那份 PPT 定制的，规则很窄。用户改用
  一句话口语描述（`11月14日带12个会员去什邡蓥华山，人均288元`）时，
  价格与人数会双双抽不到 → 活动建出来价格是 0、人数是模板默认 30。
  这里同时锁住「口语写法要抽得到」与「PPT 老写法不能退化」两侧。

运行：/Users/jckuma/.workbuddy/binaries/python/envs/default/bin/python tests/ai_extract_smoke.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai_engine  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        FAILED.append(name)


def ex(text):
    return ai_engine._extract_structured({"text": text})


print("== 1. 口语化一句话（用户实际用法） ==")
s = ex("11月14日带12个会员去什邡蓥华山，上午瑜伽，下午6公里徒步，人均288元。")
check("人均 288 抽到", s["price"] == 288.0, s["price"])
check("12 个会员抽到", s["capacity"] == 12, s["capacity"])
# 公开「人均 288」应作为对外售价进入 price，而**不**写进 fees（fees 只承载费用包含/费用不含）；
# 成本表里的「人均费用」更不会被当成公开价（见第 3 节）。这是 2026-10-06 成本闸门后的新契约。
check("公开价入 price 而非 fees", s["price"] == 288.0 and "人均费用" not in s["fees"], (s["price"], s["fees"]))
# 日期 / 地点 / 标题由 `_mock_activity` 的 `_guess` 兜底层负责（_extract_structured 只管资料文本里的
# 「活动地点」「20XX年X月X日」这类显式写法），所以这里断言端到端的 Activity Master。
master = ai_engine._mock_activity({"text": "11月14日带12个会员去什邡蓥华山，上午瑜伽，下午6公里徒步，人均288元。"})["activity_master"]
check("Master 日期抽到", master.get("date") == "11月14日", master.get("date"))
check("Master 地点抽到蓥华山", "蓥华山" in (master.get("location") or ""), master.get("location"))
check("Master 价格 288", float(master.get("price") or 0) == 288.0, master.get("price"))
check("Master 人数 12", int(master.get("capacity") or 0) == 12, master.get("capacity"))

print("== 2. 「每人 N」与「N 元/人」写法 ==")
s = ex("周末去青城山，每人 380，限 20 人参加。")
check("每人 380", s["price"] == 380.0, s["price"])
check("限 20 人", s["capacity"] == 20, s["capacity"])
s = ex("报名费 168 元/人，成团 15 人")
check("N 元/人", s["price"] == 168.0, s["price"])

print("== 3. PPT 成本表写法：成本数据绝不出（2026-10-06 用户铁律）==")
s = ex("活动人数 30 人\n人均费用 ¥1,280\n合计 未含税 ¥38,400\n活动地点 新都桥")
check("活动人数 30 人抽到", s["capacity"] == 30, s["capacity"])
check("活动地点 新都桥抽到", s["location"] == "新都桥", s["location"])
# 成本表里的「人均费用 / 合计未含税」绝不能被当对外售价抽走（此前 C 端直接按成本价开卖）
check("成本人均费不当售价", s["price"] == 0.0, s["price"])
check("成本费项不进 fees", "合计（未含税）" not in s["fees"] and "备注" not in s["fees"], s["fees"])
# 费用说明只允许出现「费用包含」且只写项名（车费/门票…），绝不写金额
check("费用包含只含项名", all(v and not any(c.isdigit() for c in str(v)) for v in s["fees"].values()), s["fees"])

print("== 3b. 成本语境判定：人均费用 看语境 ==")
# 成本信号（未含税/单价/小计…）在场时，人均费用=内部单价，必须按成本处理
from cost_guard import is_cost_row, has_cost_context
ctx = has_cost_context("合计 未含税 ¥38,400")
check("成本语境下人均费用是成本", is_cost_row("人均费用 ¥1,280", ctx) is True)
# 普通招募文案里「人均 288 元」= 公开售价，不得误判为成本
check("招募文案人均288是公开价", is_cost_row("人均 288 元", False) is False)

print("== 4. 干扰项不得被误当成人数 / 价格 ==")
s = ex("全程6公里，海拔上升600米，午餐10人一桌，4人一车。")
check("10人一桌不算人数", s["capacity"] == 0, s["capacity"])
check("里程海拔不算价格", s["price"] == 0.0, s["price"])
s = ex("备注：1人1座。")
check("1人1座不算人数", s["capacity"] == 0, s["capacity"])
s = ex("去龙泉山看桃花")
check("无价格时不编造", s["price"] == 0.0 and s["capacity"] == 0, s)

print("== 5. 边界 ==")
check("空文本安全", ex("")["price"] == 0.0 and ex("")["capacity"] == 0)
check("None 安全", ai_engine._extract_structured(None)["price"] == 0.0)

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
else:
    print("ALL PASS · 事实抽取回归通过")
raise SystemExit(1 if FAILED else 0)

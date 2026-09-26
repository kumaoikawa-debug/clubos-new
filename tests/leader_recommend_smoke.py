"""领队智能推荐的离线回归。

纯函数、不碰数据库、不起服务、不花 AI 额度：候选名册与历史带队记录都由本文件构造。
锁住的是几条不变量，而不是具体分数：

1. **不凑数**：一个信号都没有的领队不该出现在推荐里（宁可少推）。
2. **不推停用的人**。
3. **同线路经验是最强信号**，且理由必须能讲给运营听。
4. **已在本团期的人排到最后**，推荐的是"还没派的人"。
5. `limit` 生效。

运行：`python tests/leader_recommend_smoke.py`
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clubos_domain.leader_recommend import (  # noqa: E402
    ACTIVITY_TYPES, LeaderRecommendService, activity_types,
)

FAILED = []


def check(name, cond, extra=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        FAILED.append(name)


ROSTER = [
    {"id": 1, "name": "阿岭", "role": "领队", "specialties": ["徒步", "登山"], "base_city": "成都", "status": "active"},
    {"id": 2, "name": "小野", "role": "教练", "specialties": ["瑜伽"], "base_city": "成都", "status": "active"},
    {"id": 3, "name": "老周", "role": "向导", "specialties": [], "base_city": "康定", "status": "active"},
    {"id": 4, "name": "已停用", "role": "领队", "specialties": ["徒步"], "base_city": "成都", "status": "inactive"},
    {"id": 5, "name": "无信号", "role": "领队", "specialties": [], "base_city": "", "status": "active"},
]

HISTORY = [
    {"leader_id": 1, "occurrence_id": 11, "activity_id": 100, "title": "蓥华山徒步 × 户外瑜伽", "location": "什邡蓥华山"},
    {"leader_id": 1, "occurrence_id": 12, "activity_id": 101, "title": "蓥华山徒步", "location": "什邡蓥华山"},
    {"leader_id": 1, "occurrence_id": 13, "activity_id": 102, "title": "青城山徒步", "location": "青城山"},
    {"leader_id": 2, "occurrence_id": 21, "activity_id": 100, "title": "蓥华山徒步 × 户外瑜伽", "location": "什邡蓥华山"},
    {"leader_id": 3, "occurrence_id": 31, "activity_id": 200, "title": "雪山登顶", "location": "四姑娘山"},
]

ACTIVITY = {"id": 300, "title": "蓥华山徒步 × 户外瑜伽", "location": "什邡蓥华山", "event_date": "2026-10-24"}

print("== 1. 类型识别 ==")
check("识别徒步", "徒步" in activity_types("蓥华山轻徒步 6 公里"))
check("识别瑜伽", "瑜伽" in activity_types("草坪户外瑜伽"))
check("无关文本不误判", activity_types("城市读书会") == ())
check("词表非空", len(ACTIVITY_TYPES) >= 8)

svc = LeaderRecommendService()

print("== 2. 同线路经验是最强信号 ==")
recs = svc.recommend(ROSTER, HISTORY, ACTIVITY, limit=5)
by_id = {r["leaderId"]: r for r in recs}
check("阿岭被推荐", 1 in by_id)
check("阿岭排第一（带过两次同线路）", recs and recs[0]["leaderId"] == 1, f"got={[r['leaderId'] for r in recs]}")
check("阿岭同线路计数=2", by_id.get(1, {}).get("sameRouteCount") == 2, by_id.get(1))
check("理由含『带过 2 次』", "带过 2 次" in (by_id.get(1, {}).get("reason") or ""), by_id.get(1, {}).get("reason"))
check("阿岭分高于小野", by_id[1]["score"] > by_id.get(2, {}).get("score", 0))

print("== 3. 不推停用的人 / 不凑数 ==")
check("停用的 4 号不进推荐", 4 not in by_id)
check("无任何信号的 5 号不进推荐", 5 not in by_id)

print("== 4. 已在本团期的人排到最后 ==")
recs2 = svc.recommend(ROSTER, HISTORY, ACTIVITY, assigned_leader_ids=[1], limit=5)
check("已指派的仍在列表里（供展示）", 1 in {r["leaderId"] for r in recs2})
check("但排在最后", recs2[-1]["leaderId"] == 1, f"got={[r['leaderId'] for r in recs2]}")
check("已指派标记正确", {r["leaderId"]: r["alreadyAssigned"] for r in recs2}.get(1) is True)

print("== 5. limit 生效 / 空输入不炸 ==")
check("limit=1", len(svc.recommend(ROSTER, HISTORY, ACTIVITY, limit=1)) == 1)
check("空名册返回空", svc.recommend([], HISTORY, ACTIVITY) == [])
check("空历史仍能按擅长推荐", {r["leaderId"] for r in svc.recommend(ROSTER, [], ACTIVITY)} == {1, 2})

print("== 6. 常驻城市命中可作为理由 ==")
recs3 = svc.recommend(ROSTER, [], {"id": 301, "title": "折多山徒步", "location": "康定折多山"}, limit=5)
r3 = {r["leaderId"]: r for r in recs3}
check("老周按常驻城市进推荐", 3 in r3, recs3)
check("理由是常驻", "常驻" in (r3.get(3, {}).get("reason") or ""), r3.get(3))

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    sys.exit(1)
print("ALL PASS · 领队推荐引擎离线回归通过")

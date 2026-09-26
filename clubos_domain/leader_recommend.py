"""按活动推荐领队 / 团期排班建议（纯函数，不碰数据库）。

背景：俱乐部此前每场活动只能手打「领队姓名 + 电话」，没有自己的领队名册，
于是「这条线路以前是谁带的」只能靠老板的记忆。有了名册与历史带队记录之后，
推荐就是可解释的确定性计算，而不是拍脑袋。

设计纪律（与装备推荐同源）：

1. **候选只能是资源库里真实存在、且状态为在岗的领队**，引擎不生成任何领队条目。
2. **排序理由必须能讲给运营听**（「带过 3 次同线路」「擅长登山」），不做黑盒打分；
   讲不出理由的人就不进推荐列表——宁可不推，也不要凑数。
3. 引擎本身**不碰数据库**：名册与历史由调用方查好传进来，便于离线回归与复用。
4. 活动语境只影响**排序**，不会凭空造出一条带队记录。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

# 活动类型词表。与装备推荐共用同一套户外活动类型词汇，互相解释得通。
ACTIVITY_TYPES: dict[str, tuple[str, ...]] = {
    "徒步": ("徒步", "轻徒步", "穿越", "健行", "拉练"),
    "登山": ("登山", "登顶", "雪山", "高海拔"),
    "露营": ("露营", "扎营", "营地", "帐篷"),
    "溯溪": ("溯溪", "溪降", "溯水"),
    "骑行": ("骑行", "公路车", "山地车"),
    "越野跑": ("越野跑", "越野赛", "越野"),
    "瑜伽": ("瑜伽", "冥想", "拉伸"),
    "滑雪": ("滑雪", "单板", "双板"),
    "摄影": ("摄影", "旅拍", "跟拍"),
    "亲子": ("亲子", "儿童", "家庭日"),
    "研学": ("研学", "科普", "自然教育"),
}

_CONTEXT_KEYS = ("title", "location", "summary", "subtitle", "date")


def _norm(v: Any) -> str:
    return str(v or "").strip()


def activity_types(blob: str) -> tuple[str, ...]:
    """从一段文本里识别活动类型标签（按词表定义顺序返回）。"""
    if not blob:
        return ()
    return tuple(t for t, kws in ACTIVITY_TYPES.items() if any(k in blob for k in kws))


@dataclass(frozen=True)
class LeaderPick:
    """一位被推荐的领队。所有字段都来自资源库与历史记录，不做任何加工。"""

    leader_id: int
    name: str
    role: str
    score: float
    reason: str
    same_route: int = 0
    same_type: int = 0
    total: int = 0
    specialties: tuple[str, ...] = ()
    base_city: str = ""
    status: str = "active"
    already_assigned: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "leaderId": self.leader_id,
            "name": self.name,
            "role": self.role,
            "score": round(float(self.score), 2),
            "reason": self.reason,
            "sameRouteCount": self.same_route,
            "sameTypeCount": self.same_type,
            "totalCount": self.total,
            "specialties": list(self.specialties),
            "baseCity": self.base_city,
            "status": self.status,
            "alreadyAssigned": self.already_assigned,
        }


class LeaderRecommendService:
    """把「活动语境」与「历史带队记录」对到资源库里的人。构造一次可复用；无状态。"""

    route_weight = 3.0        # 带过同一地点/线路的活动，最强信号
    type_weight = 2.0         # 带过同类型活动（徒步/登山/瑜伽…）
    experience_weight = 0.6   # 累计带队量（capped），经验兜底
    specialty_weight = 1.5    # 自报擅长方向命中本次活动类型
    base_weight = 0.5         # 常驻城市与活动地点吻合

    @staticmethod
    def context_types(activity: Mapping[str, Any] | None) -> tuple[str, ...]:
        """活动类型标签：只看公开事实字段。"""
        activity = activity or {}
        return activity_types(" ".join(_norm(activity.get(k)) for k in _CONTEXT_KEYS))

    def recommend(self, roster: Sequence[Mapping[str, Any]] | None,
                  history: Sequence[Mapping[str, Any]] | None,
                  activity: Mapping[str, Any] | None,
                  *, assigned_leader_ids: Iterable[int] = (), limit: int = 3) -> list[dict[str, Any]]:
        activity = activity or {}
        location = _norm(activity.get("location"))
        types = set(self.context_types(activity))
        assigned = {int(x) for x in (assigned_leader_ids or []) if x}

        by_leader: dict[int, list[Mapping[str, Any]]] = {}
        for h in history or []:
            if not isinstance(h, Mapping):
                continue
            lid = h.get("leader_id") or h.get("leaderId")
            if not lid:
                continue
            by_leader.setdefault(int(lid), []).append(h)

        picks: list[LeaderPick] = []
        for r in roster or []:
            if not isinstance(r, Mapping):
                continue
            lid = int(r.get("id") or 0)
            if not lid:
                continue
            status = _norm(r.get("status") or "active") or "active"
            if status != "active":
                continue                      # 已停用的人不进推荐
            hist = by_leader.get(lid, [])
            same_route = sum(1 for h in hist if location and _norm(h.get("location")) == location)
            same_type = sum(1 for h in hist
                            if types and (types & set(activity_types(" ".join([_norm(h.get("title")),
                                                                              _norm(h.get("location"))])))))
            total = len(hist)
            specs = tuple(_norm(s) for s in (r.get("specialties") or ()) if _norm(s))
            spec_hit = [t for t in types if t in specs]
            base_city = _norm(r.get("base_city") or r.get("baseCity"))
            base_hit = bool(base_city and location and base_city in location)

            score = (same_route * self.route_weight
                     + same_type * self.type_weight
                     + min(total, 6) * self.experience_weight
                     + len(spec_hit) * self.specialty_weight
                     + (self.base_weight if base_hit else 0.0))
            if score <= 0:
                continue                      # 一个信号都没有：不推，别凑数
            picks.append(LeaderPick(
                leader_id=lid, name=_norm(r.get("name")), role=_norm(r.get("role") or "领队"),
                score=score, reason=self._reason(same_route, location, same_type, spec_hit,
                                                 total, base_hit, base_city),
                same_route=same_route, same_type=same_type, total=total, specialties=specs,
                base_city=base_city, status=status, already_assigned=lid in assigned,
            ))

        # 已在本团期的人排到最后（推荐的是"还没派的人"）；再按分数、id 稳定
        picks.sort(key=lambda p: (1 if p.already_assigned else 0, -p.score, p.leader_id))
        return [p.as_dict() for p in picks[:max(0, int(limit))]]

    @staticmethod
    def _reason(same_route: int, location: str, same_type: int, spec_hit: Sequence[str],
                total: int, base_hit: bool, base_city: str) -> str:
        """按信号强度给出一句能给运营看的话。"""
        if same_route and location:
            return f"带过 {same_route} 次「{location}」的活动"
        if spec_hit:
            return "擅长" + "、".join(spec_hit)
        if same_type:
            return "带过同类活动"
        if base_hit:
            return f"常驻 {base_city}"
        return f"累计带队 {total} 次"


__all__ = ["ACTIVITY_TYPES", "activity_types", "LeaderPick", "LeaderRecommendService"]

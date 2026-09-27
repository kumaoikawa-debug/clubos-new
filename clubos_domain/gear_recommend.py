"""活动「出行清单」× 商城在售装备的智能推荐。

设计纪律（与项目既有的「引用类字段」规则一致）：

1. **候选集合只能是商城里真实在售的商品**，引擎不做任何商品条目的生成或补全。
   某一项清单在商城里没有精确对应装备时，不再只留一句「暂无」：
   按 SUBSTITUTES 替代关系推荐**同类平替**，并且必须带上「平替」标记与说明，
   让顾客明确知道这不是清单里那件东西 —— 不替、也不冒充。
2. 匹配是确定性的：清单文本 → 需求标签，商品（名称 + 分类）→ 供给标签，两者求交集即为匹配。
   因此推荐免费、即时、可复现，而且结果能解释给运营看（"为什么推它"）。
3. 引擎本身**不碰数据库**：候选商品由调用方查好传进来，便于离线回归测试与复用。
4. 活动语境（标题 / 地点 / 行程）只影响**排序**，不扩大候选集合——
   排序不同不会凭空造出商品，只会把真实在售、更贴合本次活动的装备排到前面。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# 需求标签表：清单文本与商品共用同一套标签，交集即匹配。
#
# 关键词刻意避开歧义单字：
#   「水」会把「防水防滑徒步鞋」误判成饮水需求 → 饮水只用「饮水 / 水壶 / 水袋 / 保温杯」；
#   「徒步」「登山」是活动类型而非具体装备，放进 ACTIVITY_HINTS 而不是这里，
#   否则「徒步鞋」会连带把登山杖也推给只想要鞋的人。
# ---------------------------------------------------------------------------
TAXONOMY: dict[str, dict[str, Any]] = {
    "footwear": {"label": "徒步鞋袜", "emoji": "🥾", "keywords": ("鞋", "袜")},
    "clothing": {"label": "衣物面料", "emoji": "🧥",
                 "keywords": ("衣", "服装", "服饰", "裤", "速干", "抓绒", "软壳", "冲锋衣",
                              "羊毛", "排汗", "基础层", "外套", "保暖", "羽绒", "防风")},
    "sun": {"label": "防晒遮阳", "emoji": "🧢",
            "keywords": ("防晒", "遮阳", "帽", "墨镜", "太阳镜", "冰袖", "头巾")},
    "pack": {"label": "背包收纳", "emoji": "🎒",
             "keywords": ("背包", "双肩", "日行", "背负", "腰包", "驮包")},
    "trekking": {"label": "徒步支撑", "emoji": "⛰️",
                 "keywords": ("登山杖", "手杖", "护膝", "护踝")},
    "hydration": {"label": "饮水补给", "emoji": "💧",
                  "keywords": ("饮水", "饮用水", "水壶", "水袋", "保温杯", "补水")},
    "rain": {"label": "防雨", "emoji": "🌧️", "keywords": ("雨衣", "雨披", "雨具", "雨伞")},
    "lighting": {"label": "照明", "emoji": "🔦", "keywords": ("头灯", "手电", "照明")},
    "shelter": {"label": "露营住宿", "emoji": "⛺",
                "keywords": ("帐篷", "睡袋", "防潮垫", "天幕")},
    "nutrition": {"label": "能量补给", "emoji": "🍫",
                  "keywords": ("能量胶", "能量棒", "干粮", "补给", "巧克力")},
    "protection": {"label": "防护", "emoji": "🧤",
                   "keywords": ("手套", "护目镜", "面罩", "护具", "护腕")},
    "firstaid": {"label": "急救", "emoji": "🩹",
                 "keywords": ("急救", "创可贴", "药品", "医药包")},
    "eco": {"label": "环保收纳", "emoji": "♻️",
            "keywords": ("垃圾袋", "环保袋", "无痕")},
    "power": {"label": "电源续航", "emoji": "🔋",
              "keywords": ("充电宝", "移动电源", "备用电池", "充电线")},
    "comms": {"label": "通讯", "emoji": "📻", "keywords": ("对讲机", "手台", "对讲")},
    "tool": {"label": "随身工具", "emoji": "🔧",
             "keywords": ("刀具", "多功能", "哨子", "工具")},
}

# 活动语境 → 相关标签。**只用于排序**（把更贴合本次活动的真实商品排前面）。
ACTIVITY_HINTS: dict[str, tuple[str, ...]] = {
    "徒步": ("footwear", "clothing", "pack", "hydration", "trekking", "sun"),
    "登山": ("footwear", "clothing", "pack", "trekking", "hydration", "shelter"),
    "穿越": ("footwear", "clothing", "pack", "hydration"),
    "露营": ("shelter", "lighting", "nutrition", "clothing"),
    "溯溪": ("footwear", "clothing", "protection"),
    "骑行": ("protection", "clothing", "hydration", "lighting"),
    "越野跑": ("footwear", "clothing", "hydration", "nutrition"),
    "跑步": ("footwear", "clothing", "hydration"),
    "瑜伽": ("clothing",),
    "滑雪": ("clothing", "protection"),
    "摄影": ("pack",),
}

_CONTEXT_KEYS = ("title", "location", "date", "subtitle", "summary")

# ---------------------------------------------------------------------------
# 平替关系表：清单要的品类在商城缺货时，用同场景下功能最接近的品类顶上。
# 刻意保守：只收录确实「能顶一阵」的替代，不给离谱的（鞋 → 背包这种不收）。
# 顺序有意义：排在前面的替代品类优先被推荐。
# ---------------------------------------------------------------------------
SUBSTITUTES: dict[str, tuple[str, ...]] = {
    "footwear": ("trekking", "clothing"),   # 鞋袜缺 → 徒步支撑（护膝/登山杖）、功能性衣物
    "trekking": ("footwear", "clothing"),
    "sun": ("clothing",),                   # 防晒缺 → 长袖/防晒衣物
    "rain": ("clothing",),                  # 雨具缺 → 防风外套
    "protection": ("clothing",),
    "hydration": ("nutrition",),            # 饮水缺 → 能量补给
    "nutrition": ("hydration",),
    "shelter": ("lighting", "clothing"),    # 露营缺 → 照明、保暖衣物
    "lighting": ("power",),                 # 照明缺 → 电源续航
    "power": ("lighting",),
    "comms": ("power",),
}


def _text_of(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _hit_tags(text: str, table: Mapping[str, Mapping[str, Any]] = TAXONOMY) -> tuple[str, ...]:
    """按标签表的定义顺序返回命中的标签。"""
    if not text:
        return ()
    return tuple(tag for tag, meta in table.items()
                 if any(kw in text for kw in meta["keywords"]))


def _split_loose(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[\n\r;；、·|]+", raw) if p.strip()]


def normalize_checklist(master: Mapping[str, Any] | None) -> list[str]:
    """把 master.checklist 归一成字符串列表，容忍字符串 / 对象数组等历史写法。"""
    raw = (master or {}).get("checklist")
    out: list[str] = []
    if isinstance(raw, str):
        out = _split_loose(raw)
    elif isinstance(raw, (list, tuple)):
        for x in raw:
            if isinstance(x, str):
                if x.strip():
                    out.append(x.strip())
            elif isinstance(x, Mapping):
                t = _text_of(x.get("text")) or _text_of(x.get("content")) or _text_of(x.get("label"))
                if t:
                    out.append(t)
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


@dataclass(frozen=True)
class GearPick:
    """一件被推荐的真实在售商品（字段全部来自 products 表，不做任何加工）。"""

    id: int
    name: str
    price: float
    stock: int
    category: str
    image_url: str
    emoji: str
    reason: str
    tags: tuple[str, ...] = ()
    score: float = 0.0
    substitute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "price": round(float(self.price), 2),
            "stock": int(self.stock),
            "inStock": int(self.stock) > 0,
            "category": self.category,
            "imageUrl": self.image_url,
            "emoji": self.emoji,
            "reason": self.reason,
            "tags": list(self.tags),
            "substitute": self.substitute,
        }


@dataclass(frozen=True)
class ChecklistItem:
    text: str
    tags: tuple[str, ...] = ()
    tag_labels: tuple[str, ...] = ()
    matches: tuple[GearPick, ...] = ()
    substitutes: tuple[GearPick, ...] = ()
    sub_note: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.matches)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tags": list(self.tags),
            "tagLabels": list(self.tag_labels),
            "matched": self.matched,
            "matches": [m.as_dict() for m in self.matches],
            "substitutes": [s.as_dict() for s in self.substitutes],
            "subNote": self.sub_note,
        }


@dataclass(frozen=True)
class GearPlan:
    """一份完整的「出行清单 → 装备推荐」方案。"""

    items: tuple[ChecklistItem, ...] = ()
    extras: tuple[GearPick, ...] = ()
    catalog_count: int = 0
    unclassified: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        needs = len(self.items)
        matched = sum(1 for i in self.items if i.matched)
        substituted = sum(1 for i in self.items
                          if not i.matched and i.substitutes)
        return {
            "available": self.catalog_count > 0,
            "catalogCount": self.catalog_count,
            "items": [i.as_dict() for i in self.items],
            "extras": [e.as_dict() for e in self.extras],
            "unclassified": list(self.unclassified),
            "coverage": {
                "needs": needs,
                "matched": matched,
                "substituted": substituted,
                "ratio": round(matched / needs, 4) if needs else 0.0,
            },
        }


class GearRecommendService:
    """把清单项与商城商品做标签匹配。构造一次即可复用；无状态。"""

    max_per_item = 2
    max_extras = 4

    # --- 标签提取 ---------------------------------------------------------
    @staticmethod
    def need_tags(text: str) -> tuple[str, ...]:
        return _hit_tags(text)

    @staticmethod
    def product_tags(name: str, category: str) -> tuple[str, ...]:
        return _hit_tags(f"{name} {category}")

    @staticmethod
    def context_tags(master: Mapping[str, Any] | None) -> tuple[str, ...]:
        """活动语境标签：只看标题/地点等公开事实字段，用于排序偏好。"""
        master = master or {}
        parts = [_text_of(master.get(k)) for k in _CONTEXT_KEYS]
        itinerary = master.get("itinerary")
        if isinstance(itinerary, (list, tuple)):
            for x in itinerary:
                if isinstance(x, str):
                    parts.append(x)
                elif isinstance(x, Mapping):
                    parts.append(_text_of(x.get("content")) or _text_of(x.get("text")))
        blob = " ".join(p for p in parts if p)
        tags: list[str] = []
        for hint, mapped in ACTIVITY_HINTS.items():
            if hint in blob:
                for t in mapped:
                    if t not in tags:
                        tags.append(t)
        return tuple(tags)

    # --- 打分 -------------------------------------------------------------
    @staticmethod
    def _score(need: tuple[str, ...], pick_tags: tuple[str, ...], name: str) -> float:
        inter = [t for t in need if t in pick_tags]
        if not inter:
            return 0.0
        score = float(len(inter))
        for t in inter:
            # 商品名本身命中比分类命中更具体，权重更高
            if any(kw in name for kw in TAXONOMY[t]["keywords"]):
                score += 0.5
        return score

    @classmethod
    def _to_pick(cls, row: Mapping[str, Any], need: tuple[str, ...] | None = None,
                 *, reason: str | None = None) -> GearPick:
        name = str(row.get("name") or "")
        category = str(row.get("category") or "")
        tags = cls.product_tags(name, category)
        emoji = TAXONOMY[tags[0]]["emoji"] if tags else "🧰"
        if reason is None:
            hit = [t for t in (need or ()) if t in tags] or list(tags)
            reason = f"契合「{TAXONOMY[hit[0]]['label']}」" if hit else "商城在售"
        return GearPick(
            id=int(row.get("id") or 0),
            name=name,
            price=float(row.get("price") or 0),
            stock=int(row.get("stock") or 0),
            category=category,
            image_url=str(row.get("image_url") or ""),
            emoji=emoji,
            reason=reason,
            tags=tags,
            score=cls._score(need or (), tags, name) if need else 0.0,
        )

    # --- 主流程 -----------------------------------------------------------
    def plan(self, products: Sequence[Mapping[str, Any]] | None,
             master: Mapping[str, Any] | None = None) -> GearPlan:
        rows = [p for p in (products or []) if isinstance(p, Mapping)]
        catalog_count = len(rows)
        if not rows:
            # 商城没有在售商品时不编造任何条目，只如实回一份空方案
            items = tuple(ChecklistItem(text=t, tags=self.need_tags(t),
                                        tag_labels=self._labels(self.need_tags(t)))
                          for t in normalize_checklist(master))
            return GearPlan(items=items, extras=(), catalog_count=0)

        unclassified = tuple(str(p.get("name") or "") for p in rows
                             if not self.product_tags(str(p.get("name") or ""),
                                                      str(p.get("category") or "")))

        ctx = self.context_tags(master)
        used: set[int] = set()
        items: list[ChecklistItem] = []

        for text in normalize_checklist(master):
            need = self.need_tags(text)
            scored: list[GearPick] = []
            for p in rows:
                tags = self.product_tags(str(p.get("name") or ""), str(p.get("category") or ""))
                if not need or not tags:
                    continue
                score = self._score(need, tags, str(p.get("name") or ""))
                if score <= 0:
                    continue
                pick = self._to_pick(p, need)
                scored.append(GearPick(**{**pick.__dict__, "score": score}))
            # 打分高者优先；同分时有货优先，再按 id 稳定
            scored.sort(key=lambda x: (-x.score, 0 if x.stock > 0 else 1, x.id))
            chosen = tuple(scored[:self.max_per_item])
            for c in chosen:
                used.add(c.id)
            # --- 平替：精确匹配落空时，按 SUBSTITUTES 找同类里最接近的真实在售商品 ---
            subs: list[GearPick] = []
            sub_note = ""
            if not chosen and need:
                # 替代品类按需求顺序去重；需求里已有的品类不算替代（那叫精确匹配）
                fallbacks: list[str] = []
                for t in need:
                    for f in SUBSTITUTES.get(t, ()):
                        if f not in need and f not in fallbacks:
                            fallbacks.append(f)
                if fallbacks:
                    cand: list[GearPick] = []
                    for p in rows:
                        pid = int(p.get("id") or 0)
                        tags = self.product_tags(str(p.get("name") or ""), str(p.get("category") or ""))
                        if not tags or pid in used:
                            continue
                        hit_f = [t for t in fallbacks if t in tags]
                        if not hit_f:
                            continue
                        pick = self._to_pick(p, need)
                        # 命中的替代品类越靠前越贴近原需求（查它在 fallbacks 里的位次，
                        # 不是在 hit_f 里 —— hit_f 只含该商品命中的标签，index 恒为 0）；
                        # 名称直接命中再加分
                        cand.append(GearPick(**{**pick.__dict__, "score": float(len(fallbacks) - fallbacks.index(hit_f[0]))}))
                    if cand:
                        cand.sort(key=lambda x: (-x.score, 0 if x.stock > 0 else 1, x.id))
                        top = cand[0]
                        hit_f = next(t for t in fallbacks if t in top.tags)
                        need_label = self._labels(need)[0] if self._labels(need) else "该装备"
                        fall_label = TAXONOMY[hit_f]["label"]
                        top = GearPick(**{**top.__dict__, "substitute": True,
                                          "reason": f"商城暂无「{need_label}」，推荐相近的「{fall_label}」类装备"})
                        subs = [top]
                        used.add(top.id)
                        sub_note = f"商城暂无{need_label}，已按同类推荐"
            items.append(ChecklistItem(text=text, tags=need,
                                       tag_labels=self._labels(need), matches=chosen,
                                       substitutes=tuple(subs), sub_note=sub_note))

        extras_rows = [p for p in rows
                       if int(p.get("id") or 0) not in used
                       and self.product_tags(str(p.get("name") or ""), str(p.get("category") or ""))]
        extras = [self._to_pick(p, ctx, reason="本场活动常用装备") for p in extras_rows]
        # 语境命中的排前面；其次有货优先；再按契合度与 id 稳定
        extras.sort(key=lambda x: (0 if (set(x.tags) & set(ctx)) else 1,
                                   0 if x.stock > 0 else 1, -x.score, x.id))
        return GearPlan(items=tuple(items), extras=tuple(extras[:self.max_extras]),
                        catalog_count=catalog_count, unclassified=unclassified)

    @staticmethod
    def _labels(tags: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(TAXONOMY[t]["label"] for t in tags if t in TAXONOMY)


__all__ = [
    "TAXONOMY", "ACTIVITY_HINTS", "SUBSTITUTES", "GearPick", "ChecklistItem", "GearPlan",
    "GearRecommendService", "normalize_checklist",
]

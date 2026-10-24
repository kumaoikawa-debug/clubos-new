# 优惠券重设计 — 隔离实例 API 级验收（2026-10-24）
# 覆盖：分栏编辑器提交的全部字段（创建 + 编辑）+ target_club_id PATCH 新通道
#      + 投放配对校验（400 分支）+ 切换范围时自动清空配对字段。
# 用法：python3 tests/coupon_redesign_api_check.py [baseUrl]
import io, json, sys, urllib.request, urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8015").rstrip("/")
results = []

def rec(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + name + (("   | " + str(extra)) if extra != "" else ""))

def call(method, path, payload=None):
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}

# 取一个真实俱乐部 id 供「指定俱乐部」用例
st, clubs = call("GET", "/api/platform/clubs")
club_id = int(clubs[0]["id"]) if st == 200 and clubs else 1
print("== 使用俱乐部 id=%s 做定向投放用例 ==" % club_id)

# ── ① 创建：活动券 · 城市投放 · 固定有效期 · 限领 2 · 不允许分享 ──────────
body = {
    "title": "API 验收券 · 城市固定期",
    "benefitType": "activity_coupon",
    "mode": "threshold", "thresholdAmount": 120, "cashValue": 30,
    "stock": 88, "claimMode": "claim", "targetType": "city", "targetCity": "成都",
    "imageKey": "forest", "perUserLimit": 2, "shareEnabled": False,
    "validFrom": "2026-11-01 09:00", "validTo": "2026-12-31 23:59",
    "description": "仅限活动报名抵扣，不与其他优惠同享"
}
st, res = call("POST", "/api/platform/coupons", body)
rec("创建城市券（200 + couponId）", st == 200 and res.get("couponId"), "%s %s" % (st, res))
cid = int(res.get("couponId") or 0)

# ── ② 创建：装备券 · 指定俱乐部投放（targetClubId 创建通道，既有）────────
# 长期有效（不带 valid_from/to），保证 C 端 claimable 能圈到它
body2 = dict(body, title="API 验收券 · 定向俱乐部", benefitType="gear_coupon",
             targetType="club", targetCity=None, targetClubId=club_id,
             validFrom=None, validTo=None)
st, res2 = call("POST", "/api/platform/coupons", body2)
rec("创建定向俱乐部券（200）", st == 200 and res2.get("couponId"), "%s %s" % (st, res2))
cid2 = int(res2.get("couponId") or 0)

# ── ③ 列表能读回全部字段 ────────────────────────────────────────────────
st, lst = call("GET", "/api/platform/coupons")
one = next((x for x in lst if int(x["id"]) == cid), None) if st == 200 else None
rec("列表读回城市券字段", one is not None and one["target_city"] == "成都"
    and one["threshold_amount"] == 120 and one["per_user_limit"] == 2
    and int(one["share_enabled"]) == 0 and one["valid_to"] == "2026-12-31 23:59"
    and one["mode"] == "threshold" and one["description"] == body["description"],
    json.dumps({k: one.get(k) for k in ("target_city","threshold_amount","per_user_limit","share_enabled","valid_to","mode")}, ensure_ascii=False) if one else "not found")
two = next((x for x in lst if int(x["id"]) == cid2), None) if st == 200 else None
rec("列表读回定向俱乐部 target_club_id", two is not None and two.get("target_club_id") == club_id,
    json.dumps({"target_club_id": two.get("target_club_id"), "target_type": two.get("target_type")}, ensure_ascii=False) if two else "not found")

# ── ④ PATCH 新通道：改 target_club_id（snake_case，与前端编辑器一致）─────
st, res3 = call("PATCH", "/api/platform/coupons/%d" % cid, {"target_club_id": club_id, "target_type": "club"})
rec("PATCH target_club_id（200）", st == 200, "%s %s" % (st, res3))
st, lst = call("GET", "/api/platform/coupons")
one = next((x for x in lst if int(x["id"]) == cid), None)
rec("PATCH 后 target_club_id 落库", one is not None and one.get("target_club_id") == club_id,
    json.dumps({"target_club_id": one.get("target_club_id")}, ensure_ascii=False) if one else "not found")

# ── ④b 驼峰兼容（旧版改投放弹层发的是驼峰，曾被白名单静默丢弃）───────────
st, res3b = call("PATCH", "/api/platform/coupons/%d" % cid, {"targetType": "all_users", "targetCity": None, "targetClubId": None})
rec("PATCH 驼峰键也被接受（200）", st == 200, "%s %s" % (st, res3b))
st, lst = call("GET", "/api/platform/coupons")
one = next((x for x in lst if int(x["id"]) == cid), None)
rec("驼峰 PATCH 同样落库", one is not None and one.get("target_type") == "all_users"
    and one.get("target_club_id") is None and one.get("target_city") is None,
    json.dumps({"target_type": one.get("target_type"), "target_club_id": one.get("target_club_id")}, ensure_ascii=False) if one else "not found")

# ── ⑤ 配对校验：target_type=club 但不给俱乐部 → 400 ─────────────────────
st, res4 = call("PATCH", "/api/platform/coupons/%d" % cid, {"target_type": "club", "target_club_id": None})
rec("club 投放清空俱乐部 → 400", st == 400 and "俱乐部" in str(res4.get("detail")), "%s %s" % (st, res4))

# ── ⑥ 配对校验：target_type=city 但不给城市 → 400 ───────────────────────
st, res5 = call("PATCH", "/api/platform/coupons/%d" % cid, {"target_type": "city", "target_city": None})
rec("city 投放清空城市 → 400", st == 400 and "城市" in str(res5.get("detail")), "%s %s" % (st, res5))

# ── ⑦ 切到非定向范围时自动清空配对字段（前端发 null，后端照写）───────────
st, res6 = call("PATCH", "/api/platform/coupons/%d" % cid,
                {"target_type": "all_users", "target_city": None, "target_club_id": None})
rec("切到全部用户并清空配对（200）", st == 200, "%s %s" % (st, res6))
st, lst = call("GET", "/api/platform/coupons")
one = next((x for x in lst if int(x["id"]) == cid), None)
rec("配对字段已清空", one is not None and one.get("target_club_id") is None
    and one.get("target_city") is None and one.get("target_type") == "all_users",
    json.dumps({"target_type": one.get("target_type"), "target_club_id": one.get("target_club_id"), "target_city": one.get("target_city")}, ensure_ascii=False) if one else "not found")

# ── ⑧ 其余 PATCH 字段照旧工作（snake_case，与前端编辑器一致）────────────
st, res7 = call("PATCH", "/api/platform/coupons/%d" % cid, {
    "title": "API 验收券 · 改名后", "cash_value": 66, "threshold_amount": 0,
    "mode": "fixed", "valid_from": None, "valid_to": "2027-01-31 23:59",
    "per_user_limit": 3, "share_enabled": True, "description": "改过的说明",
    "image_key": "mono", "stock": 120, "claim_mode": "grant", "status": "active"
})
rec("PATCH 全量其余字段（200）", st == 200, "%s %s" % (st, res7))
st, lst = call("GET", "/api/platform/coupons")
one = next((x for x in lst if int(x["id"]) == cid), None)
rec("PATCH 全量字段落库", one is not None and one["title"] == "API 验收券 · 改名后"
    and one["cash_value"] == 66 and one["threshold_amount"] == 0
    and one["mode"] == "fixed" and one["valid_to"] == "2027-01-31 23:59"
    and one["per_user_limit"] == 3 and int(one["share_enabled"]) == 1
    and one["description"] == "改过的说明" and one["image_key"] == "mono"
    and one["stock"] == 120 and one["claim_mode"] == "grant",
    json.dumps({k: one.get(k) for k in ("title","cash_value","threshold_amount","mode","valid_to","per_user_limit","image_key","stock","claim_mode")}, ensure_ascii=False) if one else "not found")

# ── ⑨ 定向俱乐部券的 C 端可见性（投放配对真实生效）──────────────────────
st, claim = call("GET", "/api/public/coupons/claimable?user_id=1&club_id=%d" % club_id)
ids = [int(x["id"]) for x in (claim or [])] if isinstance(claim, list) else []
rec("定向俱乐部券对该俱乐部可见", cid2 in ids, "coupon %s in %s" % (cid2, ids))

# ── ⑩ 状态切换（下架/上架）─────────────────────────────────────────────
st, _ = call("PATCH", "/api/platform/coupons/%d" % cid, {"status": "inactive"})
st2, lst = call("GET", "/api/platform/coupons")
one = next((x for x in lst if int(x["id"]) == cid), None)
rec("下架生效", st == 200 and one and one["status"] == "inactive")
st, _ = call("PATCH", "/api/platform/coupons/%d" % cid, {"status": "active"})
st2, lst = call("GET", "/api/platform/coupons")
one = next((x for x in lst if int(x["id"]) == cid), None)
rec("上架生效", st == 200 and one and one["status"] == "active")

# ── ⑪ 404 分支 ─────────────────────────────────────────────────────────
st, _ = call("PATCH", "/api/platform/coupons/999999", {"title": "x"})
rec("PATCH 不存在 → 404", st == 404, str(st))

failed = [n for n, ok in results if not ok]
print("\n%d/%d 通过" % (len(results) - len(failed), len(results)) + ("" if not failed else " · 失败: " + "; ".join(failed)))
sys.exit(1 if failed else 0)

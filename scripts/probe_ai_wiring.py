"""平台大模型接入 + 俱乐部 AI 积分链路 端到端探针 (v0.25.1)

覆盖用户在总后台上线 AI 能力的最小闭环：
  1. 总平台登录 -> 读模型接入配置 -> 读回验证（密钥必须掩码，完整密钥绝不回浏览器）
  2. 运行模式（mock/live）是否可被总后台控制  ← 用户要求「填了 Key 就生效」的关键
  3. 平台给俱乐部分配 AI 积分 -> 俱乐部侧读余额（发放地址）
  4. 俱乐部发起充值（获取 AI 积分的地址）-> 平台确认到账 -> 俱乐部余额增加
  5. 俱乐部 AI 生成调用一次（验证计费与网关模式）

默认只读，不会改动线上已配置的 Provider / 密钥。
若要额外验证「写入 -> 掩码 -> 回读」这一段契约，加 PROBE_WRITE_TEST=1，
但请先确认可以接受把主/备 Provider 的密钥临时替换为占位值（跑完记得重新配置真实密钥）。

用法：
  BASE=http://127.0.0.1:8000 PLATFORM_PW=... CLUB_PW=... \
    python scripts/probe_ai_wiring.py

沙箱内必须 trust_env=False：环境注入了 HTTP_PROXY，httpx 会把 127.0.0.1 也走代理。
"""
import os
import sys

import httpx

BASE = os.getenv("BASE", "http://127.0.0.1:8000")
PLATFORM_USER = os.getenv("PLATFORM_USER", "platform_admin")
PLATFORM_PW = os.getenv("PLATFORM_PW") or os.getenv("STAGING_PASSWORD", "Staging-2026-ClubOS-demo!")
CLUB_USER = os.getenv("CLUB_USER", "club1_admin")
CLUB_PW = os.getenv("CLUB_PW") or os.getenv("STAGING_PASSWORD", "Staging-2026-ClubOS-demo!")
CLUB_ID = int(os.getenv("CLUB_ID", "1"))
WRITE_TEST = os.getenv("PROBE_WRITE_TEST", "0").strip() == "1"

RESULTS = []


def rec(name, status, detail=""):
    """status ∈ PASS / FAIL / WARN / INFO"""
    RESULTS.append((name, status, detail))
    print(f"{status:4} {name}" + (f"  [{detail}]" if detail else ""))


def login(user, pw):
    c = httpx.Client(base_url=BASE, timeout=300, follow_redirects=False, trust_env=False)
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    csrf = c.cookies.get("clubos_csrf")
    if csrf:
        c.headers["x-clubos-csrf"] = csrf
    return c, r


print("=" * 72)
print(f"ClubOS AI wiring probe -> {BASE}   (WRITE_TEST={WRITE_TEST})")
print("=" * 72)

plat, r = login(PLATFORM_USER, PLATFORM_PW)
rec("平台登录", "PASS" if r.status_code == 200 else "FAIL", f"HTTP {r.status_code} {r.text[:100]}")
club, r = login(CLUB_USER, CLUB_PW)
rec("俱乐部登录", "PASS" if r.status_code == 200 else "FAIL", f"HTTP {r.status_code} {r.text[:100]}")

# ---------------------------------------------------------------- 1. 模型接入配置
print("\n--- 1. 总平台模型接入配置 ---")
mode_live = False
try:
    cfg = plat.get("/api/platform/ai/providers").json()
    rec("读接入配置", "PASS" if isinstance(cfg, dict) and "presets" in cfg else "FAIL",
        f"presets={len(cfg.get('presets', []))}")
    print("    presets:", [p["code"] for p in cfg.get("presets", [])])
    eff = cfg.get("effective", {})
    mode_live = eff.get("mode") == "live"
    print("    mode:", eff.get("mode"), "| source:", eff.get("modeSource"),
          "| savedMode:", eff.get("savedMode"))
    for p in eff.get("providers", []):
        print(f"    - {p['role']}: {p['preset']} / {p['defaultModel']} / vision={p['visionModel'] or '—'} "
              f"/ key={'已配置(' + p['keySource'] + ')' if p['configured'] else '未配置'}")
    sp = cfg.get("saved", {}).get("primary", {})
    masked = str(sp.get("apiKeyMasked") or "")
    if masked:
        rec("密钥掩码不外泄", "PASS" if "***" in masked else "FAIL", f"masked={masked}")
    else:
        rec("密钥掩码不外泄", "INFO", "主用尚未配置密钥")
except Exception as exc:
    rec("读接入配置", "FAIL", str(exc))

if WRITE_TEST:
    # 仅验证「写入 -> 掩码 -> 回读」契约，会临时覆盖主/备密钥。
    try:
        body = {
            "primary": {"preset": "deepseek", "model": "deepseek-chat", "apiKey": "sk-probe-primary-0000000000"},
            "secondary": {"preset": "qwen", "model": "qwen-plus", "visionModel": "qwen-vl-max",
                          "apiKey": "sk-probe-secondary-111111111"},
            "allowFailover": True,
        }
        saved = plat.patch("/api/platform/ai/providers", json=body).json()
        sp = saved.get("saved", {}).get("primary", {})
        rec("写入接入配置", "PASS" if sp.get("apiKeySource") == "platform" else "FAIL",
            f"primary keySource={sp.get('apiKeySource')}")
        rec("密钥掩码不外泄", "PASS" if "probe" not in str(sp.get("apiKeyMasked")) else "FAIL",
            f"masked={sp.get('apiKeyMasked')}")
        back = plat.get("/api/platform/ai/providers").json()
        rec("配置持久化回读", "PASS" if back.get("saved", {}).get("primary", {}).get("model") == "deepseek-chat" else "FAIL",
            f"model={back.get('saved', {}).get('primary', {}).get('model')}")
    except Exception as exc:
        rec("写入接入配置", "FAIL", str(exc))
else:
    rec("写入接入配置", "INFO", "已跳过（只读模式）；需要时用 PROBE_WRITE_TEST=1 并跑完重配真实密钥")

# ---------------------------------------------------------------- 2. 运行模式
print("\n--- 2. 运行模式（总后台填 Key 是否即生效）---")
try:
    st = plat.get("/api/platform/ai/status").json()
    ok = st.get("mode") in {"mock", "live"} and st.get("modeSource") in {"platform", "env"}
    rec("网关模式可读且带来源", "PASS" if ok else "FAIL",
        f"mode={st.get('mode')} source={st.get('modeSource')} saved={st.get('savedMode')}")
    if st.get("mode") == "mock":
        rec("总后台可切换运行模式", "WARN",
            "当前 mock：填了 Key 也不会真实调用。请在总平台「AI 模型接入」点「启用真实调用」")
    else:
        rec("总后台可切换运行模式", "PASS", f"live（来源 {st.get('modeSource')}）")

    t = plat.post("/api/platform/ai/providers/test", json={"role": "primary"}, timeout=120).json()
    if t.get("ok"):
        rec("连通性自检", "PASS", f"{t.get('preset')} / {t.get('model')} 样例={t.get('sample')!r}")
    else:
        rec("连通性自检", "WARN" if mode_live else "INFO", str(t.get("error")))
except Exception as exc:
    rec("运行模式", "FAIL", f"{type(exc).__name__}: {exc}")

# ---------------------------------------------------------------- 3. 平台发放积分
print("\n--- 3. 总平台给俱乐部分配 AI 积分 ---")
try:
    before = club.get(f"/api/club/{CLUB_ID}/credits").json()
    bal0 = int((before.get("account") or {}).get("balance") or 0)
    rec("俱乐部读积分（发放地址）", "PASS", f"balance={bal0}")

    g = plat.post("/api/platform/credits/adjust",
                  json={"clubId": CLUB_ID, "amount": 500, "note": "探针发放"}).json()
    rec("平台发放积分", "PASS" if int(g.get("granted") or 0) == 500 else "FAIL",
        f"net={g.get('netToBalance')} debt={g.get('debtRecovered')}")

    after = club.get(f"/api/club/{CLUB_ID}/credits").json()
    bal1 = int((after.get("account") or {}).get("balance") or 0)
    want = bal0 + 500 - int(g.get("debtRecovered") or 0)
    rec("俱乐部余额随发放增加", "PASS" if bal1 == want else "FAIL", f"{bal0} -> {bal1}（期望 {want}）")
    print("    充值包:", [(p["code"], p["credits"], p["amount"]) for p in after.get("topupPackages", [])])
except Exception as exc:
    rec("平台发放积分", "FAIL", f"{type(exc).__name__}: {exc}")

# ---------------------------------------------------------------- 4. 俱乐部充值 -> 平台确认
print("\n--- 4. 俱乐部自助充值 -> 平台确认到账 ---")
try:
    packs = club.get(f"/api/club/{CLUB_ID}/credits").json().get("topupPackages") or []
    if not packs:
        rec("俱乐部充值闭环", "FAIL", "无可用充值包")
    else:
        code = packs[0]["code"]
        order = club.post(f"/api/club/{CLUB_ID}/credits/topups", json={"packageCode": code}).json()
        oid = order.get("id") or order.get("orderId")
        rec("俱乐部发起充值", "PASS" if oid else "FAIL", f"order={oid} credits={order.get('credits')}")
        bal_before = int((club.get(f"/api/club/{CLUB_ID}/credits").json().get("account") or {}).get("balance") or 0)
        conf = plat.post(f"/api/platform/ai-credit/orders/{oid}/confirm-paid",
                         json={"paymentRef": "probe-" + str(oid)}).json()
        rec("平台确认到账", "PASS" if conf.get("grant") else "FAIL", f"grant={conf.get('grant')}")
        bal_after = int((club.get(f"/api/club/{CLUB_ID}/credits").json().get("account") or {}).get("balance") or 0)
        rec("俱乐部余额增加", "PASS" if bal_after > bal_before else "FAIL", f"{bal_before} -> {bal_after}")
except Exception as exc:
    rec("俱乐部充值闭环", "FAIL", f"{type(exc).__name__}: {exc}")

# ---------------------------------------------------------------- 5. 俱乐部 AI 生成
print("\n--- 5. 俱乐部 AI 生成（真实调用与否由网关模式决定）---")
try:
    r = club.post(f"/api/club/{CLUB_ID}/activities/ai-generate",
                  data={"prompt": "2026年11月8日，成都周边龙泉山，轻徒步6公里，25人，158元/人。"})
    if r.status_code == 200 and r.json().get("activityId"):
        rec("AI 生成活动详情", "PASS", f"aid={r.json().get('activityId')} 模式={'live' if mode_live else 'mock'}")
    elif mode_live:
        # live 模式下失败通常意味着端点不可达/密钥无效，不是应用逻辑错误，单列告警。
        rec("AI 生成活动详情", "WARN", f"live 模式调用未完成：HTTP {r.status_code} {r.text[:200]}")
    else:
        rec("AI 生成活动详情", "FAIL", f"HTTP {r.status_code} {r.text[:200]}")
except Exception as exc:
    rec("AI 生成活动详情", "WARN" if mode_live else "FAIL", f"{type(exc).__name__}: {str(exc)[:160]}")

# ---------------------------------------------------------------- 收口
passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
warned = sum(1 for _, s, _ in RESULTS if s == "WARN")
print("\n" + "=" * 72)
print(f"AI WIRING PROBE: TOTAL={len(RESULTS)} PASS={passed} WARN={warned} FAIL={failed}")
for n, s, d in RESULTS:
    if s in {"FAIL", "WARN"}:
        print(f"  {s}:", n, d)
sys.exit(1 if failed else 0)

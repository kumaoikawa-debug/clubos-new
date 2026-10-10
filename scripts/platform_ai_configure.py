"""总平台模型接入配置器（密钥只从环境变量传入，脚本本身不含任何密钥）。

用法：
  BASE=http://127.0.0.1:8000 \
  PLATFORM_USER=platform_admin PLATFORM_PW='...' \
  AI_KEY='sk-...' AI_PRESET=qwen AI_MODEL=qwen-plus AI_VISION_MODEL=qwen3-vl-plus \
  AI_MODE=live \
  python scripts/platform_ai_configure.py

可选：
  AI_SECONDARY_PRESET / AI_SECONDARY_MODEL / AI_SECONDARY_KEY （留空则不配置备用）
  AI_FALLBACK_KEY_CLEAR=1  清掉备用 Provider 上遗留的密钥
"""
import os
import sys

import httpx

BASE = os.getenv("BASE", "http://127.0.0.1:8000")
USER = os.getenv("PLATFORM_USER", "platform_admin")
PW = os.getenv("PLATFORM_PW") or os.getenv("STAGING_PASSWORD", "Staging-2026-ClubOS-demo!")
KEY = os.getenv("AI_KEY", "").strip()
PRESET = os.getenv("AI_PRESET", "qwen").strip()
MODEL = os.getenv("AI_MODEL", "qwen-plus").strip()
VISION = os.getenv("AI_VISION_MODEL", "qwen3-vl-plus").strip()
MODE = os.getenv("AI_MODE", "live").strip()

if not KEY:
    print("缺少 AI_KEY（请用环境变量传入，不要写进脚本）")
    sys.exit(2)


def main():
    c = httpx.Client(base_url=BASE, timeout=60, follow_redirects=False, trust_env=False)
    r = c.post("/api/auth/login", json={"username": USER, "password": PW})
    if r.status_code != 200:
        print("登录失败:", r.status_code, r.text[:200])
        return 1
    csrf = c.cookies.get("clubos_csrf")
    if csrf:
        c.headers["x-clubos-csrf"] = csrf

    payload = {
        "mode": MODE,
        "primary": {"preset": PRESET, "model": MODEL, "visionModel": VISION, "enabled": True, "apiKey": KEY},
        "allowFailover": True,
    }
    sec_preset = os.getenv("AI_SECONDARY_PRESET", "").strip()
    if sec_preset:
        payload["secondary"] = {
            "preset": sec_preset,
            "model": os.getenv("AI_SECONDARY_MODEL", "").strip(),
            "enabled": True,
            "apiKey": os.getenv("AI_SECONDARY_KEY", "").strip() or None,
        }
    elif os.getenv("AI_FALLBACK_KEY_CLEAR", "").strip() == "1":
        payload["secondary"] = {"apiKey": None}

    r = c.patch("/api/platform/ai/providers", json=payload)
    print("保存配置 ->", r.status_code, r.text[:200])
    if r.status_code != 200:
        return 1

    cfg = c.get("/api/platform/ai/providers").json()
    sp, ss = cfg["saved"]["primary"], cfg["saved"]["secondary"]
    eff = cfg["effective"]
    print(f"  主用: {sp['preset']} / {sp['model']} / vision={sp['visionModel'] or '—'} / key={sp['apiKeyMasked']}（来源 {sp['apiKeySource']}）")
    print(f"  备用: {ss['preset'] or '—'} / {ss['model'] or '—'} / key={ss['apiKeyMasked'] or '未配置'}（来源 {ss['apiKeySource']}）")
    print(f"  运行模式: {eff['mode']}（来源 {eff['modeSource']}，已保存 {eff['savedMode']}）")

    print("\n连通性自检:")
    for role in ("primary", "secondary"):
        t = c.post("/api/platform/ai/providers/test", json={"role": role}).json()
        if t.get("ok"):
            print(f"  {role}: OK · {t.get('preset')} / {t.get('model')} · 样例 {t.get('sample')!r}")
        else:
            print(f"  {role}: 未通过 · {t.get('error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

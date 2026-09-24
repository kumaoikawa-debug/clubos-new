"""诊断：环境注入的 HTTP(S)_PROXY 是否会污染平台侧大模型调用。

对比 trust_env=True（默认，会读 HTTP_PROXY/HTTPS_PROXY/NO_PROXY）
与 trust_env=False（忽略环境代理，直连）两种行为。
"""
import os
import sys

import httpx

URL = os.getenv("PROBE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
BODY = {"model": "qwen-plus", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 4}

print("环境代理变量:")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy"):
    if os.getenv(k):
        print(f"  {k}={os.getenv(k)}")

for trust in (True, False):
    label = "trust_env=True （读取环境代理）" if trust else "trust_env=False（直连，忽略环境代理）"
    try:
        with httpx.Client(timeout=15, trust_env=trust) as c:
            r = c.post(URL, json=BODY, headers={"Authorization": "Bearer sk-diag"})
        print(f"{label}: HTTP {r.status_code} · body={r.text[:120]!r}")
    except Exception as exc:
        print(f"{label}: {type(exc).__name__}: {str(exc)[:160]}")

"""PEAKTRACE 出站 sharecode 门禁回归（2026-11-16）。

根因：托管网关对 PEAKTRACE app 也启用 sharecode 门禁，不带 ?sharecode= 的
服务器出站请求一律被挡成 403「链接不完整」页（不是上游业务接口的 token 鉴权）。
`peaktrace_catalog.fetch_catalog()` 曾漏加 sharecode，导致后台「测试连接」与
商品同步全 403。本测试用 httpx.MockTransport 拦截真实 HTTP 层，断言每一条出站
URL（总目录 / 商品 / 分类 / 库存）都带 sharecode，不依赖外网、可入库。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import httpx
from clubos_domain import peaktrace_catalog as pc
from clubos_domain import integration_config as icfg

checks = []
def check(name, ok, detail=''):
    assert ok, f'{name} | {detail}'
    checks.append(name)
    print('PASS ', name, (('| ' + str(detail)[:120]) if detail else ''))

# 记录每条出站 URL，返回最小可用 JSON
seen = []
def handler(request: httpx.Request) -> httpx.Response:
    seen.append(str(request.url))
    path = request.url.path
    if path == '/api/b2b/catalog':
        return httpx.Response(200, json={'products': [], 'categories': []})
    if path in ('/api/products', '/api/categories'):
        return httpx.Response(200, json=[])
    if path == '/api/admin/inventory/get':
        return httpx.Response(200, json={})
    return httpx.Response(404, json={})

orig_client = httpx.Client
def fake_client(*a, **kw):
    kw['transport'] = httpx.MockTransport(handler)
    return orig_client(*a, **kw)
httpx.Client = fake_client
try:
    # 1. 内置默认 config：总目录路径必须是第一条且带 sharecode
    seen.clear()
    cfg_default = icfg.defaults(icfg.PEAKTRACE)
    payload = pc.fetch_catalog(config=cfg_default)
    check('总目录请求带 sharecode',
          bool(seen) and 'sharecode=' + pc.UPSTREAM_SHARECODE in seen[0], seen[:2])
    check('默认 config 走 b2b 且只发目录一条', payload.get('source') == 'b2b' and len(seen) == 1, seen)

    # 2. 草稿 config（页面「测试连接」传整份表单）：路径带 query 时用 & 追加
    seen.clear()
    cfg_draft = dict(cfg_default)
    cfg_draft['catalogPath'] = '/api/b2b/catalog?lang=zh'   # 模拟运营填了带 query 的路径
    pc.fetch_catalog(config=cfg_draft)
    check('带 query 路径用 & 追加 sharecode',
          'lang=zh' in seen[0] and 'sharecode=' + pc.UPSTREAM_SHARECODE in seen[0], seen[:1])

    # 2b. 路径里已带 sharecode 时不重复追加（双参数会让网关取到不确定值）
    explicit = pc._upstream_url('https://x.test', '/api/products?sharecode=CUSTOM1')
    check('已带 sharecode 不重复追加',
          explicit.count('sharecode=') == 1 and 'CUSTOM1' in explicit, explicit)

    # 3. 目录不可用（200 但字段不对）→ 回退 legacy：商品/分类/库存三条都要带码
    seen.clear()
    cfg_legacy = dict(cfg_default)
    cfg_legacy['productsKey'] = 'not_products'    # 让 b2b 解析失败，触发回退
    pc.fetch_catalog(config=cfg_legacy)
    check('回退商品请求带 sharecode',
          len(seen) >= 2 and 'sharecode=' + pc.UPSTREAM_SHARECODE in seen[1], seen)
    check('回退分类请求带 sharecode',
          len(seen) >= 3 and 'sharecode=' + pc.UPSTREAM_SHARECODE in seen[2], seen)
    check('回退库存请求带 sharecode',
          len(seen) >= 4 and 'sharecode=' + pc.UPSTREAM_SHARECODE in seen[3], seen)

    # 4. probe_catalog（测试连接按钮）真实走 fetch_catalog，结果应 ok
    seen.clear()
    probe = pc.probe_catalog(config=cfg_default)
    check('测试连接 probe 成功', probe.get('ok') is True, probe)
    check('probe 出站带 sharecode',
          bool(seen) and 'sharecode=' + pc.UPSTREAM_SHARECODE in seen[0], seen[:1])
finally:
    httpx.Client = orig_client

print('PEAKTRACE SHARECODE GATE OK', len(checks), 'checks')

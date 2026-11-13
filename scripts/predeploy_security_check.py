#!/usr/bin/env python3
"""ClubOS NEW 上线前安全门禁（#892，2026-11-13）。

只读检查：不修改文件、不连接数据库、不发网络请求、不触碰线上。
用法：    venv/bin/python scripts/predeploy_security_check.py
退出码：  0 = 无 FAIL（允许 WARN，可继续发布流程）；1 = 存在 FAIL，禁止发布。

设计原则：只把「确定性高危、无需人工判断」的问题判为 FAIL；需要业务决策的
一律 WARN 并给出证据，避免门禁本身变成新的发布阻碍。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS: list[tuple[str, str, str]] = []  # (level, name, detail)


def rec(level: str, name: str, detail: str = '') -> None:
    RESULTS.append((level, name, detail))


def read(rel: str) -> str:
    p = ROOT / rel
    return io.open(p, encoding='utf-8').read() if p.exists() else ''


# 1) web root 敏感文件 —— 2026-11-13 线上实证：static/versions/ 下的 .db 可匿名下载
def check_webroot() -> None:
    bad_suffix = {'.db', '.sqlite', '.sqlite3', '.pem', '.key', '.p12', '.pfx'}
    hits = []
    static = ROOT / 'static'
    if static.is_dir():
        for p in static.rglob('*'):
            if not p.is_file():
                continue
            low = p.name.lower()
            if p.suffix.lower() in bad_suffix or low.startswith('.env') or '.db-' in low:
                hits.append(str(p.relative_to(ROOT)))
    if hits:
        rec('FAIL', 'web root 内存在可下载敏感文件',
            'static/ 由 app.mount 直接对外服务：' + ', '.join(hits[:5]) + (' …' if len(hits) > 5 else ''))
    else:
        rec('PASS', 'web root 内无 .db/.env/私钥文件')


# 2) .gitignore 关键规则
def check_gitignore() -> None:
    txt = read('.gitignore')
    need = ['.env', '*.db', '*.db-journal', '*.pem', '*.key', 'static/versions/', 'static/uploads/**']
    miss = [x for x in need if x not in txt]
    if miss:
        rec('FAIL', '.gitignore 缺少关键规则', '缺失：' + ', '.join(miss))
    else:
        rec('PASS', '.gitignore 覆盖环境变量/数据库/私钥/web root 快照')
    # 模板文件不能被误伤
    for tpl in ('.env.example', '.env.production.example', 'selfhost/.env.example',
                'services/medusa/.env.template'):
        if not (ROOT / tpl).exists():
            continue
        r = subprocess.run(['git', 'check-ignore', '-q', tpl], cwd=ROOT, capture_output=True)
        if r.returncode == 0:
            rec('FAIL', '环境变量模板被 .gitignore 误伤', tpl)


# 3) 四端 shared.js 版本一致性（回归护栏同款口径）
def check_shared_version() -> None:
    vers = {}
    for end in ('web', 'club', 'leader', 'platform'):
        h = read(f'static/{end}/index.html')
        m = re.search(r'shared\.js\?v=([0-9a-z]+)', h)
        vers[end] = m.group(1) if m else '(missing)'
    if len(set(vers.values())) != 1 or '(missing)' in vers.values():
        rec('FAIL', '四端 shared.js 版本不一致', str(vers))
    else:
        rec('PASS', '四端 shared.js 版本一致', list(vers.values())[0])


# 4) app.py 重复路由（同 method + path 注册两次，后者永不命中）
def check_duplicate_routes() -> None:
    app = read('app.py')
    pat = re.compile(r'^@app\.(get|post|patch|put|delete)\(\s*[\'"]([^\'"]+)[\'"]', re.M)
    seen: dict[tuple[str, str], int] = {}
    dups = []
    for m in pat.finditer(app):
        key = (m.group(1).upper(), m.group(2))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            dups.append(f'{key[0]} {key[1]}')
    if dups:
        rec('WARN', '存在重复注册的路由（需人工合并）', '; '.join(dups[:5]))
    else:
        rec('PASS', '无重复注册路由')


# 5) 生产 fail-closed 校验与并发守卫不能被摘除
def check_guards() -> None:
    sec = read('security_v025.py')
    gate = read('scripts/predeploy_security_check.py')
    must_gate = ['CLUBOS_PLATFORM_ADMIN_PASS', 'PEAKTRACE_API_TOKEN',
                 'EXTERNAL_FULFILLMENT_WEBHOOK_TOKEN']
    miss = [x for x in must_gate if x not in gate]
    if 'Unsafe production configuration' not in sec or miss:
        rec('FAIL', '生产配置校验或发布前密钥门禁被移除', '缺失：' + ', '.join(miss))
    else:
        rec('PASS', '生产配置校验完整（启动 fail-closed + 发布前密钥门禁）')
    if '默认密码 {pwd}' in sec:
        rec('FAIL', '管理员明文口令仍写入 stdout')
    else:
        rec('PASS', '管理员口令不再写入访问日志')

    mall = read('clubos_domain/points_mall.py')
    must_mall = ['club_points_balance>=?', 'stock>0', "status!='verified'", 'rowcount']
    miss2 = [x for x in must_mall if x not in mall]
    if miss2:
        rec('FAIL', '积分商城并发守卫被移除', '缺失：' + ', '.join(miss2))
    else:
        rec('PASS', '积分余额/库存/核销均为条件 UPDATE + rowcount 守卫')


# 6) 内置弱默认凭据：demo 允许，但必须已被生产校验拦住
def check_default_creds() -> None:
    sec = read('security_v025.py')
    ext = read('clubos_domain/external_fulfillment.py')
    found = []
    if "'ClubOS@2026Admin!'" in sec:
        found.append('security_v025.py 默认管理员口令')
    if "'peaktrace-admin'" in ext:
        found.append('external_fulfillment.py 上游 token 默认值')
    gate = read('scripts/predeploy_security_check.py')
    guarded = all(x in gate for x in ('CLUBOS_PLATFORM_ADMIN_PASS', 'PEAKTRACE_API_TOKEN'))
    if found and guarded:
        rec('WARN', 'demo 模式仍含内置默认凭据（发布前门禁会拦截生产缺失）', '; '.join(found))
    elif found:
        rec('FAIL', '存在内置默认凭据且发布门禁未覆盖', '; '.join(found))
    else:
        rec('PASS', '无内置默认凭据')


# 7) 会话凭据走 URL（日志/Referer 泄露面）
def check_url_token() -> None:
    hits = []
    for p in (ROOT / 'static').rglob('*.js'):
        try:
            s = io.open(p, encoding='utf-8').read()
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r'\?(token|sid)=\$\{', s):
            hits.append(str(p.relative_to(ROOT)))
    if hits:
        rec('WARN', '会话/工单令牌经 URL 查询串传递',
            ', '.join(hits[:4]) + '；建议改 Header 并关闭 uvicorn access-log')
    else:
        rec('PASS', '未发现 URL 传递令牌')


# 8) Git 历史中的真实敏感文件
def check_git_history() -> None:
    r = subprocess.run(['git', 'log', '--all', '--name-only', '--pretty=format:'],
                       cwd=ROOT, capture_output=True, text=True)
    names = {x.strip() for x in r.stdout.splitlines() if x.strip()}
    bad = sorted(n for n in names
                 if re.search(r'(^|/)\.env$|\.pem$|\.key$|\.p12$|\.pfx$|\.db$|\.sqlite', n))
    if bad:
        rec('WARN', 'Git 历史中存在疑似敏感文件（需确认是否为模板）', ', '.join(bad[:5]))
    else:
        rec('PASS', 'Git 历史无 .env/私钥/数据库文件（无需改写历史）')


# 9) 运行模式与体积
def check_runtime() -> None:
    mode = os.getenv('CLUBOS_SECURITY_MODE', 'demo').strip().lower()
    if mode == 'production':
        required = ['CLUBOS_DB_PATH', 'CLUBOS_AUTH_SIGNING_KEY', 'CLUBOS_PUBLIC_BASE_URL',
                    'CLUBOS_COMMERCE_WEBHOOK_SECRET', 'CLUBOS_PLATFORM_ADMIN_PASS',
                    'PEAKTRACE_API_TOKEN', 'EXTERNAL_FULFILLMENT_WEBHOOK_TOKEN']
        miss = [x for x in required if not os.getenv(x)]
        if miss:
            rec('FAIL', '生产模式缺少必需环境变量', ', '.join(miss))
        else:
            rec('PASS', '生产模式必需环境变量齐备')
    else:
        rec('WARN', '当前为 demo/内测模式',
            '正式对客前须设 CLUBOS_SECURITY_MODE=production；生产模式会忽略 CLUBOS_TEST_OPEN')
    if os.getenv('CLUBOS_TEST_OPEN', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        rec('WARN', 'CLUBOS_TEST_OPEN 已开启：管理端免登录（内测专用）')

    total = 0
    static = ROOT / 'static'
    if static.is_dir():
        for p in static.rglob('*'):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    mb = total / 1024 / 1024
    if mb > 100:
        rec('WARN', f'static/ 体积 {mb:.1f} MB，发布前须 hold',
            'uploads/versions/demo/outputs 与顶层 *.db/预览产物应移出发布目录')
    else:
        rec('PASS', f'static/ 体积 {mb:.1f} MB')


def main() -> int:
    for fn in (check_webroot, check_gitignore, check_shared_version,
               check_duplicate_routes, check_guards, check_default_creds,
               check_url_token, check_git_history, check_runtime):
        fn()
    icons = {'PASS': 'PASS', 'FAIL': 'FAIL', 'WARN': 'WARN'}
    for level, name, detail in RESULTS:
        print(f'[{icons[level]}] {name}' + (f' :: {detail}' if detail else ''))
    n_fail = sum(1 for x in RESULTS if x[0] == 'FAIL')
    n_warn = sum(1 for x in RESULTS if x[0] == 'WARN')
    n_pass = sum(1 for x in RESULTS if x[0] == 'PASS')
    print(f'\nRESULT: PASS={n_pass} WARN={n_warn} FAIL={n_fail} '
          f'-> {"BLOCK RELEASE" if n_fail else "OK TO PROCEED (review WARN)"}')
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main())

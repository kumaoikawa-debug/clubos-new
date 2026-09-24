"""运行模式开关自检：总平台后台配置 > 环境变量；生产环境禁止切回 mock。

用法：python scripts/check_ai_mode.py
使用独立的临时库，不触碰项目主库。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')

TMP_DB = os.path.join(tempfile.mkdtemp(prefix='clubos_aimode_'), 'clubos.db')
os.environ['CLUBOS_DB_PATH'] = TMP_DB

import ai_gateway as g  # noqa: E402
import db  # noqa: E402

db.init_db()

fails = []


def chk(name, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{name}: got={got!r} want={want!r}")
    print(("PASS " if ok else "FAIL ") + f"{name}  got={got!r}")


def set_mode(mode):
    g.update_platform_provider_config({'mode': mode})


# --- 非生产：后台模式优先，其次环境变量 -------------------------------
os.environ.pop('MOCK_AI', None)
set_mode('auto')
chk("auto + 未设 MOCK_AI -> mock", g.effective_gateway_mode(), ('mock', 'env'))

os.environ['MOCK_AI'] = '0'
chk("auto + MOCK_AI=0 -> live", g.effective_gateway_mode(), ('live', 'env'))

os.environ['MOCK_AI'] = '1'
chk("auto + MOCK_AI=1 -> mock", g.effective_gateway_mode(), ('mock', 'env'))

set_mode('live')
chk("后台 live 覆盖 MOCK_AI=1", g.effective_gateway_mode(), ('live', 'platform'))

set_mode('mock')
chk("后台 mock 覆盖 MOCK_AI=0", g.effective_gateway_mode(), ('mock', 'platform'))

set_mode('auto')
chk("回到 auto 后沿用环境变量", g.effective_gateway_mode(), ('mock', 'env'))

try:
    set_mode('nonsense')
    chk("非法 mode 被拒绝", False, True)
except ValueError:
    chk("非法 mode 被拒绝", True, True)

st = g.gateway_status()
chk("gateway_status.modeSource", st['modeSource'], 'env')
chk("gateway_status.savedMode", st['savedMode'], 'auto')
chk("gateway_status.modeLockedByProduction", st['modeLockedByProduction'], False)
cfg = g.platform_provider_config()
chk("平台配置回读 saved.mode", cfg['saved']['mode'], 'auto')

# --- 生产：禁止把模式切回 mock ----------------------------------------
set_mode('mock')  # 先在非生产下把 mock 写进库
os.environ['CLUBOS_SECURITY_MODE'] = 'production'
os.environ['MOCK_AI'] = '0'
chk("生产忽略库里的 mock（回退环境变量）", g.effective_gateway_mode(), ('live', 'env'))
chk("生产 status 标记锁定", g.gateway_status()['modeLockedByProduction'], True)

try:
    set_mode('mock')
    chk("生产拒绝写入 mock", False, True)
except ValueError:
    chk("生产拒绝写入 mock", True, True)

set_mode('live')
chk("生产允许切到 live", g.effective_gateway_mode(), ('live', 'platform'))

print()
if fails:
    print(f"AI MODE CHECK: {len(fails)} FAILED")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("AI MODE CHECK: ALL PASS")

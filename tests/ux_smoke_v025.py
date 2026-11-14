"""Static UX integration verification; no fake business API tests.

2026-11-14 · V9 修复：原断言把 club 端 data-view 写死为 10 个、platform 写死为
13 个，而当前导航早已拆成 24 / 23 个子页（且部分旧名已改名），断言永远为假，
又因为没被纳入 regression_v025.sh 而长期没暴露。改成「核心导航必须存在 +
所有本地视图都有对应容器」的增量式护栏：新增子页不再误伤，少一个容器仍会红。
"""
from pathlib import Path
import re,subprocess
ROOT=Path(__file__).resolve().parents[1]

# 核心导航（重命名后与当前 index.html 对齐）：这些入口一个都不能少。
REQUIRED={
 'platform':{'pdash','clubs','credits-acct','ppoints','pbenefits','products','supp-po',
             'warehouse','fin-settle','an-biz','orders','aftersales','commissions'},
 'club':{'dash','activities','content-channel','regs-list','execution','points',
         'members-list','mall-overview','analytics','settings','camp','settingsHub'},
}
# 这些按钮跳到独立页面（不是本页 section），不要求存在同名容器。
EXTERNAL_VIEWS={'settingsHub'}

for role in ('platform','club','web'):
 s=(ROOT/'static'/role/'index.html').read_text()
 assert '/static/ux/clubos-ux.css' in s
 assert '/static/ux/clubos-ux.js' in s
 if role=='web':
  assert '/static/ux/workflows-web.js' in s and '<nav class="web-nav"' in s
  continue
 assert '/static/ux/workflows-'+role+'.js' in s
 views=re.findall(r'data-view="([^"]+)"',s)
 required=REQUIRED[role]
 assert required.issubset(set(views)),(role,'core navigation missing',sorted(required-set(views)))
 assert len(views)>=len(required),(role,'navigation shrank',len(views))
 ids=set(re.findall(r'id="([^"]+)"',s))
 missing=[v for v in set(views)-EXTERNAL_VIEWS if v not in ids]
 assert not missing,(role,'view container missing',missing)
 assert all(f'id="{view}"' in s for view in required-EXTERNAL_VIEWS),(role,'core view missing')
assert 'openSettingsHub' in (ROOT/'static/club/index.html').read_text()
assert 'purchaseReturnList' in (ROOT/'static/platform/index.html').read_text()
assert 'OPEN SOURCE' not in (ROOT/'static/ux/clubos-ux.css').read_text()
js_files=sorted((ROOT/'static/ux').glob('*.js'))
for p in js_files:
 subprocess.run(['node','--check',str(p)],check=True,stdout=subprocess.DEVNULL)
print('UX_STATIC_INTEGRATION_PASS: 3 fronts, core nav + view containers, %d JS syntax checks, procurement returns'%len(js_files))

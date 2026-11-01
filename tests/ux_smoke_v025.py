"""Static UX integration verification; no fake business API tests."""
from pathlib import Path
import re,subprocess
ROOT=Path(__file__).resolve().parents[1]
for role,n in [('platform',13),('club',12),('web',4)]:
 s=(ROOT/'static'/role/'index.html').read_text()
 assert '/static/ux/clubos-ux.css' in s
 assert '/static/ux/clubos-ux.js' in s
 if role=='web': assert '/static/ux/workflows-web.js' in s and '<nav class="web-nav"' in s
 else:
  assert '/static/ux/workflows-'+role+'.js' in s
  required={'platform':{'pdash','clubs','pcredits','ppoints','pbenefits','products','supply','warehouse','finance','analytics','orders','aftersales','commissions'},
            'club':{'dash','activities','content','regs','execution','points','members','mall','analytics','settings'}}[role]
  views=re.findall(r'data-view="([^"]+)"',s)
  assert set(views)==required and len(views)==len(required),(role,'required navigation changed')
  assert all(f'id="{view}"' in s for view in required),(role,'view missing')
assert 'purchaseReturnList' in (ROOT/'static/platform/index.html').read_text()
assert 'OPEN SOURCE' not in (ROOT/'static/ux/clubos-ux.css').read_text()
for p in (ROOT/'static/ux').glob('*.js'):
 subprocess.run(['node','--check',str(p)],check=True,stdout=subprocess.DEVNULL)
print('UX_STATIC_INTEGRATION_PASS: 3 fronts, roles/nav, 7 JS syntax checks, procurement returns')

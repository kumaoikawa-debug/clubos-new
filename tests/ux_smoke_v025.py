"""Static UX integration verification; no fake business API tests."""
from pathlib import Path
import re,subprocess
ROOT=Path(__file__).resolve().parents[1]
for role,n in [('platform',13),('club',9),('web',4)]:
 s=(ROOT/'static'/role/'index.html').read_text()
 assert '/static/ux/clubos-ux.css' in s
 assert '/static/ux/clubos-ux.js' in s
 if role=='web': assert '/static/ux/workflows-web.js' in s and '<nav class="web-nav"' in s
 else:
  assert '/static/ux/workflows-'+role+'.js' in s
  assert len(re.findall(r'data-view=',s))==n,(role,'nav lost')
assert 'purchaseReturnList' in (ROOT/'static/platform/index.html').read_text()
assert 'OPEN SOURCE' not in (ROOT/'static/ux/clubos-ux.css').read_text()
for p in (ROOT/'static/ux').glob('*.js'):
 subprocess.run(['node','--check',str(p)],check=True,stdout=subprocess.DEVNULL)
print('UX_STATIC_INTEGRATION_PASS: 3 fronts, roles/nav, 7 JS syntax checks, procurement returns')

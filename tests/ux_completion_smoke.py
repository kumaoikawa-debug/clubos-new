"""UI/UX second-pass static contracts; business/API verification remains in regressions."""
from pathlib import Path
import subprocess,re

root=Path(__file__).resolve().parents[1]
fronts={
  'platform':['clubos-ux.js','experience-core.js','workflows-completion-platform.js'],
  'club':['clubos-ux.js','experience-core.js','workflows-completion-club.js'],
  'web':['clubos-ux.js','qr-local.js','experience-core.js','payment-experience.js','experience-consumer.js'],
  'leader':['clubos-ux.js','leader-experience.js'],
}
for frontend,scripts in fronts.items():
    html=(root/'static'/frontend/'index.html').read_text()
    assert 'clubos-ux.css' in html,frontend
    order=[]
    for script in scripts:
        path='/static/ux/'+script
        assert path in html,(frontend,script)
        order.append(html.index(path))
    assert order==sorted(order),(frontend,'script order matters')

api=(root/'app.py').read_text()
pieces={
    'workflows-completion-platform.js':[
        '/api/platform/warehouses','/api/platform/warehouse/move','/api/platform/warehouse/count',
        '/api/platform/settlements/preview','/api/platform/orders/',
    ],
    'workflows-completion-club.js':[
        '/api/club/','/activities/','/channel/','/execution/status',
    ],
    'payment-experience.js':[
        '/api/public/checkouts/','/api/public/activities/','/gear-checkout',
    ],
    'leader-experience.js':['/api/leader/occurrences/'],
}
for fname,targets in pieces.items():
    code=(root/'static/ux'/fname).read_text()
    for target in targets:
        assert target in code,(fname,target)
assert '@app.post(\'/api/platform/warehouse/move\')' in api
assert '@app.post(\'/api/platform/warehouse/count\')' in api
assert '@app.get(\'/api/public/checkouts/{checkout_id}\')' in api
assert '@app.post(\'/api/club/{club_id}/occurrences/{occurrence_id}/execution/status\')' in api
assert 'QR_VENDOR_LICENSE.txt' in (root/'static/ux/qr-local.js').read_text()
assert (root/'static/ux/QR_VENDOR_LICENSE.txt').is_file()
assert 'getBrandWCPayRequest' in (root/'static/ux/payment-experience.js').read_text()
assert 'simulation' not in (root/'static/ux/payment-experience.js').read_text().lower()
assert (root/'ClubOS_NEW_UIUX_Full_Interactive_Preview.html').is_file()
js=list((root/'static').rglob('*.js'))
for path in js:
    subprocess.run(['node','--check',str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
print('UX_COMPLETION_STATIC_PASS',len(fronts),'endpoints',len(js),'JS files, QR license, standalone preview')

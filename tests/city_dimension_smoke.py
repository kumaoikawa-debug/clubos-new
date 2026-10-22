# 城市维度冒烟测试（2026-10-23 用户反馈：SaaS 面向全国，后台新建活动要选城市、C 端要按城市筛选）。
# 覆盖：① 手工新建活动写入 city；② 俱乐部活动列表 / 详情带 city；③ PATCH 改 city 持久化；
#       ④ 公开活动列表带 city；⑤ 老活动（无 city 列历史）不报错、city 为空（前端按 location 兜底）。
# 另含一段 node 校验 clubos-cities.js 的兜底推导与定位最近城市逻辑。
import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tests'))
import _identity_patch  # noqa: F401
from pathlib import Path
import os, sys, subprocess

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ.setdefault('CLUBOS_TEST_OPEN','1')
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_city_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

PASS=[]
def check(name,cond,detail=''):
    assert cond,f'FAIL {name} {detail}'
    PASS.append(name); print('  ok -',name)

# ── 1) 手工新建活动，带 city ──
r=ok('post','/api/club/1/activities',json={'title':'city 测试·成都轻徒步','eventDate':'2026-11-01 08:00','location':'成都 邛崃 兴福寺','city':'成都','price':199,'capacity':30,'summary':'测试'})
aid=r.json().get('activityId')
check('create manual returns activityId',isinstance(aid,int) and aid>0,repr(r.json()))

# ── 2) 详情带 city ──
r=ok('get',f'/api/club/1/activities/{aid}')
check('detail.city == 成都',r.json().get('city')=='成都',repr(r.json().get('city')))

# ── 3) 俱乐部活动列表带 city ──
r=ok('get','/api/club/1/activities')
lst=r.json()
check('list is list',isinstance(lst,list))
target=[x for x in lst if x.get('id')==aid]
check('city present in list row',bool(target) and target[0].get('city')=='成都',repr(target))
check('list row has city key',all('city' in x for x in lst),'some rows missing city')

# ── 4) PATCH 改 city 为甘孜 ──
ok('patch',f'/api/club/1/activities/{aid}',json={'city':'甘孜','title':'city 测试·甘孜徒步'})
r=ok('get',f'/api/club/1/activities/{aid}')
check('patch city persisted == 甘孜',r.json().get('city')=='甘孜',repr(r.json().get('city')))

# ── 5) 公开活动列表带 city（公开接口只返回已发布活动，先发布）──
ok('post',f'/api/club/1/activities/{aid}/publish')
r=ok('get','/api/public/clubs/1/activities')
pub=r.json()
check('public list is list',isinstance(pub,list))
pt=[x for x in pub if x.get('id')==aid]
check('public row has city == 甘孜',bool(pt) and pt[0].get('city')=='甘孜',repr(pt))
check('public rows all carry city key',all('city' in x for x in pub),'some public rows missing city')

# ── 6) 老数据兜底：不传 city，location 仍可新建，详情 city 为空（前端按 location 推导）──
r=ok('post','/api/club/1/activities',json={'title':'city 测试·无 city','eventDate':'2026-11-02 08:00','location':'阿坝 四姑娘山','price':0,'capacity':10})
aid2=r.json().get('activityId')
r=ok('get',f'/api/club/1/activities/{aid2}')
check('legacy activity city empty',r.json().get('city') in ('',None),repr(r.json().get('city')))

print('PYTHON CHECKS:',len(PASS),'passed')

# ── 7) node 校验 clubos-cities.js 推导逻辑 ──
NODE=os.environ.get('CLUBOS_NODE','/Users/jckuma/.workbuddy/binaries/node/versions/22.22.2-6/bin/node')
JS=str(ROOT/'static'/'ux'/'clubos-cities.js')
node_src=r'''
const fs=require('fs');
global.window={};
eval(fs.readFileSync(process.argv[1],'utf8'));
const W=global.window;
function t(name,got,exp){ if(got!==exp){console.error('NODE FAIL',name,'got',JSON.stringify(got),'exp',JSON.stringify(exp));process.exit(1);} console.log('  ok -',name); }
t('derive 成都',W.clubosDeriveCity('成都 邛崃 兴福寺'),'成都');
t('derive 阿坝',W.clubosDeriveCity('阿坝 四姑娘山双桥沟'),'阿坝');
t('derive 甘孜',W.clubosDeriveCity('甘孜 稻城亚丁'),'甘孜');
t('derive 空串',W.clubosDeriveCity(''),'');
t('derive 无匹配',W.clubosDeriveCity('南极科考站'),'');
t('cityOf 显式优先',W.clubosCityOf({city:'乐山',location:'阿坝'}),'乐山');
t('cityOf 兜底',W.clubosCityOf({location:'雅安 牛背山'}),'雅安');
t('nearest 成都坐标',W.clubosNearestCity(30.57,104.07),'成都');
t('nearest 北京坐标',W.clubosNearestCity(39.90,116.40),'北京');
t('nearest 远离所有城市(悉尼)',W.clubosNearestCity(-33.86,151.20),'');  // 超出所有城市范围，返回空（交回全部）
t('nearest 无效',W.clubosNearestCity(NaN,0),'');
console.log('NODE CHECKS passed');
'''
proc=subprocess.run([NODE,'-e',node_src,JS],capture_output=True,text=True)
print(proc.stdout)
if proc.returncode!=0:
    print(proc.stderr); sys.exit(1)

print('\nALL CITY DIMENSION CHECKS PASSED')

# -*- coding: utf-8 -*-
"""营位照片裁剪闪退修复回归（2026-11-16）。

背景：编辑弹窗里点「上传并手动裁剪」，裁剪窗在 loadImg 异步回调里打开，
P2-1 弹层单例把宿主编辑弹窗整个关掉（用户视角＝闪退，又得从头编辑）；
showAlert 同盲区；上传失败丢图且弹窗关闭导致「可点保存重试」落空。
场景：

S1 修复主场景：编辑已有营位 → 上传并手动裁剪 → 编辑弹窗不得被关闭（修复前必现闪退）
S2 新建态：上传并手动裁剪 → 入队 → 保存 → 自动上传成功
S3 回归：新建态快速上传 → 保存 → 自动上传成功
S4 丢图修复：上传失败时照片保留在队列，可重试
"""
import json, os, sys
from playwright.sync_api import sync_playwright

BASE=os.getenv('CLUBOS_QA_BASE', 'http://127.0.0.1:8263')
IMG='/tmp/pitch_test_img.jpg'
CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
P=[];F=[];NET=[]
def ck(n,ok,d=''):
    (P if ok else F).append(n);print(('PASS ' if ok else 'FAIL ')+n+' | '+str(d)[:160],flush=True)

with sync_playwright() as p:
    b=p.chromium.launch(headless=True,executable_path=CHROME)
    ctx=b.new_context(viewport={'width':1440,'height':900})
    pg=ctx.new_page()
    errs=[];pg.on('pageerror',lambda e:errs.append(str(e)[:200]))
    pg.on('response',lambda r:NET.append(f"{r.status} {r.request.method} {r.url.split('8263')[-1]}") if '/api/' in r.url else None)
    pg.goto(BASE+'/club',wait_until='load',timeout=30000)
    pg.wait_for_timeout(1500)

    # 造数据：营地 + 2 个营位（页面内 fetch，带会话）
    seed=pg.evaluate("""async()=>{
      const c=await fetch('/api/club/1/camps',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:'回归营地S',address:'测试路1号'})}).then(r=>r.json());
      const campId=c.id;
      const mk=async n=>{const r=await fetch('/api/club/1/pitches',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({campId,code:n,name:n,pitchType:'tent',isOvernight:1,capacity:2,basePrice:100,status:'active'})});return r.json();};
      return {campId,pitch:(await mk('回归营位S1')).id};}""")
    print('seed:',json.dumps(seed,ensure_ascii=False),flush=True)
    ck('造数据成功',bool(seed.get('pitch')),str(seed))

    # 进入营地管理
    pg.evaluate("()=>document.querySelector('[data-view=\"camp\"]')?.click()")
    pg.wait_for_timeout(1200)

    # ---------- S1: 编辑已有营位 → 上传并手动裁剪 ----------
    print('== S1 edit existing pitch + manual crop ==',flush=True)
    pg.evaluate(f"()=>window.editPitch({seed['pitch']})")
    pg.wait_for_timeout(1000)
    pg.evaluate("()=>{const e=document.getElementById('f_name');if(e)e.value='回归营位S1-改';}")
    pg.set_input_files('#caMwbFile',IMG)
    pg.wait_for_timeout(400)
    pg.evaluate("""()=>{const b=[...document.querySelectorAll('#caMwb button')].find(x=>x.textContent.includes('上传并手动裁剪'));if(b)b.click();}""")
    pg.wait_for_timeout(2500)  # 等 loadImg 异步打开裁剪窗
    st=pg.evaluate("""()=>({ovs:document.querySelectorAll('.ux-overlay').length,
        titles:[...document.querySelectorAll('.ux-dialog h2')].map(h=>h.textContent),
        cropOpen:!!document.getElementById('ccropStage')})""")
    print('  crop-time state:',json.dumps(st,ensure_ascii=False)[:200],flush=True)
    ck('S1 裁剪窗已打开',st['cropOpen'],str(st['titles']))
    ck('S1 编辑弹窗未被关闭（闪退修复核心）',any('编辑营位' in t for t in st['titles']) or any('新增营位' in t for t in st['titles']),str(st['titles']))
    ck('S1 弹层数=2（编辑层+嵌套裁剪层）',st['ovs']==2,f"ovs={st['ovs']}")
    # 裁剪确认：点裁剪窗（含 #ccropStage）自己的确认按钮，绝不命中编辑弹窗的保存键
    pg.evaluate("""()=>{const d=[...document.querySelectorAll('.ux-dialog')].find(x=>x.querySelector('#ccropStage'));
        (d&&d.querySelector('[data-ok]')||{}).click&&d.querySelector('[data-ok]').click();}""")
    pg.wait_for_timeout(2200)
    st2=pg.evaluate("""()=>({ovs:document.querySelectorAll('.ux-overlay').length,
        titles:[...document.querySelectorAll('.ux-dialog h2')].map(h=>h.textContent),
        q:document.querySelectorAll('.ca-mwb__qitem').length,
        grid:document.querySelectorAll('.ca-mwb__img').length})""")
    print('  after-crop state:',json.dumps(st2,ensure_ascii=False)[:200],flush=True)
    ck('S1 裁剪确认后编辑弹窗仍在',st2['ovs']>=1 and any(('营位' in t) for t in st2['titles']),str(st2))
    ck('S1 编辑态照片直接上传入列表',st2['grid']>=1,f"grid={st2['grid']} queue={st2['q']}")
    # 保存
    pg.evaluate("()=>document.getElementById('campFormOk').click()")
    pg.wait_for_timeout(2500)
    st3=pg.evaluate("""()=>({ovs:document.querySelectorAll('.ux-overlay').length,
        media:0,titles:[...document.querySelectorAll('.ux-dialog h2')].map(h=>h.textContent)})""")
    ck('S1 保存成功弹窗关闭',st3['ovs']==0,str(st3))
    saved=any('POST /api/club/1/camp-media' in x for x in NET)
    ck('S1 保存后照片自动上传（POST camp-media 200）',saved, NET[-6:] and ' | '.join(NET[-6:]))

    # ---------- S2: 新建态手动裁剪 ----------
    print('== S2 new pitch + manual crop ==',flush=True)
    pg.evaluate("()=>window.editPitch(0)")
    pg.wait_for_timeout(900)
    pg.evaluate("()=>{const e=document.getElementById('f_name');if(e){e.value='回归营位S2';e.dispatchEvent(new Event('input',{bubbles:true}));}}")
    pg.set_input_files('#caMwbFile',IMG)
    pg.wait_for_timeout(400)
    pg.evaluate("""()=>{const b=[...document.querySelectorAll('#caMwb button')].find(x=>x.textContent.includes('上传并手动裁剪'));if(b)b.click();}""")
    pg.wait_for_timeout(2500)
    st=pg.evaluate("""()=>({ovs:document.querySelectorAll('.ux-overlay').length,
        titles:[...document.querySelectorAll('.ux-dialog h2')].map(h=>h.textContent)})""")
    ck('S2 新建态裁剪窗为嵌套层（编辑弹窗保留）',st['ovs']==2 and any('新增营位' in t for t in st['titles']),str(st))
    pg.evaluate("""()=>{const d=[...document.querySelectorAll('.ux-dialog')].find(x=>x.querySelector('#ccropStage'));
        (d&&d.querySelector('[data-ok]')||{}).click&&d.querySelector('[data-ok]').click();}""")
    pg.wait_for_timeout(1500)
    q=pg.evaluate("()=>document.querySelectorAll('.ca-mwb__qitem').length")
    ck('S2 照片入队',q>=1,f"queue={q}")
    pg.evaluate("()=>document.getElementById('campFormOk').click()")
    pg.wait_for_timeout(2500)
    fin=pg.evaluate("()=>document.querySelectorAll('.ux-overlay').length")
    ck('S2 保存成功弹窗关闭',fin==0,f"ovs={fin}")
    posts=[x for x in NET if 'POST /api/club/1/pitches' in x]
    ck('S2 新建营位 API 200',posts and posts[-1].startswith('200'),posts[-1] if posts else 'none')

    # ---------- S3: 新建态快速上传（回归不破坏） ----------
    print('== S3 new pitch + auto upload ==',flush=True)
    pg.evaluate("()=>window.editPitch(0)")
    pg.wait_for_timeout(900)
    pg.evaluate("()=>{const e=document.getElementById('f_name');if(e){e.value='回归营位S3';e.dispatchEvent(new Event('input',{bubbles:true}));}}")
    pg.set_input_files('#caMwbFile',IMG)
    pg.wait_for_timeout(400)
    pg.evaluate("""()=>{const b=[...document.querySelectorAll('#caMwb button')].find(x=>x.textContent.includes('快速上传'));if(b)b.click();}""")
    pg.wait_for_timeout(2000)
    q=pg.evaluate("()=>document.querySelectorAll('.ca-mwb__qitem').length")
    ck('S3 快速上传入队',q>=1,f"queue={q}")
    pg.evaluate("()=>document.getElementById('campFormOk').click()")
    pg.wait_for_timeout(2500)
    media=[x for x in NET if 'POST /api/club/1/camp-media' in x]
    ck('S3 保存后自动上传',media and media[-1].startswith('200'),media[-1] if media else 'none')

    # ---------- S4: 上传失败不丢图 ----------
    print('== S4 upload-failure keeps photos ==',flush=True)
    # 拦截下一次 camp-media POST 返回 500
    pg.route('**/api/club/1/camp-media',lambda route:(route.fulfill(status=500,body='{"detail":"模拟上传失败"}') if route.request.method=='POST' else route.continue_()))
    pg.evaluate("()=>window.editPitch(0)")
    pg.wait_for_timeout(900)
    pg.evaluate("()=>{const e=document.getElementById('f_name');if(e){e.value='回归营位S4';e.dispatchEvent(new Event('input',{bubbles:true}));}}")
    pg.set_input_files('#caMwbFile',IMG)
    pg.wait_for_timeout(400)
    pg.evaluate("""()=>{const b=[...document.querySelectorAll('#caMwb button')].find(x=>x.textContent.includes('快速上传'));if(b)b.click();}""")
    pg.wait_for_timeout(1500)
    q0=pg.evaluate("()=>document.querySelectorAll('.ca-mwb__qitem').length")
    ck('S4 入队 1 张',q0==1,f"queue={q0}")
    pg.evaluate("()=>document.getElementById('campFormOk').click()")
    pg.wait_for_timeout(3500)
    st4=pg.evaluate("""()=>({ovs:document.querySelectorAll('.ux-overlay').length,
        titles:[...document.querySelectorAll('.ux-dialog h2')].map(h=>h.textContent),
        q:document.querySelectorAll('.ca-mwb__qitem').length,
        alert:[...document.querySelectorAll('.ux-dialog')].map(d=>d.innerText.slice(0,80))})""")
    print('  S4 state:',json.dumps(st4,ensure_ascii=False)[:300],flush=True)
    ck('S4 失败后照片仍在队列（不丢图）',st4['q']>=1,f"queue={st4['q']}")
    ck('S4 失败提示可见',any('上传失败' in t for t in st4['alert']),str(st4['alert'])[:120])
    ck('S4 编辑弹窗未关闭（可重试）',any('新增营位' in t for t in st4['titles']),str(st4['titles']))
    # 重试闭环：关掉失败提示 → 解除拦截 → 再点保存 → 上传成功 → 弹窗关闭
    pg.evaluate("""()=>{const d=[...document.querySelectorAll('.ux-dialog')].find(x=>x.innerText.includes('部分照片上传失败'));
        (d&&d.querySelector('[data-ok]')||{}).click&&d.querySelector('[data-ok]').click();}""")
    pg.wait_for_timeout(600)
    pg.unroute('**/api/club/1/camp-media')
    pg.evaluate("()=>document.getElementById('campFormOk').click()")
    pg.wait_for_timeout(3000)
    st5=pg.evaluate("()=>({ovs:document.querySelectorAll('.ux-overlay').length,alert:[...document.querySelectorAll('.ux-dialog')].map(d=>d.innerText.slice(0,50))})")
    ck('S4 重试后上传成功弹窗关闭',st5['ovs']==0 and not any('失败' in a for a in st5['alert']),str(st5)[:150])
    media_ok=[x for x in NET if 'POST /api/club/1/camp-media' in x]
    ck('S4 重试 POST camp-media 200',media_ok and media_ok[-1].startswith('200'),media_ok[-1] if media_ok else 'none')

    ck('全程无 JS 异常',not errs,str(errs)[:150])
    b.close()

print(f"\nRESULT: {len(P)} PASS / {len(F)} FAIL",flush=True)
for f in F: print('  FAIL:',f,flush=True)
sys.exit(1 if F else 0)

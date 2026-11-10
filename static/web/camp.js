/* ===========================================================================
 * ClubOS 营地业态 · C 端（web H5，2026-11-10）
 * 依赖（web.js / shared.js 已先加载）：CLUB / USER / PAYER_NAME / PAYER_PHONE /
 *       api / esc / toast / showAlert / showForm / $ / $$
 * 契约见 app.py 营地模块公开接口：
 *   /api/public/clubs/{CLUB}                 → 含 module_switches（前端 tab 显隐唯一数据源）
 *   /api/public/clubs/{CLUB}/camps           GET 启用的营地档案
 *   /api/public/clubs/{CLUB}/pitches?camp_id GET 营位（snake_case）
 *   /api/public/clubs/{CLUB}/camp-availability?camp_id&start&end → 营位×日期房态
 *   /api/public/clubs/{CLUB}/camp-bookings   POST 营位预订（酒店式跨日锁，返回 pending）
 *   /api/public/clubs/{CLUB}/rental-items|store-products|tickets?camp_id → 列表
 * 设计：活动/营地互斥同槽（共用一个底部导航格）；营位=酒店房态，过夜跨日锁。
 * ===========================================================================*/

let CAMP_SW={home:true,activity:true,camp:false,bizIntro:true};
let CAMP_LIST=[];
let CAMP_PITCHES=[];

function _iso(d){const z=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+z(d.getMonth()+1)+'-'+z(d.getDate());}
function _pitchTypeLabel(t){return ({tent:'帐篷位',rv:'房车位',cabin:'木屋/舱房',day:'日间营位'}[t]||t);}

/* 按经营业态开关显隐底部导航：活动/营地互斥同槽；户外能力(wability) 由 bizIntro 控制。 */
async function applyModuleSwitches(){
  try{
    const club=await api('/api/public/clubs/'+CLUB,{cache:'no-store'});
    if(club&&club.module_switches)CAMP_SW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},club.module_switches);
  }catch(e){/* 默认兜底：活动开、营地关、户外能力开 */}
  const actBtn=document.querySelector('.web-nav button[data-wv="wactivities"]');
  const campBtn=document.querySelector('.web-nav button[data-wv="wcamp"]');
  const abBtn=document.querySelector('.web-nav button[data-wv="wability"]');
  if(actBtn)actBtn.style.display=(CAMP_SW.activity&&!CAMP_SW.camp)?'':'none';
  if(campBtn)campBtn.style.display=CAMP_SW.camp?'':'none';
  if(abBtn)abBtn.style.display=CAMP_SW.bizIntro?'':'none';
}

async function loadCampPage(){
  const root=$('#campRoot'); if(!root)return;
  root.innerHTML='<div class="w-pad" style="padding:40px 16px;color:#9aa">营地加载中…</div>';
  // 开关是异步拉取的：首屏直接切到营地页时可能早于开关返回，进页重新对齐一次，
  // 避免「导航显示营地、页面却提示未开启」的竞态。
  try{
    const club=await api('/api/public/clubs/'+CLUB,{cache:'no-store'});
    if(club&&club.module_switches)CAMP_SW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},club.module_switches);
  }catch(e){/* 沿用脚本加载时已取得的开关 */}
  if(!CAMP_SW.camp){
    root.innerHTML='<div class="w-pad" style="padding:60px 16px;text-align:center;color:#9aa">本俱乐部暂未开启「营地」业态。</div>';
    return;
  }
  let camps;
  try{ camps=await api('/api/public/clubs/'+CLUB+'/camps',{cache:'no-store'}); }
  catch(e){ root.innerHTML='<div class="w-pad" style="padding:40px 16px;color:#c8452d">营地读取失败：'+(e.message||'')+'</div>'; return; }
  CAMP_LIST=camps||[];
  if(!CAMP_LIST.length){
    root.innerHTML='<div class="w-pad" style="padding:60px 16px;text-align:center;color:#9aa">营地暂未开放预订，敬请期待。</div>';
    return;
  }
  const campOpts=CAMP_LIST.map(c=>`<option value="${c.id}">${esc(c.name||'')}</option>`).join('');
  root.innerHTML=`<div class="w-pad" style="padding-bottom:24px">
    <div style="margin:8px 0 4px"><h2 style="margin:0;font-size:20px">营地预订</h2>
      <p style="margin:6px 0 0;color:#8a958f;font-size:13px;line-height:1.5">选择营地、入住与离营日期，实时查看房态与总价。营位按日期房态管理，过夜营位像酒店一样跨日锁定。</p></div>
    <div style="margin-top:12px"><label style="font-size:13px;color:#6a756f">营地</label>
      <select id="campSel" onchange="campOnChange()" style="width:100%;margin-top:4px;padding:9px 10px;border:1px solid #e2e6e3;border-radius:10px;font-size:15px">${campOpts}</select></div>
    <div id="campBody"></div>
  </div>`;
  await campOnChange();
}

async function campOnChange(){
  const campId=Number($('#campSel')?.value||0);
  const body=$('#campBody'); if(!body)return;
  body.innerHTML='<div style="padding:20px 0;color:#9aa">加载中…</div>';
  let pitches,rental,store,tickets;
  try{
    [pitches,rental,store,tickets]=await Promise.all([
      api('/api/public/clubs/'+CLUB+'/pitches?camp_id='+campId,{cache:'no-store'}),
      api('/api/public/clubs/'+CLUB+'/rental-items?camp_id='+campId,{cache:'no-store'}),
      api('/api/public/clubs/'+CLUB+'/store-products?camp_id='+campId,{cache:'no-store'}),
      api('/api/public/clubs/'+CLUB+'/tickets?camp_id='+campId,{cache:'no-store'})
    ]);
  }catch(e){ body.innerHTML='<div style="padding:20px 0;color:#c8452d">读取失败：'+(e.message||'')+'</div>'; return; }
  CAMP_PITCHES=pitches||[];
  const today=new Date();
  const s=_iso(today);
  const e=new Date(today); e.setDate(e.getDate()+1); const eS=_iso(e);
  const pitchOpts=CAMP_PITCHES.map(p=>`<option value="${p.id}">${esc(p.name||'')}（${_pitchTypeLabel(p.pitch_type)}）</option>`).join('');
  body.innerHTML=`
    <div style="margin-top:14px;background:#fff;border:1px solid #eceeeb;border-radius:14px;padding:14px">
      <div style="font-weight:700;font-size:15px;margin-bottom:10px">营位预订</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <label style="display:block;font-size:12px;color:#6a756f">营位<select id="bkPitch" style="width:100%;margin-top:3px;padding:8px;border:1px solid #e2e6e3;border-radius:9px">${pitchOpts||'<option value="">该营地暂无营位</option>'}</select></label>
        <label style="display:block;font-size:12px;color:#6a756f">数量<input id="bkQty" type="number" min="1" value="1" onchange="campRecalc()" style="width:100%;margin-top:3px;padding:8px;border:1px solid #e2e6e3;border-radius:9px"></label>
        <label style="display:block;font-size:12px;color:#6a756f">入住<input id="bkIn" type="date" value="${s}" onchange="campRecalc()" style="width:100%;margin-top:3px;padding:8px;border:1px solid #e2e6e3;border-radius:9px"></label>
        <label style="display:block;font-size:12px;color:#6a756f">离营<input id="bkOut" type="date" value="${eS}" onchange="campRecalc()" style="width:100%;margin-top:3px;padding:8px;border:1px solid #e2e6e3;border-radius:9px"></label>
        <label style="display:block;font-size:12px;color:#6a756f">押金（可选）<input id="bkDep" type="number" min="0" step="0.01" value="0" placeholder="如 100" style="width:100%;margin-top:3px;padding:8px;border:1px solid #e2e6e3;border-radius:9px"></label>
      </div>
      <div id="bkSummary" style="margin-top:12px;font-size:14px"></div>
      <button type="button" onclick="campBook()" style="margin-top:12px;width:100%;padding:12px;border:0;border-radius:11px;background:#1f7a52;color:#fff;font-size:15px;font-weight:700">立即预订</button>
    </div>
    ${campGoodsBlock('装备租赁',rental,r=>`日租 ¥${r.daily_price||0} · 押金 ¥${r.deposit||0} · 库存 ${r.available_qty||0}`)}
    ${campGoodsBlock('营地小站',store,s=>`¥${s.price||0} · 库存 ${s.stock||0}${s.category?' · '+esc(s.category):''}`)}
    ${campGoodsBlock('活动票',tickets,t=>`¥${t.price||0} · ${ {single:'单次入场',multi:'多次入场'}[t.entry_type]||t.entry_type }${t.event_date?' · '+esc(t.event_date):''}`)}
  `;
  await campRecalc();
}

function campGoodsBlock(title,list,subFn){
  if(!list||!list.length)return '';
  const items=list.map(x=>`<div style="display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px solid #f1f3f1">
    <div><div style="font-size:14px">${esc(x.name||'')}</div><div style="font-size:12px;color:#8a958f;margin-top:2px">${subFn(x)}</div></div></div>`).join('');
  return `<div style="margin-top:14px;background:#fff;border:1px solid #eceeeb;border-radius:14px;padding:14px">
    <div style="font-weight:700;font-size:15px;margin-bottom:4px">${title}</div>
    ${items}
    <div style="font-size:12px;color:#9aa;margin-top:8px">在线购买 / 租赁下单将在结算层接入后开放。</div></div>`;
}

async function campRecalc(){
  const sumEl=$('#bkSummary'); if(!sumEl)return;
  const pid=Number($('#bkPitch')?.value||0), ci=$('#bkIn')?.value, co=$('#bkOut')?.value;
  const qty=Math.max(1,Number($('#bkQty')?.value||1)), dep=Number($('#bkDep')?.value||0);
  if(!pid||!ci||!co){sumEl.innerHTML='请选择营位与日期';return;}
  sumEl.innerHTML='<span style="color:#9aa">计算价格中…</span>';
  let data;
  try{ data=await api('/api/public/clubs/'+CLUB+'/camp-availability?camp_id='+Number($('#campSel')?.value||0)+'&start='+encodeURIComponent(ci)+'&end='+encodeURIComponent(co),{cache:'no-store'}); }
  catch(e){ sumEl.innerHTML='<span style="color:#c8452d">价格获取失败：'+(e.message||'')+'</span>'; return; }
  const p=(data.pitches||[]).find(x=>x.pitchId===pid);
  if(!p){sumEl.innerHTML='该营位暂无房态';return;}
  const inStay=d=>d.day>=ci && d.day<co;            // 过夜：结算到离营前一天；日间：ci==co 即当天
  const stay=(p.days||[]).filter(inStay);
  const blocked=(p.days||[]).filter(d=>inStay(d)&&d.status!=='available');
  const payable=Math.round((stay.reduce((a,d)=>a+(d.price||0),0))*qty*100)/100;
  const total=Math.round((payable+dep)*100)/100;
  if(!stay.length){ sumEl.innerHTML='所选区间无可用房态'; return; }
  if(blocked.length){
    sumEl.innerHTML=`<div style="color:#c8452d;font-size:13px">所选区间含不可订日期（${blocked.map(b=>b.day.slice(5)).join('、')}），请调整日期。</div>`;
    return;
  }
  sumEl.innerHTML=`<div style="display:flex;justify-content:space-between;padding:3px 0"><span style="color:#6a756f">${stay.length} 晚 × ${qty} 间 · 营位费</span><b>¥${payable}</b></div>
    <div style="display:flex;justify-content:space-between;padding:3px 0"><span style="color:#6a756f">押金</span><b>¥${dep}</b></div>
    <div style="display:flex;justify-content:space-between;padding:6px 0 0;margin-top:4px;border-top:1px solid #eee;font-size:15px"><span>合计</span><b style="color:#1f7a52">¥${total}</b></div>`;
}

window.campBook=async function(){
  const pid=Number($('#bkPitch')?.value||0), ci=$('#bkIn')?.value, co=$('#bkOut')?.value;
  const qty=Math.max(1,Number($('#bkQty')?.value||1)), dep=Number($('#bkDep')?.value||0);
  if(!pid||!ci||!co){showAlert({title:'请选择营位与日期'});return;}
  if(ci>co){showAlert({title:'离营日期必须晚于入住日期'});return;}
  let gname=PAYER_NAME||'', gphone=PAYER_PHONE||'';
  if(!gname||!gphone){
    const v=await showForm({title:'预订信息',submitText:'提交预订',fields:[
      {name:'gname',label:'入住人姓名',required:true,value:gname},
      {name:'gphone',label:'手机号',required:true,value:gphone}
    ]});
    if(!v)return; gname=v.gname; gphone=v.gphone;
  }
  try{
    const r=await api('/api/public/clubs/'+CLUB+'/camp-bookings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pitchId:pid,checkIn:ci,checkOut:co,isDayUse:ci===co,qty,deposit:dep,userId:USER,guestName:gname,guestPhone:gphone})});
    let co=null;
    try{ co=await api('/api/public/clubs/'+CLUB+'/camp-checkout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bookingId:r.id})}); }catch(e){/* 结算单生成失败不阻断预订提示 */}
    showAlert({title:'预订已提交',message:`预订号 #${r.id}\n营位费 ¥${r.payable} + 押金 ¥${r.deposit} = ¥${r.total}\n结算单：${co?co.checkoutId:'生成中'}\n状态：待支付（30 分钟内完成，超时房态自动释放）`});
    await campOnChange();
  }catch(e){showAlert({title:'预订失败',message:e.message||'请重试'})}
};

applyModuleSwitches();

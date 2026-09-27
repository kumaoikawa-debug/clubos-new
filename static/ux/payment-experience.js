/* ClubOS NEW: consumer checkout and payment experience. No fabricated payment or client-side settlement. */
(function(){'use strict';if(!location.pathname.startsWith('/web'))return;
// 报名 / 装备下单都是「建单 → 支付 → 查单 → 收据 → 刷新」的多请求流程，防重入统一交给
// shared.js 的 uxFlow（同名流程在跑时直接返回 null），这里不再自建 boolean 锁，也不再手工摆弄
// 按钮的 disabled / innerHTML —— 按钮反馈由 uxFlow 复用写操作那套 .is-busy + disabled。
const post=(p,d)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const validPhone=p=>/^[+\d()\-\s]{7,24}$/.test((p||'').trim());
function sheet({title,desc='',body,actionLabel='关闭',onClose}){const node=document.createElement('div');node.className='ux-overlay ux-payment-overlay';node.innerHTML='<div class="ux-dialog ux-payment-dialog" role="dialog" aria-modal="true" aria-label="支付处理"><div class="ux-dialog-head"><div><div class="eyebrow">SECURE CHECKOUT / CLUBOS</div><h2></h2><p></p></div><button type="button" class="x" aria-label="关闭">×</button></div><div class="ux-dialog-body"></div><div class="ux-dialog-foot"><button class="btn ghost ux-sheet-close" type="button"></button></div></div>';node.querySelector('h2').textContent=title;node.querySelector('.ux-dialog-head p').textContent=desc;node.querySelector('.ux-dialog-body').append(body);node.querySelector('.ux-sheet-close').textContent=actionLabel;document.body.append(node);
 let sheetDone=false,sheetSession=null;
 /* 与 shared.js 的 uxDialogSession 共用同一套弹窗行为：焦点进入弹窗、Tab 圈闭、Esc 关闭、
    背景滚动锁、关闭后归还焦点。之前这一层没有 Tab 圈闭，焦点能从收据弹窗跑到背后的活动页。 */
 const close=()=>{if(sheetDone)return;sheetDone=true;node.remove();sheetSession&&sheetSession.release();onClose?.()};
 node.querySelector('.x').onclick=close;node.querySelector('.ux-sheet-close').onclick=close;
 sheetSession=window.uxDialogSession(node,{onEscape:close,initialFocus:'.x'});
 return {node,close};}async function receipt(title,lines){const box=document.createElement('div');box.className='ux-receipt';const mark=document.createElement('div');mark.className='ux-receipt-mark';mark.textContent='✓';box.append(mark);for(const [name,value] of lines){const row=document.createElement('div');row.className='ux-receipt-line';const a=document.createElement('span'),b=document.createElement('strong');a.textContent=name;b.textContent=value;row.append(a,b);box.append(row)}const button=document.createElement('button');button.className='btn';button.type='button';button.textContent='查看我的订单';box.append(button);const dialog=sheet({title,desc:'付款结果已由服务端确认。',body:box,actionLabel:'继续浏览'});/* 按 data-wv 找「我的」而不是按按钮下标：下标 3 在导航改成 4 板块前后含义不同，写死位置迟早错位。 */button.onclick=()=>{dialog.close();const btn=document.querySelector('.web-nav button[data-wv="wme"]');if(btn)wv('wme',btn)};}
async function qrPayment(checkoutId,url){let closed=false,finished=false,finish;const result=new Promise(resolve=>finish=resolve);const box=document.createElement('div');box.className='ux-payment-body';const note=document.createElement('p');note.className='sub';note.textContent='使用付款设备扫码。此页面只展示支付渠道返回的收款码，并通过订单接口查询实际付款状态。';const canvas=document.createElement('canvas');canvas.className='ux-pay-qr';const code=document.createElement('code');code.className='ux-pay-code';code.textContent=url;const controls=document.createElement('div');controls.className='ux-pay-controls';const copy=document.createElement('button');copy.type='button';copy.className='btn secondary';copy.textContent='复制支付链接 / 码';const check=document.createElement('button');check.type='button';check.className='btn';check.textContent='我已付款，查询状态';const status=document.createElement('div');status.className='ux-payment-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');status.textContent='等待支付渠道确认…';controls.append(copy,check);box.append(note,canvas,code,controls,status);
 try{window.ClubOSQR.draw(canvas,url)}catch(err){canvas.hidden=true;status.textContent='此支付码暂无法显示为二维码，请复制支付链接并在支持的环境中打开。';}
 const dlg=sheet({title:'完成订单支付',desc:'订单号 '+checkoutId,body:box,actionLabel:'稍后到订单查看',onClose:()=>{closed=true;if(!finished)finish(null)}});
 copy.onclick=async()=>{try{await navigator.clipboard.writeText(url);status.textContent='已复制，请使用支付应用打开。'}catch{status.textContent='复制失败，请长按下方支付码手动复制。'}};
 const inspect=async()=>{if(closed||finished)return null;const d=await api('/api/public/checkouts/'+checkoutId);if(d.status==='paid'||d.payment_status==='succeeded'){finished=true;status.textContent='付款成功，订单已确认';const r=d.result||d;finish(r);dlg.close();return r}if(d.payment_status==='failed'){status.textContent='付款未完成，可从订单重新发起或联系支付渠道。';return null}status.textContent='尚未查询到已付款记录。请勿重复付款。';return null};
 check.onclick=async()=>{const st=uxBusyOn(check);try{const r=await inspect();if(r)finish(r)}catch(e){status.textContent=e.message||'查询失败，请稍后重试'}finally{uxBusyOff(st)}};
 // Avoid polling after user closes the dialog; no optimistic success state.
 for(let i=0;i<45&&!closed&&!finished;i++){await pause(2000);if(closed||finished)break;try{const r=await inspect();if(r)return r}catch(e){if(!closed)status.textContent='网络暂不可用：'+e.message+'。可以手动查询或稍后在订单查看。'}}
 if(!closed&&!finished)status.textContent='支付仍在处理中。可手动查询，或关闭后从订单查看；请勿重复付款。';
 return result;
}
 window.payCheckout=async function(checkout){const d=await post('/api/public/checkouts/'+checkout.checkoutId+'/pay',{simulateSuccess:!Boolean(clubosCookie('clubos_csrf')),returnUrl:location.href});if(d.paymentStatus==='succeeded')return d.result;const a=d.paymentAction||{};
  if(a.type==='redirect'&&a.url){if(!/^https?:\/\//i.test(a.url))throw new Error('支付链接格式异常');location.assign(a.url);return null}
  if(a.type==='jsapi'&&a.params){if(!window.WeixinJSBridge){toast('请在微信中打开该页面完成 JSAPI 支付');return null}await new Promise((resolve,reject)=>WeixinJSBridge.invoke('getBrandWCPayRequest',a.params,r=>String(r.err_msg||'').includes(':ok')?resolve(r):reject(new Error(r.err_msg||'微信支付未完成'))));return await waitForCheckoutPaid(checkout.checkoutId)}
  if(a.type==='qrcode'&&a.url)return await qrPayment(checkout.checkoutId,a.url);
  /* provider=local 时后端只回 {"type":"mock"}，没有任何可执行付款方式。旧代码落到下面那句兜底
     toast，订单永远停在 pending_payment —— 而已登录会员必然走这条分支（未登录访客因为没有
     csrf cookie、simulateSuccess 短路反而能成功），也就是说这个断点只在「正常登录下单」时出现，
     demo 里几乎每单都会撞上。补一条模拟支付面板：不接任何真实渠道，但走完整状态机 ——
     确认走 /confirm（后端 local-manual 的幂等确认），返回的是与真实渠道同一个形状的 result。 */
  if(a.type==='mock')return await localSimulatePayment(checkout.checkoutId,a);
  toast('当前支付渠道没有返回可执行的付款方式，请到订单查看。');return null;
 };
/* 「本地模拟支付」面板：金额 / 确认成功 / 取消订单。
   这里刻意不提供「模拟支付失败」按钮 —— 后端没有暴露标记失败的端点，前端硬造一个假失败
   只会让界面状态与服务端不一致，一旦有人以为失败就不会再查单。失败的真实来源（渠道回调
   或商家关门）仍会由 qrPayment 的轮询读出 payment_status=failed。
   取消走 /cancel，后端会把积分冻结一并释放，与真实渠道取消的行为一致。 */
async function localSimulatePayment(checkoutId,action){
  const amount=Number((action&&action.amount)||0)||0;
  const box=document.createElement('div');box.className='ux-payment-body';
  const note=document.createElement('p');note.className='sub';
  note.textContent='当前为本地模拟支付：不会产生任何真实扣款，确认后由服务端按本地通道幂等确认这笔订单。';
  const amt=document.createElement('div');amt.className='ux-pay-code';amt.textContent=amount?money(amount):'—';
  const controls=document.createElement('div');controls.className='ux-pay-controls';
  const pay=document.createElement('button');pay.type='button';pay.className='btn';pay.textContent='确认已支付';
  const cancel=document.createElement('button');cancel.type='button';cancel.className='btn secondary';cancel.textContent='取消这笔订单';
  controls.append(pay,cancel);
  const status=document.createElement('div');status.className='ux-payment-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  box.append(note,amt,controls,status);
  let finished=false,finish;const result=new Promise(res=>finish=res);
  const dlg=sheet({title:'本地模拟支付',desc:'订单 '+checkoutId,body:box,actionLabel:'稍后到订单查看',
    onClose:()=>{if(!finished)finish(null)}});
  pay.onclick=async()=>{const st=uxBusyOn(pay);try{const r=await post('/api/public/checkouts/'+checkoutId+'/confirm',{commerceOrderId:checkoutId});finished=true;status.textContent='已确认，订单完成';finish(r);dlg.close()}catch(e){status.textContent=e.message||'确认失败，可稍后从订单重新发起'}finally{uxBusyOff(st)}};
  cancel.onclick=async()=>{const st=uxBusyOn(cancel);try{await post('/api/public/checkouts/'+checkoutId+'/cancel',{});finished=true;status.textContent='订单已取消，积分占用已释放';finish(null);dlg.close()}catch(e){status.textContent=e.message||'取消失败'}finally{uxBusyOff(st)}};
  return result;
}
 window.signupNow=async function(){if(!currentAct||!currentOcc){toast('请先选择团期');return}if(Number(currentOcc.remaining)<bookingParticipants.length){toast('本团期剩余名额不足，请重新选择');return}const requireComplete=currentAct.participantPolicy?.allowIncompleteAtCheckout===false;
  for(let i=0;i<bookingParticipants.length;i++){const p=bookingParticipants[i];if(!p.name?.trim()){toast('请填写第 '+(i+1)+' 位参加人姓名');document.querySelector('#participantForms input')?.focus();return}if(!validPhone(p.phone)){toast('第 '+(i+1)+' 位参加人电话格式需要核对');return}if(requireComplete&&(!p.idType||!p.idNumber||!p.emergencyContactName||!validPhone(p.emergencyContactPhone))){toast('本活动要求付款前补齐第 '+(i+1)+' 位参加人的证件与紧急联系人资料');return}}
  // 校验放在锁外：资料没填全时立刻给提示，不该让「立即报名」闪一下忙态。
  return uxFlow('checkout',async()=>{
   try{const cp=Number(document.getElementById('clubUse')?.value||0),gp=Number(document.getElementById('gearUse')?.value||0),code=document.getElementById('benefitUse')?.value||'';const q=await post('/api/public/activities/'+currentAct.id+'/checkout',{name:PAYER_NAME,phone:PAYER_PHONE,occurrenceId:currentOcc.id,clubPoints:cp,gearPoints:gp,voucherCodes:code?[code]:[],participants:bookingParticipants});const paid=await payCheckout(q);if(!paid)return;await receipt('报名成功',[['参加人数',(paid.participantCount||bookingParticipants.length)+' 人'],['实际支付',money(paid.cashPaid)],['获得活动积分',String(paid.clubPointsEarned||0)]]);await loadWallet();await openAct(currentAct.id)}catch(e){toast(e.message||'报名未完成，请到订单查看状态')}
  });
 };
 window.buy=async function(pid){return uxFlow('checkout',async()=>{try{const wallet=await api('/api/public/users/'+USER+'/wallet?club_id='+CLUB);let vouchers=[];try{vouchers=await api('/api/public/clubs/'+CLUB+'/vouchers?user_id='+USER+'&kind=gear')}catch{}const d=await uxForm({title:'确认装备订单',subtitle:'商品由总平台统一履约、发货和处理售后。',fields:[{name:'gearPoints',label:'使用装备积分',type:'number',min:0,max:wallet.gearPoints||0,step:1,value:0,required:true,full:true,help:'可用 Gear Points '+(wallet.gearPoints||0)},{name:'voucher',label:'本单福利券',type:'select',full:true,options:[{value:'',label:'不使用'}].concat(vouchers.map(v=>({value:v.voucher_code,label:v.title+' · 抵扣 '+money(v.cash_value)})))}],hint:'下单后以服务端结算金额为准。',submitText:'确认订单并继续支付'});if(!d)return;const q=await post('/api/public/clubs/'+CLUB+'/gear-checkout',{userId:USER,items:[{productId:pid,quantity:1}],gearPoints:d.gearPoints,voucherCodes:d.voucher?[d.voucher]:[]});const paid=await payCheckout(q);if(!paid)return;await receipt('装备订单付款成功',[['本单实付',money(paid.cashPaid)],['获得装备积分',String(paid.gearPointsEarned||0)],['履约方','ClubOS 平台']]);loadWallet();loadMemberCenter()}catch(e){toast(e.message||'订单未完成，请查看我的订单')}})};
})();

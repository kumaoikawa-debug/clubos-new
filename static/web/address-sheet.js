/* ===========================================================================
   ClubOS · C 端「收货地址」抽屉（2026-11-09）
   ---------------------------------------------------------------------------
   确认订单页（web.js / #gearCheckout）与「我的 › 收货地址」（me.js）共用这一份。
   之前两处各写一套：确认订单页是挤成 3×2 的内联 input，我的页是 showForm
   的通用弹窗 —— 顾客在结算页填地址时输入框被裁切、看不到标签，体验割裂。
   现统一为 JD 式「底部整屏抽屉」：智能粘贴识别 + 左标签右输入 + 所在地区
   定位 + 设为默认 + 底部固定保存按钮。

   设计纪律：
   - 不新造第六套弹层：Esc / Tab 圈闭 / 焦点进入与归还 / 背景滚动锁全部复用
     shared.js 的 uxDialogSession(root, {onEscape, initialFocus})（见 clubos
     UI 技能 §16）。这里只负责视觉与业务。
   - 抽屉挂 document.body，在 .web-shell 之外 ⇒ CSS 一律写字面色值，
     不依赖 .web-shell 上定义的 var(--w-*)，也不要 .web-shell 前缀。
   - 后端契约一字未改：仍是 GET/POST/PATCH /api/public/users/{id}/addresses，
     字段 consignee/phone/province/city/district/detail/isDefault。
   - 输入框字号 16px：iOS 聚焦时 <16px 会整页放大（技能 §18.3）。
   - 不调用方传 userId 就直接用全局 USER —— 这里要求调用方显式传，避免
     本文件依赖 web.js 顶层 `let USER` 的脚本级词法绑定。
   =========================================================================== */
(function(){
'use strict';

/* 城市 → 省份：与 static/ux/clubos-cities.js 的 CLUBOS_CITY_COORDS 同一批城市。
   只用于「定位命中城市后顺手带出省份」，是客观地理归属，不是业务数据；
   覆盖不到时省份留空让用户自己填，绝不猜一个填进去。 */
var PROVINCE_CITIES={
  '北京市':['北京'],'上海市':['上海'],'天津市':['天津'],'重庆市':['重庆'],
  '四川省':['成都','阿坝','甘孜','凉山','雅安','眉山','乐山','绵阳','德阳'],
  '云南省':['昆明','大理','丽江','香格里拉','西双版纳','腾冲'],
  '贵州省':['贵阳','遵义','六盘水'],'西藏自治区':['拉萨','林芝'],
  '广东省':['广州','深圳','珠海','佛山','东莞','惠州'],
  '广西壮族自治区':['南宁','桂林','北海','柳州'],'海南省':['海口','三亚'],
  '浙江省':['杭州','宁波','温州','绍兴','嘉兴'],
  '江苏省':['南京','苏州','无锡','常州','南通','扬州','徐州'],
  '安徽省':['合肥','黄山','芜湖'],'福建省':['福州','厦门','泉州','漳州'],
  '江西省':['南昌','九江','景德镇','上饶'],
  '山东省':['济南','青岛','烟台','威海','临沂','淄博'],
  '湖北省':['武汉','宜昌','襄阳','恩施','十堰'],
  '湖南省':['长沙','张家界','岳阳','衡阳','常德'],
  '河南省':['郑州','洛阳','开封','新乡','南阳'],
  '河北省':['石家庄','秦皇岛','承德','张家口','保定'],
  '山西省':['太原','大同','忻州'],
  '内蒙古自治区':['呼和浩特','包头','鄂尔多斯','呼伦贝尔','赤峰'],
  '辽宁省':['沈阳','大连','丹东'],'吉林省':['长春','吉林','延吉'],
  '黑龙江省':['哈尔滨','齐齐哈尔','牡丹江','漠河'],
  '陕西省':['西安','宝鸡','延安','榆林','汉中'],
  '甘肃省':['兰州','天水','张掖','敦煌','嘉峪关'],
  '青海省':['西宁'],'宁夏回族自治区':['银川','中卫'],
  '新疆维吾尔自治区':['乌鲁木齐','喀什','伊犁','阿勒泰','克拉玛依','吐鲁番']
};
function provinceOfCity(city){
  if(!city)return '';
  for(var p in PROVINCE_CITIES){if(PROVINCE_CITIES[p].indexOf(city)>=0)return p;}
  return '';
}
var PROVINCES=['北京','上海','天津','重庆','河北','山西','辽宁','吉林','黑龙江','江苏',
  '浙江','安徽','福建','江西','山东','河南','湖北','湖南','广东','海南','四川','贵州',
  '云南','陕西','甘肃','青海','内蒙古','广西','西藏','宁夏','新疆'];
var AUTONOMOUS=['内蒙古','广西','西藏','宁夏','新疆'];
var MUNICIPAL=['北京','上海','天津','重庆'];

/* 把「张三 13800138000 四川省成都市武侯区天府三街199号」拆成 6 个字段。
   纯前端字符串解析，不请求任何外部地理服务；识别不出的字段保持原值不动，
   由用户手动补，不做「猜一个填进去」。 */
function parseSmart(text){
  var s=String(text||'').replace(/[\r\n\t]+/g,' ').replace(/[，,；;|]+/g,' ').replace(/\s+/g,' ').trim();
  var o={};if(!s)return o;
  var ph=s.match(/1[3-9]\d{9}/);if(ph)o.phone=ph[0];
  var rest=ph?s.replace(ph[0],' '):s;
  var named=rest.match(/(?:收货人|联系人|姓名)\s*[:：]?\s*([\u4e00-\u9fa5·]{2,6})/);
  if(named){o.consignee=named[1];rest=rest.replace(named[0],' ');}
  else{
    var head=rest.match(/^\s*([\u4e00-\u9fa5·]{2,4})(?=\s|$)/);
    if(head&&!/[省市区县镇街道路号]/.test(head[1])){o.consignee=head[1];rest=rest.slice(head[0].length);}
  }
  rest=rest.replace(/\s+/g,' ').trim();
  for(var i=0;i<PROVINCES.length;i++){
    var p=PROVINCES[i],pos=rest.indexOf(p);
    if(pos<0)continue;
    o.province=p+(AUTONOMOUS.indexOf(p)>=0?'自治区':(MUNICIPAL.indexOf(p)>=0?'市':'省'));
    rest=(rest.slice(0,pos)+rest.slice(pos+p.length)).replace(/^\s*/,'');
    break;
  }
  rest=rest.replace(/^\s*(?:省|壮族自治区|回族自治区|维吾尔自治区|自治区|市)\s*/,'').trim();
  var cm=rest.match(/^([\u4e00-\u9fa5]{2,8}?)(市|自治州|地区|盟)/);
  if(cm){o.city=cm[1];rest=rest.slice(cm[0].length).trim();}
  var dm=rest.match(/^([\u4e00-\u9fa5]{1,10}?)(区|县|旗)/);
  if(dm){o.district=dm[0];rest=rest.slice(dm[0].length).trim();}
  if(rest)o.detail=rest.slice(0,80);
  return o;
}

var _root=null,_session=null,_ctx=null;
function $(id){return _root?_root.querySelector('#'+id):null;}
function val(id){var e=$(id);return e?String(e.value||'').trim():'';}
function setVal(id,v){var e=$(id);if(e&&v)e.value=v;}
function esc(s){return window.esc?window.esc(s):String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function toast(m){if(window.toast)window.toast(m);}

function closeSheet(){
  if(!_root)return;
  if(_session){try{_session.release();}catch(e){}_session=null;}
  try{_root.remove();}catch(e){}
  document.documentElement.classList.remove('w-sheet-open');
  _root=null;_ctx=null;
}

function cityOptions(){
  var list=window.CLUBOS_CITIES||[];
  return list.map(function(c){return '<option value="'+esc(c)+'"></option>';}).join('');
}

function sheetHtml(a){
  var edit=!!(a&&a.id);
  var title=edit?'编辑收货地址':'新增收货地址';
  return '<div class="w-sheet__mask" data-close></div>'
    +'<div class="w-sheet__panel w-addr" role="dialog" aria-modal="true" aria-labelledby="asTitle" tabindex="-1">'
      +'<div class="w-addr__hd">'
        +'<button type="button" class="w-addr__back" data-close aria-label="返回">‹</button>'
        +'<strong id="asTitle">'+esc(title)+'</strong>'
        +'<button type="button" class="w-sheet__x" data-close aria-label="关闭">×</button>'
      +'</div>'
      +'<div class="w-addr__scroll">'
        +'<div class="w-addr__smart">'
          +'<textarea id="asSmart" rows="2" placeholder="粘贴「收货人 手机号 省市区详细地址」，一键识别填入"></textarea>'
          +'<div class="w-addr__smart__bar"><span>也可以手动填写下面的信息</span><button type="button" id="asSmartBtn">智能识别</button></div>'
        +'</div>'
        +'<div class="w-addr__form">'
          +'<label class="w-addr__row"><span class="w-addr__lb">收货人</span>'
            +'<input id="asName" class="w-addr__inp" type="text" placeholder="请填写收货人姓名" maxlength="20" autocomplete="name"></label>'
          +'<label class="w-addr__row"><span class="w-addr__lb">手机号</span>'
            +'<span class="w-addr__field"><i class="w-addr__cc">+86</i>'
            +'<input id="asPhone" class="w-addr__inp" type="tel" placeholder="请填写收货人手机号" inputmode="numeric" maxlength="11" autocomplete="tel"></span></label>'
          +'<div class="w-addr__region">'
            +'<div class="w-addr__region__hd"><span>所在地区</span>'
              +'<button type="button" id="asLocate" class="w-addr__locate">定位当前城市</button></div>'
            +'<label class="w-addr__row"><span class="w-addr__lb">省份</span>'
              +'<input id="asProv" class="w-addr__inp" type="text" placeholder="如 四川省" maxlength="20"></label>'
            +'<label class="w-addr__row"><span class="w-addr__lb">城市</span>'
              +'<input id="asCity" class="w-addr__inp" type="text" placeholder="如 成都" maxlength="20" list="asCityList" autocomplete="address-level2"></label>'
            +'<datalist id="asCityList">'+cityOptions()+'</datalist>'
            +'<label class="w-addr__row"><span class="w-addr__lb">区 / 县</span>'
              +'<input id="asDist" class="w-addr__inp" type="text" placeholder="选填，如 武侯区" maxlength="20"></label>'
          +'</div>'
          +'<label class="w-addr__row w-addr__row--area"><span class="w-addr__lb">详细地址</span>'
            +'<textarea id="asDetail" class="w-addr__inp" rows="2" placeholder="街道、门牌号、小区名、楼层" maxlength="80" autocomplete="street-address"></textarea></label>'
          +'<label class="w-addr__switch"><span class="w-addr__switch__tx"><b>设为默认地址</b><em>下单时优先使用这个地址</em></span>'
            +'<input type="checkbox" id="asDefault" class="w-addr__cb"><i class="w-addr__track"></i></label>'
        +'</div>'
        +'<div class="w-addr__err" id="asErr" hidden></div>'
      +'</div>'
      +'<div class="w-addr__foot"><button type="button" class="w-addr__save" id="asSave">保存并使用</button></div>'
    +'</div>';
}

function showErr(msg){
  var e=$('asErr');if(!e)return;
  e.textContent=msg;e.hidden=!msg;
}
function locate(){
  var btn=$('asLocate');
  if(!window.navigator||!navigator.geolocation||!window.clubosNearestCity){toast('当前浏览器不支持定位，请手动填写所在地区');return;}
  if(btn){btn.disabled=true;btn.textContent='定位中…';}
  var done=function(){if(btn){btn.disabled=false;btn.textContent='定位当前城市';}};
  try{
    navigator.geolocation.getCurrentPosition(function(pos){
      var c=window.clubosNearestCity(pos.coords.latitude,pos.coords.longitude);
      if(c){
        setVal('asCity',c);
        var p=provinceOfCity(c);if(p)setVal('asProv',p);
        showErr('');toast('已定位到 '+c+'，请补全详细地址');
      }else toast('附近暂无可选城市，请手动填写所在地区');
      done();
    },function(){toast('定位未开启或已拒绝，请手动填写所在地区');done();},
      {timeout:8000,maximumAge:600000});
  }catch(e){toast('定位失败，请手动填写所在地区');done();}
}
function smart(){
  var t=val('asSmart');
  if(!t){toast('先粘贴一段收货信息再识别');return;}
  var o=parseSmart(t),n=0;
  ['consignee:asName','phone:asPhone','province:asProv','city:asCity','district:asDist','detail:asDetail'].forEach(function(pair){
    var kv=pair.split(':');
    if(o[kv[0]]){setVal(kv[1],o[kv[0]]);n++;}
  });
  if(n>=2){showErr('');toast('已识别 '+n+' 项，请核对后保存');}
  else toast('没识别出完整信息，请手动填写');
}
function validate(){
  var v={consignee:val('asName'),phone:val('asPhone'),province:val('asProv'),
         city:val('asCity'),district:val('asDist'),detail:val('asDetail')};
  var msg='',focus=null;
  if(!v.consignee){msg='请填写收货人姓名';focus='asName';}
  else if(!/^1\d{10}$/.test(v.phone)){msg='请填写 11 位手机号';focus='asPhone';}
  else if(!v.province||!v.city){msg='请填写省份与城市';focus=!v.province?'asProv':'asCity';}
  else if(!v.detail){msg='请填写详细地址';focus='asDetail';}
  return {v:v,msg:msg,focus:focus};
}
async function save(){
  if(!_ctx)return;
  var r=validate();
  if(r.msg){showErr(r.msg);toast(r.msg);var f=$(r.focus);if(f)f.focus();return;}
  showErr('');
  var def=$('asDefault');
  var v=r.v;
  if(def&&def.checked)v.isDefault=true;
  var edit=!!(_ctx.addr&&_ctx.addr.id);
  var url='/api/public/users/'+encodeURIComponent(_ctx.userId)+'/addresses'+(edit?'/'+encodeURIComponent(_ctx.addr.id):'');
  try{
    var a=await window.api(url,{method:edit?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)});
    var cb=_ctx.onSaved;
    closeSheet();
    toast(edit?'地址已更新':'地址已保存');
    if(typeof cb==='function')cb(a);
  }catch(e){showErr((e&&e.message)||'保存失败，请稍后再试');}
}

/** 打开地址抽屉。
 *  opts.userId  必填，顾客 id
 *  opts.addr    可选，编辑时传已有地址对象（含 id）
 *  opts.onSaved 可选，保存成功回调，参数是后端返回的地址对象
 */
function openAddressSheet(opts){
  opts=opts||{};
  var userId=Number(opts.userId||0);
  if(!userId){toast('无法识别当前用户，请重新进入页面');return;}
  if(_root)closeSheet();
  _ctx={userId:userId,addr:opts.addr||null,onSaved:opts.onSaved||null};
  var root=document.createElement('div');
  root.id='addrSheet';root.className='w-sheet w-addr-sheet';
  root.innerHTML=sheetHtml(_ctx.addr);
  document.body.appendChild(root);
  document.documentElement.classList.add('w-sheet-open');
  _root=root;
  var a=_ctx.addr||{};
  setVal('asName',a.consignee||'');setVal('asPhone',a.phone||'');
  setVal('asProv',a.province||'');setVal('asCity',a.city||'');
  setVal('asDist',a.district||'');setVal('asDetail',a.detail||'');
  var def=$('asDefault');if(def)def.checked=!!Number(a.is_default);
  /* 复用全站弹层会话：Esc 关闭、Tab 圈闭、焦点进入/归还、背景滚动锁。
     抽屉挂在 body，uxDialogSession 只要求传一个 root 节点，不挑 class。 */
  if(window.uxDialogSession){
    _session=window.uxDialogSession(root,{onEscape:closeSheet,initialFocus:'#asName'});
  }
  root.addEventListener('click',function(e){
    var t=e.target.closest?e.target.closest('[data-close]'):null;
    if(t&&root.contains(t)){closeSheet();return;}
    if(e.target.id==='asSmartBtn')smart();
    else if(e.target.id==='asLocate')locate();
    else if(e.target.id==='asSave')save();
  });
  /* 表单里回车不提交（抽屉不是 form，避免浏览器默认行为与重复保存）。 */
  root.addEventListener('keydown',function(e){
    if(e.key==='Enter'&&e.target&&e.target.tagName==='INPUT'){e.preventDefault();}
  });
  root.addEventListener('input',function(e){
    if(e.target&&e.target.closest&&e.target.closest('.w-addr__form'))showErr('');
  });
}

window.openAddressSheet=openAddressSheet;
window.closeAddressSheet=closeSheet;
/* 供离线单测 / 浏览器探针调用，不在页面里用 */
window.__addrSheetParse=parseSmart;
})();

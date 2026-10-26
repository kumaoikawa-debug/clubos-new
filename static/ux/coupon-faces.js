/* 通用券面（2026-10-24 优惠券体系 · 2026-10-26 视觉升级 + 商家自定义配色）
   ---------------------------------------------------------------------------------------
   后台建券时挑一款内置模板，也可以自定义渐变双色与文字色（face.c1/c2/ink，手填 #RRGGBB）：
   平台端券列表、C 端券包、分享卡都用这一套渲染，视觉一致，分享出去也好看。
   券面信息：面额（无门槛 / 满 X 减 Y / N 折）、用途、有效期、券码。
   本文件只挂 window.*，不依赖其它脚本。 */
(function () {
  /* 内置模板：老 key（sunrise/forest/alpine/dusk/sand/mono）保持原色不变 —— 已落库的券不能变色；
     2026-10-26 新增 4 款年轻潮流配色 + 1 款黑金。 */
  var TEMPLATES = [
    { key: 'sunrise', name: '暖橙日出', from: '#FF9A5A', to: '#EF6236', ink: '#FFFFFF' },
    { key: 'sakura',  name: '蜜桃粉',   from: '#FF8FA3', to: '#E85D75', ink: '#FFF6F7' },
    { key: 'ocean',   name: '海盐蓝',   from: '#4FA3E3', to: '#2C6FBF', ink: '#F2F9FF' },
    { key: 'forest',  name: '松林墨绿', from: '#2F6B4F', to: '#1D4432', ink: '#EAF3EC' },
    { key: 'grape',   name: '葡萄紫',   from: '#8E6BD9', to: '#5F3FB8', ink: '#F6F2FF' },
    { key: 'mint',    name: '薄荷青',   from: '#3FBFA8', to: '#1F8E7C', ink: '#EFFCF9' },
    { key: 'alpine',  name: '雪山灰蓝', from: '#5A7D9A', to: '#3A5871', ink: '#F0F5F8' },
    { key: 'dusk',    name: '暮色紫粉', from: '#7B5EA7', to: '#C06BA0', ink: '#FBF2F8' },
    { key: 'sand',    name: '沙丘米金', from: '#EACB95', to: '#D5A257', ink: '#4A3A1E', dark: true },
    { key: 'gold',    name: '曜石黑金', from: '#3A342B', to: '#17130E', ink: '#EFD9A7', dark: true },
    { key: 'mono',    name: '素墨',     from: '#333B37', to: '#151B18', ink: '#EAF0EC' }
  ];
  window.uxCouponTemplates = TEMPLATES;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }
  function money(n) {
    var v = Number(n || 0);
    return '¥' + (Math.round(v * 100) / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }
  window.uxCouponMoney = money;

  /* 安全解析 face_json（后端下发的是 JSON 字符串）：{"c1","c2","ink"} 只认 #RRGGBB */
  function parseFace(raw) {
    if (!raw) return null;
    var f = raw;
    if (typeof raw === 'string') {
      try { f = JSON.parse(raw); } catch (e) { return null; }
    }
    if (!f || typeof f !== 'object') return null;
    var ok = function (v) { return typeof v === 'string' && /^#[0-9a-fA-F]{6}$/.test(v); };
    var out = {};
    if (ok(f.c1)) out.c1 = f.c1;
    if (ok(f.c2)) out.c2 = f.c2;
    if (ok(f.ink)) out.ink = f.ink;
    return (out.c1 || out.c2 || out.ink) ? out : null;
  }
  window.uxCouponFaceColors = function (imageKey, face) {
    var tpl = window.uxCouponTpl(imageKey);
    var f = parseFace(face);
    var dark = !!tpl.dark;
    var from = tpl.from, to = tpl.to, ink = tpl.ink;
    if (f) {
      if (f.c1) from = f.c1;
      if (f.c2) to = f.c2;
      if (f.ink) ink = f.ink;
      /* 自定义配色时按文字色亮度判断要不要用深色水印 */
      var hex = (f.ink || tpl.ink).replace('#', '');
      var r = parseInt(hex.slice(0, 2), 16), g = parseInt(hex.slice(2, 4), 16), b = parseInt(hex.slice(4, 6), 16);
      dark = (r * 0.299 + g * 0.587 + b * 0.114) < 140;
    }
    return { from: from, to: to, ink: ink, dark: dark };
  };

  /* 投放范围文案：后台列表与 C 端都要说清「谁能领」 */
  window.uxCouponScopeText = function (b) {
    b = b || {};
    var t = String(b.target_type || b.targetType || 'all_clubs');
    if (t === 'city') return (b.target_city || b.targetCity || '指定城市') + '区域';
    if (t === 'club') return '指定俱乐部';
    if (t === 'all_users') return '全部 C 端用户';
    return '全部俱乐部';
  };

  /* 用途文案 */
  window.uxCouponUseText = function (b) {
    b = b || {};
    var t = String(b.benefit_type || b.benefitType || '');
    return t === 'activity_coupon' ? '活动报名可用' : (t === 'gear_coupon' ? '装备购买可用' : '平台福利');
  };

  /* 按 key 取模板，拿不到回落第一个 —— 平台端拆分券卡的左侧渐变块要用同一套色板 */
  window.uxCouponTpl = function (key) {
    for (var i = 0; i < TEMPLATES.length; i++) { if (TEMPLATES[i].key === key) return TEMPLATES[i]; }
    return TEMPLATES[0];
  };

  /* 面额 + 门槛文案：{amount:'¥50', sub:'无门槛' | '满 200 可用'}
     折扣券（2026-10-26）：amount 显示「9 折」，sub 显示门槛或「无门槛」 */
  window.uxCouponAmountText = function (cashValue, thresholdAmount, opts) {
    var o = opts || {};
    var mode = String(o.mode || '');
    var threshold = Number(thresholdAmount || 0);
    if (mode === 'discount') {
      var zhe = Number(o.discountPercent != null ? o.discountPercent : (o.discount_percent || 0)) / 10;
      return { amount: (Math.round(zhe * 10) / 10) + ' 折', sub: threshold > 0 ? ('满 ' + money(threshold) + ' 可用') : '无门槛' };
    }
    var value = Number(cashValue || 0);
    return {
      amount: money(value),
      sub: threshold > 0 ? ('满 ' + money(threshold) + ' 可用') : '无门槛'
    };
  };

  /* 券面渲染。opts: {title, imageKey, face, cashValue, thresholdAmount, mode, benefitType,
                       targetType, targetCity, validTo, voucherCode, status, scope}
     face：{c1,c2,ink}（对象或 JSON 字符串），商家自定义配色，优先于模板色。 */
  window.uxCouponFace = function (opts) {
    var o = opts || {};
    var col = window.uxCouponFaceColors(o.imageKey || o.image_key, (o.face != null ? o.face : o.face_json));
    var threshold = Number(o.thresholdAmount != null ? o.thresholdAmount : (o.threshold_amount || 0));
    var value = Number(o.cashValue != null ? o.cashValue : (o.cash_value || 0));
    var amtObj = window.uxCouponAmountText(value, threshold, { mode: o.mode, discountPercent: (o.discountPercent != null ? o.discountPercent : o.discount_percent) });
    var amt = amtObj.amount, sub = amtObj.sub;
    var meta = [];
    if (o.validTo || o.valid_to) meta.push('有效期至 ' + esc(o.validTo || o.valid_to));
    else meta.push('长期有效');
    var scope = o.scope != null ? o.scope : window.uxCouponScopeText(o);
    if (scope) meta.push(scope);
    var code = o.voucherCode || o.voucher_code;
    var kindCls = o.status === 'used' ? 'ux-cpn--used' : (o.status === 'expired' ? 'ux-cpn--expired' : '');
    return '' +
      '<div class="ux-cpn ' + (col.dark ? 'ux-cpn--darkink' : '') + ' ' + kindCls + '" style="--cpn-from:' + col.from + ';--cpn-to:' + col.to + ';--cpn-ink:' + col.ink + '">' +
        '<i class="ux-cpn__wm" aria-hidden="true"></i>' +
        '<div class="ux-cpn__amt"><b>' + amt + '</b><span>' + sub + '</span></div>' +
        '<div class="ux-cpn__body">' +
          '<strong>' + esc(o.title || '优惠券') + '</strong>' +
          '<span class="ux-cpn__use">' + esc(o.useText || window.uxCouponUseText(o)) + '</span>' +
          '<span class="ux-cpn__meta">' + meta.join(' · ') + '</span>' +
          (code ? '<span class="ux-cpn__code">券码 ' + esc(code) + '</span>' : '') +
        '</div>' +
      '</div>';
  };
})();

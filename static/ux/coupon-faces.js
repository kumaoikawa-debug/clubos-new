/* 通用券面（2026-10-24 优惠券体系）
   ---------------------------------------------------------------------------------------
   后台建券时挑一款内置模板（不做图片上传，也就没有「图传不上去 / 传歪了」的问题）：
   平台端券列表、C 端券包、分享卡都用这一套渲染，视觉一致，分享出去也好看。
   券面信息：面额（无门槛 / 满 X 减 Y）、用途、有效期、券码。
   本文件只挂 window.*，不依赖其它脚本。 */
(function () {
  var TEMPLATES = [
    { key: 'sunrise', name: '暖橙日出', from: '#FF9A5A', to: '#EF6236', ink: '#FFFFFF' },
    { key: 'forest', name: '松林墨绿', from: '#2F6B4F', to: '#1D4432', ink: '#EAF3EC' },
    { key: 'alpine', name: '雪山灰蓝', from: '#5A7D9A', to: '#3A5871', ink: '#F0F5F8' },
    { key: 'dusk', name: '暮色紫粉', from: '#7B5EA7', to: '#C06BA0', ink: '#FBF2F8' },
    { key: 'sand', name: '沙丘米黄', from: '#EACB95', to: '#D5A257', ink: '#4A3A1E' },
    { key: 'mono', name: '素墨', from: '#333B37', to: '#151B18', ink: '#EAF0EC' }
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

  /* 面额 + 门槛文案：{amount:'¥50', sub:'无门槛' | '满 200 可用'} */
  window.uxCouponAmountText = function (cashValue, thresholdAmount) {
    var value = Number(cashValue || 0);
    var threshold = Number(thresholdAmount || 0);
    return {
      amount: money(value),
      sub: threshold > 0 ? ('满 ' + money(threshold) + ' 可用') : '无门槛'
    };
  };

  /* 券面渲染。opts: {title, imageKey, cashValue, thresholdAmount, mode, benefitType,
                       targetType, targetCity, validTo, voucherCode, status, scope} */
  window.uxCouponFace = function (opts) {
    var o = opts || {};
    var tpl = null;
    for (var i = 0; i < TEMPLATES.length; i++) { if (TEMPLATES[i].key === (o.imageKey || o.image_key)) { tpl = TEMPLATES[i]; break; } }
    if (!tpl) tpl = TEMPLATES[0];
    var threshold = Number(o.thresholdAmount != null ? o.thresholdAmount : (o.threshold_amount || 0));
    var value = Number(o.cashValue != null ? o.cashValue : (o.cash_value || 0));
    var amt = threshold > 0 ? money(value) : money(value);
    var sub = threshold > 0 ? ('满 ' + money(threshold) + ' 可用') : '无门槛';
    var meta = [];
    if (o.validTo || o.valid_to) meta.push('有效期至 ' + esc(o.validTo || o.valid_to));
    else meta.push('长期有效');
    var scope = o.scope != null ? o.scope : window.uxCouponScopeText(o);
    if (scope) meta.push(scope);
    var code = o.voucherCode || o.voucher_code;
    var kindCls = o.status === 'used' ? 'ux-cpn--used' : (o.status === 'expired' ? 'ux-cpn--expired' : '');
    return '' +
      '<div class="ux-cpn ux-cpn--' + tpl.key + ' ' + kindCls + '" style="--cpn-from:' + tpl.from + ';--cpn-to:' + tpl.to + ';--cpn-ink:' + tpl.ink + '">' +
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

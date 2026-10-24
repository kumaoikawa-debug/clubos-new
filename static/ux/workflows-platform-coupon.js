/* 平台端优惠券中心（2026-10-24）
 * ---------------------------------------------------------------------------------------
 * 设计目标：建券要「少字段、少步骤」，多数场景一键完成。
 *   ① 四个快捷模板（新客立减 / 装备满减 / 城市券 / 装备小额券）覆盖八成场景 → 一键创建；
 *   ② 自定义新建只问 7 件事：用途 / 名称 / 面额 / 门槛 / 数量 / 领取方式 / 券图；
 *   ③ 投放范围（全部俱乐部 / 全部用户 / 指定城市 / 指定俱乐部）单独一步，在券卡上改，
 *      不塞进建券表单 —— 一次只做一件事，运营不会填错。
 * 本文件是平台端最后一个脚本：window.onView 在这里再包一层，保证点「优惠券」时能加载。
 */
(function () {
  var CITY_OPTIONS = [];
  var CLUB_OPTIONS = [];

  var PRESETS = [
    { label: '新客报名立减 ¥30', desc: '活动券 · 无门槛 · 全部俱乐部 · 用户自领',
      benefitType: 'activity_coupon', mode: 'fixed', thresholdAmount: 0, cashValue: 30,
      stock: 500, claimMode: 'claim', targetType: 'all_clubs', imageKey: 'sunrise',
      from: '#FF9A5A', to: '#EF6236' },
    { label: '装备满 200 减 50', desc: '装备券 · 满减 · 全部用户 · 后台发放',
      benefitType: 'gear_coupon', mode: 'threshold', thresholdAmount: 200, cashValue: 50,
      stock: 200, claimMode: 'grant', targetType: 'all_users', imageKey: 'forest',
      from: '#2F6B4F', to: '#1D4432' },
    { label: '成都区域 ¥50 券', desc: '活动券 · 无门槛 · 成都区域 · 用户自领',
      benefitType: 'activity_coupon', mode: 'fixed', thresholdAmount: 0, cashValue: 50,
      stock: 300, claimMode: 'claim', targetType: 'city', targetCity: '成都', imageKey: 'dusk',
      from: '#7B5EA7', to: '#C06BA0' },
    { label: '装备无门槛 ¥10', desc: '装备券 · 无门槛 · 全部用户 · 后台发放',
      benefitType: 'gear_coupon', mode: 'fixed', thresholdAmount: 0, cashValue: 10,
      stock: 1000, claimMode: 'grant', targetType: 'all_users', imageKey: 'sand',
      from: '#EACB95', to: '#D5A257' }
  ];

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }
  function money(n) {
    var v = Number(n || 0);
    return '¥' + (Math.round(v * 100) / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }

  async function ensureMeta() {
    if (CITY_OPTIONS.length) return;
    var cities = (window.CLUBOS_CITIES || []);
    CITY_OPTIONS = cities.map(function (c) { return { value: c, label: c }; });
    try {
      var clubs = await api('/api/platform/clubs');
      CLUB_OPTIONS = (clubs || []).map(function (c) { return { value: c.id, label: c.name }; });
    } catch (e) { /* 俱乐部列表拿不到时，投放里就不给「指定俱乐部」选项 */ }
  }

  function couponCardHtml(x) {
    var st = x.stats || {};
    var scope = window.uxCouponScopeText ? window.uxCouponScopeText(x) : '';
    var face = window.uxCouponFace ? window.uxCouponFace({
      title: x.title, imageKey: x.image_key, cashValue: x.cash_value,
      thresholdAmount: x.threshold_amount, mode: x.mode, benefitType: x.benefit_type,
      targetType: x.target_type, targetCity: x.target_city, validTo: x.valid_to
    }) : '';
    return '' +
      '<div class="plat-cpn">' + face +
        '<div class="plat-cpn__bar">' +
          '<span class="tag ' + (x.status === 'active' ? '' : 'orange') + '">' + (x.status === 'active' ? '进行中' : '已下架') + '</span>' +
          '<span class="plat-cpn__scope">' + esc(scope) + ' · ' + (x.claim_mode === 'claim' ? '用户自领' : '后台发放') + '</span>' +
          '<span class="plat-cpn__stat">已发 ' + (st.issued || 0) + (st.stock != null ? ' / ' + st.stock : '') +
            ' · 已核销 ' + (st.used || 0) + '</span>' +
          '<div class="plat-cpn__ops">' +
            '<button class="btn ghost" onclick="grantPlatformCoupon(' + x.id + ')">发放</button>' +
            '<button class="btn ghost" onclick="setCouponScope(' + x.id + ')">改投放</button>' +
            '<button class="btn ghost" onclick="togglePlatformCoupon(' + x.id + ",'" + x.status + "')\">" +
              (x.status === 'active' ? '下架' : '上架') + '</button>' +
          '</div>' +
        '</div>' +
      '</div>';
  }

  window.renderCouponPresets = function () {
    var box = document.getElementById('couponPresets');
    if (!box) return;
    box.innerHTML = PRESETS.map(function (p, i) {
      return '<button class="plat-cpnpreset" onclick="quickCreateCoupon(' + i + ')">' +
        '<i style="background:linear-gradient(135deg,' + p.from + ',' + p.to + ')"></i>' +
        '<b>' + esc(p.label) + '</b><span>' + esc(p.desc) + '</span></button>';
    }).join('');
  };

  window.loadPlatformCoupons = async function () {
    var box = document.getElementById('platformCoupons');
    if (!box) return;
    try {
      box.innerHTML = '<div class="skel" style="height:92px"></div><div class="skel" style="height:92px"></div>';
      renderCouponPresets();
      var list = await api('/api/platform/coupons');
      if (!list || !list.length) {
        box.innerHTML = '<div class="empty">还没有优惠券。点上面的快捷模板一键创建，或右上角「＋ 新建优惠券」。</div>';
        return;
      }
      box.innerHTML = list.map(couponCardHtml).join('');
    } catch (e) {
      box.innerHTML = '<div class="empty">优惠券加载失败：' + esc((e && e.message) || e) + '</div>';
    }
  };

  window.quickCreateCoupon = async function (i) {
    var p = PRESETS[i];
    if (!p) return;
    try {
      await api('/api/platform/coupons', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: p.label, description: p.desc, benefitType: p.benefitType, mode: p.mode,
          thresholdAmount: p.thresholdAmount, cashValue: p.cashValue, stock: p.stock,
          claimMode: p.claimMode, targetType: p.targetType, targetCity: p.targetCity,
          imageKey: p.imageKey, perUserLimit: 1, shareEnabled: true
        })
      });
      toast('已创建：' + p.label);
      loadPlatformCoupons();
    } catch (e) { toast((e && e.message) || e); }
  };

  window.addPlatformCoupon = async function () {
    await ensureMeta();
    var tpls = window.uxCouponTemplates || [];
    var imageOptions = tpls.map(function (t) { return { value: t.key, label: t.name }; });
    var v = await uxForm({
      title: '新建优惠券',
      subtitle: '成本由总平台承担。先把券建出来，投放范围随时可以在券卡上改。',
      fields: [
        { name: 'benefitType', label: '券用途', type: 'select', required: true, value: 'activity_coupon',
          options: [{ value: 'activity_coupon', label: '活动报名券（报名时抵）' }, { value: 'gear_coupon', label: '装备购买券（买装备时抵）' }] },
        { name: 'title', label: '券名称', required: true, full: true, value: '新客报名立减 ¥30' },
        { name: 'cashValue', label: '抵扣金额（元）', type: 'number', required: true, min: 0.01, step: 1, value: 30 },
        { name: 'thresholdAmount', label: '使用门槛（元，0 = 无门槛直接抵）', type: 'number', required: true, min: 0, step: 1, value: 0 },
        { name: 'stock', label: '发行数量', type: 'number', required: true, min: 1, step: 1, value: 200 },
        { name: 'claimMode', label: '领取方式', type: 'select', required: true, value: 'claim',
          options: [{ value: 'claim', label: '用户自己在领券中心领' }, { value: 'grant', label: '后台定向发放' }] },
        { name: 'imageKey', label: '券面样式', type: 'select', required: true, value: 'sunrise', options: imageOptions }
      ],
      submitText: '创建优惠券'
    });
    if (!v) return;
    try {
      await api('/api/platform/coupons', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: v.title, benefitType: v.benefitType,
          mode: Number(v.thresholdAmount) > 0 ? 'threshold' : 'fixed',
          thresholdAmount: Number(v.thresholdAmount) || 0, cashValue: Number(v.cashValue),
          stock: Number(v.stock), claimMode: v.claimMode, targetType: 'all_clubs',
          imageKey: v.imageKey, perUserLimit: 1, shareEnabled: true
        })
      });
      toast('优惠券已创建');
      loadPlatformCoupons();
    } catch (e) { toast((e && e.message) || e); }
  };

  window.setCouponScope = async function (id) {
    await ensureMeta();
    var v = await uxForm({
      title: '设置投放范围',
      subtitle: '全部俱乐部：所有俱乐部的用户都能领；指定城市：按注册城市圈人。',
      fields: [
        { name: 'targetType', label: '投放范围', type: 'select', required: true, value: 'all_clubs',
          options: [
            { value: 'all_clubs', label: '全部俱乐部（所有用户可领）' },
            { value: 'all_users', label: '全部 C 端用户' },
            { value: 'city', label: '指定城市（按注册城市）' },
            { value: 'club', label: '指定俱乐部' }
          ] },
        { name: 'targetCity', label: '投放城市（城市投放时填，如「成都」）', type: 'text', value: '成都' },
        { name: 'targetClubId', label: '投放俱乐部（俱乐部投放时选）', type: 'select', value: '', options: CLUB_OPTIONS },
        { name: 'validTo', label: '有效期至（可空）', type: 'datetime', value: '' }
      ],
      submitText: '保存投放'
    });
    if (!v) return;
    try {
      await api('/api/platform/coupons/' + id, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          targetType: v.targetType, targetCity: v.targetCity,
          targetClubId: v.targetType === 'club' ? Number(v.targetClubId || 0) : null,
          validTo: v.validTo || null
        })
      });
      toast('投放范围已更新');
      loadPlatformCoupons();
    } catch (e) { toast((e && e.message) || e); }
  };

  window.grantPlatformCoupon = async function (id) {
    await ensureMeta();
    var v = await uxForm({
      title: '发放优惠券',
      subtitle: '定向群发：可以发给全部 C 端用户、某个俱乐部的成员，或某个城市的用户。成本由总平台承担。',
      fields: [
        { name: 'scope', label: '发放对象', type: 'select', required: true, value: 'all_users',
          options: [
            { value: 'all_users', label: '全部 C 端用户' },
            { value: 'club', label: '指定俱乐部的成员' },
            { value: 'city', label: '指定城市的用户' }
          ] },
        { name: 'clubId', label: '俱乐部（俱乐部发放时选）', type: 'select', value: '', options: CLUB_OPTIONS },
        { name: 'city', label: '城市（城市发放时填，如「成都」）', type: 'text', value: '成都' }
      ],
      submitText: '立即发放'
    });
    if (!v) return;
    try {
      var res = await api('/api/platform/coupons/' + id + '/grant', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: v.scope, clubId: Number(v.clubId || 0), city: v.city })
      });
      toast('已发放 ' + (res.issued || 0) + ' 张' + (res.skipped ? '（' + res.skipped + ' 人已达限领）' : ''));
      loadPlatformCoupons();
    } catch (e) { toast((e && e.message) || e); }
  };

  window.togglePlatformCoupon = async function (id, status) {
    try {
      await api('/api/platform/coupons/' + id, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: status === 'active' ? 'inactive' : 'active' })
      });
      toast(status === 'active' ? '已下架' : '已上架');
      loadPlatformCoupons();
    } catch (e) { toast((e && e.message) || e); }
  };

  /* 点「优惠券」栏目时加载：包一层 onView（experience-core 已经包过一次，这里再包一层） */
  var prev = window.onView;
  window.onView = function (view) {
    if (view === 'pbenefits') {
      try { loadPlatformCoupons(); } catch (e) { /* 忽略：页面还没就绪 */ }
    }
    return prev ? prev.call(window, view) : undefined;
  };
  /* 带 hash 直接进「优惠券」栏目时，navInit 已经用旧的 onView 跑过一次，
     上面的包装赶不上 —— 这里补一次首屏加载，否则直接分享链接进来是空列表。 */
  try {
    if (location.hash.slice(1) === 'pbenefits' && document.getElementById('platformCoupons')) {
      setTimeout(loadPlatformCoupons, 120);
    }
  } catch (e) { /* 忽略 */ }
})();

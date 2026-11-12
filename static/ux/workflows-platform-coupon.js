/* 平台端优惠券中心（2026-10-24 重设计）
 * ---------------------------------------------------------------------------------------
 * 设计目标（照有赞券工作台）：
 *   ① 券卡拆成两半：左侧渐变券面（面额 + 门槛），右侧白色信息区
 *      （标题 / 用途 / 有效期 / 状态行 / 操作行）—— 一张卡看清一张券的全部状态；
 *   ② 建券与改券同一页「分栏编辑器」：右侧栏目导航（基本信息 / 使用条件 /
 *      效期设置 / 领用限制 / 其他设置），左侧内容区自己滚，顶部实时预览券卡；
 *   ③ 字段全部映射既有 member_benefits 列；券形态三型显式选择
 *      （fixed 直抵 / threshold 满减 / discount 折扣），提交格式与旧版一致；
 *   ④ 四个快捷模板保留一键创建，覆盖八成场景。
 * 本文件是平台端最后一个脚本：window.onView 在这里再包一层，保证点「优惠券」时能加载。
 */
(function () {
  'use strict';

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

  var SECTIONS = [
    { id: 'basic', label: '基本信息' },
    { id: 'cond', label: '使用条件' },
    { id: 'valid', label: '效期设置' },
    { id: 'limit', label: '领用限制' },
    { id: 'other', label: '其他设置' }
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
  /* DB 存的 '2026-10-24 18:00[:ss]' / '2026-10-24' → datetime-local 的 'YYYY-MM-DDTHH:mm' */
  function toLocalInput(v) {
    if (!v) return '';
    var s = String(v).trim().replace(' ', 'T');
    if (s.length >= 16) return s.slice(0, 16);
    if (s.length === 10) return s + 'T00:00';
    return s;
  }
  /* datetime-local → DB 口味 'YYYY-MM-DD HH:mm'（benefits._parse_dt 认这种） */
  function toDbInput(v) {
    if (!v) return null;
    return String(v).replace('T', ' ');
  }
  function num(v, dft) {
    var n = Number(v);
    return isFinite(n) ? n : dft;
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

  /* ── 券卡（拆分式：左渐变券面 + 右白信息区）
     列表里一行一张（variant 缺省）；编辑器顶部预览用 'card'（竖排卡片）。
     两态共用同一段结构，只差信息区的排布 class，避免两套 DOM 各改各的。 */
  function couponCardHtml(x, variant) {
    var st = x.stats || {};
    var tpl = window.uxCouponTpl ? window.uxCouponTpl(x.image_key) : null;
    var amt = window.uxCouponAmountText
      ? window.uxCouponAmountText(x.cash_value, x.threshold_amount, { mode: x.mode, discountPercent: x.discount_percent })
      : { amount: money(x.cash_value), sub: '无门槛' };
    var from = tpl ? tpl.from : '#FF9A5A';
    var to = tpl ? tpl.to : '#EF6236';
    var ink = tpl ? tpl.ink : '#FFFFFF';
    var active = x.status === 'active';
    var useText = window.uxCouponUseText ? window.uxCouponUseText(x) : '';
    var scope = window.uxCouponScopeText ? window.uxCouponScopeText(x) : '';
    var claimText = x.claim_mode === 'claim' ? '用户自领' : '后台发放';
    var validText = x.valid_to ? ('有效期至 ' + String(x.valid_to).slice(0, 10)) : '长期有效';
    var stockText = st.stock != null ? String(st.stock) : '不限';
    var issued = Number(st.issued || 0);
    /* 删除只对「一张都没发出去」的券开放：一旦有人领过/核销过，删掉会让券包里的券
       变成孤儿（C 端 JOIN 不到券面），这种券只能下架。按钮置灰 + tooltip 说明原因，
       后端 DELETE 也会 409 兜底。 */
    var delHtml = issued > 0
      ? '<span class="plat-cpn__delwrap" title="已有 ' + issued + ' 张被领取，不能删除；如需停止投放请点「下架」">' +
          '<button class="btn ghost plat-cpn__del" type="button" disabled>删除</button></span>'
      : '<button class="btn ghost plat-cpn__del" type="button" onclick="deletePlatformCoupon(' + x.id + ')">删除</button>';
    return '' +
      '<div class="plat-cpn' + (variant === 'card' ? ' plat-cpn--card' : '') + '">' +
        '<div class="plat-cpn__face" style="--cpn-from:' + from + ';--cpn-to:' + to + ';--cpn-ink:' + ink + '">' +
          '<b>' + amt.amount + '</b><span>' + amt.sub + '</span>' +
        '</div>' +
        '<div class="plat-cpn__info">' +
          '<div class="plat-cpn__main">' +
            '<div class="plat-cpn__head"><strong>' + esc(x.title) + '</strong></div>' +
            '<div class="plat-cpn__use">' + esc(useText) + '</div>' +
            '<div class="plat-cpn__valid">' + esc(validText) + (scope ? ' · ' + esc(scope) : '') + '</div>' +
          '</div>' +
          '<div class="plat-cpn__meta">' +
            '<span class="plat-cpn__badge' + (active ? '' : ' plat-cpn__badge--off') + '">' +
              (active ? '进行中' : '已下架') + '</span>' +
            '<span class="plat-cpn__scope">' + esc(scope) + ' · ' + claimText + '</span>' +
            '<span class="plat-cpn__stat">已发 ' + issued + ' / ' + stockText +
              ' · 已核销 ' + (st.used || 0) + '</span>' +
          '</div>' +
          '<div class="plat-cpn__ops">' +
            '<button class="btn ghost" type="button" onclick="grantPlatformCoupon(' + x.id + ')">发放</button>' +
            '<button class="btn ghost" type="button" onclick="editPlatformCoupon(' + x.id + ')">改投放</button>' +
            '<button class="btn ghost" type="button" onclick="togglePlatformCoupon(' + x.id + ",'" + x.status + "')\">" +
              (active ? '下架' : '上架') + '</button>' +
            delHtml +
          '</div>' +
        '</div>' +
      '</div>';
  }

  window.renderCouponPresets = function () {
    var box = document.getElementById('couponPresets');
    if (!box) return;
    box.innerHTML = PRESETS.map(function (p, i) {
      return '<button class="plat-cpnpreset" type="button" onclick="quickCreateCoupon(' + i + ')">' +
        '<i class="plat-cpnpreset__icon" style="background:linear-gradient(135deg,' + p.from + ',' + p.to + ')">' +
          money(p.cashValue) + '</i>' +
        '<span class="plat-cpnpreset__txt"><b>' + esc(p.label) + '</b><span>' + esc(p.desc) + '</span></span>' +
        '</button>';
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

  /* ═══════════════════════════════════════════════════════════════════════════
     分栏编辑器（照图3-13 的栏目结构）：右侧栏目导航 + 左侧可滚动内容区，
     顶部实时预览。创建与编辑共用一页；编辑时「券用途」只读（后端不可改）。
     ═══════════════════════════════════════════════════════════════════════════ */
  function radiosHtml(name, options, value) {
    return '<div class="plat-cpe__radios">' + options.map(function (o) {
      var on = String(o.value) === String(value);
      return '<label class="plat-cpe__radio' + (on ? ' is-on' : '') + '">' +
        '<input type="radio" name="' + name + '" value="' + esc(o.value) + '"' +
        (on ? ' checked' : '') + (o.disabled ? ' disabled' : '') + '>' +
        '<span>' + esc(o.label) + '</span></label>';
    }).join('') + '</div>';
  }
  function selectHtml(id, options, value, placeholder) {
    var opts = placeholder ? '<option value="">' + esc(placeholder) + '</option>' : '';
    opts += (options || []).map(function (o) {
      return '<option value="' + esc(o.value) + '"' +
        (String(o.value) === String(value == null ? '' : value) ? ' selected' : '') + '>' +
        esc(o.label) + '</option>';
    }).join('');
    return '<select id="' + id + '">' + opts + '</select>';
  }
  function tplPickerHtml(imageKey) {
    var tpls = window.uxCouponTemplates || [];
    return '<div class="plat-cpe__tpls">' + tpls.map(function (t) {
      var on = t.key === imageKey;
      return '<label class="plat-cpe__tpl' + (on ? ' is-on' : '') + '" style="--cpn-from:' + t.from +
        ';--cpn-to:' + t.to + ';--cpn-ink:' + t.ink + '">' +
        '<input type="radio" name="imageKey" value="' + t.key + '"' + (on ? ' checked' : '') + '>' +
        '<i></i><span>' + esc(t.name) + '</span></label>';
    }).join('') + '</div>';
  }

  window.openCouponEditor = async function (opts) {
    var cfg = opts || {};
    var cid = cfg.id ? Number(cfg.id) : null;
    await ensureMeta();

    /* 编辑模式：拉最新数据回填（benefit_type 后端不可改，只读展示） */
    var cur = null;
    if (cid) {
      try {
        var list = await api('/api/platform/coupons');
        cur = (list || []).find(function (x) { return Number(x.id) === cid; }) || null;
      } catch (e) { /* 走下面的报错分支 */ }
      if (!cur) {
        await showAlert({ title: '优惠券不存在', message: '列表可能已刷新，请关闭后重新进入优惠券中心。' });
        return;
      }
    }
    var isEdit = !!cur;

    var st = {
      benefitType: cur ? cur.benefit_type : 'activity_coupon',
      title: cur ? cur.title : '新客报名立减 ¥30',
      mode: cur ? (cur.mode || 'fixed') : 'fixed',
      cashValue: cur ? num(cur.cash_value, 30) : 30,
      thresholdAmount: cur ? num(cur.threshold_amount, 0) : 0,
      discountPercent: cur ? num(cur.discount_percent, 0) : 90,
      maxDiscountAmount: cur ? num(cur.max_discount_amount, 0) : 0,
      stock: cur && cur.stock != null ? cur.stock : 200,
      claimMode: cur ? cur.claim_mode : 'claim',
      imageKey: cur ? cur.image_key : 'sunrise',
      targetType: cur ? cur.target_type : 'all_clubs',
      targetCity: cur ? (cur.target_city || '') : '成都',
      targetClubId: cur && cur.target_club_id != null ? cur.target_club_id : '',
      fixedValid: !!(cur && (cur.valid_from || cur.valid_to)),
      validFrom: cur ? toLocalInput(cur.valid_from) : '',
      validTo: cur ? toLocalInput(cur.valid_to) : '',
      perUserLimit: cur ? num(cur.per_user_limit, 1) : 1,
      shareEnabled: cur ? !!cur.share_enabled : true,
      description: cur ? (cur.description || '') : ''
    };

    var titleText = isEdit ? ('编辑优惠券 · ' + st.title) : '新建优惠券';

    /* ---------- 骨架 ---------- */
    var root = document.createElement('div');
    root.className = 'plat-cpe';
    root.innerHTML = '' +
      '<div class="plat-cpe__sheet" role="dialog" aria-modal="true" aria-label="' + esc(titleText) + '">' +
        '<header class="plat-cpe__bar">' +
          '<button class="btn ghost" type="button" data-act="close">← 优惠券列表</button>' +
          '<div class="plat-cpe__title">' + esc(titleText) + '</div>' +
          '<div class="plat-cpe__baracts">' +
            '<span class="plat-cpe__msg" id="cpeMsg"></span>' +
            '<button class="btn" type="button" data-act="save">' + (isEdit ? '保存修改' : '创建优惠券') + '</button>' +
          '</div>' +
        '</header>' +
        '<div class="plat-cpe__body">' +
          '<div class="plat-cpe__scroll" id="cpeScroll">' +
            '<div class="plat-cpe__preview" id="cpePreview"></div>' +

            '<section class="plat-cpe__sec" id="cpeSec-basic">' +
              '<div class="plat-cpe__sec-h"><h3>基本信息</h3><p>券用途创建后不可改；券形态决定 C 端看到的样子（直抵 / 满减 / 折扣）。</p></div>' +
              '<div class="plat-cpe__row"><span>券用途 <i>*</i></span><div>' +
                radiosHtml('benefitType', [
                  { value: 'activity_coupon', label: '活动报名券（报名时抵）' },
                  { value: 'gear_coupon', label: '装备购买券（买装备时抵）' }
                ], st.benefitType) +
                (isEdit ? '<em class="plat-cpe__hint">券用途创建后不可修改</em>' : '') +
              '</div></div>' +
              '<div class="plat-cpe__row"><span>券名称 <i>*</i></span><div>' +
                '<input id="cpeTitle" type="text" maxlength="60" placeholder="例如：新客报名立减 ¥30" value="' + esc(st.title) + '">' +
              '</div></div>' +
              '<div class="plat-cpe__row"><span>券形态 <i>*</i></span><div>' +
                radiosHtml('mode', [
                  { value: 'fixed', label: '直抵券（无门槛直接抵）' },
                  { value: 'threshold', label: '满减券（满 X 减 Y）' },
                  { value: 'discount', label: '折扣券（如 9 折）', disabled: st.benefitType === 'gear_coupon' }
                ], st.mode) +
                '<em class="plat-cpe__hint">折扣券只支持活动报名券；装备购买券不能设为折扣券</em>' +
              '</div></div>' +
              '<div class="plat-cpe__cond" data-when="amount">' +
                '<div class="plat-cpe__row plat-cpe__row--2">' +
                  '<div><span>抵扣金额（元） <i>*</i></span><input id="cpeCash" type="number" min="0.01" step="1" value="' + st.cashValue + '"></div>' +
                  '<div data-when="threshold"><span>使用门槛（元） <i>*</i></span><input id="cpeThreshold" type="number" min="0.01" step="1" value="' + st.thresholdAmount + '">' +
                  '<em class="plat-cpe__hint">满减券必须设置门槛</em></div>' +
                '</div>' +
              '</div>' +
              '<div class="plat-cpe__cond" data-when="discount">' +
                '<div class="plat-cpe__row plat-cpe__row--2">' +
                  '<div><span>折扣力度 <i>*</i></span><input id="cpeDiscount" type="number" min="1" max="99" step="1" value="' + st.discountPercent + '">' +
                  '<em class="plat-cpe__hint">填折后百分比：90 = 9 折</em></div>' +
                  '<div><span>最大抵扣金额（元）</span><input id="cpeMaxDiscount" type="number" min="0" step="1" value="' + st.maxDiscountAmount + '">' +
                  '<em class="plat-cpe__hint">0 = 不限制</em></div>' +
                '</div>' +
                '<div class="plat-cpe__row"><span>使用门槛（元）</span><div>' +
                  '<input id="cpeDiscountThreshold" type="number" min="0" step="1" value="' + st.thresholdAmount + '">' +
                  '<em class="plat-cpe__hint">满此金额才可用；0 = 无门槛</em>' +
                '</div></div>' +
              '</div>' +
              '<div class="plat-cpe__row plat-cpe__row--2">' +
                '<div><span>发行数量</span><input id="cpeStock" type="number" min="1" step="1" value="' + (st.stock == null ? '' : st.stock) + '">' +
                '<em class="plat-cpe__hint">留空 = 不限量</em></div>' +
                '<div><span>领取方式 <i>*</i></span><div>' +
                  radiosHtml('claimMode', [
                    { value: 'claim', label: '用户自己在领券中心领' },
                    { value: 'grant', label: '后台定向发放' }
                  ], st.claimMode) +
                '</div></div>' +
              '</div>' +
              '<div class="plat-cpe__row"><span>券面样式 <i>*</i></span><div>' + tplPickerHtml(st.imageKey) +
                '<em class="plat-cpe__hint">券面是 C 端券包与分享卡的主视觉，选一款顺眼的</em></div></div>' +
            '</section>' +

            '<section class="plat-cpe__sec" id="cpeSec-cond">' +
              '<div class="plat-cpe__sec-h"><h3>使用条件</h3><p>谁能领这张券。全部俱乐部 = 所有俱乐部的用户；指定城市按注册城市圈人；指定俱乐部只给该俱乐部成员。</p></div>' +
              '<div class="plat-cpe__row"><span>投放范围 <i>*</i></span><div>' +
                radiosHtml('targetType', [
                  { value: 'all_clubs', label: '全部俱乐部（所有用户可领）' },
                  { value: 'all_users', label: '全部 C 端用户' },
                  { value: 'city', label: '指定城市（按注册城市）' },
                  { value: 'club', label: '指定俱乐部' }
                ], st.targetType) +
              '</div></div>' +
              '<div class="plat-cpe__cond" data-when="city"><span>投放城市 <i>*</i></span><div>' +
                selectHtml('cpeCity', CITY_OPTIONS, st.targetCity, '请选择城市') + '</div></div>' +
              '<div class="plat-cpe__cond" data-when="club"><span>投放俱乐部 <i>*</i></span><div>' +
                selectHtml('cpeClub', CLUB_OPTIONS, st.targetClubId, '请选择俱乐部') + '</div></div>' +
            '</section>' +

            '<section class="plat-cpe__sec" id="cpeSec-valid">' +
              '<div class="plat-cpe__sec-h"><h3>效期设置</h3><p>固定有效期内可用 = 到点自动失效；长期有效 = 只要不下架就一直能领。</p></div>' +
              '<div class="plat-cpe__row"><span>有效期类型</span><div>' +
                radiosHtml('validType', [
                  { value: 'fixed', label: '固定有效期内可用' },
                  { value: 'forever', label: '长期有效' }
                ], st.fixedValid ? 'fixed' : 'forever') +
              '</div></div>' +
              '<div class="plat-cpe__cond" data-when="fixed">' +
                '<div class="plat-cpe__row plat-cpe__row--2">' +
                  '<div><span>生效时间</span><input id="cpeValidFrom" type="datetime-local" value="' + st.validFrom + '"></div>' +
                  '<div><span>失效时间</span><input id="cpeValidTo" type="datetime-local" value="' + st.validTo + '"></div>' +
                '</div>' +
                '<em class="plat-cpe__hint">失效时间建议比活动结束略晚，避免用户报名后券已过期</em>' +
              '</div>' +
            '</section>' +

            '<section class="plat-cpe__sec" id="cpeSec-limit">' +
              '<div class="plat-cpe__sec-h"><h3>领用限制</h3><p>同一个用户最多能领几张，防止羊毛党把券囤完。</p></div>' +
              '<div class="plat-cpe__row"><span>每人限领（张） <i>*</i></span><div>' +
                '<input id="cpePerUser" type="number" min="1" step="1" value="' + st.perUserLimit + '">' +
              '</div></div>' +
            '</section>' +

            '<section class="plat-cpe__sec" id="cpeSec-other">' +
              '<div class="plat-cpe__sec-h"><h3>其他设置</h3><p>分享转赠控制用户能不能把券转给别人；说明会展示在券详情里。</p></div>' +
              '<div class="plat-cpe__row"><span>分享转赠</span><div>' +
                radiosHtml('shareEnabled', [
                  { value: '1', label: '允许' },
                  { value: '0', label: '不允许' }
                ], st.shareEnabled ? '1' : '0') +
              '</div></div>' +
              '<div class="plat-cpe__row"><span>规则附加说明</span><div>' +
                '<textarea id="cpeDesc" rows="4" maxlength="500" placeholder="例如：仅限活动报名抵扣，不与其他优惠同享">' + esc(st.description) + '</textarea>' +
              '</div></div>' +
            '</section>' +
          '</div>' +

          '<nav class="plat-cpe__nav" id="cpeNav">' +
            SECTIONS.map(function (s, i) {
              return '<a href="javascript:void(0)" data-sec="' + s.id + '"' + (i === 0 ? ' class="is-on"' : '') + '>' +
                esc(s.label) + '</a>';
            }).join('') +
          '</nav>' +
        '</div>' +

        '<footer class="plat-cpe__foot">' +
          '<span class="sub">成本由总平台承担，核销后进平台补贴账</span>' +
          '<div class="plat-cpe__footacts">' +
            '<button class="btn ghost" type="button" data-act="close">取消</button>' +
            '<button class="btn" type="button" data-act="save">' + (isEdit ? '保存修改' : '创建优惠券') + '</button>' +
          '</div>' +
        '</footer>' +
      '</div>';
    document.body.appendChild(root);

    var dirty = false;
    var closed = false;
    var session = null;
    var close = function () {
      if (closed) return;
      if (dirty) {
        showConfirm({ title: '放弃未保存的修改？', message: '这一页还有没保存的内容，关闭后不会保留。', confirmText: '放弃修改', danger: true })
          .then(function (ok) { if (ok) hardClose(); });
        return;
      }
      hardClose();
    };
    var hardClose = function () {
      if (closed) return;
      closed = true;
      session && session.release();
      root.remove();
    };
    session = uxDialogSession(root, { onEscape: function () { close(); } });

    /* ---------- 元素 ---------- */
    var $ = function (sel) { return root.querySelector(sel); };
    var previewBox = $('#cpePreview');
    var scrollBox = $('#cpeScroll');
    var msgBox = $('#cpeMsg');
    var titleInput = $('#cpeTitle');
    var cashInput = $('#cpeCash');
    var thresholdInput = $('#cpeThreshold');
    var discountInput = $('#cpeDiscount');
    var maxDiscountInput = $('#cpeMaxDiscount');
    var discountThresholdInput = $('#cpeDiscountThreshold');
    var stockInput = $('#cpeStock');
    var citySel = $('#cpeCity');
    var clubSel = $('#cpeClub');
    var validFromInput = $('#cpeValidFrom');
    var validToInput = $('#cpeValidTo');
    var perUserInput = $('#cpePerUser');
    var descInput = $('#cpeDesc');

    /* 编辑模式下券用途只读 */
    if (isEdit) {
      root.querySelectorAll('input[name=benefitType]').forEach(function (r) {
        r.disabled = true;
        var lab = r.closest('.plat-cpe__radio');
        if (lab) lab.classList.add('is-locked');
      });
    }

    function radioVal(name) {
      var el = root.querySelector('input[name=' + name + ']:checked');
      return el ? el.value : '';
    }
    function syncRadios() {
      var tt = radioVal('targetType');
      var vt = radioVal('validType');
      var bt = radioVal('benefitType');
      var mode = radioVal('mode');
      /* 折扣形态只在活动报名券下可选；装备券下若当前是折扣，自动切回直抵 */
      var dRadio = root.querySelector('input[name=mode][value=discount]');
      if (dRadio) {
        var lockDiscount = bt === 'gear_coupon';
        dRadio.disabled = lockDiscount;
        var dLab = dRadio.closest('.plat-cpe__radio');
        if (dLab) dLab.classList.toggle('is-locked', lockDiscount);
        if (lockDiscount && mode === 'discount') {
          var fRadio = root.querySelector('input[name=mode][value=fixed]');
          if (fRadio) fRadio.checked = true;
          mode = 'fixed';
        }
      }
      /* 单选胶囊组 + 券面样式组都要跟着 checked 换 is-on（两组结构不同，一起刷） */
      root.querySelectorAll('.plat-cpe__radios, .plat-cpe__tpls').forEach(function (group) {
        group.querySelectorAll('input[type=radio]').forEach(function (r) {
          var lab = r.closest('.plat-cpe__radio') || r.closest('.plat-cpe__tpl');
          if (lab) lab.classList.toggle('is-on', r.checked);
        });
      });
      /* 条件字段显隐 */
      root.querySelectorAll('[data-when]').forEach(function (box) {
        var w = box.getAttribute('data-when');
        var show = (w === 'city' && tt === 'city') || (w === 'club' && tt === 'club') ||
          (w === 'fixed' && vt === 'fixed') ||
          (w === 'amount' && (mode === 'fixed' || mode === 'threshold')) ||
          (w === 'threshold' && mode === 'threshold') ||
          (w === 'discount' && mode === 'discount');
        box.style.display = show ? '' : 'none';
      });
    }

    function paintPreview() {
      var mode = radioVal('mode');
      var discount = mode === 'discount';
      var like = {
        title: titleInput.value || '优惠券名称',
        image_key: radioVal('imageKey'),
        mode: mode,
        discount_percent: discount ? num(discountInput.value, 0) : 0,
        cash_value: discount ? 0 : num(cashInput.value, 0),
        threshold_amount: discount ? num(discountThresholdInput.value, 0)
          : (mode === 'threshold' ? num(thresholdInput.value, 0) : 0),
        status: 'active',
        claim_mode: radioVal('claimMode'),
        target_type: radioVal('targetType'),
        target_city: citySel.value,
        valid_to: toDbInput(validToInput.value),
        stats: { issued: 0, used: 0, stock: stockInput.value === '' ? null : num(stockInput.value, 0) }
      };
      previewBox.innerHTML = couponCardHtml(like, 'card');
    }

    /* 顶部预览卡不挂操作行（编辑器里点了会两层弹窗，直接摘掉） */
    function neutralizePreviewButtons() {
      previewBox.querySelectorAll('.plat-cpn__ops').forEach(function (n) { n.remove(); });
    }

    function refresh() { syncRadios(); paintPreview(); neutralizePreviewButtons(); }
    refresh();

    root.addEventListener('input', function () { dirty = true; refresh(); });
    root.addEventListener('change', function () { dirty = true; refresh(); });

    /* ---------- 右侧栏目导航：点击跳转 + 滚动高亮 ---------- */
    var navLinks = Array.prototype.slice.call(root.querySelectorAll('#cpeNav a'));
    var secEls = SECTIONS.map(function (s) { return $('#cpeSec-' + s.id); });
    navLinks.forEach(function (a) {
      a.addEventListener('click', function () {
        var idx = SECTIONS.findIndex(function (s) { return s.id === a.getAttribute('data-sec'); });
        if (idx >= 0 && secEls[idx]) {
          scrollBox.scrollTo({ top: secEls[idx].offsetTop - 12, behavior: 'smooth' });
        }
      });
    });
    var onScroll = function () {
      var top = scrollBox.scrollTop;
      var act = 0;
      secEls.forEach(function (el, i) { if (el && el.offsetTop - 90 <= top) act = i; });
      navLinks.forEach(function (a, i) { a.classList.toggle('is-on', i === act); });
    };
    scrollBox.addEventListener('scroll', onScroll, { passive: true });

    /* ---------- 保存 ---------- */
    async function save() {
      var title = (titleInput.value || '').trim();
      if (!title) { msgBox.textContent = '请填写券名称'; titleInput.focus(); return; }
      var benefitType = radioVal('benefitType');
      var mode = radioVal('mode');
      var discountPercent = num(discountInput.value, 0);
      var maxDiscountAmount = num(maxDiscountInput.value, 0);
      if (mode === 'discount') {
        if (benefitType !== 'activity_coupon') { msgBox.textContent = '折扣券只支持活动报名券'; return; }
        if (!(discountPercent >= 1 && discountPercent < 100)) {
          msgBox.textContent = '折扣力度必须在 1~99 之间（如 90 表示 9 折）'; discountInput.focus(); return;
        }
        if (!(maxDiscountAmount >= 0)) { msgBox.textContent = '最大抵扣金额不能为负'; maxDiscountInput.focus(); return; }
      }
      var cashValue = num(cashInput.value, 0);
      if (mode !== 'discount' && !(cashValue > 0)) { msgBox.textContent = '抵扣金额必须大于 0'; cashInput.focus(); return; }
      var thresholdAmount = mode === 'threshold' ? num(thresholdInput.value, 0)
        : (mode === 'discount' ? num(discountThresholdInput.value, 0) : 0);
      if (mode === 'threshold' && !(thresholdAmount > 0)) { msgBox.textContent = '满减券必须设置使用门槛'; thresholdInput.focus(); return; }
      var targetType = radioVal('targetType');
      var targetCity = citySel.value;
      var targetClubId = clubSel.value;
      if (targetType === 'city' && !targetCity) { msgBox.textContent = '请选择投放城市'; citySel.focus(); return; }
      if (targetType === 'club' && !targetClubId) { msgBox.textContent = '请选择投放俱乐部'; clubSel.focus(); return; }
      var stockRaw = (stockInput.value || '').trim();
      /* PATCH 的 allowed 白名单按列名（snake_case）匹配，必须发列名 —— 发驼峰会被
         静默丢弃（旧版「改投放没反应」就是这个坑）。POST 走 benefits.py 的驼峰约定。 */
      var patch = {
        title: title,
        mode: mode,
        threshold_amount: thresholdAmount,
        cash_value: mode === 'discount' ? 0 : cashValue,
        discount_percent: mode === 'discount' ? discountPercent : 0,
        max_discount_amount: mode === 'discount' ? (maxDiscountAmount > 0 ? maxDiscountAmount : null) : null,
        stock: stockRaw === '' ? null : Math.max(0, parseInt(stockRaw, 10) || 0),
        claim_mode: radioVal('claimMode'),
        target_type: targetType,
        target_city: targetType === 'city' ? targetCity : null,
        target_club_id: targetType === 'club' ? (parseInt(targetClubId, 10) || null) : null,
        image_key: radioVal('imageKey'),
        per_user_limit: Math.max(1, parseInt(perUserInput.value, 10) || 1),
        share_enabled: radioVal('shareEnabled') === '1',
        valid_from: radioVal('validType') === 'fixed' ? toDbInput(validFromInput.value) : null,
        valid_to: radioVal('validType') === 'fixed' ? toDbInput(validToInput.value) : null,
        description: (descInput.value || '').trim()
      };
      msgBox.textContent = '';
      var saveBtn = root.querySelector('[data-act=save]');
      if (saveBtn) saveBtn.disabled = true;
      try {
        if (isEdit) {
          await api('/api/platform/coupons/' + cid, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch)
          });
          toast('优惠券已保存');
        } else {
          /* POST 侧是 benefits.py 的驼峰约定（benefitType / cashValue / …） */
          var post = {
            title: patch.title, benefitType: benefitType, mode: patch.mode,
            thresholdAmount: patch.threshold_amount, cashValue: patch.cash_value,
            discountPercent: patch.discount_percent, maxDiscountAmount: patch.max_discount_amount,
            stock: patch.stock, claimMode: patch.claim_mode, targetType: patch.target_type,
            targetCity: patch.target_city, targetClubId: patch.target_club_id,
            imageKey: patch.image_key, perUserLimit: patch.per_user_limit,
            shareEnabled: patch.share_enabled, validFrom: patch.valid_from,
            validTo: patch.valid_to, description: patch.description
          };
          await api('/api/platform/coupons', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(post)
          });
          toast('优惠券已创建');
        }
        hardClose();
        loadPlatformCoupons();
      } catch (e) {
        msgBox.textContent = (e && e.message) || String(e);
        if (saveBtn) saveBtn.disabled = false;
      }
    }

    /* ---------- 事件（委托在根节点：底部栏与内容区是兄弟节点） ---------- */
    root.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-act]');
      if (!btn || !root.contains(btn)) return;
      var act = btn.getAttribute('data-act');
      if (act === 'close') close();
      else if (act === 'save') save();
    });
  };

  /* 新建入口（index.html 头部按钮） */
  window.addPlatformCoupon = function () { openCouponEditor({}); };
  /* 券卡「改投放」入口：打开同一页编辑器（使用条件分区就在里面） */
  window.editPlatformCoupon = function (id) { openCouponEditor({ id: id }); };

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

  /* 删除券（只允许删「一张都没发出去」的券）。已发过的券后端 409，前端也置灰。 */
  window.deletePlatformCoupon = async function (id) {
    var ok = await showConfirm({
      title: '删除这张优惠券？',
      message: '删除后不可恢复：后台列表、C 端领券中心都会立刻不再出现这张券。' +
               '已经发出去的券不能删（只能下架），这里只会删掉从未发放过的券。',
      confirmText: '确认删除', danger: true
    });
    if (!ok) return;
    try {
      await api('/api/platform/coupons/' + id, { method: 'DELETE' });
      toast('已删除');
      loadPlatformCoupons();
    } catch (e) {
      /* 并发场景：别人刚领了 / 别的窗口刚发过 —— 后端 409 带原因，直接透给用户 */
      var msg = (e && e.message) || String(e);
      await showAlert({ title: '删除失败', message: msg });
    }
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

/* 总平台 · 营销中心（2026-11-15）
 * ---------------------------------------------------------------------------------------
 * 把俱乐部端「俱乐部营销」的四类玩法、消费送券、邀请奖励、发放记录搬到总平台，
 * 但保持平台端自己的视觉语言与「券统一分栏编辑器」这个更好用的建券方式：
 *   ① 券中心（保留原快捷模板 + 分栏编辑器，指定对象改为实时分组下拉）
 *   ② 消费送券  ③ 营销活动  ④ 邀请奖励  ⑤ 发放记录
 * 用 5 个分区 tab 收拢长页面，手机端可横向滑动，避免一屏塞十几张卡片。
 * 所有平台玩法都走 /api/platform/*，与俱乐部端数据严格隔离；本文件不碰共用文件。
 */
(function () {
  'use strict';

  var TAB = 'coupons';
  var CTYPE_CN = {
    NEWCOMER: '首单立减', TIME_LIMIT: '限时折扣', BUY_GIFT: '满额买赠', INVITE: '邀请有礼',
    GROUP: '拼团', CHECKIN: '签到', LOTTERY: '抽奖', STAMP: '集章', GIFT_CARD: '礼品卡'
  };
  var STATUS_CN = { DRAFT: '草稿', ACTIVE: '启用中', PAUSED: '已暂停', ENDED: '已结束' };
  var GIFT_CN = { coupon: '优惠券', points: '活动积分', gift_card: '礼品卡额度' };
  var cache = { coupons: [], invite: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }
  function money(n) { return '¥' + (Math.round(Number(n || 0) * 100) / 100).toLocaleString('zh-CN'); }
  function box(id) { return document.getElementById(id); }
  function write(url, method, body) {
    return api(url, {
      method: method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }
  function empty(text) { return '<div class="empty">' + esc(text) + '</div>'; }

  /* ── 顶部统计：口径与各列表一致 ───────────────────────────────────── */
  function overviewHtml(o) {
    var items = [
      ['优惠券', o.coupons, o.couponsActive + ' 张进行中', 'coupons'],
      ['消费送券', o.giftRules, o.giftRulesEnabled + ' 条启用', 'gift'],
      ['营销活动', o.campaigns, o.campaignsActive + ' 个生效中', 'campaign'],
      ['赠品发放', o.campaignGrants, '成交自动留痕', 'grants'],
      ['平台券发放', o.couponIssues, '含定向发放', 'grants'],
      ['邀请奖励', o.inviteEnabled ? '已启用' : '未启用', '俱乐部自设优先', 'invite']
    ];
    return items.map(function (it) {
      return '<button type="button" class="plat-mkt__stat" onclick="platMktTab(\'' + it[3] + '\')">' +
        '<span>' + esc(it[0]) + '</span><b>' + esc(it[1]) + '</b><em>' + esc(it[2]) + '</em></button>';
    }).join('');
  }

  /* ── 分区切换 ─────────────────────────────────────────────────────── */
  window.platMktTab = function (name) {
    TAB = name;
    var tabs = document.querySelectorAll('#platMktTabs [data-mtab]');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.toggle('is-on', tabs[i].getAttribute('data-mtab') === name);
    }
    var panels = document.querySelectorAll('.plat-mkt__panel');
    for (var j = 0; j < panels.length; j++) {
      var p = panels[j];
      var on = p.id === 'platMktPanel-' + name;
      if (on) p.removeAttribute('hidden'); else p.setAttribute('hidden', '');
    }
    loadTab();
  };

  function loadTab() {
    if (TAB === 'coupons') { if (window.loadPlatformCoupons) loadPlatformCoupons(); return; }
    if (TAB === 'gift') return loadGiftRules();
    if (TAB === 'campaign') return loadCampaigns();
    if (TAB === 'invite') return loadInvite();
    if (TAB === 'grants') return loadGrants();
  }

  /* ── ② 消费送券 ───────────────────────────────────────────────────── */
  function couponPoolOptions(list) {
    var items = (list || []).filter(function (x) {
      return x.status === 'active' && x.owner_type === 'PLATFORM' &&
        ['activity_coupon', 'gear_coupon'].indexOf(x.benefit_type) >= 0 &&
        (x.stock == null || Number(x.stock) > 0);
    }).map(function (x) {
      return { value: String(x.id), label: '#' + x.id + ' · ' + (x.title || '未命名') + ' · ' + money(x.cash_value) };
    });
    return [{ value: '', label: '请选择平台券' }].concat(
      items.length ? [{ group: '可赠送的平台券（' + items.length + '）', options: items }] : []);
  }

  function giftRuleHtml(g) {
    var on = !!g.enabled;
    var stock = g.benefit_stock == null ? '不限' : String(g.benefit_stock);
    return '<div class="plat-mkt__row">' +
      '<div class="plat-mkt__rowmain">' +
        '<strong>单笔实付满 ' + money(g.threshold_amount) + ' → 送「' + esc(g.benefit_title || ('#' + g.benefit_id)) + '」</strong>' +
        '<span class="sub">' + esc(g.benefit_type === 'gear_coupon' ? '装备券' : '活动券') +
          ' · 每人最多 ' + (g.per_user_limit || 1) + ' 次 · 券余量 ' + esc(stock) +
          ' · 券状态 ' + (g.benefit_status === 'active' ? '进行中' : '已下架') + '</span>' +
      '</div>' +
      '<div class="plat-mkt__rowops">' +
        '<span class="tag ' + (on ? 'green' : '') + '">' + (on ? '启用中' : '已停用') + '</span>' +
        '<button class="btn ghost" type="button" onclick="platToggleGiftRule(' + g.id + ',' + (on ? 'false' : 'true') + ')">' +
          (on ? '停用' : '启用') + '</button>' +
        '<button class="btn ghost" type="button" onclick="platDelGiftRule(' + g.id + ')">删除</button>' +
      '</div></div>';
  }

  async function loadGiftRules() {
    var host = box('platGiftRuleList');
    if (!host) return;
    host.innerHTML = '<div class="sub">读取规则中…</div>';
    try {
      var rules = await api('/api/platform/coupon-gift-rules');
      host.innerHTML = (rules && rules.length)
        ? rules.map(giftRuleHtml).join('')
        : empty('还没有消费送券规则。点「＋ 送券规则」创建，顾客订单实付达标后会自动收到平台券。');
    } catch (e) { host.innerHTML = empty('读取失败：' + ((e && e.message) || e)); }
  }

  window.platAddGiftRule = function () {
    return uxTask(async function () {
      var pool = await api('/api/platform/coupons');
      cache.coupons = pool || [];
      var opts = couponPoolOptions(cache.coupons);
      if (opts.length <= 1) {
        await showAlert({ title: '还没有可用的平台券', message: '请先在「券中心」创建一张进行中、且有库存的活动券或装备券，再来设置消费送券。' });
        return;
      }
      var d = await uxForm({
        title: '新增消费送券规则',
        subtitle: '顾客单笔订单实付达到门槛后，成交时自动把平台券送进 TA 的券包。活动订单与装备商城订单都参与，成本由总平台承担。',
        fields: [
          { name: 'thresholdAmount', label: '单笔实付满（元）', type: 'number', min: 0.01, step: 0.01, value: 200, required: true },
          { name: 'benefitId', label: '赠送哪张平台券', type: 'dropdown', full: true, required: true, value: '', options: opts },
          { name: 'perUserLimit', label: '每人最多赠送次数', type: 'number', min: 1, step: 1, value: 1, required: true }
        ], submitText: '创建规则'
      });
      if (!d) return;
      await write('/api/platform/coupon-gift-rules', 'POST', {
        thresholdAmount: Number(d.thresholdAmount), benefitId: Number(d.benefitId),
        perUserLimit: Number(d.perUserLimit) || 1, enabled: true
      });
      toast('送券规则已创建并启用');
      loadGiftRules(); loadOverview();
    });
  };

  window.platToggleGiftRule = function (id, enabled) {
    return uxTask(async function () {
      await write('/api/platform/coupon-gift-rules/' + id, 'PATCH', { enabled: !!enabled });
      toast(enabled ? '规则已启用' : '规则已停用');
      loadGiftRules(); loadOverview();
    });
  };

  window.platDelGiftRule = function (id) {
    return uxTask(async function () {
      var ok = await showConfirm({ title: '删除这条送券规则？', message: '删除后，已经送出去的券不受影响，只是不再自动赠送。', confirmText: '确认删除', danger: true });
      if (!ok) return;
      await write('/api/platform/coupon-gift-rules/' + id, 'DELETE', {});
      toast('规则已删除');
      loadGiftRules(); loadOverview();
    });
  };

  /* ── ③ 营销活动（4 类玩法）───────────────────────────────────────── */
  function rulesText(x) {
    var r = x.rules || {};
    if (x.type === 'NEWCOMER' || x.type === 'TIME_LIMIT') {
      return r.mode === 'percent'
        ? ('按 ' + Number(r.percent || 0) + '% 收取' + (r.maxDiscount ? ' · 封顶 ' + money(r.maxDiscount) : ''))
        : ('每单立减 ' + money(r.amount));
    }
    if (x.type === 'BUY_GIFT' || x.type === 'INVITE') {
      return '实付满 ' + money(r.threshold) + ' 送 ' + Number(r.giftValue || 0) + ' ' +
        (r.giftType === 'points' ? '积分' : (GIFT_CN[r.giftType] || '元'));
    }
    return '自定义玩法';
  }
  function scheduleText(x) {
    var s = x.schedule || {};
    if (!Object.keys(s).length) return '长期生效';
    var bits = [];
    if (s.startAt) bits.push('从 ' + s.startAt);
    if (s.endAt) bits.push('至 ' + s.endAt);
    if (s.timeFrom != null || s.timeTo != null) bits.push('每日 ' + (s.timeFrom != null ? s.timeFrom : '00:00') + '–' + (s.timeTo != null ? s.timeTo : '24:00'));
    if (s.weekdays) bits.push('指定星期');
    return bits.join(' · ') || '长期生效';
  }
  function campaignHtml(x) {
    var active = x.status === 'ACTIVE';
    return '<div class="plat-mkt__row">' +
      '<div class="plat-mkt__rowmain">' +
        '<strong>' + esc(CTYPE_CN[x.type] || x.type) + ' · ' + esc(x.title) + '</strong>' +
        '<span class="sub">' + esc(rulesText(x)) + ' · ' + esc(scheduleText(x)) +
          ' · 对所有俱乐部的活动 / 营地订单生效</span>' +
      '</div>' +
      '<div class="plat-mkt__rowops">' +
        '<span class="tag ' + (active ? 'green' : '') + '">' + esc(STATUS_CN[x.status] || x.status) + '</span>' +
        '<button class="btn ghost" type="button" onclick="platToggleCampaign(' + x.id + ',\'' + (active ? 'PAUSED' : 'ACTIVE') + '\')">' +
          (active ? '暂停' : '启用') + '</button>' +
        '<button class="btn ghost" type="button" onclick="platDelCampaign(' + x.id + ')">结束</button>' +
      '</div></div>';
  }

  async function loadCampaigns() {
    var host = box('platCampaignList');
    if (!host) return;
    host.innerHTML = '<div class="sub">读取活动中…</div>';
    try {
      var list = await api('/api/platform/campaigns');
      list = (list || []).filter(function (x) { return x.status !== 'ENDED'; });
      host.innerHTML = list.length
        ? list.map(campaignHtml).join('')
        : empty('还没有平台营销活动。用「＋ 立减 / 限时」或「＋ 买赠 / 邀请」创建，报价与成交会自动兑现。');
    } catch (e) { host.innerHTML = empty('读取失败：' + ((e && e.message) || e)); }
  }

  window.platAddPriceCampaign = function () {
    return uxTask(async function () {
      var d = await uxForm({
        title: '新增立减 / 限时活动',
        subtitle: '「首单立减」只对没有已支付订单的新顾客生效；「限时折扣」必须设置生效时段。平台活动对所有俱乐部的活动 / 营地订单生效，装备商城订单不参与。',
        fields: [
          { name: 'ctype', label: '活动类型', type: 'select', full: true, required: true, value: 'NEWCOMER',
            options: [{ value: 'NEWCOMER', label: '首单立减（新顾客首单专享）' }, { value: 'TIME_LIMIT', label: '限时折扣（全部顾客 · 需设时段）' }] },
          { name: 'title', label: '活动名称（仅后台识别用）', type: 'text', required: true, full: true, placeholder: '如：全平台新客立减 30 元' },
          { name: 'mode', label: '优惠方式', type: 'select', full: true, required: true, value: 'fixed',
            options: [{ value: 'fixed', label: '立减固定金额（元）' }, { value: 'percent', label: '按百分比折扣' }] },
          { name: 'amount', label: '立减金额（元）', type: 'number', min: 0, step: 0.01, value: 30, required: true, when: { field: 'mode', in: ['fixed'] } },
          { name: 'percent', label: '折扣率（90 = 9 折）', type: 'number', min: 1, max: 100, step: 1, value: 90, required: true, when: { field: 'mode', in: ['percent'] } },
          { name: 'maxDiscount', label: '单笔最多优惠金额（可空）', type: 'number', min: 0, step: 0.01, when: { field: 'mode', in: ['percent'] } },
          { name: 'startAt', label: '生效开始（北京时间，可空）', type: 'text', full: true, placeholder: '2026-11-20 00:00', when: { field: 'ctype', in: ['TIME_LIMIT'] },
            help: '格式 YYYY-MM-DD HH:mm；留空 = 立即生效。' },
          { name: 'endAt', label: '生效结束（北京时间，可空）', type: 'text', full: true, placeholder: '2026-11-30 23:59', when: { field: 'ctype', in: ['TIME_LIMIT'] } }
        ], submitText: '创建活动'
      });
      if (!d) return;
      var rules = { mode: String(d.mode || 'fixed') };
      if (rules.mode === 'percent') {
        var pc = Number(d.percent);
        if (!(pc > 0 && pc <= 100)) { toast('折扣率请填 1–100（90 = 9 折）'); return; }
        rules.percent = pc;
        if (Number(d.maxDiscount || 0) > 0) rules.maxDiscount = Number(d.maxDiscount);
      } else {
        rules.amount = Number(d.amount);
      }
      var schedule = {};
      if (String(d.ctype) === 'TIME_LIMIT') {
        if (String(d.startAt || '').trim()) schedule.startAt = String(d.startAt).trim();
        if (String(d.endAt || '').trim()) schedule.endAt = String(d.endAt).trim();
        if (!Object.keys(schedule).length) { toast('限时折扣必须填写至少一项生效时段'); return; }
        schedule.timezone = 'UTC+8';
      }
      await write('/api/platform/campaigns', 'POST', {
        type: String(d.ctype), title: String(d.title || '').trim(), status: 'ACTIVE',
        schedule: schedule, scope: {}, rules: rules
      });
      showAlert({ title: '活动已创建 · 启用中', message: '「' + String(d.title || '').trim() + '」已对全部俱乐部的活动 / 营地报价生效；成交发放可在「发放记录」核对。装备商城订单不参与。' });
      loadCampaigns(); loadOverview();
    });
  };

  window.platAddBuyGift = function () {
    return uxTask(async function () {
      var d = await uxForm({
        title: '新增买赠 / 邀请有礼规则',
        subtitle: '顾客单笔活动 / 营地订单实付满门槛后，成交时自动发放赠品，不改订单应付金额。同一订单同一活动只发一次，成本由总平台承担。',
        fields: [
          { name: 'ctype', label: '规则类型', type: 'select', full: true, required: true, value: 'BUY_GIFT',
            options: [{ value: 'BUY_GIFT', label: '满额买赠（对所有顾客生效）' }, { value: 'INVITE', label: '邀请有礼（仅被邀请好友首单）' }] },
          { name: 'title', label: '规则名称（仅后台识别用）', type: 'text', required: true, full: true, placeholder: '如：满 500 送 30 元券' },
          { name: 'threshold', label: '单笔实付满（元）', type: 'number', min: 0.01, step: 0.01, value: 500, required: true },
          { name: 'giftType', label: '赠品类型', type: 'select', full: true, required: true, value: 'coupon',
            options: [{ value: 'coupon', label: '优惠券（按面额入顾客券包）' }, { value: 'points', label: '活动积分' }, { value: 'gift_card', label: '礼品卡额度' }] },
          { name: 'giftValue', label: '赠品面额（积分数量 / 元）', type: 'number', min: 0.01, step: 0.01, value: 30, required: true },
          { name: 'maxGifts', label: '单笔订单最多发放份数（留空 = 不限）', type: 'number', min: 1, step: 1 }
        ], submitText: '创建规则'
      });
      if (!d) return;
      var rules = { threshold: Number(d.threshold), giftType: String(d.giftType), giftValue: Number(d.giftValue) };
      if (Number(d.maxGifts || 0) > 0) rules.maxGifts = Number(d.maxGifts);
      await write('/api/platform/campaigns', 'POST', {
        type: String(d.ctype), title: String(d.title || '').trim(), status: 'ACTIVE',
        schedule: {}, scope: {}, rules: rules
      });
      toast('规则已创建并启用');
      loadCampaigns(); loadOverview();
    });
  };

  window.platToggleCampaign = function (id, status) {
    return uxTask(async function () {
      await write('/api/platform/campaigns/' + id, 'PATCH', { status: status });
      toast(status === 'ACTIVE' ? '活动已启用' : '活动已暂停');
      loadCampaigns(); loadOverview();
    });
  };

  window.platDelCampaign = function (id) {
    return uxTask(async function () {
      var ok = await showConfirm({ title: '结束这个营销活动？', message: '结束后不再对新订单生效，已发放的赠品与已有订单不受影响。', confirmText: '结束活动', danger: true });
      if (!ok) return;
      await write('/api/platform/campaigns/' + id, 'DELETE', {});
      toast('活动已结束');
      loadCampaigns(); loadOverview();
    });
  };

  /* ── ④ 邀请奖励 ───────────────────────────────────────────────────── */
  function inviteHtml(d) {
    var s = d.settings || {}, pool = d.pool || [], stat = d.stats || {};
    var nameOf = function (id) {
      var x = (pool || []).find(function (p) { return Number(p.id) === Number(id); });
      return x ? x.title : ('#' + id);
    };
    var rowsHtml = (d.records || []).slice(0, 12).map(function (r) {
      return '<tr><td>' + esc(r.inviter_name || ('用户 ' + r.inviter_user_id)) + '</td>' +
        '<td>' + esc(r.invitee_name || ('用户 ' + r.invitee_user_id)) + '</td>' +
        '<td>' + esc(r.club_id != null ? '俱乐部 ' + r.club_id : '—') + '</td>' +
        '<td>' + esc(r.created_at || '') + '</td></tr>';
    }).join('');
    return '<div class="plat-mkt__invitegrid">' +
      '<div class="card"><div class="panel-title"><h3>当前规则</h3>' +
        '<span class="tag ' + (s.enabled ? 'green' : '') + '">' + (s.enabled ? '已启用' : '未启用') + '</span></div>' +
        '<div class="plat-mkt__kv"><span>被邀请人券</span><b>' + (s.inviteeBenefitId ? esc(nameOf(s.inviteeBenefitId)) : '未设置') + '</b></div>' +
        '<div class="plat-mkt__kv"><span>邀请人奖励券</span><b>' + (s.inviterBenefitId ? esc(nameOf(s.inviterBenefitId)) : '未设置') + '</b></div>' +
        '<div class="plat-mkt__kv"><span>邀请人额外积分</span><b>' + Number(s.inviterPoints || 0) + '</b></div>' +
        '<div class="plat-mkt__kv"><span>分享标题</span><b>' + esc(s.shareTitle || '未设置') + '</b></div>' +
        '<div class="sub" style="margin-top:12px">俱乐部自己启用了邀请奖励（新版或旧版邀请券）时，以俱乐部配置为准；这里只作为全平台兜底，不会覆盖俱乐部的设置。</div>' +
        '<button class="btn secondary" type="button" onclick="platEditInvite()">编辑基本设置</button>' +
      '</div>' +
      '<div class="card"><div class="panel-title"><h3>邀请记录</h3><span class="tag">共 ' + Number(stat.total || 0) + ' 条</span></div>' +
        '<div class="sub">累计发放邀请人积分 ' + Number(stat.points_awarded || 0) + '。</div>' +
        (rowsHtml ? '<div class="plat-tablewrap"><table class="table"><thead><tr><th>邀请人</th><th>被邀请人</th><th>归属</th><th>时间</th></tr></thead><tbody>' + rowsHtml + '</tbody></table></div>'
                  : empty('还没有邀请记录')) +
      '</div></div>';
  }

  async function loadInvite() {
    var host = box('platInviteRoot');
    if (!host) return;
    host.innerHTML = '<div class="sub">读取邀请奖励配置…</div>';
    try {
      var d = await api('/api/platform/invite-reward');
      cache.invite = d;
      host.innerHTML = inviteHtml(d);
    } catch (e) { host.innerHTML = empty('读取失败：' + ((e && e.message) || e)); }
  }

  window.platEditInvite = function () {
    return uxTask(async function () {
      var d = cache.invite || await api('/api/platform/invite-reward');
      var s = d.settings || {}, pool = d.pool || [];
      var opts = [{ value: '', label: '不设置' }];
      var items = pool.filter(function (x) { return x.status === 'active'; }).map(function (x) {
        return { value: String(x.id), label: '#' + x.id + ' · ' + (x.title || '未命名') };
      });
      if (items.length) opts.push({ group: '可用平台券（' + items.length + '）', options: items });
      var v = await uxForm({
        title: '编辑平台邀请奖励',
        subtitle: '被邀请人注册领券、邀请人得奖励券与额外积分。俱乐部自己设了邀请奖励时，本配置不生效。',
        fields: [
          { name: 'enabled', label: '启用平台邀请奖励', type: 'select', full: true, value: s.enabled ? '1' : '0',
            options: [{ value: '1', label: '启用' }, { value: '0', label: '停用' }] },
          { name: 'inviteeBenefitId', label: '被邀请人领的券', type: 'dropdown', full: true, value: String(s.inviteeBenefitId || ''), options: opts },
          { name: 'inviterBenefitId', label: '邀请人奖励券', type: 'dropdown', full: true, value: String(s.inviterBenefitId || ''), options: opts },
          { name: 'inviterPoints', label: '邀请人额外活动积分', type: 'number', min: 0, step: 1, value: Number(s.inviterPoints || 0) },
          { name: 'shareTitle', label: '分享卡片标题', type: 'text', full: true, value: s.shareTitle || '' },
          { name: 'shareDesc', label: '分享卡片副文案', type: 'textarea', full: true, value: s.shareDesc || '' }
        ], submitText: '保存设置'
      });
      if (!v) return;
      await write('/api/platform/invite-reward', 'PUT', {
        enabled: String(v.enabled) === '1',
        inviteeBenefitId: Number(v.inviteeBenefitId || 0) || null,
        inviterBenefitId: Number(v.inviterBenefitId || 0) || null,
        inviterPoints: Number(v.inviterPoints || 0),
        shareTitle: v.shareTitle || '', shareDesc: v.shareDesc || '',
        shareImageKey: s.shareImageKey || 'sunrise'
      });
      toast('平台邀请奖励已保存');
      loadInvite(); loadOverview();
    });
  };

  /* ── ⑤ 发放记录 ───────────────────────────────────────────────────── */
  function grantRows(list, kind) {
    return (list || []).map(function (g) {
      if (kind === 'campaign') {
        return '<tr><td>' + esc(g.created_at || '') + '</td><td>' + esc(g.campaign_title || ('活动 #' + g.campaign_id)) +
          '</td><td>' + esc(GIFT_CN[g.gift_type] || g.gift_type) + ' × ' + Number(g.copies || 1) +
          '</td><td>' + esc(g.user_name || ('用户 ' + g.user_id)) + '</td><td>' + esc(g.order_id || '') + '</td></tr>';
      }
      return '<tr><td>' + esc(g.created_at || '') + '</td><td>' + esc(g.benefit_title || ('券 #' + g.benefit_id)) +
        '</td><td>' + esc(g.status || '') + '</td><td>' + esc(g.user_name || ('用户 ' + g.user_id)) +
        '</td><td>' + esc(g.gift_note || '') + '</td></tr>';
    }).join('');
  }
  function grantTable(title, list, kind, heads) {
    var body = grantRows(list, kind);
    return '<div class="card section"><div class="panel-title"><h3>' + esc(title) + '</h3>' +
      '<span class="tag">' + ((list || []).length) + ' 条</span></div>' +
      (body ? '<div class="plat-tablewrap"><table class="table"><thead><tr>' +
        heads.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') +
        '</tr></thead><tbody>' + body + '</tbody></table></div>' : empty('暂无记录，产生发放后会自动出现在这里。')) +
      '</div>';
  }
  async function loadGrants() {
    var host = box('platGrantRoot');
    if (!host) return;
    host.innerHTML = '<div class="sub">读取发放记录…</div>';
    try {
      var d = await api('/api/platform/marketing/grant-log?limit=100');
      host.innerHTML =
        grantTable('营销活动赠品发放 · 成交自动留痕', d.campaignGrants, 'campaign',
                   ['时间', '活动', '赠品', '用户', '订单']) +
        grantTable('平台券发放 · 定向发放与消费送券', d.couponIssues, 'coupon',
                   ['时间', '优惠券', '状态', '用户', '来源']);
    } catch (e) { host.innerHTML = empty('读取失败：' + ((e && e.message) || e)); }
  }

  /* ── 入口 ─────────────────────────────────────────────────────────── */
  async function loadOverview() {
    var host = box('platMktStats');
    if (!host) return;
    try { host.innerHTML = overviewHtml(await api('/api/platform/marketing/overview')); }
    catch (e) { host.innerHTML = ''; }
  }

  window.loadPlatformMarketing = async function () {
    await loadOverview();
    await loadTab();
  };

  var prev = window.onView;
  window.onView = function (view) {
    if (view === 'pbenefits') {
      try { loadPlatformMarketing(); } catch (e) { /* 页面还没就绪 */ }
    }
    return prev ? prev.call(window, view) : undefined;
  };
  try {
    if (location.hash.slice(1) === 'pbenefits' && document.getElementById('platMktStats')) {
      setTimeout(loadPlatformMarketing, 160);
    }
  } catch (e) { /* 忽略 */ }
})();

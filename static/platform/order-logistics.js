/* 订单筛选 + 物流履约界面（总平台 · 2026-10-24）
   ──────────────────────────────────────────────────────────────────────
   两件事：
   ① 订单页的筛选：俱乐部 / 时间 / 用户名 / 手机号。原来的「筛选当前列表」是
     experience-core 注入的**客户端**模糊搜索，只能过滤已经渲染出来的行 ——
     订单多了以后既慢又筛不全（时间范围、跨俱乐部根本表达不了）。这里换成
     服务端 SQL 筛选，表格加 data-ux-skip 让通用搜索条退出。
   ② 物流：发货 / 轨迹时间线 / 同步 / 补录 / 服务商配置。全部走
     /api/platform/orders/{id}/shipment*，后端是 logistics_engine 的适配层，
     换承运商只改配置，不动这里。

   ★ 独立文件而不是塞进 platform.js：那边已经 700 多行的单行压缩风格，
     再加 UI 只会更难维护；也避开「后加载覆盖前一个同名函数」的隐性依赖。
*/
(function () {
  'use strict';

  const STATUS_CN = {
    created: '已下单', in_transit: '运输中', out_for_delivery: '派送中',
    delivered: '已签收', exception: '异常'
  };
  const STATUS_CLS = {
    created: '', in_transit: 'blue', out_for_delivery: 'orange', delivered: '', exception: 'red'
  };

  function stTag(s) {
    const k = String(s || '').toLowerCase();
    return `<span class="tag ${STATUS_CLS[k] || ''}">${esc(STATUS_CN[k] || s || '—')}</span>`;
  }

  function fmt(ts) {
    if (!ts) return '';
    /* DB 存 UTC；+8h 后按 UTC 字段读，避免浏览器时区二次偏移 */
    const d = new Date(String(ts).replace(' ', 'T') + 'Z');
    if (isNaN(d)) return String(ts).slice(0, 16);
    const l = new Date(d.getTime() + 8 * 3600e3);
    const p = n => String(n).padStart(2, '0');
    return `${p(l.getUTCMonth() + 1)}-${p(l.getUTCDate())} ${p(l.getUTCHours())}:${p(l.getUTCMinutes())}`;
  }

  function localDay(offset) {
    const d = new Date(Date.now() + (offset || 0) * 86400e3 + 8 * 3600e3);
    const p = n => String(n).padStart(2, '0');
    return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
  }

  /* ---------- 筛选条 ---------- */
  let filtersReady = false;

  window.ensureOrderFilters = async function () {
    if (filtersReady) return;
    const clubSel = document.getElementById('ordFilterClub');
    const stSel = document.getElementById('ordFilterStatus');
    if (!clubSel || !stSel) return;
    try {
      const data = await api('/api/platform/orders/filters');
      const keep = clubSel.value;
      clubSel.innerHTML = '<option value="">全部俱乐部</option>' +
        (data.clubs || []).map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
      if (keep) clubSel.value = keep;
      stSel.innerHTML = '<option value="">全部状态</option>' +
        (data.statuses || []).map(s => `<option value="${esc(s.value)}">${esc(s.label)}</option>`).join('');
      filtersReady = true;
    } catch (e) { /* 下拉拉不到不影响按时间/用户名筛 */ }
  };

  window.orderFilterQuery = function () {
    const qs = new URLSearchParams();
    const club = document.getElementById('ordFilterClub');
    const from = document.getElementById('ordFilterFrom');
    const to = document.getElementById('ordFilterTo');
    const user = document.getElementById('ordFilterUser');
    const phone = document.getElementById('ordFilterPhone');
    const status = document.getElementById('ordFilterStatus');
    if (club && club.value) qs.set('club_id', club.value);
    if (from && from.value) qs.set('date_from', from.value);
    if (to && to.value) qs.set('date_to', to.value);
    if (user && user.value.trim()) qs.set('user', user.value.trim());
    if (phone && phone.value.trim()) qs.set('phone', phone.value.trim());
    if (status && status.value) qs.set('status', status.value);
    return qs.toString();
  };

  window.setOrderRange = function (preset) {
    const from = document.getElementById('ordFilterFrom');
    const to = document.getElementById('ordFilterTo');
    if (!from || !to) return;
    const today = localDay(0);
    if (preset === 'today') { from.value = today; to.value = today; }
    else if (preset === '7d') { from.value = localDay(-6); to.value = today; }
    else if (preset === '30d') { from.value = localDay(-29); to.value = today; }
    document.querySelectorAll('#orderFilter .plat-ofilter__chip').forEach(b =>
      b.classList.toggle('is-on', b.dataset.range === preset));
    loadOrders();
  };

  window.resetOrderFilter = function () {
    ['ordFilterClub', 'ordFilterFrom', 'ordFilterTo', 'ordFilterUser', 'ordFilterPhone', 'ordFilterStatus']
      .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    document.querySelectorAll('#orderFilter .plat-ofilter__chip').forEach(b => b.classList.remove('is-on'));
    loadOrders();
  };

  /* ---------- 物流：发货 ---------- */
  window.openShipmentDialog = async function (orderId) {
    let data = null;
    try { data = await api(`/api/platform/orders/${orderId}/shipment`); } catch (e) { data = null; }
    if (data && data.shipment) return openTimeline(orderId, data);

    let cfg;
    try { cfg = await api('/api/platform/logistics/config'); }
    catch (e) { await showAlert({ title: '加载失败', message: e.message }); return; }

    const v = await uxForm({
      title: '填写物流信息',
      subtitle: `填单号即发货；轨迹由「${cfg.providerName}」提供，可在右上角「物流对接」里切换服务商。`,
      submitText: '确认发货',
      fields: [
        {
          name: 'carrierCode', label: '承运商', type: 'select', value: 'SF',
          options: (cfg.carriers || []).map(c => ({ value: c.code, label: c.name }))
        },
        { name: 'trackingNo', label: '物流单号', required: true, placeholder: '例如 SF1234567890', full: true }
      ]
    });
    if (!v || !v.trackingNo) return;
    const carrier = ((cfg.carriers || []).find(c => c.code === v.carrierCode) || {}).name || '';
    try {
      await api(`/api/platform/orders/${orderId}/shipment`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ carrierCode: v.carrierCode, carrier, trackingNo: v.trackingNo })
      });
      toast('已发货');
      if (typeof loadOrders === 'function') loadOrders();
      const fresh = await api(`/api/platform/orders/${orderId}/shipment`);
      openTimeline(orderId, fresh);
    } catch (e) {
      await showAlert({ title: '发货失败', message: e.message });
    }
  };

  /* ---------- 物流：轨迹时间线 ---------- */
  function timelineHtml(data) {
    const s = data.shipment || {};
    const events = data.events || [];
    return `<div class="plat-lgt">
      <div class="plat-lgt__head">
        ${stTag(s.status)}
        <span>${esc(s.carrier || '—')}</span>
        <span class="plat-lgt__no">${esc(s.tracking_no || '—')}</span>
        ${s.shipped_at ? `<span>发货 ${fmt(s.shipped_at)}</span>` : ''}
        ${s.last_sync_at ? `<span>同步于 ${fmt(s.last_sync_at)}</span>` : ''}
      </div>
      ${events.length
        ? `<div class="plat-lgt__tl">${events.map((e, i) => `
            <div class="plat-lgt__ev${i === 0 ? ' is-first' : ''}">
              <b>${esc(e.description || '')}</b>
              <span>${fmt(e.event_time)}${e.location ? ' · ' + esc(e.location) : ''}</span>
            </div>`).join('')}</div>`
        : '<div class="empty">还没有轨迹。点下方「同步轨迹」向服务商拉取，或手动补录一条。</div>'}
    </div>`;
  }

  async function openTimeline(orderId, preloaded) {
    let data = preloaded;
    if (!data) {
      try { data = await api(`/api/platform/orders/${orderId}/shipment`); }
      catch (e) { await showAlert({ title: '加载失败', message: e.message }); return; }
    }
    if (!data.shipment) { return window.openShipmentDialog(orderId); }

    const dlg = uxDialog({
      title: '物流轨迹 · 订单 #' + orderId,
      desc: '轨迹来自物流适配层；「同步轨迹」向服务商拉最新一条，回调推送与手动补录走同一张轨迹表。',
      wide: true,
      body: `<div id="lgtBody">${timelineHtml(data)}</div>`,
      foot: `<button class="btn ghost" data-act="manual">手动补录</button>
             <button class="btn ghost" data-act="sync">同步轨迹</button>
             <button class="btn" data-act="done">关闭</button>`
    });

    const repaint = d => {
      const box = dlg.querySelector('#lgtBody');
      if (box) box.innerHTML = timelineHtml(d);
    };

    dlg.addEventListener('click', async e => {
      const btn = e.target.closest('[data-act]');
      if (!btn) return;
      const act = btn.dataset.act;
      if (act === 'done') { dlg.uxClose(); return; }
      if (act === 'sync') {
        btn.disabled = true; btn.textContent = '同步中…';
        try {
          const r = await api(`/api/platform/orders/${orderId}/shipment/sync`, { method: 'POST' });
          const fresh = await api(`/api/platform/orders/${orderId}/shipment`);
          repaint(fresh);
          toast(r.added ? `新增 ${r.added} 条轨迹` : '没有新轨迹（与服务商一致）');
          if (typeof loadOrders === 'function') loadOrders();
        } catch (err) {
          await showAlert({ title: '同步失败', message: err.message });
        }
        btn.disabled = false; btn.textContent = '同步轨迹';
        return;
      }
      if (act === 'manual') {
        const v = await uxForm({
          title: '手动补录轨迹', submitText: '保存',
          fields: [
            { name: 'description', label: '轨迹描述', required: true, full: true, placeholder: '例如：快递员已取件，预计明日送达' },
            { name: 'location', label: '地点（可空）', placeholder: '例如：杭州转运中心' },
            { name: 'status', label: '状态', type: 'select', value: 'in_transit', options: [
              { value: 'in_transit', label: '运输中' }, { value: 'out_for_delivery', label: '派送中' },
              { value: 'delivered', label: '已签收' }, { value: 'exception', label: '异常' }] }
          ]
        });
        if (!v || !v.description) return;
        try {
          await api(`/api/platform/orders/${orderId}/shipment/events`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ description: v.description, location: v.location || '', status: v.status || 'in_transit' })
          });
          const fresh = await api(`/api/platform/orders/${orderId}/shipment`);
          repaint(fresh);
          toast('已补录');
          if (typeof loadOrders === 'function') loadOrders();
        } catch (err) { await showAlert({ title: '补录失败', message: err.message }); }
      }
    });
  }
  window.openShipmentTimeline = orderId => openTimeline(orderId);

  /* ---------- 物流：服务商配置 ---------- */
  window.openLogisticsSettings = async function () {
    let cfg;
    try { cfg = await api('/api/platform/logistics/config'); }
    catch (e) { await showAlert({ title: '加载失败', message: e.message }); return; }
    const v = await uxForm({
      title: '物流对接',
      subtitle: '发货与轨迹查询走这里的服务商。切换到「快递100」需填 key 与 customer；回调地址见下方说明，外部系统按同一份协议推送即可。',
      submitText: '保存配置',
      wide: true,
      fields: [
        {
          name: 'provider', label: '服务商', type: 'select', value: cfg.provider,
          options: (cfg.providers || []).map(p => ({ value: p.code, label: p.name }))
        },
        { name: 'key', label: '授权 key', value: '', placeholder: cfg.hasKey ? '已配置（留空表示不修改）' : '快递100 授权码', help: '密钥只写在服务端，前端只回显「是否已配置」' },
        { name: 'customer', label: 'customer（企业编号）', value: cfg.customer || '' },
        { name: 'token', label: '回调 token', value: '', placeholder: cfg.tokenSet ? '已配置（留空表示不修改）' : '自定义一个足够长的随机串', help: '承运商推送轨迹时要带 X-ClubOS-Token 头，未配置时回调一律拒绝', full: true }
      ]
    });
    if (!v) return;
    const body = { provider: v.provider };
    if ((v.key || '').trim()) body.key = v.key.trim();
    if (typeof v.customer === 'string') body.customer = v.customer.trim();
    if ((v.token || '').trim()) body.token = v.token.trim();
    try {
      await api('/api/platform/logistics/config', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      toast('物流配置已保存');
      await showAlert({
        title: '已保存',
        message: `当前服务商：${v.provider}\n回调地址：${location.origin}/api/webhooks/logistics/${v.provider}\n\n外部系统推送时：POST 该地址，Header 带 X-ClubOS-Token: <你设置的 token>，Body 为 { trackingNo, events:[{event_time,status,description,location}] }。`
      });
    } catch (e) {
      await showAlert({ title: '保存失败', message: e.message });
    }
  };
})();

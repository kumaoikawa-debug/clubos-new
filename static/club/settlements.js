/* ClubOS NEW · 结算编排管理（P3）俱乐部后台。纯前端，走 club 鉴权。 */
(function () {
  'use strict';
  function getClubId() {
    const q = new URLSearchParams(location.search).get('club_id');
    if (q) return Number(q);
    if (window.CLUB) return Number(window.CLUB);
    return 1;
  }
  const CLUB = getClubId();
  const B = `/api/club/${CLUB}/settlements`;
  const SCOPE_API = `/api/club/${CLUB}/commerce-scope`;
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const yuan = n => '¥' + Number(n || 0).toFixed(2);
  const JSON_HEAD = { 'Content-Type': 'application/json' };

  async function req(method, url, body) {
    const opt = { method };
    if (body !== undefined) {
      opt.headers = JSON_HEAD;
      opt.body = JSON.stringify(body);
    }
    return api(url, opt);
  }
  function ok(msg) { if (window.toast) window.toast(msg); }
  function fail(e, out) {
    const text = '操作失败：' + String(e && e.message || e);
    if (window.toast) window.toast(text);
    if (out) $(out).textContent = text;
  }
  const stBadge = st => {
    const map = {
      RESERVED: ['warn', '已锁定'], SETTLED: ['on', '已成交'],
      RELEASED: ['', '已释放'], FAILED: ['off', '失败待重试'],
      REFUNDED: ['off', '已退款']
    };
    const [cls, label] = map[st] || ['', st || '-'];
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  };

  async function loadScope() {
    try {
      const d = await req('GET', SCOPE_API);
      $('#scopeNote').textContent = `适用范围：${d.note} 允许订单类型 ${d.allowedOrderTypes.join(' / ')}；装备下单接口不接营销。`;
    } catch (_) {
      $('#scopeNote').textContent = '适用范围：仅俱乐部活动 / 营地产品，不含总平台装备销售。';
    }
  }

  async function loadStats() {
    let d = {};
    try { d = await req('GET', `${B}?limit=1`); } catch (_) { d = {}; }
    const s = d.summary || {};
    const items = [
      ['编排订单', s.total || 0],
      ['拼团立减', yuan(s.groupBuyAmount)],
      ['礼品卡抵扣', yuan(s.giftCardAmount)],
      ['分销佣金', yuan(s.commissionAmount)],
      ['实收现金', yuan(s.cashAfterAmount)],
      ['总开关', d.moduleEnabled ? '开启' : '关闭']
    ];
    $('#stats').innerHTML = items.map(([k, v]) =>
      `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');
    $('#hubHint').textContent = d.moduleEnabled
      ? '总开关开启：带 settlement 参数的下单会进入编排'
      : '总开关已关闭：所有下单回落普通口径';
  }

  async function loadList() {
    const st = $('#filterStatus').value;
    let d = {};
    try { d = await req('GET', `${B}?limit=100${st ? '&status=' + encodeURIComponent(st) : ''}`); }
    catch (e) { return fail(e, '#actOut'); }
    const items = d.items || [];
    if (!items.length) { $('#list').innerHTML = '<div class="empty">暂无编排记录</div>'; return; }
    $('#list').innerHTML = `<table><thead><tr>
      <th>结算单</th><th>业务域</th><th>状态</th><th>应付</th><th>拼团立减</th><th>礼品卡</th>
      <th>实收</th><th>佣金</th><th>会员</th><th>时间</th></tr></thead><tbody>` +
      items.map(x => `<tr>
        <td><span class="code">${esc(x.checkoutId)}</span></td>
        <td class="muted">${esc({ activity: '俱乐部活动', camp_order: '营地订单', camp_booking: '营位预订' }[x.kind] || x.kind || '-')}</td>
        <td>${stBadge(x.status)}</td>
        <td>${yuan(x.cashBefore)}</td>
        <td>${yuan(x.groupBuyAmount)}</td>
        <td>${yuan(x.giftCardAmount)}</td>
        <td><b>${yuan(x.cashAfter)}</b></td>
        <td>${yuan(x.commissionAmount)}</td>
        <td>${esc(x.userId)}</td>
        <td class="muted">${esc(x.createdAt || '')}</td>
      </tr>`).join('') + '</tbody></table>';
  }

  function showDetail(d) {
    const x = d.item || {};
    $('#detailOut').textContent = JSON.stringify({
      checkoutId: x.checkoutId,
      status: x.status,
      cashBefore: x.cashBefore,
      groupBuyAmount: x.groupBuyAmount,
      giftCardAmount: x.giftCardAmount,
      orderAmount: x.orderAmount,
      cashAfter: x.cashAfter,
      commissionAmount: x.commissionAmount,
      commissionReason: x.commissionReason,
      kind: x.kind,
      registrationId: x.registrationId,
      expiresAt: x.expiresAt,
      refundedAt: x.refundedAt,
      request: x.request,
      events: (d.events || []).map(e => `${e.createdAt} ${e.eventType} ${e.fromStatus || '-'} → ${e.toStatus || '-'}`)
    }, null, 2);
  }

  async function loadDetail() {
    const id = $('#detailId').value.trim();
    if (!id) { $('#detailOut').textContent = '请先填结算单 checkoutId'; return; }
    try { showDetail(await req('GET', `${B}/${encodeURIComponent(id)}`)); }
    catch (e) { fail(e, '#detailOut'); }
  }

  async function act(path) {
    const id = $('#detailId').value.trim();
    if (!id) { $('#actOut').textContent = '请先填结算单 checkoutId'; return; }
    try {
      const d = await req('POST', `${B}/${encodeURIComponent(id)}/${path}`, {});
      $('#actOut').textContent = '操作完成';
      ok('操作完成');
      if (d && d.item) showDetail(d);
      await loadList(); await loadStats();
    } catch (e) { fail(e, '#actOut'); }
  }

  async function preview() {
    const settlement = {
      useGiftCard: $('#pvGift').checked,
      commission: $('#pvComm').checked,
      teamId: Number($('#pvTeam').value || 0)
    };
    const body = {
      settlement,
      cashAmount: Number($('#pvCash').value || 0),
      userId: Number($('#pvUid').value || 0),
      activityId: Number($('#pvAct').value || 0),
      participantCount: Number($('#pvCount').value || 1)
    };
    try {
      const d = await req('POST', `${B}/preview`, body);
      $('#pvOut').textContent = JSON.stringify(d, null, 2);
    } catch (e) { fail(e, '#pvOut'); }
  }

  $('#refreshBtn').addEventListener('click', () => { loadList(); loadStats(); });
  $('#filterStatus').addEventListener('change', loadList);
  $('#detailBtn').addEventListener('click', loadDetail);
  $('#pvBtn').addEventListener('click', preview);
  $('#releaseBtn').addEventListener('click', () => act('release'));
  $('#retryBtn').addEventListener('click', () => act('retry'));
  $('#expireBtn').addEventListener('click', async () => {
    $('#expireOut').textContent = '执行中…';
    try {
      const d = await req('POST', `${B}/expire-run`, {});
      const r = d.result || {};
      $('#expireOut').textContent = `已释放 ${(r.released || []).length} 单，冲回礼品卡 ¥${(Number(r.giftCardCents || 0) / 100).toFixed(2)}`;
      ok('过期释放完成');
      await loadList(); await loadStats();
    } catch (e) { fail(e, '#expireOut'); }
  });
  $('#list').addEventListener('click', e => {
    const code = e.target.closest('.code');
    if (code) { $('#detailId').value = code.textContent.trim(); loadDetail(); }
  });

  loadList();
  loadStats();
  loadScope();
})();

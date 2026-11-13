/* ClubOS NEW · 礼品卡管理（P2-2）俱乐部后台。纯前端，走 club 鉴权。 */
(function () {
  'use strict';
  function getClubId() {
    const q = new URLSearchParams(location.search).get('club_id');
    if (q) return Number(q);
    if (window.CLUB) return Number(window.CLUB);
    return 1;
  }
  const CLUB = getClubId();
  const B = `/api/club/${CLUB}/gift-cards`;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const yuan = n => '¥' + Number(n || 0).toFixed(2);
  const JSON_HEAD = { 'Content-Type': 'application/json' };

  let lastCards = [];

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
      ACTIVE: ['on', '未兑换'], REDEEMED: ['', '已兑换'], FROZEN: ['off', '已冻结'],
      VOID: ['off', '已作废'], EXPIRED: ['warn', '已过期'], NOT_STARTED: ['warn', '未生效']
    };
    const [cls, label] = map[st] || ['', st || '-'];
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  };

  // ── 汇总 ──────────────────────────────────────────────────────────────────
  async function loadStats() {
    let s = {};
    try { s = await api(`${B}/summary`); } catch (_) { s = {}; }
    const items = [
      ['批次 / 卡密', `${s.batches || 0} / ${s.cards || 0}`],
      ['未兑换 / 已兑换', `${s.activeCards || 0} / ${s.redeemedCards || 0}`],
      ['余额合计', yuan(s.balanceAmount)],
      ['累计兑换', yuan(s.totalRedeemedAmount)],
      ['累计核销', yuan(s.totalConsumedAmount)],
      ['状态', s.enabled ? '启用' : '未启用']
    ];
    $('#stats').innerHTML = items.map(([k, v]) =>
      `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');
  }

  // ── 配置 ──────────────────────────────────────────────────────────────────
  async function loadConfig() {
    let cfg = {};
    try { cfg = await api(`${B}/config`); } catch (_) { cfg = {}; }
    $('#cfgEnabled').checked = !!cfg.enabled;
    $('#cfgLimit').value = cfg.maxRedeemsPerUser != null ? cfg.maxRedeemsPerUser : 0;
    $('#cfgNotice').value = cfg.notice || '';
    $('#hubHint').textContent = cfg.moduleEnabled === false ? '注意：设置中枢已关闭礼品卡模块' : '';
  }
  async function saveConfig() {
    const body = {
      enabled: $('#cfgEnabled').checked,
      maxRedeemsPerUser: Number($('#cfgLimit').value || 0),
      notice: $('#cfgNotice').value.trim()
    };
    try {
      await req('PATCH', `${B}/config`, body);
      ok('配置已保存');
      await loadAll();
    } catch (e) { fail(e); }
  }

  // ── 批次 ──────────────────────────────────────────────────────────────────
  function batchHTML(b) {
    const voided = b.status === 'VOID';
    return `<div class="item" data-id="${esc(b.id)}">
      <div class="item__head">
        <div><span class="item__title">${esc(b.name)}</span>
          <span class="muted">面额 ${yuan(b.faceAmount)} · 已发 ${esc(b.issued)}/${esc(b.planCount)} · 兑换 ${esc(b.redeemed)}</span></div>
        <div>${stBadge(b.status)}
          <button class="btn sm ghost" data-act="toggleBatch">${voided ? '恢复' : '停用'}</button>
        </div>
      </div>
      <div class="muted">有效期：${esc(b.validFrom || '不限')} ~ ${esc(b.validUntil || '不限')}${b.note ? ' · ' + esc(b.note) : ''}</div>
      <div class="row" style="margin-top:6px">
        <input type="number" class="sm more-count" min="1" max="500" step="1" placeholder="追加发卡数">
        <button class="btn sm ghost" data-act="issueMore">追加发卡</button>
        <button class="btn sm ghost" data-act="filterBatch">查看卡密</button>
      </div>
    </div>`;
  }
  async function loadBatches() {
    let list = [];
    try { list = await api(`${B}/batches`); } catch (_) { list = []; }
    $('#batches').innerHTML = list.length ? list.map(batchHTML).join('')
      : '<div class="empty">暂无批次，先新建一个并发行卡密。</div>';
    const sel = $('#filterBatch');
    const cur = sel.value;
    sel.innerHTML = '<option value="">全部批次</option>' + list.map(b =>
      `<option value="${esc(b.id)}">#${esc(b.id)} ${esc(b.name)}（${yuan(b.faceAmount)}）</option>`).join('');
    if (cur && list.some(b => String(b.id) === cur)) sel.value = cur;
  }
  async function createBatch() {
    const body = {
      name: $('#bName').value.trim(),
      faceAmount: Number($('#bFace').value || 0),
      count: Number($('#bCount').value || 0),
      validFrom: $('#bFrom').value.trim(),
      validUntil: $('#bUntil').value.trim()
    };
    if (!body.name || !body.faceAmount || !body.count) {
      if (window.toast) window.toast('请填批次名称 / 面额 / 发卡数');
      return;
    }
    try {
      const r = await req('POST', `${B}/batches`, body);
      ok(`已发行 ${r.issued} 张卡密`);
      ['#bName', '#bFace', '#bCount', '#bFrom', '#bUntil'].forEach(s => $(s).value = '');
      await loadAll();
    } catch (e) { fail(e); }
  }
  async function onBatchClick(e) {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const item = btn.closest('.item');
    const id = Number(item && item.dataset.id);
    const act = btn.dataset.act;
    if (!id) return;
    if (act === 'filterBatch') {
      $('#filterBatch').value = String(id);
      await loadCards();
      $('#cards').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      return;
    }
    if (act === 'toggleBatch') {
      const voided = btn.textContent.trim() === '恢复';
      try {
        await req('PATCH', `${B}/batches/${id}`, { status: voided ? 'ACTIVE' : 'VOID' });
        ok(voided ? '批次已恢复' : '批次已停用，该批次卡密不能再兑换');
        await loadAll();
      } catch (err) { fail(err); }
      return;
    }
    if (act === 'issueMore') {
      const n = Number($('.more-count', item).value || 0);
      if (!n) { if (window.toast) window.toast('请输入追加数量'); return; }
      try {
        const r = await req('POST', `${B}/batches/${id}/issue`, { count: n });
        ok(`追加发行 ${r.issued} 张`);
        await loadAll();
      } catch (err) { fail(err); }
    }
  }

  // ── 卡密 ──────────────────────────────────────────────────────────────────
  function cardHTML(k) {
    const eff = k.effectiveStatus || k.status;
    const editable = k.status !== 'REDEEMED';
    return `<tr data-id="${esc(k.id)}">
      <td><span class="code">${esc(k.code)}</span></td>
      <td>${yuan(k.faceAmount)}</td>
      <td>${stBadge(eff)}</td>
      <td>${k.userId ? '#' + esc(k.userId) : '-'}</td>
      <td>${esc(k.validFrom || '-')} ~ ${esc(k.validUntil || '-')}</td>
      <td>${editable
        ? `<button class="btn sm ghost" data-act="${k.status === 'ACTIVE' ? 'freeze' : 'unfreeze'}">${k.status === 'ACTIVE' ? '冻结' : '恢复'}</button>
           <button class="btn sm danger" data-act="void">作废</button>`
        : '<span class="muted">已入账</span>'}</td>
    </tr>`;
  }
  async function loadCards() {
    const q = new URLSearchParams();
    if ($('#filterBatch').value) q.set('batch_id', $('#filterBatch').value);
    if ($('#filterStatus').value) q.set('status', $('#filterStatus').value);
    if ($('#filterCode').value.trim()) q.set('code', $('#filterCode').value.trim());
    q.set('limit', '500');
    let list = [];
    try { list = await api(`${B}/cards?${q.toString()}`); } catch (_) { list = []; }
    lastCards = list;
    $('#cards').innerHTML = list.length
      ? `<table><thead><tr><th>卡密</th><th>面额</th><th>状态</th><th>兑换会员</th><th>有效期</th><th>操作</th></tr></thead>
         <tbody>${list.map(cardHTML).join('')}</tbody></table>`
      : '<div class="empty">没有符合条件的卡密。</div>';
  }
  async function onCardClick(e) {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const id = Number(tr && tr.dataset.id);
    const act = btn.dataset.act;
    if (!id || !act) return;
    const status = { freeze: 'FROZEN', unfreeze: 'ACTIVE', void: 'VOID' }[act];
    try {
      await req('PATCH', `${B}/cards/${id}`, { status });
      ok('卡密状态已更新');
      await loadAll();
    } catch (err) { fail(err); }
  }
  function exportCards() {
    if (!lastCards.length) { if (window.toast) window.toast('当前没有可导出的卡密'); return; }
    const head = ['卡密', '面额(元)', '状态', '有效状态', '兑换会员', '生效日', '到期日', '批次'];
    const lines = [head.join(',')].concat(lastCards.map(k => [
      k.code, k.faceAmount, k.status, k.effectiveStatus, k.userId || '',
      k.validFrom || '', k.validUntil || '', k.batchId || ''
    ].map(v => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`).join(',')));
    const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `gift-cards-${CLUB}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // ── 兑换 / 核销 ───────────────────────────────────────────────────────────
  async function redeem() {
    const body = { code: $('#redeemCode').value.trim(), userId: Number($('#redeemUid').value || 0) };
    if (!body.code || !body.userId) { $('#redeemOut').textContent = '请填卡密与会员 user_id'; return; }
    try {
      const r = await req('POST', `${B}/redeem`, body);
      $('#redeemOut').textContent = `兑换成功 ${yuan(r.faceAmount)}，当前余额 ${yuan(r.balanceAmount)}`;
      ok('兑换成功');
      await loadAll();
    } catch (e) { fail(e, '#redeemOut'); }
  }
  async function consume(reverse) {
    const body = {
      userId: Number($('#useUid').value || 0),
      orderType: $('#useType').value,
      orderId: $('#useOrder').value.trim()
    };
    if (!reverse) body.amount = Number($('#useAmount').value || 0);
    if (!body.userId || !body.orderId || (!reverse && !body.amount)) {
      $('#useOut').textContent = '请填会员 / 金额 / 订单号';
      return;
    }
    try {
      const r = await req('POST', `${B}/${reverse ? 'reverse' : 'consume'}`, body);
      $('#useOut').textContent = reverse
        ? `已冲回 ${yuan(r.amount)}，余额 ${yuan(r.balanceCents / 100)}`
        : `已核销 ${yuan(r.usedAmount)}，余额 ${yuan(r.balanceAmount)}`;
      ok(reverse ? '冲回成功' : '核销成功');
      await loadAll();
    } catch (e) { fail(e, '#useOut'); }
  }

  // ── 账户 / 流水 ───────────────────────────────────────────────────────────
  async function loadAccounts() {
    let list = [];
    try { list = await api(`${B}/accounts`); } catch (_) { list = []; }
    $('#accounts').innerHTML = list.length
      ? `<table><thead><tr><th>会员</th><th>可用余额</th><th>累计兑换</th><th>累计核销</th><th>状态</th></tr></thead>
         <tbody>${list.map(a => `<tr>
           <td>#${esc(a.userId)}</td><td>${yuan(a.balanceAmount)}</td>
           <td>${yuan(a.totalRedeemedAmount)}</td><td>${yuan(a.totalConsumedAmount)}</td>
           <td>${stBadge(a.status === 'ACTIVE' ? 'ACTIVE' : 'FROZEN')}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">还没有会员兑换过礼品卡。</div>';
  }
  async function loadLedger() {
    const uid = Number($('#ledgerUid').value || 0);
    let list = [];
    try { list = await api(`${B}/ledger?limit=200${uid ? '&user_id=' + uid : ''}`); } catch (_) { list = []; }
    const typeLabel = { REDEEM: '兑换入账', CONSUME: '余额核销', REFUND: '核销冲回', ADJUST_UP: '手工调增', ADJUST_DOWN: '手工调减' };
    $('#ledger').innerHTML = list.length
      ? `<table><thead><tr><th>类型</th><th>金额</th><th>余额快照</th><th>会员</th><th>来源</th><th>时间</th></tr></thead>
         <tbody>${list.map(l => `<tr>
           <td>${esc(typeLabel[l.type] || l.type)}</td>
           <td>${l.amountCents < 0 ? '-' : '+'}${yuan(l.amountAmount)}</td>
           <td>${yuan(l.balanceAfterAmount)}</td>
           <td>#${esc(l.userId)}</td>
           <td>${esc(l.sourceId || '-')}</td>
           <td>${esc(l.createdAt || '-')}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">暂无流水。</div>';
  }

  async function loadAll() {
    await Promise.all([loadStats(), loadConfig(), loadBatches(), loadCards(), loadAccounts(), loadLedger()]);
  }

  // ── 绑定 ──────────────────────────────────────────────────────────────────
  $('#saveCfg').addEventListener('click', saveConfig);
  $('#createBatch').addEventListener('click', createBatch);
  $('#batches').addEventListener('click', onBatchClick);
  $('#refreshCards').addEventListener('click', loadCards);
  $('#exportCards').addEventListener('click', exportCards);
  $('#cards').addEventListener('click', onCardClick);
  $('#redeemBtn').addEventListener('click', redeem);
  $('#useBtn').addEventListener('click', () => consume(false));
  $('#reverseBtn').addEventListener('click', () => consume(true));
  $('#refreshLedger').addEventListener('click', loadLedger);
  $('#filterBatch').addEventListener('change', loadCards);
  $('#filterStatus').addEventListener('change', loadCards);

  loadAll();
})();

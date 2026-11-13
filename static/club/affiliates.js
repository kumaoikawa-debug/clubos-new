/* ClubOS NEW · 分销员管理（P2-1）俱乐部后台。纯前端，走 club 鉴权。 */
(function () {
  'use strict';
  function getClubId() {
    const q = new URLSearchParams(location.search).get('club_id');
    if (q) return Number(q);
    if (window.CLUB) return Number(window.CLUB);
    return 1;
  }
  const CLUB = getClubId();
  const B = `/api/club/${CLUB}/affiliate`;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
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

  function toastOk(msg) { if (window.toast) window.toast(msg); }
  function toastErr(e) { if (window.toast) window.toast('操作失败：' + String(e && e.message || e)); }

  // ── 等级档位 ──────────────────────────────────────────────────────────────
  function levelHTML(lv) {
    lv = lv || {};
    return `<div class="lv">
      <input class="l-key" value="${esc(lv.key || '')}" placeholder="等级 key">
      <input class="l-label wide" value="${esc(lv.label || '')}" placeholder="名称">
      <input class="l-min" type="number" min="0" step="1" value="${esc(lv.minAmountCents != null ? lv.minAmountCents / 100 : 0)}" placeholder="累计≥(元)">
      <input class="l-orders" type="number" min="0" step="1" value="${esc(lv.minOrders != null ? lv.minOrders : 0)}" placeholder="订单数≥">
      <input class="l-rate" type="number" min="1" max="5000" step="1" value="${esc(lv.rateBp != null ? lv.rateBp : 500)}" placeholder="费率bp">
      <button class="btn sm ghost" data-act="delLevel">删除</button>
    </div>`;
  }
  function readLevels() {
    return $$('#levels .lv').map(el => ({
      key: $('.l-key', el).value.trim(),
      label: $('.l-label', el).value.trim(),
      minAmountCents: Math.round(Number($('.l-min', el).value || 0) * 100),
      minOrders: Number($('.l-orders', el).value || 0),
      rateBp: Number($('.l-rate', el).value || 0)
    }));
  }

  async function loadConfig() {
    let cfg = {};
    try { cfg = await api(`${B}/config`); } catch (_) { cfg = {}; }
    $('#cfgEnabled').checked = !!cfg.enabled;
    $('#cfgAuto').checked = cfg.autoApprove !== false;
    $('#cfgSelf').checked = !!cfg.allowSelfPurchase;
    $('#cfgRate').value = cfg.defaultRateBp != null ? cfg.defaultRateBp : 500;
    $('#cfgFreeze').value = cfg.freezeDays != null ? cfg.freezeDays : 7;
    $('#cfgMin').value = cfg.minSettleAmount != null ? cfg.minSettleAmount : 10;
    $('#levels').innerHTML = (cfg.levels || []).length
      ? cfg.levels.map(levelHTML).join('')
      : '<div class="empty">暂无档位，保存时会写入默认三档。</div>';
    if (!cfg.moduleEnabled) {
      $('#previewOut').textContent = '注意：设置中枢已关闭分销模块';
    }
  }

  async function saveConfig() {
    const body = {
      enabled: $('#cfgEnabled').checked,
      autoApprove: $('#cfgAuto').checked,
      allowSelfPurchase: $('#cfgSelf').checked,
      defaultRateBp: Number($('#cfgRate').value || 0),
      freezeDays: Number($('#cfgFreeze').value || 0),
      minSettleAmount: Number($('#cfgMin').value || 0)
    };
    const lv = readLevels();
    if (lv.length) body.levels = lv;
    try {
      await req('PATCH', `${B}/config`, body);
      toastOk('配置已保存');
      await loadAll();
    } catch (e) { toastErr(e); }
  }

  // ── 汇总 ──────────────────────────────────────────────────────────────────
  async function loadStats() {
    let s = {};
    try { s = await api(`${B}/summary`); } catch (_) { s = {}; }
    const items = [
      ['分销员', s.activeMembers != null ? `${s.activeMembers}/${s.members}` : '0'],
      ['推广关系', s.bindings || 0],
      ['待结佣金', yuan(s.pendingAmount)],
      ['已结佣金', yuan(s.settledAmount)],
      ['已打款', yuan(s.paidAmount)],
      ['状态', s.enabled ? '启用' : '未启用']
    ];
    $('#stats').innerHTML = items.map(([k, v]) =>
      `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');
  }

  // ── 分销员 ────────────────────────────────────────────────────────────────
  function memberHTML(m) {
    const st = m.status || 'PENDING';
    const cls = st === 'ACTIVE' ? 'on' : (st === 'FROZEN' ? 'off' : 'warn');
    return `<div class="item" data-id="${esc(m.id)}">
      <div class="item__head">
        <div>
          <span class="item__title">会员 #${esc(m.userId)}</span>
          <span class="code" style="margin-left:8px">${esc(m.code)}</span>
          <span class="badge ${cls}" style="margin-left:6px">${esc(st)}</span>
        </div>
        <div class="muted">${esc(m.levelLabel || m.levelKey || '')} · ${esc(m.rateBp)}bp</div>
      </div>
      <div class="muted" style="margin-top:6px">
        累计 ${esc(m.totalOrders)} 单 / ${yuan(m.totalAmount)} ·
        佣金 ${yuan(m.totalCommission)} · 已结 ${yuan(m.settledAmount)} · 待结 ${yuan(m.pendingCents / 100)}
      </div>
      <div class="row" style="margin-top:8px">
        <select class="m-status sm">
          ${['PENDING', 'ACTIVE', 'FROZEN'].map(x => `<option value="${x}" ${x === st ? 'selected' : ''}>${x}</option>`).join('')}
        </select>
        <input class="m-rate sm" type="number" min="0" max="5000" placeholder="单独费率bp"
               value="${esc(m.rateSource === 'override' ? m.rateBp : '')}">
        <input class="m-note" value="${esc(m.note || '')}" placeholder="备注">
        <button class="btn sm ghost" data-act="saveMember">保存</button>
        <button class="btn sm danger" data-act="delMember">删除</button>
        <button class="btn sm ghost" data-act="settleMember">结算</button>
      </div>
    </div>`;
  }
  async function loadMembers() {
    let rs = [];
    try { rs = await api(`${B}/members`); } catch (_) { rs = []; }
    $('#members').innerHTML = rs.length ? rs.map(memberHTML).join('')
      : '<div class="empty">还没有分销员。填入会员 user_id 添加，或让会员在 C 端自助申请。</div>';
  }

  // ── 绑定 ──────────────────────────────────────────────────────────────────
  async function loadBindings() {
    let rs = [];
    try { rs = await api(`${B}/bindings`); } catch (_) { rs = []; }
    $('#bindings').innerHTML = rs.length
      ? `<table><thead><tr><th>推广员</th><th>被邀请人</th><th>来源</th><th>状态</th><th>时间</th></tr></thead><tbody>` +
        rs.map(r => `<tr><td>#${esc(r.affiliate_user_id)}</td><td>#${esc(r.invitee_user_id)}</td>
          <td>${esc(r.channel || '')}</td><td>${esc(r.status)}</td><td>${esc(r.created_at || '')}</td></tr>`).join('') +
        '</tbody></table>'
      : '<div class="empty">暂无推广关系</div>';
  }

  // ── 佣金流水 ──────────────────────────────────────────────────────────────
  async function loadCommissions() {
    let rs = [];
    try { rs = await api(`${B}/commissions`); } catch (_) { rs = []; }
    $('#commissions').innerHTML = rs.length
      ? `<table><thead><tr><th>订单</th><th>推广员</th><th>金额</th><th>费率</th><th>佣金</th><th>状态</th><th></th></tr></thead><tbody>` +
        rs.map(r => `<tr>
          <td>${esc(r.orderType)}#${esc(r.orderId)}</td><td>#${esc(r.affiliateUserId)}</td>
          <td>${yuan(r.orderAmount)}</td><td>${esc(r.rateBp)}bp</td><td>${yuan(r.commission)}</td>
          <td>${esc(r.status)}</td>
          <td><button class="btn sm ghost" data-act="reverse" data-type="${esc(r.orderType)}" data-oid="${esc(r.orderId)}">冲回</button></td>
        </tr>`).join('') + '</tbody></table>'
      : '<div class="empty">暂无佣金流水</div>';
  }

  // ── 结算单 ────────────────────────────────────────────────────────────────
  async function loadSettlements() {
    let rs = [];
    try { rs = await api(`${B}/settlements`); } catch (_) { rs = []; }
    $('#settlements').innerHTML = rs.length
      ? `<table><thead><tr><th>结算单</th><th>推广员</th><th>金额</th><th>笔数</th><th>状态</th><th></th></tr></thead><tbody>` +
        rs.map(r => `<tr><td>${esc(r.id)}</td><td>#${esc(r.affiliateUserId)}</td>
          <td>${yuan(r.grossAmount)}</td><td>${esc(r.entryCount)}</td><td>${esc(r.status)}</td>
          <td>${r.status === 'GENERATED'
            ? `<button class="btn sm ghost" data-act="pay" data-sid="${esc(r.id)}">标记已打款</button>` : ''}</td></tr>`).join('') +
        '</tbody></table>'
      : '<div class="empty">暂无结算单</div>';
  }

  function loadAll() {
    return Promise.all([loadConfig(), loadStats(), loadMembers(), loadBindings(),
                        loadCommissions(), loadSettlements()]);
  }

  // ── 事件 ──────────────────────────────────────────────────────────────────
  $('#addLevel').onclick = () => {
    if (!$('#levels .lv')) $('#levels').innerHTML = '';
    $('#levels').insertAdjacentHTML('beforeend', levelHTML({ rateBp: 500 }));
  };
  $('#saveCfg').onclick = saveConfig;

  $('#addMember').onclick = async () => {
    const userId = Number($('#newUid').value || 0);
    if (!userId) { toastErr('请填会员 user_id'); return; }
    const body = { userId };
    const rate = Number($('#newRate').value || 0);
    if (rate > 0) body.rateBp = rate;
    const note = $('#newNote').value.trim();
    if (note) body.note = note;
    try {
      await req('POST', `${B}/members`, body);
      $('#newUid').value = ''; $('#newRate').value = ''; $('#newNote').value = '';
      toastOk('分销员已添加'); await loadAll();
    } catch (e) { toastErr(e); }
  };

  $('#addBinding').onclick = async () => {
    const affiliateUserId = Number($('#bindAff').value || 0);
    const inviteeUserId = Number($('#bindInvitee').value || 0);
    if (!affiliateUserId || !inviteeUserId) { toastErr('请填两个 user_id'); return; }
    try {
      await req('POST', `${B}/bindings`, { affiliateUserId, inviteeUserId });
      toastOk('绑定成功'); await loadAll();
    } catch (e) { toastErr(e); }
  };

  $('#previewBtn').onclick = async () => {
    const buyerUserId = Number($('#qBuyer').value || 0);
    const orderAmount = Number($('#qAmount').value || 0);
    if (!buyerUserId || !orderAmount) { toastErr('请填下单人与金额'); return; }
    try {
      const r = await req('POST', `${B}/preview`, { buyerUserId, orderAmount });
      $('#previewOut').textContent = r.eligible
        ? `可返 ${yuan(r.commissionCents / 100)}（${r.rateBp}bp / ${r.levelKey}）`
        : `不计佣：${r.reason}`;
    } catch (e) { toastErr(e); }
  };

  $('#accrueBtn').onclick = async () => {
    const buyerUserId = Number($('#qBuyer').value || 0);
    const orderAmount = Number($('#qAmount').value || 0);
    const orderType = $('#qOrderType').value;
    const orderId = $('#qOrderId').value.trim();
    if (!buyerUserId || !orderAmount || !orderId) { toastErr('请填完整下单人/金额/订单号'); return; }
    try {
      const r = await req('POST', `${B}/accrue`, { buyerUserId, orderAmount, orderType, orderId });
      toastOk(r.accrued ? `入账 ${yuan(r.commissionAmount)}` : `未入账：${r.reason}`);
      await loadAll();
    } catch (e) { toastErr(e); }
  };

  $('#settleBtn').onclick = async () => {
    const affiliateUserId = Number($('#settleUid').value || 0);
    if (!affiliateUserId) { toastErr('请填推广员 user_id'); return; }
    try {
      const r = await req('POST', `${B}/settle`, { affiliateUserId });
      toastOk(r.settled ? `结算单 ${r.settlementId} 已生成（${yuan(r.grossAmount)}）` : `未结算：${r.reason}`);
      await loadAll();
    } catch (e) { toastErr(e); }
  };

  document.addEventListener('click', async ev => {
    const btn = ev.target.closest('[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === 'delLevel') { btn.closest('.lv').remove(); return; }
    const item = btn.closest('.item');
    try {
      if (act === 'saveMember' && item) {
        const body = { status: $('.m-status', item).value, note: $('.m-note', item).value.trim() };
        const rate = Number($('.m-rate', item).value || 0);
        body.rateBp = rate > 0 ? rate : 0;   // 0 = 清除单独费率，回落等级档
        await req('PATCH', `${B}/members/${item.dataset.id}`, body);
        toastOk('已保存');
      } else if (act === 'delMember' && item) {
        await req('DELETE', `${B}/members/${item.dataset.id}`);
        toastOk('已删除');
      } else if (act === 'settleMember' && item) {
        const uid = item.querySelector('.item__title').textContent.replace(/\D/g, '');
        $('#settleUid').value = uid;
        $('#settleBtn').click();
        return;
      } else if (act === 'reverse') {
        await req('POST', `${B}/reverse`, { orderType: btn.dataset.type, orderId: btn.dataset.oid });
        toastOk('已冲回');
      } else if (act === 'pay') {
        await req('POST', `${B}/settlements/${encodeURIComponent(btn.dataset.sid)}/pay`, {});
        toastOk('已标记打款');
      }
      await loadAll();
    } catch (e) { toastErr(e); }
  });

  loadAll();
})();

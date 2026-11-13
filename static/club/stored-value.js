/* ClubOS NEW · 会员储值管理（P1-1）俱乐部后台。纯前端，走 club 鉴权。 */
(function () {
  'use strict';
  function getClubId() {
    const q = new URLSearchParams(location.search).get('club_id');
    if (q) return Number(q);
    if (window.CLUB) return Number(window.CLUB);
    return 1;
  }
  const CLUB = getClubId();
  const B = `/api/club/${CLUB}/stored-value`;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const yuan = n => '¥' + Number(n || 0).toFixed(2);

  function ruleHTML(r) {
    r = r || {};
    return `<div class="rule">
      <input class="r-min" type="number" min="0" step="1" value="${esc(r.minAmount != null ? r.minAmount : '')}" placeholder="充值≥(元)">
      <span class="muted">送</span>
      <input class="r-bonus" type="number" min="0" step="1" value="${esc(r.bonusAmount != null ? r.bonusAmount : '')}" placeholder="赠额(元)">
      <input class="r-label wide" value="${esc(r.label || '')}" placeholder="标签(选填)">
      <label class="muted" style="display:flex;align-items:center;gap:4px">
        <input class="r-enabled" type="checkbox" ${r.enabled === false ? '' : 'checked'}>启用
      </label>
      <button class="btn sm ghost" data-act="delRule">删除</button>
    </div>`;
  }

  async function loadConfig() {
    let cfg = {};
    try { cfg = await api(`${B}/config`); } catch (_) { cfg = {}; }
    $('#cfgEnabled').checked = !!cfg.enabled;
    $('#cfgAllowActivity').checked = cfg.allowActivityPay !== false;
    $('#cfgRatio').value = cfg.maxPayRatio != null ? cfg.maxPayRatio : 100;
    $('#cfgMinRecharge').value = cfg.minRechargeAmount != null ? cfg.minRechargeAmount : 0;
    $('#rules').innerHTML = (cfg.rules || []).length
      ? cfg.rules.map(ruleHTML).join('')
      : '<div class="empty">还没有档位。不设档位也可充值（只入本金，不赠送）。</div>';
  }

  function readConfig() {
    const rules = $$('.rule', $('#rules')).map(el => ({
      minAmount: Number($('.r-min', el).value || 0),
      bonusAmount: Number($('.r-bonus', el).value || 0),
      label: $('.r-label', el).value.trim(),
      enabled: $('.r-enabled', el).checked,
    })).filter(r => r.minAmount > 0);
    return {
      enabled: $('#cfgEnabled').checked,
      allowActivityPay: $('#cfgAllowActivity').checked,
      maxPayRatio: Number($('#cfgRatio').value || 0),
      minRechargeAmount: Number($('#cfgMinRecharge').value || 0),
      rules,
    };
  }

  async function loadSummary() {
    let s = {};
    try { s = await api(`${B}/summary`); } catch (_) { s = {}; }
    const cells = [
      ['储值账户', s.accounts || 0], ['当前余额', yuan(s.balanceAmount)],
      ['累计充值', yuan(s.rechargedAmount)], ['累计赠送', yuan(s.bonusAmount)],
      ['累计消费', yuan(s.spentAmount)], ['待确认充值单', s.pendingRecharges || 0],
    ];
    $('#stats').innerHTML = cells.map(([k, v]) =>
      `<div class="stat"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join('');
  }

  const STATUS_CN = { PENDING: '待确认', PAID: '已到账', CANCELLED: '已取消' };
  const STATUS_CLS = { PENDING: 'warn', PAID: 'on', CANCELLED: 'off' };

  async function loadRecharges() {
    let list = [];
    try { list = await api(`${B}/recharges?limit=50`); } catch (_) { list = []; }
    const box = $('#recharges');
    if (!list.length) { box.innerHTML = '<div class="empty">还没有充值单。</div>'; return; }
    box.innerHTML = list.map(r => `
      <div class="item" data-id="${r.id}">
        <div class="item__head">
          <div><span class="item__title">#${r.id} · 会员 ${r.userId}</span>
            <span class="badge ${STATUS_CLS[r.status] || ''}">${STATUS_CN[r.status] || esc(r.status)}</span></div>
          <div class="muted">${esc(r.createdAt || '')}</div>
        </div>
        <div class="muted" style="margin-top:5px">
          充值 ${yuan(r.amountAmount)}${r.bonusCents > 0 ? ` ＋ 赠送 ${yuan(r.bonusAmount)}` : ''}
          = 到账 ${yuan(r.totalAmount)}${r.ruleLabel ? ` · ${esc(r.ruleLabel)}` : ''}${r.note ? ` · ${esc(r.note)}` : ''}
        </div>
        ${r.status === 'PENDING' ? `<div class="row" style="justify-content:flex-end;margin-bottom:0">
          <button class="btn sm" data-act="confirmRc" data-id="${r.id}">确认到账</button>
          <button class="btn sm danger" data-act="cancelRc" data-id="${r.id}">取消</button>
        </div>` : ''}
      </div>`).join('');
  }

  async function loadAccounts() {
    const q = $('#acctQ').value.trim();
    let list = [];
    try { list = await api(`${B}/accounts?q=${encodeURIComponent(q)}&limit=100`); } catch (_) { list = []; }
    const box = $('#accounts');
    if (!list.length) { box.innerHTML = '<div class="empty">还没有储值账户。开一张充值单并确认到账后会自动建户。</div>'; return; }
    box.innerHTML = list.map(a => `
      <div class="item" data-uid="${a.userId}">
        <div class="item__head">
          <div><span class="item__title">${esc(a.userName || '会员 ' + a.userId)}</span>
            <span class="muted">#${a.userId}${a.userPhone ? ' · ' + esc(a.userPhone) : ''}</span>
            ${a.status === 'FROZEN' ? '<span class="badge off">已冻结</span>' : ''}</div>
          <div class="item__title" style="color:#0a7d52">${yuan(a.balanceAmount)}</div>
        </div>
        <div class="muted" style="margin-top:4px">累计充值 ${yuan(a.totalRechargedCents / 100)} · 赠送 ${yuan(a.totalBonusCents / 100)} · 消费 ${yuan(a.totalSpentCents / 100)}</div>
        <div class="row" style="justify-content:flex-end;margin-bottom:0">
          <button class="btn sm ghost" data-act="ledger" data-uid="${a.userId}">流水</button>
          <button class="btn sm ghost" data-act="adjust" data-uid="${a.userId}">手工调整</button>
        </div>
        <div class="acct-extra"></div>
      </div>`).join('');
  }

  const TYPE_CN = { RECHARGE: '充值', BONUS: '充值赠送', ADJUST_UP: '后台赠送', ADJUST_DOWN: '后台扣减', PAY: '消费抵扣', REFUND: '退款', CANCEL: '冲正' };

  async function showLedger(uid, item) {
    const extra = $('.acct-extra', item);
    let d = {};
    try { d = await api(`${B}/accounts/${uid}`); } catch (_) { d = { ledger: [] }; }
    const led = d.ledger || [];
    extra.innerHTML = led.length ? `<div class="ledger"><table>
      <thead><tr><th>时间</th><th>类型</th><th>变动</th><th>余额</th><th>备注</th></tr></thead>
      <tbody>${led.map(x => `<tr>
        <td>${esc(x.createdAt || '')}</td><td>${TYPE_CN[x.type] || esc(x.type)}</td>
        <td style="color:${x.amountCents >= 0 ? '#0a7d52' : '#b03a2e'}">${x.amountCents >= 0 ? '+' : '-'}${yuan(x.amountAmount)}</td>
        <td>${yuan(x.balanceAfterAmount)}</td><td>${esc(x.note || '')}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">暂无流水</div>';
  }

  function adjustForm(uid) {
    return `<div class="row" style="margin-top:8px;background:#f7faf8;padding:9px;border-radius:9px">
      <input type="number" class="sm a-amount" placeholder="金额(元)" min="0" step="0.01">
      <select class="a-direction" style="flex:0 1 100px;padding:6px;border:1px solid #d7e2dd;border-radius:8px;font-size:12px">
        <option value="credit">赠送</option><option value="debit">扣减</option></select>
      <input class="a-note" placeholder="备注(选填)">
      <button class="btn sm" data-act="saveAdjust" data-uid="${uid}">确认</button>
    </div>`;
  }

  function loadAll() { return Promise.all([loadSummary(), loadConfig(), loadRecharges(), loadAccounts()]); }

  function bind() {
    $('#addRule').addEventListener('click', () => {
      const box = $('#rules');
      if (box.querySelector('.empty')) box.innerHTML = '';
      box.insertAdjacentHTML('beforeend', ruleHTML({}));
    });
    $('#rules').addEventListener('click', e => {
      const b = e.target.closest('[data-act="delRule"]'); if (!b) return;
      b.closest('.rule').remove();
      if (!$('#rules').querySelector('.rule')) $('#rules').innerHTML = '<div class="empty">还没有档位。不设档位也可充值（只入本金，不赠送）。</div>';
    });
    $('#saveCfg').addEventListener('click', async () => {
      try {
        await api(`${B}/config`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(readConfig()) });
        toast('配置已保存'); loadConfig(); loadSummary();
      } catch (err) { showAlert({ title: '保存失败', message: err.message }); }
    });
    $('#createRc').addEventListener('click', async () => {
      const userId = Number($('#rcUserId').value || 0), amount = Number($('#rcAmount').value || 0);
      if (!userId || amount <= 0) { showAlert({ title: '请填写', message: '需要会员 ID 和充值金额' }); return; }
      try {
        await api(`${B}/recharges`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ userId, amount }) });
        toast('充值单已创建（待确认）'); $('#rcAmount').value = ''; loadAll();
      } catch (err) { showAlert({ title: '创建失败', message: err.message }); }
    });
    $('#recharges').addEventListener('click', async e => {
      const b = e.target.closest('[data-act]'); if (!b) return;
      const id = b.dataset.id, act = b.dataset.act;
      if (act === 'confirmRc' || act === 'cancelRc') {
        const verb = act === 'confirmRc' ? '确认到账' : '取消';
        if (act === 'cancelRc' && !confirm('确认取消这张充值单？')) return;
        try {
          await api(`${B}/recharges/${id}/${act === 'confirmRc' ? 'confirm' : 'cancel'}`, { method: 'POST' });
          toast('已' + verb); loadAll();
        } catch (err) { showAlert({ title: verb + '失败', message: err.message }); }
      }
    });
    $('#acctSearch').addEventListener('click', loadAccounts);
    $('#acctQ').addEventListener('keydown', e => { if (e.key === 'Enter') loadAccounts(); });
    $('#accounts').addEventListener('click', async e => {
      const b = e.target.closest('[data-act]'); if (!b) return;
      const uid = b.dataset.uid, act = b.dataset.act, item = b.closest('.item');
      if (act === 'ledger') {
        const extra = $('.acct-extra', item);
        if (extra.innerHTML) { extra.innerHTML = ''; return; }
        showLedger(uid, item);
      } else if (act === 'adjust') {
        const extra = $('.acct-extra', item);
        extra.innerHTML = extra.querySelector('.a-amount') ? '' : adjustForm(uid);
      } else if (act === 'saveAdjust') {
        const amount = Number($('.a-amount', item).value || 0);
        const direction = $('.a-direction', item).value;
        const note = $('.a-note', item).value.trim();
        if (amount <= 0) { showAlert({ title: '请填写', message: '调整金额需大于 0' }); return; }
        try {
          await api(`${B}/adjust`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ userId: Number(uid), amount, direction, note }) });
          toast('已调整'); loadAll();
        } catch (err) { showAlert({ title: '调整失败', message: err.message }); }
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => { bind(); loadAll(); });
})();

/* ClubOS NEW · 互动玩法配置（P1-2）俱乐部后台。纯前端，走 club 鉴权。 */
(function () {
  'use strict';
  function getClubId() {
    const q = new URLSearchParams(location.search).get('club_id');
    if (q) return Number(q);
    if (window.CLUB) return Number(window.CLUB);
    return 1;
  }
  const CLUB = getClubId();
  const B = `/api/club/${CLUB}/engagement`;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : (d || 0); };
  let CACHE = {};
  let TAB = 'logs';

  const EMPTY = {
    CHECKIN: '还没有连签档位。只设「每日基础积分」也能正常签到。',
    LOTTERY: '还没有奖品。空奖品池时每次抽奖都是「谢谢参与」。',
    STAMP: '还没有动作。保存时会回落为系统默认的三个动作。',
  };

  function ckRuleHTML(r) {
    r = r || {};
    return `<div class="line">
      <span class="muted">连签</span><input class="f-days tiny" type="number" min="1" step="1" value="${esc(r.days || '')}" placeholder="天数">
      <span class="muted">天，额外送</span><input class="f-points tiny" type="number" min="0" step="1" value="${esc(r.points != null ? r.points : '')}" placeholder="积分">
      <input class="f-name wide" value="${esc(r.prizeName || '')}" placeholder="奖励名(选填)">
      <label class="muted" style="display:flex;align-items:center;gap:4px"><input class="f-enabled" type="checkbox" ${r.enabled === false ? '' : 'checked'}>启用</label>
      <button class="btn sm ghost" data-del="line">删除</button>
    </div>`;
  }

  function prizeHTML(p) {
    p = p || {};
    return `<div class="line">
      <input class="f-id" type="hidden" value="${esc(p.id || '')}">
      <input class="f-name" value="${esc(p.name || '')}" placeholder="奖品名">
      <select class="f-type">
        <option value="POINTS" ${p.type !== 'PRIZE' ? 'selected' : ''}>积分</option>
        <option value="PRIZE" ${p.type === 'PRIZE' ? 'selected' : ''}>实物/待核销</option>
      </select>
      <input class="f-points tiny" type="number" min="0" step="1" value="${esc(p.points != null ? p.points : '')}" placeholder="积分">
      <input class="f-stock tiny" type="number" min="-1" step="1" value="${esc(p.stock != null ? p.stock : -1)}" title="库存，-1=不限">
      <input class="f-weight tiny" type="number" min="0" step="1" value="${esc(p.weight != null ? p.weight : 1)}" title="权重">
      <label class="muted" style="display:flex;align-items:center;gap:4px"><input class="f-enabled" type="checkbox" ${p.enabled === false ? '' : 'checked'}>启用</label>
      <button class="btn sm ghost" data-del="line">删除</button>
    </div>`;
  }

  function actionHTML(a) {
    a = a || {};
    return `<div class="line">
      <input class="f-code" value="${esc(a.code || '')}" placeholder="动作码 如 signup">
      <input class="f-label wide" value="${esc(a.label || '')}" placeholder="展示名称">
      <label class="muted" style="display:flex;align-items:center;gap:4px"><input class="f-enabled" type="checkbox" ${a.enabled === false ? '' : 'checked'}>启用</label>
      <button class="btn sm ghost" data-del="line">删除</button>
    </div>`;
  }

  function fillLines(sel, list, html, kind) {
    const box = $(sel);
    box.innerHTML = (list && list.length) ? list.map(html).join('') : `<div class="empty">${EMPTY[kind]}</div>`;
  }

  function lines(sel) { return $$('.line', $(sel)); }

  function setBadge(id, on) {
    const el = $(id);
    el.textContent = on ? '已开启' : '未开启';
    el.className = 'badge' + (on ? ' on' : '');
  }

  async function loadConfigs() {
    try { CACHE = await api(`${B}/config`); } catch (_) { CACHE = {}; }
    const ck = CACHE.CHECKIN || {}, lt = CACHE.LOTTERY || {}, st = CACHE.STAMP || {};
    $('#ckEnabled').checked = !!ck.enabled;
    $('#ckDaily').value = ck.dailyPoints != null ? ck.dailyPoints : 5;
    $('#ckCycle').value = ck.cycleDays != null ? ck.cycleDays : 7;
    fillLines('#ckRules', ck.streakRules, ckRuleHTML, 'CHECKIN');

    $('#ltEnabled').checked = !!lt.enabled;
    $('#ltLimit').value = lt.dailyLimit != null ? lt.dailyLimit : 3;
    fillLines('#ltPrizes', lt.prizes, prizeHTML, 'LOTTERY');

    $('#stEnabled').checked = !!st.enabled;
    $('#stRequired').value = st.required != null ? st.required : 3;
    $('#stPoints').value = st.rewardPoints != null ? st.rewardPoints : 50;
    $('#stRewardName').value = st.rewardName || '';
    fillLines('#stActions', st.actions, actionHTML, 'STAMP');

    setBadge('#badgeCheckin', ck.enabled);
    setBadge('#badgeLottery', lt.enabled);
    setBadge('#badgeStamp', st.enabled);
  }

  function readCheckin() {
    return {
      enabled: $('#ckEnabled').checked,
      dailyPoints: num($('#ckDaily').value, 5),
      cycleDays: num($('#ckCycle').value, 7),
      streakRules: lines('#ckRules').map(el => ({
        days: num($('.f-days', el).value, 0),
        points: num($('.f-points', el).value, 0),
        prizeName: $('.f-name', el).value.trim(),
        enabled: $('.f-enabled', el).checked,
      })).filter(r => r.days > 0),
    };
  }

  function readLottery() {
    return {
      enabled: $('#ltEnabled').checked,
      dailyLimit: num($('#ltLimit').value, 3),
      prizes: lines('#ltPrizes').map((el, i) => ({
        id: $('.f-id', el).value.trim() || ('p' + (i + 1)),
        name: $('.f-name', el).value.trim(),
        type: $('.f-type', el).value,
        points: num($('.f-points', el).value, 0),
        stock: num($('.f-stock', el).value, -1),
        weight: num($('.f-weight', el).value, 1),
        enabled: $('.f-enabled', el).checked,
      })).filter(p => p.name),
    };
  }

  function readStamp() {
    return {
      enabled: $('#stEnabled').checked,
      required: num($('#stRequired').value, 3),
      rewardPoints: num($('#stPoints').value, 50),
      rewardName: $('#stRewardName').value.trim(),
      actions: lines('#stActions').map(el => ({
        code: $('.f-code', el).value.trim().toLowerCase(),
        label: $('.f-label', el).value.trim(),
        enabled: $('.f-enabled', el).checked,
      })).filter(a => a.code),
    };
  }

  const READERS = { CHECKIN: readCheckin, LOTTERY: readLottery, STAMP: readStamp };

  async function save(kind) {
    try {
      await api(`${B}/config/${kind}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(READERS[kind]()),
      });
      toast('配置已保存');
      loadConfigs();
    } catch (err) { showAlert({ title: '保存失败', message: err.message }); }
  }

  async function loadSummary() {
    let s = {};
    try { s = await api(`${B}/summary`); } catch (_) { s = {}; }
    const cells = [
      ['参与会员', s.participants || 0], ['签到次数', s.checkins || 0],
      ['抽奖次数', s.draws || 0], ['集章枚数', s.stamps || 0],
      ['已发积分', s.pointsGranted || 0], ['待核销奖品', s.pendingPrizes || 0],
    ];
    $('#stats').innerHTML = cells.map(([k, v]) =>
      `<div class="stat"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join('');
  }

  const KIND_CN = { CHECKIN: '签到', LOTTERY: '抽奖', STAMP: '集章' };
  const RSTATUS_CN = { GRANTED: '已发积分', PENDING: '待核销', FULFILLED: '已发放', CANCELLED: '已取消' };
  const RSTATUS_CLS = { GRANTED: 'on', PENDING: 'warn', FULFILLED: 'on', CANCELLED: 'off' };

  async function loadRecords() {
    const url = TAB === 'logs' ? `${B}/logs?limit=100` : `${B}/rewards?limit=100`;
    let list = [];
    try { list = await api(url); } catch (_) { list = []; }
    const box = $('#records');
    if (!list.length) { box.innerHTML = '<div class="empty">暂无记录。</div>'; return; }
    if (TAB === 'logs') {
      box.innerHTML = `<div class="scroll"><table><thead><tr>
        <th>#</th><th>玩法</th><th>会员</th><th>动作 / 业务键</th><th>日期</th><th>时间</th></tr></thead><tbody>` +
        list.map(r => `<tr><td>${r.id}</td><td>${KIND_CN[r.kind] || esc(r.kind)}</td>
          <td>${esc(r.userName || '')} <span class="muted">#${r.userId}</span></td>
          <td>${esc(r.action || '')} <span class="muted">${esc(r.bizKey || '')}</span></td>
          <td>${esc(r.dayKey || '')}</td><td class="muted">${esc(r.createdAt || '')}</td></tr>`).join('') +
        '</tbody></table></div>';
    } else {
      box.innerHTML = `<div class="scroll"><table><thead><tr>
        <th>#</th><th>玩法</th><th>会员</th><th>奖励</th><th>状态</th><th>时间</th><th></th></tr></thead><tbody>` +
        list.map(r => `<tr><td>${r.id}</td><td>${KIND_CN[r.kind] || esc(r.kind)}</td>
          <td>${esc(r.userName || '')} <span class="muted">#${r.userId}</span></td>
          <td>${esc(r.prizeName || '')}${r.rewardType === 'POINTS' ? ` <b>+${r.points}</b> 积分` : '（实物）'}</td>
          <td><span class="badge ${RSTATUS_CLS[r.status] || ''}">${RSTATUS_CN[r.status] || esc(r.status)}</span></td>
          <td class="muted">${esc(r.createdAt || '')}</td>
          <td>${r.status === 'PENDING' ? `<button class="btn sm" data-fulfill="${r.id}">标记已发放</button>` : ''}</td></tr>`).join('') +
        '</tbody></table></div>';
    }
  }

  function loadAll() { loadConfigs(); loadSummary(); loadRecords(); }

  $$('[data-save]').forEach(b => b.addEventListener('click', () => save(b.dataset.save)));

  const ADDS = [
    ['#addCkRule', '#ckRules', ckRuleHTML, 'CHECKIN'],
    ['#addLtPrize', '#ltPrizes', prizeHTML, 'LOTTERY'],
    ['#addStAction', '#stActions', actionHTML, 'STAMP'],
  ];
  ADDS.forEach(([btn, sel, html]) => $(btn).addEventListener('click', () => {
    const box = $(sel);
    const e = box.querySelector('.empty');
    if (e) e.remove();
    box.insertAdjacentHTML('beforeend', html({}));
  }));

  document.addEventListener('click', async e => {
    const del = e.target.closest('[data-del="line"]');
    if (del) {
      const line = del.closest('.line');
      const box = line.parentElement;
      line.remove();
      const kind = box.id === 'ckRules' ? 'CHECKIN' : (box.id === 'ltPrizes' ? 'LOTTERY' : 'STAMP');
      if (!box.querySelector('.line')) box.innerHTML = `<div class="empty">${EMPTY[kind]}</div>`;
      return;
    }
    const tab = e.target.closest('[data-tab]');
    if (tab) {
      TAB = tab.dataset.tab;
      $$('[data-tab]').forEach(x => {
        x.classList.toggle('on', x === tab);
        x.classList.toggle('ghost', x !== tab);
      });
      loadRecords();
      return;
    }
    const f = e.target.closest('[data-fulfill]');
    if (f) {
      try {
        await api(`${B}/rewards/${f.dataset.fulfill}/fulfill`, { method: 'POST' });
        toast('已标记发放'); loadRecords(); loadSummary();
      } catch (err) { showAlert({ title: '操作失败', message: err.message }); }
    }
  });

  loadAll();
})();

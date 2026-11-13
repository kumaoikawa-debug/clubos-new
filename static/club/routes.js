/* ClubOS NEW · 功能路由表 俱乐部后台（P1-5）
 * 注册表只读，所有改动都是「编排覆盖」；显隐三态可逆（跟随默认 / 显示 / 隐藏）。
 * 写操作统一走 shared.js 的 api()（含 CSRF + 防重复提交），一次保存批量提交。
 */
(function () {
  'use strict';
  function getClubId() {
    const q = new URLSearchParams(location.search).get('club_id');
    if (q) return Number(q);
    if (window.CLUB) return Number(window.CLUB);
    return 1;
  }
  const CLUB = getClubId();
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
  const base = () => `/api/club/${CLUB}/routes`;
  const scope = () => $('#scopeSel').value;

  let REG = null, EFF = null, DIRTY = {};

  function hint(msg) {
    $('#hint').textContent = msg;
    try { if (window.toast) window.toast(msg); } catch (_) { }
  }

  function badgeFor(i) {
    if (i.enabled) return '<span class="badge on">可用</span>';
    const m = { hidden: '<span class="badge off">已隐藏</span>',
                'tab-off': '<span class="badge warn">tab 已关</span>',
                'module-off': '<span class="badge warn">模块已关</span>' };
    return m[i.reason] || '<span class="badge off">不可用</span>';
  }

  function renderStats() {
    const s = EFF.summary;
    const cells = [
      ['登记入口', s.total], ['当前可用', s.enabled], ['已隐藏', s.hidden],
      ['tab 关闭', s.disabledByTab], ['模块关闭', s.disabledByModule], ['已定制', s.customized],
    ];
    $('#stats').innerHTML = cells.map(([k, v]) =>
      `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${Number(v)}</div></div>`).join('');
  }

  function rtHTML(i, note) {
    const vis = i.hiddenOverridden ? (i.hidden ? 'hide' : 'show') : 'follow';
    const k = esc(i.key);
    return `<div class="rt ${i.enabled ? '' : 'is-off'}" data-rt="${k}">
      <div class="rt__head">
        <span class="rt__icon">${esc(i.icon)}</span>
        <span class="rt__t">${esc(i.label)}</span>
        <span class="rt__key">${k}</span>
        ${badgeFor(i)}
        ${i.navigable ? '<span class="badge">可作导航目标</span>' : ''}
        ${i.url ? `<a class="btn ghost sm" href="${esc(i.url)}" target="_blank" rel="noopener">打开</a>` : ''}
      </div>
      <div class="rt__grid">
        <label class="fld">显示名称<input data-f="label" data-k="${k}" value="${esc(i.label)}"></label>
        <label class="fld">图标<input data-f="icon" data-k="${k}" value="${esc(i.icon)}"></label>
        <label class="fld">排序<input type="number" min="0" max="9999" data-f="sort" data-k="${k}" value="${Number(i.order)}"></label>
        <label class="fld">显隐<select data-f="vis" data-k="${k}">
          <option value="follow" ${vis === 'follow' ? 'selected' : ''}>跟随默认</option>
          <option value="show" ${vis === 'show' ? 'selected' : ''}>显示</option>
          <option value="hide" ${vis === 'hide' ? 'selected' : ''}>隐藏</option>
        </select></label>
        <label class="fld">自定义跳转（留空用注册表路径）<input data-f="customUrl" data-k="${k}" value="${esc(i.customUrl || '')}" placeholder="/web?... 或 https://..."></label>
      </div>
      <div class="rt__url">生效路径：${esc(i.url || '—（深链模板需填参数，不下发可跳 URL）')} · ${esc(i.kind)} / ${esc(i.auth)}${i.module ? ' · 模块 ' + esc(i.module) : ''}${i.tab ? ' · tab ' + esc(i.tab) : ''}</div>
      ${note ? `<div class="rt__note">${esc(note)}</div>` : ''}
    </div>`;
  }

  function renderRoutes() {
    const notes = {};
    (REG.routes || []).forEach(r => { notes[r.key] = r.note; });
    const groups = (REG.groups || []).slice().sort((a, b) => a.order - b.order);
    const used = new Set();
    const html = groups.map(g => {
      const items = EFF.items.filter(i => i.group === g.key);
      items.forEach(i => used.add(i.key));
      if (!items.length) return '';
      return `<div class="grp"><div class="grp__t">${esc(g.label)} · ${items.length} 项</div>${items.map(i => rtHTML(i, notes[i.key])).join('')}</div>`;
    }).join('');
    const rest = EFF.items.filter(i => !used.has(i.key));
    const restHtml = rest.length
      ? `<div class="grp"><div class="grp__t">其它</div>${rest.map(i => rtHTML(i, notes[i.key])).join('')}</div>` : '';
    $('#routes').innerHTML = html + restHtml || '<div class="empty">该端暂无入口</div>';
  }

  function markDirty(key, field, value) {
    DIRTY[key] = DIRTY[key] || { key: key };
    DIRTY[key][field] = value;
    // key 只含 [A-Za-z0-9._-]，可直接拼属性选择器，无需 CSS.escape
    const el = document.querySelector('[data-rt="' + key + '"]');
    if (el) el.classList.add('dirty');
    hint('有未保存的改动');
  }

  async function loadAudit() {
    try {
      const d = await api(`${base()}/audit?limit=30`);
      const rows = d.items || [];
      $('#audit').innerHTML = rows.length
        ? `<table><thead><tr><th>时间</th><th>端</th><th>路由</th><th>动作</th><th>详情</th></tr></thead><tbody>${rows.map(a =>
            `<tr><td>${esc(a.createdAt)}</td><td>${esc(a.scope)}</td><td>${esc(a.routeKey || '—')}</td><td>${esc(a.action)}</td><td>${esc(JSON.stringify(a.detail || {}))}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">暂无变更记录</div>';
    } catch (_) {
      $('#audit').innerHTML = '<div class="empty">审计读取失败</div>';
    }
  }

  async function load() {
    DIRTY = {};
    hint('读取中…');
    try {
      const d = await api(`${base()}?scope=${encodeURIComponent(scope())}`);
      REG = d.registry;
      EFF = d.effective;
    } catch (e) {
      $('#routes').innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`;
      return;
    }
    renderStats();
    renderRoutes();
    await loadAudit();
    hint('已加载');
  }

  async function save() {
    const keys = Object.keys(DIRTY);
    if (!keys.length) { hint('没有改动'); return; }
    const changes = keys.map(k => {
      const ch = DIRTY[k];
      const o = { key: k };
      if (ch.vis !== undefined) o.hidden = ch.vis === 'follow' ? null : (ch.vis === 'hide');
      if (ch.label !== undefined) o.label = ch.label;
      if (ch.icon !== undefined) o.icon = ch.icon;
      if (ch.sort !== undefined && ch.sort !== '') o.sort = Number(ch.sort);
      if (ch.customUrl !== undefined) o.customUrl = ch.customUrl;
      return o;
    });
    try {
      const r = await api(base(), {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: scope(), changes: changes })
      });
      EFF = r.effective;
      DIRTY = {};
      renderStats();
      renderRoutes();
      await loadAudit();
      hint(`已保存 ${r.updated.length} 项`);
    } catch (e) {
      hint('保存失败：' + e.message);
    }
  }

  async function reset() {
    if (!confirm('确认重置本端全部编排覆盖？将回落到代码注册表的默认显隐 / 名称 / 排序。')) return;
    try {
      const r = await api(`${base()}/reset`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: scope() })
      });
      EFF = r.effective;
      DIRTY = {};
      renderStats();
      renderRoutes();
      await loadAudit();
      hint('已重置本端');
    } catch (e) {
      hint('重置失败：' + e.message);
    }
  }

  $('#routes').addEventListener('input', e => {
    const el = e.target;
    if (!el.dataset || !el.dataset.f || !el.dataset.k) return;
    if (el.dataset.f === 'vis') return;  // select 由 change 处理
    markDirty(el.dataset.k, el.dataset.f, el.value);
  });
  $('#routes').addEventListener('change', e => {
    const el = e.target;
    if (el.dataset && el.dataset.f === 'vis') markDirty(el.dataset.k, 'vis', el.value);
  });
  $('#scopeSel').addEventListener('change', load);
  $('#refreshBtn').addEventListener('click', load);
  $('#saveBtn').addEventListener('click', save);
  $('#resetBtn').addEventListener('click', reset);

  document.addEventListener('DOMContentLoaded', load);
})();

/* ClubOS NEW · 小程序对接桥接层（P2-3）俱乐部后台。纯前端，走 club 鉴权。 */
(function () {
  'use strict';
  function getClubId() {
    const q = new URLSearchParams(location.search).get('club_id');
    if (q) return Number(q);
    if (window.CLUB) return Number(window.CLUB);
    return 1;
  }
  const CLUB = getClubId();
  const B = `/api/club/${CLUB}/miniprogram-bridge`;
  const PUB = `/api/public/clubs/${CLUB}/miniprogram`;
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const JSON_HEAD = { 'Content-Type': 'application/json' };

  let items = [];

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
  const badge = (on, text) => `<span class="badge ${on ? 'on' : 'off'}">${esc(text)}</span>`;
  const levelBadge = lv => `<span class="badge ${lv === 'READY' ? 'on' : lv === 'PARTIAL' ? 'warn' : 'off'}">${esc(lv || '-')}</span>`;

  // ── 一屏数据 ──────────────────────────────────────────────────────────────
  async function loadOverview() {
    let o = {};
    try { o = await api(`${B}/overview`); } catch (e) { fail(e); return; }
    items = o.items || [];
    const s = o.summary || {}, r = o.readiness || {};

    $('#stats').innerHTML = [
      ['能力总数', o.capabilityTotal || 0],
      ['已启用 / C 端', `${s.enabled || 0} / ${s.total || 0}`],
      ['原生页绑定', s.nativeBound || 0],
      ['模块关闭', s.disabledByModule || 0],
      ['就绪度', `${r.score != null ? r.score : 0} 分 ${r.level || '-'}`],
      ['中枢总开关', o.moduleEnabled === false ? '已关闭' : '开启']
    ].map(([k, v]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');

    const cfg = o.config || {};
    $('#cfgEnabled').checked = !!cfg.enabled;
    $('#cfgMode').value = cfg.launchMode || 'h5';
    $('#cfgTab').value = cfg.defaultTab || 'home';
    $('#cfgShowDisabled').checked = !!cfg.showDisabled;
    $('#cfgMaxPerRow').value = cfg.maxPerRow || 4;
    $('#cfgNotice').value = cfg.notice || '';
    $('#hubHint').textContent = o.moduleEnabled === false ? '注意：设置中枢已关闭「小程序对接」模块，C 端不再下发新增能力入口' : '';

    renderReadiness(r);
    renderMp(o.mp || {}, o.settings || {});
    renderCaps(items);
    renderHistory(o.recentChecks || [], o.recentAudit || []);
  }

  function renderReadiness(r) {
    $('#readinessMeta').textContent = r.checkedAt ? `评分 ${r.score} / 100 · ${r.level}` : '';
    const rows = (r.checks || []).map(x =>
      `<div class="check">${badge(x.ok, x.ok ? '通过' : '未过')}<span>${esc(x.label)}</span>` +
      `<span class="d">${esc(x.detail || '')}</span><span class="muted">${x.weight}分</span></div>`).join('');
    $('#checks').innerHTML = rows || '<div class="empty">暂无检查项</div>';
    $('#missing').innerHTML = (r.missing || []).length
      ? '必需项未完成：' + (r.missing || []).map(esc).join('、')
      : '<span class="out">必需项全部通过</span>';
  }

  function renderMp(mp, settings) {
    const fields = [
      ['AppID', mp.appid || '未登记'], ['名称', mp.appName || '-'],
      ['当前版本', mp.currentVersion || '未登记'], ['发布状态', mp.releaseStatus || '-'],
      ['授权', mp.authorized ? '已授权' : '未授权'], ['转发标题', settings.shareTitle || '-']
    ];
    $('#mpInfo').innerHTML = fields.map(([k, v]) =>
      `<div class="row"><label>${esc(k)}</label><span class="code">${esc(v)}</span></div>`).join('');
  }

  function renderCaps(list) {
    if (!list.length) { $('#caps').innerHTML = '<tr><td colspan="8" class="empty">暂无能力</td></tr>'; return; }
    $('#caps').innerHTML = list.map(i => {
      const hid = i.hiddenOverridden ? (i.hidden ? 'hidden' : 'show') : 'inherit';
      return `<tr data-key="${esc(i.key)}">
        <td><b>${esc(i.label)}</b><br><span class="muted">${esc(i.key)}</span><br>${badge(i.enabled, i.enabled ? '已启用' : (i.reason || '未启用'))}</td>
        <td>${esc(i.phase)}<br><span class="muted">${esc(i.group)}</span></td>
        <td>${i.module ? badge(i.moduleEnabled, i.moduleEnabled ? '开启' : '关闭') : '<span class="muted">不受控</span>'}</td>
        <td><input class="f-path" value="${esc(i.pagePath || '')}" placeholder="留空=回落 ${esc(i.defaultFallback)}"></td>
        <td><input class="f-sort" type="number" min="0" max="9999" value="${Number(i.customized ? i.order : i.defaultOrder) || 0}"></td>
        <td><select class="f-hidden"><option value="inherit"${hid === 'inherit' ? ' selected' : ''}>未覆盖</option><option value="show"${hid === 'show' ? ' selected' : ''}>显式显示</option><option value="hidden"${hid === 'hidden' ? ' selected' : ''}>显式隐藏</option></select></td>
        <td><input class="f-enabled" type="checkbox"${i.bindingEnabled ? ' checked' : ''}></td>
        <td><button class="btn sm act-save">保存</button> <button class="btn sm ghost act-reset">重置</button></td>
      </tr>`;
    }).join('');
  }

  function renderHistory(checks, audit) {
    $('#checkHistory').innerHTML = checks.length
      ? checks.map(x => `<div class="check">${levelBadge(x.level)}<span>${x.score} 分</span><span class="d">${esc(x.createdAt || '')} · ${esc(x.operator || '')}</span></div>`).join('')
      : '<div class="empty">尚未跑过检查</div>';
    $('#audit').innerHTML = audit.length
      ? audit.map(x => `<div class="check"><span class="code">${esc(x.action)}</span><span class="d">${esc(x.operator || '')} · ${esc(x.createdAt || '')}</span></div>`).join('')
      : '<div class="empty">暂无变更</div>';
  }

  async function loadNavPreview() {
    let b = {};
    try { b = await api(`${PUB}/bootstrap`); } catch (e) { $('#navPreview').textContent = '预览失败：' + String(e.message || e); return; }
    const nav = b.navigation || [];
    $('#navPreview').innerHTML = b.enabled === false
      ? '<div class="empty">桥接总开关已关闭，C 端不下发能力入口</div>'
      : (nav.length
        ? nav.map(x => `<div class="check"><span>${esc(x.icon || '•')} ${esc(x.label)}</span><span class="d code">${esc(x.path)}${x.requiresLogin ? ' · 需登录' : ''}</span></div>`).join('')
        : '<div class="empty">暂无可下发宫格</div>');
  }

  // ── 写操作 ────────────────────────────────────────────────────────────────
  async function saveConfig() {
    const body = {
      enabled: $('#cfgEnabled').checked,
      launchMode: $('#cfgMode').value,
      defaultTab: $('#cfgTab').value,
      showDisabled: $('#cfgShowDisabled').checked,
      maxPerRow: Number($('#cfgMaxPerRow').value || 4),
      notice: $('#cfgNotice').value.trim()
    };
    try {
      await req('PATCH', `${B}/config`, body);
      $('#cfgOut').textContent = '已保存';
      ok('启动配置已保存');
      await Promise.all([loadOverview(), loadNavPreview()]);
    } catch (e) { fail(e, '#cfgOut'); }
  }

  async function runCheck() {
    try {
      const r = await req('POST', `${B}/check`, {});
      renderReadiness(r);
      ok(`检查完成：${r.score} 分 / ${r.level}`);
      await loadOverview();
    } catch (e) { fail(e); }
  }

  async function saveBinding(key, tr) {
    const hid = $('.f-hidden', tr).value;
    const body = {
      pagePath: $('.f-path', tr).value.trim(),
      sort: Number($('.f-sort', tr).value || 0),
      enabled: $('.f-enabled', tr).checked,
      hidden: hid === 'inherit' ? null : (hid === 'hidden'),
      note: ''
    };
    try {
      await req('PATCH', `${B}/bindings/${encodeURIComponent(key)}`, body);
      $('#bindOut').textContent = `${key} 已保存`;
      ok(`${key} 绑定已保存`);
      await Promise.all([loadOverview(), loadNavPreview()]);
    } catch (e) { fail(e, '#bindOut'); }
  }

  async function resetBinding(key) {
    try {
      await req('DELETE', `${B}/bindings/${encodeURIComponent(key)}`, undefined);
      $('#bindOut').textContent = `${key} 已重置为默认`;
      ok(`${key} 已重置`);
      await Promise.all([loadOverview(), loadNavPreview()]);
    } catch (e) { fail(e, '#bindOut'); }
  }

  // ── 事件 ──────────────────────────────────────────────────────────────────
  $('#saveCfg').addEventListener('click', saveConfig);
  $('#runCheck').addEventListener('click', runCheck);
  $('#caps').addEventListener('click', e => {
    const tr = e.target.closest('tr[data-key]');
    if (!tr) return;
    const key = tr.getAttribute('data-key');
    if (e.target.classList.contains('act-save')) saveBinding(key, tr);
    if (e.target.classList.contains('act-reset')) resetBinding(key);
  });

  (async function init() {
    await Promise.all([loadOverview(), loadNavPreview()]);
  })();
})();

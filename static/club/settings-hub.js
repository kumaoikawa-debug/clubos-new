/* ClubOS NEW · 设置中枢（P1-4）俱乐部后台。只读聚合 + 覆盖偏好/总开关 + 审计。 */
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
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = v => v == null ? '—' : (typeof v === 'boolean' ? (v ? '开' : '关') : String(v));

  let REG = null, SNAP = null;
  const base = () => $('#scope').value === 'platform'
    ? '/api/platform/settings-hub'
    : `/api/club/${CLUB}/settings-hub`;

  function toast(msg) {
    try { if (window.toast) window.toast(msg); } catch (_) {}
    $('#saveHint').textContent = msg;
  }

  function scopeBadge(p) {
    if (p.scope === 'club') return '<span class="badge on">本俱乐部覆盖</span>';
    if (p.scope === 'platform') return '<span class="badge warn">平台默认</span>';
    return '<span class="badge">系统默认</span>';
  }

  function controlHTML(p) {
    const common = `class="hub-ctl" data-key="${esc(p.key)}" data-type="${esc(p.type)}"`;
    if (p.type === 'bool') {
      return `<input type="checkbox" ${common} ${p.value ? 'checked' : ''}>`;
    }
    if (p.type === 'enum') {
      return `<select class="sm hub-ctl" data-key="${esc(p.key)}" data-type="enum">` +
        (p.options || []).map(o => `<option value="${esc(o)}" ${o === p.value ? 'selected' : ''}>${esc(o)}</option>`).join('') +
        `</select>`;
    }
    return `<input type="number" class="sm hub-ctl" data-key="${esc(p.key)}" data-type="int" ` +
      `min="${p.min != null ? p.min : 0}" max="${p.max != null ? p.max : 9999}" value="${esc(p.value)}">`;
  }

  function renderStats(s) {
    const m = s.summary || {};
    $('#stats').innerHTML = [
      ['能力总数', m.totalModules], ['已配置', m.configuredModules],
      ['中枢关闭', m.disabledByHub], ['覆盖的偏好', m.overriddenPreferences],
      ['状态未知', m.unknownModules]
    ].map(([k, v]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v == null ? 0 : v)}</div></div>`).join('');
  }

  function renderPrefs(s) {
    const groups = (REG && REG.groups) || [];
    const order = {};
    groups.forEach((g, i) => { order[g.key] = i; });
    const keys = Object.keys(order).length ? Object.keys(order) : [...new Set(s.preferences.map(p => p.group))];
    $('#prefs').innerHTML = keys.map(g => {
      const items = s.preferences.filter(p => p.group === g);
      if (!items.length) return '';
      const label = (groups.find(x => x.key === g) || {}).label || g;
      return `<div class="grp"><div class="grp__t">${esc(label)}</div>` +
        items.map(p => `<div class="row">
          <label>${esc(p.label)}</label>${controlHTML(p)}
          ${scopeBadge(p)}<span class="muted">${esc(p.note || '')}</span>
        </div>`).join('') + `</div>`;
    }).join('');
  }

  function statusBadge(m) {
    if (m.configured === null) return '<span class="badge">状态未知</span>';
    if (m.configured === false) return '<span class="badge off">未配置</span>';
    if (m.toggleable && !m.hubEnabled) return '<span class="badge off">中枢已关闭</span>';
    return '<span class="badge on">生效中</span>';
  }

  function renderModules(s) {
    const groups = (REG && REG.groups) || [];
    $('#modules').innerHTML = groups.map(g => {
      const items = s.modules.filter(m => m.group === g.key);
      if (!items.length) return '';
      return `<div class="grp"><div class="grp__t">${esc(g.label)}</div><div class="mods">` +
        items.map(m => {
          const count = m.count != null ? `<span class="muted">· ${esc(m.count)} 项</span>` : '';
          const toggle = m.toggleable
            ? `<label><input type="checkbox" class="hub-ctl mod-ctl" data-key="module.${esc(m.key)}" data-type="bool" ${m.hubEnabled ? 'checked' : ''}> 总开关</label>`
            : '<span class="muted">状态只读</span>';
          const page = m.page + (m.page.indexOf('?') >= 0 ? '&' : '?') + 'club_id=' + CLUB;
          return `<div class="mod">
            <div class="mod__head"><span class="mod__t">${esc(m.label)}</span>${statusBadge(m)}</div>
            <div class="mod__note">${esc(m.note || '')} ${count}</div>
            <div class="mod__foot">${toggle}
              <button class="btn sm ghost" data-page="${esc(page)}">前往配置</button>
            </div>
          </div>`;
        }).join('') + `</div></div>`;
    }).join('');
  }

  function renderAudit(items) {
    if (!items.length) { $('#audit').innerHTML = '<div class="empty">还没有变更记录</div>'; return; }
    $('#audit').innerHTML = '<table><thead><tr><th>时间</th><th>设置项</th><th>动作</th><th>旧值</th><th>新值</th><th>操作人</th><th>备注</th></tr></thead><tbody>' +
      items.map(a => `<tr><td>${esc(a.createdAt)}</td><td>${esc(a.key)}</td>` +
        `<td>${a.action === 'RESET' ? '<span class="badge warn">重置</span>' : '<span class="badge on">设置</span>'}</td>` +
        `<td>${esc(fmt(a.oldValue))}</td><td>${esc(fmt(a.newValue))}</td>` +
        `<td>${esc(a.operator || '—')}</td><td>${esc(a.note || '')}</td></tr>`).join('') +
      '</tbody></table>';
  }

  async function loadAll() {
    try {
      // 注册表是全局静态定义，与当前编辑层级无关，固定走俱乐部端点。
      if (!REG) REG = await api(`/api/club/${CLUB}/settings-hub/registry`);
      SNAP = await api(base());
      renderStats(SNAP);
      renderPrefs(SNAP);
      renderModules(SNAP);
      const aud = await api(`${base()}/audit`).catch(() => ({ items: [] }));
      renderAudit(aud.items || []);
      $('#scopeHint').textContent = $('#scope').value === 'platform'
        ? '· 当前编辑平台级默认' : `· 当前编辑俱乐部 #${CLUB}`;
      $('#saveHint').textContent = '未修改时不会写入，也不会留下审计记录';
    } catch (e) {
      $('#prefs').innerHTML = '<div class="empty">读取设置失败，请刷新重试</div>';
    }
  }

  function collectDirty() {
    const changes = {};
    $$('.hub-ctl').forEach(el => {
      if (!el.dataset.dirty) return;
      const k = el.dataset.key, t = el.dataset.type;
      changes[k] = t === 'bool' ? !!el.checked : (t === 'int' ? Number(el.value) : el.value);
    });
    return changes;
  }

  document.addEventListener('change', e => {
    const el = e.target;
    if (!el.classList || !el.classList.contains('hub-ctl')) return;
    el.dataset.dirty = '1';
    el.classList.add('dirty');
  });

  document.addEventListener('click', async e => {
    const btn = e.target.closest('button');
    if (!btn) return;
    if (btn.dataset.page) { window.open(btn.dataset.page, '_blank'); return; }
    if (btn.id === 'savePrefs') {
      const changes = collectDirty();
      if (!Object.keys(changes).length) { toast('没有待保存的变更'); return; }
      btn.disabled = true;
      try {
        const r = await api(base(), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ changes }) });
        toast(`已保存 ${r.updated.length} 项` + (r.skipped.length ? `（跳过 ${r.skipped.length} 项未变化）` : ''));
        await loadAll();
      } catch (err) {
        toast('保存失败：' + (err && err.message ? err.message : err));
      } finally { btn.disabled = false; }
      return;
    }
    if (btn.id === 'resetAll') {
      if (!window.confirm('将清除当前层级的全部覆盖值，回落到上一级默认。确定继续？')) return;
      btn.disabled = true;
      try {
        const r = await api(`${base()}/reset`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
        toast(`已重置 ${r.reset.length} 项`);
        await loadAll();
      } catch (err) {
        toast('重置失败：' + (err && err.message ? err.message : err));
      } finally { btn.disabled = false; }
    }
  });

  $('#scope').addEventListener('change', loadAll);
  loadAll();
})();

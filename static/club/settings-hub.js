/* ClubOS NEW · 设置中枢（P1-4）俱乐部后台。只读聚合 + 覆盖偏好/总开关 + 审计。
   UI/UX：吸顶保存条、统计点击筛选、能力搜索/筛选/分组折叠、统一开关、状态未知可诊断。 */
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
  let FILTER = 'all', QUERY = '';
  const COLLAPSED = new Set();
  const base = () => $('#scope').value === 'platform'
    ? '/api/platform/settings-hub'
    : `/api/club/${CLUB}/settings-hub`;

  function toast(msg) {
    try { if (window.toast) window.toast(msg); } catch (_) {}
    $('#saveHint').textContent = msg;
  }

  /* 全站统一弹层（shared.js:showConfirm）。独立页只有在脚本未就绪时才退回原生确认。 */
  function confirmBox(o) {
    if (window.showConfirm) return window.showConfirm(o);
    return Promise.resolve(window.confirm(o.message || o.title || '确认继续？'));
  }

  /* BUG-HUB-003：平台级默认是一次写入影响所有未覆盖俱乐部的全局配置。
     俱乐部账号（含 TEST_OPEN 下 /api/auth/me 返回的俱乐部兜底身份）不应看到该入口。 */
  async function initScope() {
    const sel = $('#scope');
    sel.dataset.current = sel.value;
    let role = '';
    try { const me = await api('/api/auth/me'); role = (me && me.role) || ''; } catch (_) {}
    const note = $('#scopeNote');
    if (role !== 'platform') {
      $$('#scope option[value="platform"]').forEach(o => o.remove());
      if (note) note.textContent = '平台默认由总平台账号维护，当前账号不可编辑';
    } else {
      /* UI/UX：总平台账号对 /api/club/{id}/* 无归属权限，首屏直接落在平台层级，
         避免打开页面先看到一次「读取失败」再手动切换。 */
      if ([...sel.options].some(o => o.value === 'platform')) sel.value = 'platform';
      sel.dataset.current = sel.value;
      if (note) note.textContent = '平台默认会影响所有未单独覆盖的俱乐部，请谨慎修改';
    }
  }

  function dirtyCount() { return $$('.hub-ctl[data-dirty]').length; }

  /* UI/UX：注册表原本固定走 /api/club/{id}/settings-hub/registry，但总平台账号
     对俱乐部专属端点没有归属权限（403），切到平台层级后整页会卡在读取失败。
     快照本身已下发 groups / modules / preferences，直接构建即可，少一次请求。 */
  function registryFromSnapshot(s) {
    return {
      groups: (s.groups || []).map(g => ({ key: g.key, label: g.label })),
      modules: (s.modules || []).map(m => ({ key: m.key, label: m.label })),
      preferences: (s.preferences || []).map(p => ({ key: p.key, label: p.label }))
    };
  }

  /* UI/UX：未保存改动数量常驻吸顶条；长滚动时也能一眼看出「还有东西没保存」。 */
  function updateDirty() {
    const n = dirtyCount();
    const chip = $('#dirtyChip');
    if (chip) {
      chip.hidden = !n;
      chip.textContent = n ? `${n} 项未保存` : '';
    }
    document.body.classList.toggle('has-dirty', !!n);
  }

  function syncScopeUI() {
    const plat = $('#scope').value === 'platform';
    document.body.classList.toggle('is-platform', plat);
    const chip = $('#hubbarScope');
    if (chip) chip.textContent = plat ? '平台默认 · 影响全部俱乐部' : `本俱乐部 #${CLUB}`;
    const impact = $('#scopeImpact');
    if (impact) impact.hidden = !plat;
    const reset = $('#resetAll');
    if (reset) reset.textContent = plat ? '重置平台覆盖' : '重置覆盖';
  }

  /* BUG-HUB-012：审计表不该让管理员读 portal.homeLayoutMode 这种原始 key。 */
  function labelOf(key) {
    const p = REG && (REG.preferences || []).find(x => x.key === key);
    if (p) return p.label;
    if (String(key).startsWith('module.')) {
      const k = String(key).slice(7);
      const m = REG && (REG.modules || []).find(x => x.key === k);
      if (m) return m.label + ' · 总开关';
    }
    return key;
  }
  const ENUM_CN = { decorated: '装修版面', default: '系统默认首页' };

  /* BUG-HUB-011：6 个模块的配置入口在 SPA 首页里，过去跳过去不带任何定位参数，
     管理员还得自己在侧栏里找。这里给出每个模块对应的 data-view / settings tab。 */
  const DEEP_LINK = {
    clubBranding: { view: 'settings', tab: 'brand' },
    cProfile: { view: 'settings', tab: 'cprofile' },
    mpSettings: { view: 'settings', tab: 'mpset' },
    campaigns: { view: 'members-benefits' },
    inappMessages: { view: 'info' },
    members: { view: 'members-list' }
  };
  function pageFor(m) {
    let url = m.page + (m.page.indexOf('?') >= 0 ? '&' : '?') + 'club_id=' + CLUB;
    const sc = new URLSearchParams(location.search).get('sharecode');
    if (sc) url += '&sharecode=' + encodeURIComponent(sc);
    const dl = DEEP_LINK[m.key];
    if (dl) {
      url += '&view=' + encodeURIComponent(dl.view);
      if (dl.tab) url += '&tab=' + encodeURIComponent(dl.tab);
    }
    return url;
  }

  /* 平台级默认是一次写入影响所有未覆盖俱乐部的全局操作，保存前必须二次确认。 */
  async function confirmScopeWrite(verb) {
    if ($('#scope').value !== 'platform') return true;
    return confirmBox({
      title: `确认${verb}平台级默认？`,
      message: '平台默认会立即作用于所有未单独覆盖的俱乐部，包含首页呈现、外发通知策略与各模块总开关。请确认已评估影响范围。',
      confirmText: `确认${verb}`, cancelText: '再想想', danger: true
    });
  }

  function scopeBadge(p) {
    if (p.scope === 'club') return '<span class="badge on">本俱乐部覆盖</span>';
    if (p.scope === 'platform') return '<span class="badge warn">平台默认</span>';
    return '<span class="badge">系统默认</span>';
  }

  function controlHTML(p) {
    if (p.type === 'bool') {
      /* UI/UX：布尔偏好与模块总开关统一为可见开关，保留原生 checkbox 语义与键盘操作。 */
      return `<input type="checkbox" class="hub-ctl hub-switch" data-key="${esc(p.key)}" data-type="bool" ` +
        `aria-label="${esc(p.label)}" ${p.value ? 'checked' : ''}>`;
    }
    if (p.type === 'enum') {
      return `<select class="sm hub-ctl" data-key="${esc(p.key)}" data-type="enum" aria-label="${esc(p.label)}">` +
        (p.options || []).map(o => `<option value="${esc(o)}" ${o === p.value ? 'selected' : ''}>${esc(ENUM_CN[o] || o)}</option>`).join('') +
        `</select>`;
    }
    const lo = p.min != null ? p.min : 0, hi = p.max != null ? p.max : 9999;
    return `<input type="number" class="sm hub-ctl" data-key="${esc(p.key)}" data-type="int" ` +
      `min="${lo}" max="${hi}" value="${esc(p.value)}" aria-label="${esc(p.label)}（${lo}~${hi}）">`;
  }

  /* UI/UX：统计卡变成筛选入口。.k / .v 保持纯文本数字，便于回归脚本解析。 */
  function renderStats(s) {
    const m = s.summary || {};
    const items = [
      ['能力总数', m.totalModules, '查看全部', 'all', ''],
      ['已配置', m.configuredModules, '筛选生效中', 'ok', ''],
      ['中枢关闭', m.disabledByHub, '筛选需处理', 'todo', ''],
      ['本层级覆盖', m.overriddenPreferences, '定位偏好覆盖', '', 'prefs'],
      ['状态未知', m.unknownModules, '筛选探测失败', 'unknown', '']
    ];
    $('#stats').innerHTML = items.map(([k, v, hint, f, jump]) =>
      `<div class="stat" role="button" tabindex="0" title="${esc(hint)}"` +
      `${f ? ` data-filter="${f}"` : ''}${jump ? ` data-jump="${jump}"` : ''}>` +
      `<div class="k">${esc(k)}</div><div class="v">${esc(v == null ? 0 : v)}</div>` +
      `<div class="stat__hint">${esc(hint)}</div></div>`).join('');
    $$('#stats .stat').forEach(el => el.classList.toggle('active', el.dataset.filter === FILTER));
  }

  function renderPrefs(s) {
    const groups = (REG && REG.groups) || [];
    const keys = groups.length ? groups.map(g => g.key) : [...new Set(s.preferences.map(p => p.group))];
    const layer = s.layer || 'club';
    $('#prefs').innerHTML = keys.map(g => {
      const items = s.preferences.filter(p => p.group === g);
      if (!items.length) return '';
      const label = (groups.find(x => x.key === g) || {}).label || g;
      return `<div class="grp"><div class="grp__t"><span>${esc(label)}</span></div>` +
        items.map(p => `<div class="row prow" data-overridden="${p.scope === layer ? '1' : '0'}">
          <label>${esc(p.label)}</label>${controlHTML(p)}
          ${scopeBadge(p)}<span class="muted">${esc(p.note || '')}</span>
        </div>`).join('') + `</div>`;
    }).join('');
  }

  function statusBadge(m) {
    /* BUG-HUB-006：判断顺序必须先「中枢已关闭」再「未配置」。原来的顺序会让
       刚被关掉但还没配置过内容的模块永远显示「未配置」，统计里「中枢关闭 +1」
       却在 19 张卡里找不到对应卡片。文案保持不变，仅调整语义配色。 */
    if (m.configured === null) {
      const why = m.probeError ? '状态探测失败：' + m.probeError : '该能力的状态探测未能完成';
      return `<span class="badge unknown" title="${esc(why)}">状态未知</span>`;
    }
    if (m.toggleable && !m.hubEnabled) return '<span class="badge off">中枢已关闭</span>';
    if (m.configured === false) return '<span class="badge todo">未配置</span>';
    return '<span class="badge on">生效中</span>';
  }

  function stateOf(m) {
    if (m.configured === null) return 'unknown';
    if (m.toggleable && !m.hubEnabled) return 'off';
    if (m.configured === false) return 'todo';
    return 'ok';
  }

  function renderModules(s) {
    const groups = (REG && REG.groups) || [];
    $('#modules').innerHTML = groups.map(g => {
      const items = s.modules.filter(m => m.group === g.key);
      if (!items.length) return '';
      return `<div class="grp" data-group="${esc(g.key)}">` +
        `<button type="button" class="grp__t" data-group="${esc(g.key)}"><i class="caret" aria-hidden="true"></i>` +
        `<span>${esc(g.label)}</span><span class="grp__n">${items.length} 项</span></button><div class="mods">` +
        items.map(m => {
          const count = m.count != null ? `<span class="muted">· ${esc(m.count)} 项</span>` : '';
          const toggle = m.toggleable
            ? `<label class="mod__sw"><input type="checkbox" class="hub-ctl mod-ctl hub-switch" data-key="module.${esc(m.key)}" data-type="bool" ${m.hubEnabled ? 'checked' : ''}> <span>总开关</span></label>`
            : '<span class="muted">状态只读</span>';
          const page = pageFor(m);
          const probe = m.probeError
            ? `<details class="mod__err"><summary>状态探测失败，查看原因</summary><code>${esc(m.probeError)}</code></details>`
            : '';
          const hay = (m.label + ' ' + (m.note || '') + ' ' + m.key).toLowerCase();
          return `<div class="mod" data-key="${esc(m.key)}" data-state="${stateOf(m)}" data-search="${esc(hay)}">
            <div class="mod__head"><span class="mod__t">${esc(m.label)}</span>${statusBadge(m)}</div>
            <div class="mod__note">${esc(m.note || '')} ${count}</div>
            ${probe}
            <div class="mod__foot">${toggle}
              <button class="btn sm ghost" data-page="${esc(page)}">前往配置</button>
            </div>
          </div>`;
        }).join('') + `</div></div>`;
    }).join('');
  }

  function renderAudit(items) {
    if (!items.length) {
      $('#audit').innerHTML = '<div class="empty"><strong>还没有变更记录</strong><br>' +
        '<span class="muted">保存偏好、切换总开关或重置覆盖后，这里会留下可追溯的操作人与时间</span></div>';
      return;
    }
    $('#audit').innerHTML = '<table><thead><tr><th>时间</th><th>设置项</th><th>动作</th><th>旧值</th><th>新值</th><th>操作人</th><th>备注</th></tr></thead><tbody>' +
      items.map(a => `<tr><td>${esc(a.createdAt)}</td><td><span title="${esc(a.key)}">${esc(labelOf(a.key))}</span></td>` +
        `<td>${a.action === 'RESET' ? '<span class="badge warn">重置</span>' : '<span class="badge on">设置</span>'}</td>` +
        `<td title="${esc(fmt(a.oldValue))}">${esc(fmt(a.oldValue))}</td>` +
        `<td title="${esc(fmt(a.newValue))}">${esc(fmt(a.newValue))}</td>` +
        `<td>${esc(a.operator || '—')}</td><td>${esc(a.note || '')}</td></tr>`).join('') +
      '</tbody></table>';
  }

  /* UI/UX：19 张卡不再一次性糊在屏幕上，可按状态/关键词筛选、按分组折叠。
     隐藏用 display:none，卡片仍留在 DOM 里，回归与跳转脚本的计数不受影响。 */
  function applyFilters() {
    const q = QUERY.trim().toLowerCase();
    let shown = 0, total = 0;
    $$('#modules .grp').forEach(grp => {
      let vis = 0;
      $$('.mod', grp).forEach(card => {
        total++;
        const st = card.dataset.state || '';
        const matchF = FILTER === 'all'
          || (FILTER === 'ok' && st === 'ok')
          || (FILTER === 'todo' && (st === 'todo' || st === 'off'))
          || (FILTER === 'unknown' && st === 'unknown');
        const matchQ = !q || (card.dataset.search || '').indexOf(q) >= 0;
        const ok = matchF && matchQ;
        card.style.display = ok ? '' : 'none';
        if (ok) vis++;
      });
      grp.style.display = vis ? '' : 'none';
      grp.classList.toggle('collapsed', !!vis && COLLAPSED.has(grp.dataset.group));
      shown += vis;
    });
    const note = $('#filterNote');
    if (!note) return;
    if (!total) { note.textContent = ''; return; }
    if (shown === total && !q && FILTER === 'all') {
      note.innerHTML = `<span class="muted">共 ${total} 项能力 · 点上方统计数字可快速筛选</span>`;
    } else {
      note.innerHTML = `筛选出 <strong>${shown}</strong> / ${total} 项` +
        (q ? `（关键词「${esc(QUERY.trim())}」）` : '') +
        ` <button type="button" class="linkbtn" id="clearFilter">清除筛选</button>`;
    }
  }

  function setFilter(f, scroll) {
    FILTER = f;
    $$('#modFilters .chip').forEach(b => b.classList.toggle('active', b.dataset.filter === f));
    $$('#stats .stat').forEach(s => s.classList.toggle('active', s.dataset.filter === f));
    applyFilters();
    if (scroll && $('#modulesCard')) $('#modulesCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function jumpToPrefs() {
    if ($('#prefsCard')) $('#prefsCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $$('#prefs .prow[data-overridden="1"]').forEach(r => {
      r.classList.add('flash');
      setTimeout(() => r.classList.remove('flash'), 1600);
    });
  }

  async function loadAll(keepHint) {
    try {
      SNAP = await api(base());
      REG = registryFromSnapshot(SNAP);
      renderStats(SNAP);
      renderPrefs(SNAP);
      renderModules(SNAP);
      syncScopeUI();
      const tools = $('#modTools'); if (tools) tools.hidden = false;
      applyFilters();
      updateDirty();
      const aud = await api(`${base()}/audit`).catch(() => ({ items: [] }));
      renderAudit(aud.items || []);
      const hint = $('#scope').value === 'platform'
        ? '· 当前编辑平台级默认' : `· 当前编辑俱乐部 #${CLUB}`;
      $('#scopeHint').textContent = hint;
      const h2 = $('#scopeHint2'); if (h2) h2.textContent = hint;
      $('#saveHint').textContent = keepHint || '未修改时不会写入，也不会留下审计记录';
    } catch (e) {
      /* BUG-HUB-005：过去只替换 #prefs，统计与 19 张卡仍是上一层级数据，
         切到无权限层级（403）后页面看起来像「成功读到平台数据」，误导性极强。
         失败时把全部派生区块一起清空并给统一重试入口。 */
      SNAP = null;
      const why = e && e.message ? '：' + e.message : '';
      $('#stats').innerHTML = '<div class="stat"><div class="k">当前层级</div><div class="v" style="font-size:14px;color:#b03a2e">读取失败</div></div>';
      $('#prefs').innerHTML = `<div class="empty">${esc('读取设置失败' + why)}<br><button class="btn sm ghost" id="retryLoad" type="button" style="margin-top:10px">重试</button></div>`;
      $('#modules').innerHTML = '<div class="empty">当前层级数据不可用，已清空以免误读上一层级内容</div>';
      $('#audit').innerHTML = '<div class="empty">审计记录不可用</div>';
      const tools = $('#modTools'); if (tools) tools.hidden = true;
      const note = $('#filterNote'); if (note) note.textContent = '';
      const impact = $('#scopeImpact'); if (impact) impact.hidden = true;
      $('#scopeHint').textContent = '· 读取失败';
      const h2 = $('#scopeHint2'); if (h2) h2.textContent = '· 读取失败';
      $('#saveHint').textContent = '读取失败，请点「重试」';
      updateDirty();
    }
  }

  function collectDirty() {
    const changes = {}, invalid = [];
    $$('.hub-ctl').forEach(el => {
      if (!el.dataset.dirty) return;
      const k = el.dataset.key, t = el.dataset.type;
      const raw = String(el.value == null ? '' : el.value).trim();
      if (t === 'int') {
        /* BUG-HUB-015：清空数字输入过去被 Number('')=0 洗成 0，等于悄悄把
           免打扰开始改成 0 点。空值 / 非整数 / 越界一律拒绝提交。 */
        if (raw === '' || !/^-?\d+$/.test(raw)) { invalid.push(labelOf(k)); return; }
        const n = Number(raw), lo = Number(el.min || 0), hi = Number(el.max || 23);
        if (n < lo || n > hi) { invalid.push(`${labelOf(k)}（需 ${lo}~${hi}）`); return; }
        changes[k] = n;
        return;
      }
      changes[k] = t === 'bool' ? !!el.checked : raw;
    });
    return { changes, invalid };
  }

  document.addEventListener('change', e => {
    const el = e.target;
    if (!el.classList || !el.classList.contains('hub-ctl')) return;
    el.dataset.dirty = '1';
    el.classList.add('dirty');
    updateDirty();
  });

  document.addEventListener('input', e => {
    if (e.target && e.target.id === 'modSearch') { QUERY = e.target.value || ''; applyFilters(); }
  });

  document.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target && e.target.matches('#stats .stat[role="button"]')) {
      e.preventDefault(); e.target.click();
    }
  });

  document.addEventListener('click', async e => {
    const stat = e.target.closest && e.target.closest('#stats .stat');
    if (stat) {
      if (stat.dataset.jump === 'prefs') jumpToPrefs();
      else if (stat.dataset.filter) setFilter(stat.dataset.filter, true);
      return;
    }
    const btn = e.target.closest('button');
    if (!btn) return;
    if (btn.id === 'retryLoad') { await loadAll(); return; }
    if (btn.dataset.page) {
      /* BUG-HUB-016：默认同标签跳转；只有按住 Ctrl/Cmd/Shift 才新开标签。 */
      if (e.metaKey || e.ctrlKey || e.shiftKey) window.open(btn.dataset.page, '_blank');
      else location.href = btn.dataset.page;
      return;
    }
    if (btn.dataset.filter) { setFilter(btn.dataset.filter, false); return; }
    if (btn.id === 'clearFilter') {
      QUERY = ''; const s = $('#modSearch'); if (s) s.value = '';
      setFilter('all', false); return;
    }
    if (btn.id === 'toggleGroups') {
      const keys = $$('#modules .grp').map(g => g.dataset.group);
      if (COLLAPSED.size) COLLAPSED.clear();
      else keys.forEach(k => COLLAPSED.add(k));
      btn.textContent = COLLAPSED.size ? '展开全部分组' : '折叠全部分组';
      applyFilters(); return;
    }
    if (btn.dataset.group) {
      const k = btn.dataset.group;
      if (COLLAPSED.has(k)) COLLAPSED.delete(k); else COLLAPSED.add(k);
      const t = $('#toggleGroups');
      if (t) t.textContent = COLLAPSED.size ? '展开全部分组' : '折叠全部分组';
      applyFilters(); return;
    }
    if (btn.id === 'savePrefs') {
      const { changes, invalid } = collectDirty();
      if (invalid.length) { toast('请修正：' + invalid.join('、')); return; }
      if (!Object.keys(changes).length) { toast('没有待保存的变更'); return; }
      if (!await confirmScopeWrite('保存')) return;
      btn.disabled = true;
      try {
        const r = await api(base(), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ changes }) });
        const msg = `已保存 ${r.updated.length} 项` + (r.skipped.length ? `（跳过 ${r.skipped.length} 项未变化）` : '');
        toast(msg);
        await loadAll(msg);
      } catch (err) {
        toast('保存失败：' + (err && err.message ? err.message : err));
      } finally { btn.disabled = false; }
      return;
    }
    if (btn.id === 'resetAll') {
      /* BUG-HUB-013：原生 window.confirm 换成全站统一弹层，并写清作用域与回落目标。 */
      const isPlat = $('#scope').value === 'platform';
      const n = dirtyCount();
      const scopeText = isPlat
        ? '将清除平台级的全部覆盖值，所有未单独覆盖的俱乐部都会回落到系统内置默认。'
        : '将清除本俱乐部的全部覆盖值，回落到平台默认或系统内置默认。';
      const ok = await confirmBox({
        title: '重置当前层级的全部覆盖',
        message: scopeText + (n ? `另有 ${n} 项未保存的修改也会一并丢弃。` : '') + '此操作会写入审计记录。',
        confirmText: '确认重置', cancelText: '取消', danger: true
      });
      if (!ok) return;
      btn.disabled = true;
      try {
        const r = await api(`${base()}/reset`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
        const msg = `已重置 ${r.reset.length} 项`;
        toast(msg);
        await loadAll(msg);
      } catch (err) {
        toast('重置失败：' + (err && err.message ? err.message : err));
      } finally { btn.disabled = false; }
    }
  });

  /* BUG-HUB-004：切换层级会 loadAll() 重绘，未保存的改动被静默丢弃。
       有 dirty 时先确认；取消则把下拉框还原到原层级。 */
  $('#scope').addEventListener('change', async () => {
    const sel = $('#scope'), prev = sel.dataset.current || 'club';
    const n = dirtyCount();
    if (n) {
      const ok = await confirmBox({
        title: '放弃未保存的修改？',
        message: `还有 ${n} 项修改未保存。切换配置层级会丢失这些修改，确定继续吗？`,
        confirmText: '放弃并切换', cancelText: '继续编辑', danger: true
      });
      if (!ok) { sel.value = prev; syncScopeUI(); return; }
    }
    sel.dataset.current = sel.value;
    syncScopeUI();
    await loadAll();
  });

  (async function boot() {
    /* BUG-HUB-016：独立页补返回后台入口，沿用当前 query（club_id / sharecode）。 */
    const back = $('#backToAdmin');
    if (back) back.href = '/club?' + new URLSearchParams(location.search).toString();
    await initScope();
    syncScopeUI();
    await loadAll();
  })();
})();

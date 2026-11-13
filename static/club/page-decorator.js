/* ClubOS NEW · 页面装修 俱乐部后台编辑器（P0-2）
 * 纯前端编辑器：拉取 /api/club/{club}/page-layouts，按组件 schema 动态生成表单，
 * 保存/发布/删除。图片上传复用现有 /api/club/{club}/home-banners/image（落 /static/uploads/{club}/home/，
 * 发布后由 page-media 公开代理托管）。所有写操作都走 club 鉴权（cookie + CSRF）。
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

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // P1-5：路由表候选（异步填充）；端点不可用时保持空数组，编辑器退化成手填，不阻断保存。
  const ROUTE_OPTIONS = [''];
  async function loadRouteOptions() {
    try {
      const d = await api(`/api/club/${CLUB}/routes/registry?scope=web&navigable=1`);
      const keys = (d.routes || []).filter(r => r.auth === 'public').map(r => r.key);
      ROUTE_OPTIONS.splice(0, ROUTE_OPTIONS.length, '', ...keys);
    } catch (_) { /* 静默：没有路由表也能用旧的 targetType / targetId 编辑 */ }
  }

  // 组件字段 schema（v1 六种）
  const COMP_SCHEMA = {
    carousel: { label: '轮播图', fields: [{ k: 'items', type: 'list', item: [
      { k: 'image', type: 'image', label: '图片' },
      { k: 'title', type: 'text', label: '标题' },
      { k: 'subtitle', type: 'text', label: '副标题' },
      { k: 'badge', type: 'text', label: '角标' },
      { k: 'buttonText', type: 'text', label: '按钮文字' },
      { k: 'targetType', type: 'select', label: '跳转类型', options: ['', 'activity', 'product', 'page', 'external', 'marketing', 'route'] },
      { k: 'targetId', type: 'text', label: '跳转目标ID' },
      { k: 'targetKey', type: 'select', label: '路由表页面(选 route 时生效)', options: ROUTE_OPTIONS },
    ] }] },
    nav_grid: { label: '导航宫格', fields: [{ k: 'items', type: 'list', item: [
      { k: 'icon', type: 'image', label: '图标(或emoji)' },
      { k: 'label', type: 'text', label: '文字' },
      { k: 'targetType', type: 'select', label: '跳转类型', options: ['', 'activity', 'product', 'page', 'external', 'marketing', 'route'] },
      { k: 'targetId', type: 'text', label: '跳转目标ID' },
      { k: 'targetKey', type: 'select', label: '路由表页面(选 route 时生效)', options: ROUTE_OPTIONS },
    ] }] },
    activity_list: { label: '活动列表', fields: [
      { k: 'title', type: 'text', label: '标题' },
      { k: 'filter_json', type: 'object', fields: [
        { k: 'kind', type: 'text', label: '类型过滤' },
        { k: 'tag', type: 'text', label: '标签/关键词' },
        { k: 'limit', type: 'number', label: '显示数量' },
      ] },
    ] },
    marketing_entry: { label: '营销入口', fields: [
      { k: 'title', type: 'text', label: '标题' },
      { k: 'items', type: 'list', item: [
        { k: 'label', type: 'text', label: '文字' },
        { k: 'icon', type: 'image', label: '图标' },
        { k: 'link_type', type: 'select', label: '入口类型', options: ['', 'member', 'points', 'coupon', 'activity', 'invite'] },
        { k: 'link_value', type: 'text', label: '链接值' },
      ] },
    ] },
    notice: { label: '公告栏', fields: [{ k: 'items', type: 'list', item: [
      { k: 'text', type: 'text', label: '公告文字' },
      { k: 'link', type: 'text', label: '链接' },
    ] }] },
    free_container: { label: '自由容器', fields: [
      { k: 'title', type: 'text', label: '标题' },
      { k: 'image', type: 'image', label: '图片' },
      { k: 'content', type: 'textarea', label: '正文' },
      { k: 'link', type: 'text', label: '链接' },
    ] },
  };

  const state = { id: null, page_key: 'club_home', title: '', status: 'DRAFT', components: [] };

  function defaultPropsFor(type) {
    switch (type) {
      case 'carousel': return { items: [] };
      case 'nav_grid': return { items: [] };
      case 'activity_list': return { title: '', filter_json: { kind: '', tag: '', limit: 6 } };
      case 'marketing_entry': return { title: '', items: [] };
      case 'notice': return { items: [] };
      case 'free_container': return { title: '', image: '', content: '', link: '' };
      default: return {};
    }
  }
  function emptyItem(fields) { const o = {}; (fields || []).forEach(f => o[f.k] = ''); return o; }
  function getPath(o, p) { return p.split('.').reduce((a, k) => (a == null ? a : a[k]), o); }
  function setPath(o, p, v) {
    const ks = p.split('.'); let cur = o;
    for (let i = 0; i < ks.length - 1; i++) { if (cur[ks[i]] == null) cur[ks[i]] = {}; cur = cur[ks[i]]; }
    cur[ks[ks.length - 1]] = v;
  }

  function fieldHTML(ci, f, basePath) {
    const p = basePath + '.' + f.k;
    if (f.type === 'list') {
      const arr = getPath(state, p) || [];
      let html = `<div class="row"><label>${esc(f.label)}</label><button class="btn sm" data-act="add-item" data-comp="${ci}" data-field="${f.k}">＋ 添加一项</button></div>`;
      arr.forEach((_, j) => {
        html += `<div class="comp" style="background:#fff;margin:6px 0">`;
        (f.item || []).forEach(sf => { html += fieldHTML(ci, sf, p + '.' + j); });
        html += `<div class="row" style="justify-content:flex-end"><button class="btn sm danger" data-act="del-item" data-comp="${ci}" data-field="${f.k}" data-idx="${j}">移除该项</button></div></div>`;
      });
      return html;
    }
    if (f.type === 'object') {
      let html = '';
      (f.fields || []).forEach(sf => { html += fieldHTML(ci, sf, p); });
      return html;
    }
    const val = getPath(state, p);
    const common = `data-path="${p}"`;
    if (f.type === 'textarea') return `<div class="row"><label>${esc(f.label)}</label><textarea ${common}>${esc(val || '')}</textarea></div>`;
    if (f.type === 'select') {
      const opts = f.options.map(o => `<option ${o === (val || '') ? 'selected' : ''}>${o}</option>`).join('');
      return `<div class="row"><label>${esc(f.label)}</label><select ${common}>${opts}</select></div>`;
    }
    if (f.type === 'image') {
      return `<div class="row"><label>${esc(f.label)}</label>
        <img class="mini-img" src="${val ? esc(val) : ''}" ${val ? '' : 'style="display:none"'}>
        <input type="file" accept="image/*" data-act="upload" data-path="${p}">
        <input type="hidden" ${common} value="${esc(val || '')}"></div>`;
    }
    const t = f.type === 'number' ? 'number' : 'text';
    return `<div class="row"><label>${esc(f.label)}</label><input type="${t}" ${common} value="${esc(val == null ? '' : val)}"></div>`;
  }

  function renderComps() {
    const box = $('#comps');
    if (!state.components.length) { box.innerHTML = '<div class="empty">还没有组件，点下面「＋ 添加组件」开始拼装页面。</div>'; }
    else {
      box.innerHTML = state.components.map((c, i) => {
        const schema = COMP_SCHEMA[c.type] || { label: c.type, fields: [] };
        const fields = (schema.fields || []).map(f => fieldHTML(i, f, `components.${i}.props`)).join('');
        return `<div class="comp" data-comp="${i}">
          <div class="comp__head"><span class="comp__type">${esc(schema.label)}</span>
            <span>
              <button class="btn sm ghost" data-act="up" data-comp="${i}">↑</button>
              <button class="btn sm ghost" data-act="down" data-comp="${i}">↓</button>
              <button class="btn sm danger" data-act="del" data-comp="${i}">删除</button>
            </span></div>${fields}</div>`;
      }).join('');
    }
    const pill = $('#statusPill');
    pill.textContent = state.id ? (state.status === 'PUBLISHED' ? '已发布' : '草稿') : '未配置';
    pill.className = 'status ' + (state.status === 'PUBLISHED' ? 'pub' : 'draft');
    const ti = $('#titleInput'); if (ti) ti.value = state.title || '';
  }

  function reload() { load(); }

  async function load() {
    state.page_key = $('#pageKey').value;
    try {
      const list = await api(`/api/club/${CLUB}/page-layouts`);
      const found = (list || []).find(x => x.page_key === state.page_key);
      if (found) {
        state.id = found.id; state.page_key = found.page_key; state.title = found.title || ''; state.status = found.status || 'DRAFT';
        state.components = found.components || [];
      } else {
        state.id = null; state.title = ''; state.status = 'DRAFT'; state.components = [];
      }
    } catch (e) {
      state.id = null; state.components = [];
    }
    renderComps();
  }

  async function uploadImage(file) {
    const fd = new FormData(); fd.append('file', file);
    const csrf = (typeof clubosCookie === 'function') ? clubosCookie('clubos_csrf') : '';
    const r = await fetch(`/api/club/${CLUB}/home-banners/image`, {
      method: 'POST', credentials: 'same-origin',
      headers: csrf ? { 'X-ClubOS-CSRF': decodeURIComponent(csrf) } : {},
      body: fd
    });
    if (!r.ok) throw new Error('上传失败');
    const d = await r.json();
    return d.url;
  }

  async function save(status) {
    const payload = {
      page_key: state.page_key,
      title: ($('#titleInput') || {}).value || state.title || '',
      status: status || 'DRAFT',
      components: state.components
    };
    try {
      if (state.id) {
        await api(`/api/club/${CLUB}/page-layouts/${state.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      } else {
        const d = await api(`/api/club/${CLUB}/page-layouts`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        state.id = d.id;
      }
      state.status = payload.status; state.title = payload.title;
      renderComps();
      toast(payload.status === 'PUBLISHED' ? '已发布，C 端立即生效' : '已保存草稿');
    } catch (e) {
      showAlert({ title: '保存失败', message: e.message });
    }
  }

  // 事件委托
  function bind() {
    const box = $('#comps');
    box.addEventListener('input', e => {
      const el = e.target;
      if (el.dataset && el.dataset.path) setPath(state, el.dataset.path, el.value);
    });
    box.addEventListener('change', async e => {
      const el = e.target;
      if (el.dataset && el.dataset.act === 'upload') {
        const file = el.files && el.files[0]; if (!file) return;
        try {
          const url = await uploadImage(file);
          const hidden = el.parentElement.querySelector('input[type=hidden]');
          hidden.value = url; setPath(state, el.dataset.path, url);
          const img = el.parentElement.querySelector('img'); img.src = url; img.style.display = '';
        } catch (_) { showAlert({ title: '图片上传失败', message: '请重试' }); }
      } else if (el.dataset && el.dataset.path && el.tagName === 'SELECT') {
        setPath(state, el.dataset.path, el.value);
      }
    });
    box.addEventListener('click', e => {
      const b = e.target.closest('[data-act]'); if (!b) return;
      const act = b.dataset.act, ci = +b.dataset.comp;
      if (act === 'del') state.components.splice(ci, 1);
      else if (act === 'up' && ci > 0) [state.components[ci - 1], state.components[ci]] = [state.components[ci], state.components[ci - 1]];
      else if (act === 'down' && ci < state.components.length - 1) [state.components[ci + 1], state.components[ci]] = [state.components[ci], state.components[ci + 1]];
      else if (act === 'add-item') {
        const fk = b.dataset.field;
        const schema = COMP_SCHEMA[state.components[ci].type];
        const listField = (schema.fields || []).find(f => f.k === fk);
        const arr = getPath(state, `components.${ci}.props.${fk}`) || [];
        arr.push(emptyItem(listField ? listField.item : []));
        setPath(state, `components.${ci}.props.${fk}`, arr);
      } else if (act === 'del-item') {
        const fk = b.dataset.field, idx = +b.dataset.idx;
        const arr = getPath(state, `components.${ci}.props.${fk}`) || [];
        arr.splice(idx, 1); setPath(state, `components.${ci}.props.${fk}`, arr);
      }
      renderComps();
    });

    $('#pageKey').addEventListener('change', reload);
    $('#addBtn').addEventListener('click', () => {
      const t = $('#addType').value;
      state.components.push({ type: t, props: defaultPropsFor(t) });
      renderComps();
    });
    $('#saveBtn').addEventListener('click', () => save('DRAFT'));
    $('#publishBtn').addEventListener('click', () => save('PUBLISHED'));
    $('#unpublishBtn').addEventListener('click', () => save('DRAFT'));
    $('#deleteBtn').addEventListener('click', async () => {
      if (!state.id) { renderComps(); return; }
      if (!confirm('确认删除该页面布局？')) return;
      try { await api(`/api/club/${CLUB}/page-layouts/${state.id}`, { method: 'DELETE' }); state.id = null; state.components = []; state.status = 'DRAFT'; renderComps(); toast('已删除'); }
      catch (e) { showAlert({ title: '删除失败', message: e.message }); }
    });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    bind();
    await loadRouteOptions();  // 先填充候选，再渲染表单，否则 select 里只有空选项
    load();
  });
})();

/* ClubOS NEW · 可视化页面装修器 V2（P0-2 重构）
 * 交互对齐主流小程序装修台（参考微盟/有赞式「界面设置」）：
 *   左：组件库（点选即添加，模块化功能入口）
 *   中：手机实时预览（点预览块 = 选中该模块，观感对齐 C 端 pl-* 渲染）
 *   结构列表：≡ 拖拽排序 + 上移/下移/删除（触屏兜底）
 *   右：属性面板（只编辑当前选中模块）+ 全局配置（布局标题 / 发布开关）
 * 后端协议不变：/api/club/{club}/page-layouts（6 种组件 schema、DRAFT/PUBLISHED）。
 * 图片上传复用 /api/club/{club}/home-banners/image；所有写操作走 club 鉴权（cookie + CSRF）。
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
  const $$ = (s, r = document) => [...(r || document).querySelectorAll(s)];

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  /* 组件库分组（模块化：每个模块对应后台一项真实功能，老板按功能找模块）
   * 装备商城不开放自定义：组件库无商城模块，跳转类型无 product，路由选项过滤 mall/gear。 */
  const LIB_GROUPS = [
    { group: '活动经营', note: '对应后台「活动」', items: [
      { type: 'carousel', label: '轮播图', ico: '🎞️', desc: '首页大图轮播，放活动海报 / 俱乐部宣传图' },
      { type: 'activity_list', label: '活动报名列表', ico: '⛰️', desc: '自动展示后台已发布的活动，顾客可直接报名' },
    ] },
    { group: '会员营销', note: '对应后台「会员 / 营销玩法」', items: [
      { type: 'marketing_entry', label: '会员·积分·券', ico: '🎁', desc: '会员中心 / 活动积分 / 优惠券 / 邀请好友入口' },
      { type: 'notice', label: '公告栏', ico: '📢', desc: '置顶公告文字（对应后台公告设置）' },
    ] },
    { group: '页面导航', items: [
      { type: 'nav_grid', label: '快捷入口宫格', ico: '⊞', desc: '图标宫格，跳转到活动 / 会员 / 自定义链接' },
    ] },
    { group: '自定义内容', items: [
      { type: 'free_container', label: '自由容器', ico: '🧩', desc: '图文自由编辑，放俱乐部介绍 / 合作说明等' },
    ] },
  ];
  const TYPE_LABEL = {}; LIB_GROUPS.forEach(g => g.items.forEach(i => TYPE_LABEL[i.type] = i.label));
  const TYPE_ICO = {}; LIB_GROUPS.forEach(g => g.items.forEach(i => TYPE_ICO[i.type] = i.ico));

  // P1-5：路由表候选（异步填充）；端点不可用时保持空数组，编辑器退化成手填，不阻断保存。
  // 装备商城不开放自定义：mall / gear 路由不出现在可选页面里。
  const ROUTE_OPTIONS = [''];
  const MALL_ROUTE_KEYS = new Set(['web.mall', 'web.gearAnchor']);
  async function loadRouteOptions() {
    try {
      const d = await api(`/api/club/${CLUB}/routes/registry?scope=web&navigable=1`);
      const keys = (d.routes || []).filter(r => r.auth === 'public' && !MALL_ROUTE_KEYS.has(r.key)).map(r => r.key);
      ROUTE_OPTIONS.splice(0, ROUTE_OPTIONS.length, '', ...keys);
    } catch (_) { /* 静默 */ }
  }
  // 跳转类型：无 product（装备商城由平台统一管理，不支持自定义）
  const TARGET_TYPE_OPTIONS = ['', 'activity', 'page', 'external', 'marketing', 'route'];

  /* 组件字段 schema（v1 六种，沿用原表单引擎） */
  const COMP_SCHEMA = {
    carousel: { label: '轮播图', fields: [{ k: 'items', type: 'list', item: [
      { k: 'image', type: 'image', label: '图片' },
      { k: 'title', type: 'text', label: '标题' },
      { k: 'subtitle', type: 'text', label: '副标题' },
      { k: 'badge', type: 'text', label: '角标' },
      { k: 'buttonText', type: 'text', label: '按钮文字' },
      { k: 'targetType', type: 'select', label: '跳转类型', options: TARGET_TYPE_OPTIONS },
      { k: 'targetId', type: 'text', label: '跳转目标ID' },
      { k: 'targetKey', type: 'select', label: '路由表页面(选 route 时生效)', options: ROUTE_OPTIONS },
    ] }] },
    nav_grid: { label: '快捷入口宫格', fields: [{ k: 'items', type: 'list', item: [
      { k: 'icon', type: 'image', label: '图标(或emoji)' },
      { k: 'label', type: 'text', label: '文字' },
      { k: 'targetType', type: 'select', label: '跳转类型', options: TARGET_TYPE_OPTIONS },
      { k: 'targetId', type: 'text', label: '跳转目标ID' },
      { k: 'targetKey', type: 'select', label: '路由表页面(选 route 时生效)', options: ROUTE_OPTIONS },
    ] }] },
    activity_list: { label: '活动报名列表', fields: [
      { k: 'title', type: 'text', label: '标题' },
      { k: 'filter_json', type: 'object', fields: [
        { k: 'kind', type: 'text', label: '类型过滤' },
        { k: 'tag', type: 'text', label: '标签/关键词' },
        { k: 'limit', type: 'number', label: '显示数量' },
      ] },
    ] },
    marketing_entry: { label: '会员·积分·券', fields: [
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

  const state = {
    id: null, page_key: 'club_home', title: '', status: 'DRAFT', components: [],
    sel: -1,          // 当前选中模块下标
    tab: 'module',    // 右侧面板 tab
    dirty: false,
    snapshot: '',
  };

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
  function compSummary(c) {
    const p = c.props || {};
    if (c.type === 'carousel' || c.type === 'nav_grid' || c.type === 'marketing_entry' || c.type === 'notice')
      return (p.items || []).length ? `${(p.items || []).length} 项内容` : '未添加内容';
    if (c.type === 'activity_list') return `显示 ${Number((p.filter_json || {}).limit) || 6} 条活动`;
    if (c.type === 'free_container') return p.title || (p.content ? p.content.slice(0, 14) : '未填写内容');
    return '';
  }

  /* ── 左：组件库 ── */
  function renderLib() {
    $('#libGroups').innerHTML = LIB_GROUPS.map(g => `
      <div class="pd-lib__group">${esc(g.group)}${g.note ? `<span class="pd-lib__note">${esc(g.note)}</span>` : ''}</div>
      <div class="pd-lib__grid">${g.items.map(i => `
        <div class="pd-tile" data-type="${i.type}" title="${esc(i.desc)}">
          <div class="pd-tile__ico">${i.ico}</div><div class="pd-tile__lb">${esc(i.label)}</div>
        </div>`).join('')}</div>`).join('')
      + `<div class="pd-lib__group pd-lib__group--lock">🔒 装备商城</div>
      <div class="pd-hint" style="padding:0 4px 8px;line-height:1.6">装备商城由平台统一管理，<b>不支持自定义</b>，也不在跳转目标里提供。</div>`;
  }

  /* ── 中：手机预览（观感对齐 C 端 pl-*） ── */
  function pvCarousel(p, i) {
    const items = p.items || [];
    if (!items.length) return phBlock(i, '轮播图 · 还没有图片');
    const it = items[0];
    const dots = items.length > 1 ? `<div class="pv-carousel__dots">${items.map(() => '<i></i>').join('')}</div>` : '';
    return `<div class="pv-block" data-idx="${i}"><span class="pv-block__tag">${TYPE_ICO.carousel} ${TYPE_LABEL.carousel}</span>
      <div class="pv-carousel">${it.image ? `<img src="${esc(it.image)}" alt="">` : ''}
        <div class="pv-carousel__scrim"></div>
        <div class="pv-carousel__copy">
          ${it.badge ? `<span class="pv-carousel__badge">${esc(it.badge)}</span>` : ''}
          ${it.title ? `<b>${esc(it.title)}</b>` : ''}
          ${it.subtitle ? `<p>${esc(it.subtitle)}</p>` : ''}
          ${it.buttonText ? `<span class="pv-carousel__btn">${esc(it.buttonText)}</span>` : ''}
        </div>${dots}</div></div>`;
  }
  function pvGrid(p, i) {
    const items = p.items || [];
    if (!items.length) return phBlock(i, '快捷入口宫格 · 还没有入口');
    const tiles = items.map(it => {
      const isImg = /^\/static\/|^https?:\/\//.test(it.icon || '');
      const ico = isImg ? `<img class="pv-grid__ico" src="${esc(it.icon)}" alt="">`
        : `<span class="pv-grid__ico">${esc(it.icon || '•')}</span>`;
      return `<div class="pv-grid__it">${ico}<span class="pv-grid__lb">${esc(it.label)}</span></div>`;
    }).join('');
    return `<div class="pv-block" data-idx="${i}"><span class="pv-block__tag">${TYPE_ICO.nav_grid} ${TYPE_LABEL.nav_grid}</span><div class="pv-grid">${tiles}</div></div>`;
  }
  function pvActs(p, i, acts) {
    const f = p.filter_json || {};
    const limit = Math.max(1, Math.min(12, Number(f.limit) || 6));
    let list = (acts || []).slice(0, limit);
    if (f.kind) list = list.filter(a => String(a.kind || '') === String(f.kind));
    if (f.tag) list = list.filter(a => (a.title || '').indexOf(f.tag) >= 0);
    const rows = list.length ? list.map(a => `
      <div class="pv-act"><div><b>${esc(a.title || '活动')}</b><i>${esc(a.event_date || '')} · ${esc(a.location || '')}</i></div>
      <em>${a.price ? '¥' + Number(a.price).toFixed(0) : '价格待定'}</em></div>`).join('')
      : '<div class="pv-act"><div><b>示例活动</b><i>发布后这里显示真实活动</i></div><em>¥--</em></div>';
    return `<div class="pv-block" data-idx="${i}"><span class="pv-block__tag">${TYPE_ICO.activity_list} ${TYPE_LABEL.activity_list}</span>
      <div class="pv-acts"><div class="pv-acts__t">${esc(p.title || '活动')}</div>${rows}</div></div>`;
  }
  function pvMkt(p, i) {
    const items = p.items || [];
    if (!items.length) return phBlock(i, '会员·积分·券 · 还没有入口');
    const tiles = items.map(it => {
      const isImg = /^\/static\/|^https?:\/\//.test(it.icon || '');
      const ico = isImg ? `<img class="pv-mkt__ico" src="${esc(it.icon)}" alt="">`
        : `<span class="pv-mkt__ico">${esc(it.icon || '★')}</span>`;
      return `<div class="pv-mkt__it">${ico}<span class="pv-mkt__lb">${esc(it.label)}</span></div>`;
    }).join('');
    return `<div class="pv-block" data-idx="${i}"><span class="pv-block__tag">${TYPE_ICO.marketing_entry} ${TYPE_LABEL.marketing_entry}</span>
      <div class="pv-mkt"><div class="pv-mkt__t">${esc(p.title || '会员中心')}</div><div class="pv-mkt__grid">${tiles}</div></div></div>`;
  }
  function pvNotice(p, i) {
    const items = p.items || [];
    if (!items.length) return phBlock(i, '公告栏 · 还没有公告');
    return `<div class="pv-block" data-idx="${i}"><span class="pv-block__tag">${TYPE_ICO.notice} ${TYPE_LABEL.notice}</span>
      <div class="pv-notice">${items.map(it => `<div>${esc(it.text)} ›</div>`).join('')}</div></div>`;
  }
  function pvFree(p, i) {
    if (!p.title && !p.content && !p.image) return phBlock(i, '自由容器 · 还没有内容');
    return `<div class="pv-block" data-idx="${i}"><span class="pv-block__tag">${TYPE_ICO.free_container} ${TYPE_LABEL.free_container}</span>
      <div class="pv-free">${p.image ? `<img src="${esc(p.image)}" alt="">` : ''}
      ${p.title ? `<b>${esc(p.title)}</b>` : ''}${p.content ? `<p>${esc(p.content)}</p>` : ''}</div></div>`;
  }
  function phBlock(i, text) {
    return `<div class="pv-block" data-idx="${i}"><span class="pv-block__tag">模块</span><div class="pv-block__ph">${esc(text)}</div></div>`;
  }
  function renderPhone(acts) {
    const body = $('#phoneBody');
    if (!state.components.length) {
      body.innerHTML = '<div class="pd-empty">页面还是空的<br>从左侧「组件库」点选组件<br>开始装修你的 C 端主页</div>';
      return;
    }
    body.innerHTML = state.components.map((c, i) => {
      const p = c.props || {};
      switch (c.type) {
        case 'carousel': return pvCarousel(p, i);
        case 'nav_grid': return pvGrid(p, i);
        case 'activity_list': return pvActs(p, i, acts);
        case 'marketing_entry': return pvMkt(p, i);
        case 'notice': return pvNotice(p, i);
        case 'free_container': return pvFree(p, i);
        default: return phBlock(i, c.type);
      }
    }).join('');
    $$('.pv-block', body).forEach(el => el.classList.toggle('sel', Number(el.dataset.idx) === state.sel));
  }

  /* ── 结构列表（拖拽排序） ── */
  function renderStruct() {
    const box = $('#structList');
    if (!state.components.length) { box.innerHTML = '<div class="pd-hint" style="padding:18px;text-align:center">还没有模块</div>'; return; }
    box.innerHTML = state.components.map((c, i) => `
      <div class="pd-row ${i === state.sel ? 'sel' : ''}" data-idx="${i}" draggable="true">
        <span class="pd-row__h">≡</span>
        <div class="pd-row__main"><div class="pd-row__t">${TYPE_ICO[c.type] || ''} ${esc(TYPE_LABEL[c.type] || c.type)}</div>
          <div class="pd-row__s">${esc(compSummary(c))}</div></div>
        <span class="pd-row__ops">
          <button data-op="up" data-idx="${i}" title="上移">↑</button>
          <button data-op="down" data-idx="${i}" title="下移">↓</button>
          <button data-op="del" data-idx="${i}" class="del" title="删除">🗑</button>
        </span>
      </div>`).join('');
  }

  /* ── 右：属性面板（沿用字段引擎，仅渲染选中模块） ── */
  function fieldHTML(ci, f, basePath) {
    const p = basePath + '.' + f.k;
    if (f.type === 'list') {
      const arr = getPath(state, p) || [];
      let html = `<div class="row"><label>${esc(f.label)}</label><button class="btn sm ghost" data-act="add-item" data-comp="${ci}" data-field="${f.k}">＋ 添加一项</button></div>`;
      arr.forEach((_, j) => {
        html += `<div class="comp">`;
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
  function renderProps() {
    const box = $('#propsForm');
    const c = state.components[state.sel];
    if (!c) {
      box.innerHTML = '<div class="pd-hint" style="padding:24px;text-align:center;line-height:1.8">在预览或「页面结构」里<br>点选一个模块后，这里显示它的配置</div>';
      return;
    }
    const schema = COMP_SCHEMA[c.type] || { label: c.type, fields: [] };
    box.innerHTML = `<div class="pd-row sel" style="cursor:default;margin-bottom:10px">
        <span class="pd-row__h">${TYPE_ICO[c.type] || ''}</span>
        <div class="pd-row__main"><div class="pd-row__t">${esc(schema.label || c.type)} · 模块 ${state.sel + 1}</div></div></div>`
      + (schema.fields || []).map(f => fieldHTML(state.sel, f, `components.${state.sel}.props`)).join('');
  }

  function markDirty() {
    const snap = JSON.stringify([state.title, state.status, state.components]);
    state.dirty = state.id ? snap !== state.snapshot : true;
    $('#dirtyPill').classList.toggle('on', state.dirty);
    $('#saveBtn').textContent = state.dirty ? '保存草稿 ●' : '保存草稿';
  }
  function takeSnapshot() { state.snapshot = JSON.stringify([state.title, state.status, state.components]); state.dirty = false; $('#dirtyPill').classList.remove('on'); $('#saveBtn').textContent = '保存草稿'; }

  function renderStatus() {
    const pill = $('#statusPill');
    pill.textContent = state.id ? (state.status === 'PUBLISHED' ? '已发布' : '草稿') : '未配置';
    pill.className = 'status ' + (state.id && state.status === 'PUBLISHED' ? 'pub' : 'draft');
    const sw = $('#pubSwitch'); if (sw) sw.checked = state.status === 'PUBLISHED';
    const ti = $('#titleInput'); if (ti) ti.value = state.title || '';
    $('#phoneTitle').textContent = state.page_key === 'club_home' ? '俱乐部主页' : '活动详情页';
    document.title = '页面装修 · ' + $('#phoneTitle').textContent;
  }

  let ACTS = [];
  function renderAll() { renderStruct(); renderPhone(ACTS); renderProps(); renderStatus(); }

  /* ── 数据加载 / 保存 ── */
  async function load() {
    state.page_key = $('#pageKey').value;
    state.sel = -1;
    try {
      const list = await api(`/api/club/${CLUB}/page-layouts`);
      const found = (list || []).find(x => x.page_key === state.page_key);
      if (found) {
        state.id = found.id; state.page_key = found.page_key; state.title = found.title || '';
        state.status = found.status || 'DRAFT';
        // 兜底：列表端点缺 components 时拉详情（旧后端兼容）
        if (Array.isArray(found.components)) state.components = found.components;
        else {
          try { const d = await api(`/api/club/${CLUB}/page-layouts/${found.id}`); state.components = d.components || []; }
          catch (_) { state.components = []; }
        }
      } else { state.id = null; state.title = ''; state.status = 'DRAFT'; state.components = []; }
    } catch (e) { state.id = null; state.components = []; }
    takeSnapshot(); renderAll();
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
      status: status || (state.status === 'PUBLISHED' ? 'PUBLISHED' : 'DRAFT'),
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
      takeSnapshot(); renderStatus(); markDirtyAfterSave(payload);
      toast(payload.status === 'PUBLISHED' ? '已发布，C 端立即生效' : '已保存草稿');
    } catch (e) { showAlert({ title: '保存失败', message: e.message }); }
  }
  function markDirtyAfterSave(payload) { state.snapshot = JSON.stringify([payload.title, payload.status, state.components]); state.dirty = false; $('#dirtyPill').classList.remove('on'); $('#saveBtn').textContent = '保存草稿'; }

  /* ── 事件绑定 ── */
  function selectIdx(i, scroll) {
    state.sel = (i >= 0 && i < state.components.length) ? i : -1;
    state.tab = 'module';
    $$('.pd-tab').forEach(t => t.classList.toggle('on', t.dataset.tab === 'module'));
    $('#paneModule').classList.add('on'); $('#paneGlobal').classList.remove('on');
    renderStruct(); renderPhone(ACTS); renderProps();
    if (scroll && state.sel >= 0) {
      const el = $(`#phoneBody .pv-block[data-idx="${state.sel}"]`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  function bind() {
    // 组件库：点选即添加并选中新模块
    $('#libGroups').addEventListener('click', e => {
      const tile = e.target.closest('.pd-tile'); if (!tile) return;
      const t = tile.dataset.type;
      state.components.push({ type: t, props: defaultPropsFor(t) });
      markDirty(); selectIdx(state.components.length - 1, true);
      toast(`已添加「${TYPE_LABEL[t]}」，在右侧填写内容`);
    });

    // 预览：点击选中（双向）
    $('#phoneBody').addEventListener('click', e => {
      const b = e.target.closest('.pv-block'); if (!b) return;
      selectIdx(Number(b.dataset.idx), false);
    });

    // 结构列表：选中 / 拖拽排序 / 上移下移删除
    const sbox = $('#structList');
    let dragIdx = -1;
    sbox.addEventListener('click', e => {
      const btn = e.target.closest('[data-op]');
      if (btn) {
        const i = Number(btn.dataset.idx), op = btn.dataset.op;
        if (op === 'del') {
          state.components.splice(i, 1);
          if (state.sel >= state.components.length) state.sel = state.components.length - 1;
          markDirty(); renderAll();
        } else if (op === 'up' && i > 0) {
          [state.components[i - 1], state.components[i]] = [state.components[i], state.components[i - 1]];
          if (state.sel === i) state.sel = i - 1; else if (state.sel === i - 1) state.sel = i;
          markDirty(); renderAll();
        } else if (op === 'down' && i < state.components.length - 1) {
          [state.components[i + 1], state.components[i]] = [state.components[i], state.components[i + 1]];
          if (state.sel === i) state.sel = i + 1; else if (state.sel === i + 1) state.sel = i;
          markDirty(); renderAll();
        }
        return;
      }
      const row = e.target.closest('.pd-row'); if (row) selectIdx(Number(row.dataset.idx), true);
    });
    sbox.addEventListener('dragstart', e => {
      const row = e.target.closest('.pd-row'); if (!row) return;
      dragIdx = Number(row.dataset.idx); row.style.opacity = '.5';
      e.dataTransfer.effectAllowed = 'move';
    });
    sbox.addEventListener('dragend', e => { $$('.pd-row', sbox).forEach(r => { r.style.opacity = ''; r.classList.remove('drag-over'); }); });
    sbox.addEventListener('dragover', e => {
      e.preventDefault();
      const row = e.target.closest('.pd-row'); if (!row) return;
      $$('.pd-row', sbox).forEach(r => r.classList.remove('drag-over'));
      row.classList.add('drag-over');
    });
    sbox.addEventListener('drop', e => {
      e.preventDefault();
      const row = e.target.closest('.pd-row'); if (!row || dragIdx < 0) return;
      const to = Number(row.dataset.idx);
      if (to === dragIdx) return;
      const [m] = state.components.splice(dragIdx, 1);
      state.components.splice(to, 0, m);
      if (state.sel === dragIdx) state.sel = to;
      dragIdx = -1; markDirty(); renderAll();
    });

    // 属性面板：输入即时同步 + 预览刷新
    const pform = $('#propsForm');
    pform.addEventListener('input', e => {
      const el = e.target;
      if (el.dataset && el.dataset.path) { setPath(state, el.dataset.path, el.value); markDirty(); renderPhone(ACTS); renderStruct(); }
    });
    pform.addEventListener('change', async e => {
      const el = e.target;
      if (el.dataset && el.dataset.act === 'upload') {
        const file = el.files && el.files[0]; if (!file) return;
        try {
          const url = await uploadImage(file);
          const hidden = el.parentElement.querySelector('input[type=hidden]');
          hidden.value = url; setPath(state, el.dataset.path, url);
          const img = el.parentElement.querySelector('img'); img.src = url; img.style.display = '';
          markDirty(); renderPhone(ACTS); renderStruct();
        } catch (_) { showAlert({ title: '图片上传失败', message: '请重试' }); }
      } else if (el.dataset && el.dataset.path && el.tagName === 'SELECT') {
        setPath(state, el.dataset.path, el.value); markDirty(); renderPhone(ACTS); renderStruct();
      }
    });
    pform.addEventListener('click', e => {
      const b = e.target.closest('[data-act]'); if (!b) return;
      const act = b.dataset.act, ci = Number(b.dataset.comp);
      if (act === 'add-item') {
        const fk = b.dataset.field;
        const schema = COMP_SCHEMA[state.components[ci].type];
        const listField = (schema.fields || []).find(f => f.k === fk);
        const arr = getPath(state, `components.${ci}.props.${fk}`) || [];
        arr.push(emptyItem(listField ? listField.item : []));
        setPath(state, `components.${ci}.props.${fk}`, arr);
        markDirty(); renderProps(); renderPhone(ACTS); renderStruct();
      } else if (act === 'del-item') {
        const fk = b.dataset.field, idx = Number(b.dataset.idx);
        const arr = getPath(state, `components.${ci}.props.${fk}`) || [];
        arr.splice(idx, 1); setPath(state, `components.${ci}.props.${fk}`, arr);
        markDirty(); renderProps(); renderPhone(ACTS); renderStruct();
      }
    });

    // 全局配置
    $('#titleInput').addEventListener('input', () => { state.title = $('#titleInput').value; markDirty(); });
    $('#pubSwitch').addEventListener('change', () => {
      state.status = $('#pubSwitch').checked ? 'PUBLISHED' : 'DRAFT'; markDirty(); renderStatus();
    });

    // tabs
    $$('.pd-tab').forEach(t => t.addEventListener('click', () => {
      state.tab = t.dataset.tab;
      $$('.pd-tab').forEach(x => x.classList.toggle('on', x === t));
      $('#paneModule').classList.toggle('on', state.tab === 'module');
      $('#paneGlobal').classList.toggle('on', state.tab === 'global');
    }));

    // 顶部操作
    $('#pageKey').addEventListener('change', load);
    $('#saveBtn').addEventListener('click', () => save('DRAFT'));
    $('#publishBtn').addEventListener('click', () => save('PUBLISHED'));
    $('#deleteBtn').addEventListener('click', async () => {
      if (!state.id) { renderAll(); return; }
      if (!confirm('确认删除该页面布局？删除后 C 端回落默认页面。')) return;
      try { await api(`/api/club/${CLUB}/page-layouts/${state.id}`, { method: 'DELETE' }); state.id = null; state.components = []; state.status = 'DRAFT'; state.sel = -1; takeSnapshot(); renderAll(); toast('已删除'); }
      catch (e) { showAlert({ title: '删除失败', message: e.message }); }
    });

    // 返回链接带上 club_id
    $('#backLink').href = `/club?club_id=${CLUB}&view=settings`;
  }

  async function loadActs() {
    try { ACTS = await api(`/api/public/clubs/${CLUB}/activities`) || []; } catch (_) { ACTS = []; }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    renderLib(); bind();
    await Promise.all([loadRouteOptions(), loadActs()]);
    load();
  });
})();

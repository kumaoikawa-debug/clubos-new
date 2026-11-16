/* 总平台 · 外部接口配置固定页（2026-11-15）
 * ---------------------------------------------------------------------------------------
 * 上游接口的地址、路径、请求方法、令牌、字段名都在这一个页面里填；
 * 保存后同步立刻按新配置执行，换接口不再改代码 / 不再发版。
 * 表单由后端 FIELD_SPECS 驱动渲染，以后加字段只改后端元数据。
 * 本文件是平台端脚本，不动四端共用文件。
 */
(function () {
  'use strict';

  var KEY = 'peaktrace-catalog';
  var BASE = '/api/platform/integration-configs/' + KEY;
  var ROOT_ID = 'apiConfigRoot';
  var state = { spec: null, dirty: false, probe: null, busy: false };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }
  function root() { return document.getElementById(ROOT_ID); }
  function q(id) { return document.getElementById(id); }

  /* 按后端元数据渲染一个字段，保持平台后台的 form-row 语言 */
  function fieldHtml(f, cfg) {
    var v = cfg[f.key];
    var id = 'apiCfg_' + f.key;
    var help = f.help ? '<div class="plat-api__help">' + esc(f.help) + '</div>' : '';
    var input;
    if (f.type === 'bool') {
      input = '<label class="plat-api__switch"><input type="checkbox" id="' + id + '" data-key="' + f.key + '"' +
        (v ? ' checked' : '') + '><span>' + esc(f.label) + '</span></label>' + help;
      return '<div class="plat-api__field plat-api__field--bool">' + input + '</div>';
    }
    if (f.type === 'select') {
      input = '<select id="' + id + '" data-key="' + f.key + '" class="plat-api__input">' +
        (f.options || []).map(function (o) {
          return '<option value="' + esc(o.value) + '"' + (String(v) === String(o.value) ? ' selected' : '') +
            '>' + esc(o.label) + '</option>';
        }).join('') + '</select>';
    } else if (f.type === 'textarea' || f.type === 'json') {
      var text = f.type === 'json' ? JSON.stringify(v || {}, null, 2) : (v || '');
      input = '<textarea id="' + id + '" data-key="' + f.key + '" rows="' + (f.type === 'json' ? 3 : 2) +
        '" class="plat-api__input plat-api__input--area" placeholder="' + esc(f.placeholder || '') +
        '" spellcheck="false">' + esc(text) + '</textarea>';
    } else {
      var t = f.type === 'number' ? 'number' : (f.type === 'secret' ? 'text' : 'text');
      var extra = f.type === 'number'
        ? ' min="' + (f.min || 1) + '" max="' + (f.max || 120) + '" step="1"' : '';
      input = '<input type="' + t + '" id="' + id + '" data-key="' + f.key + '" class="plat-api__input"' +
        extra + ' value="' + esc(v == null ? '' : v) + '" placeholder="' + esc(f.placeholder || '') +
        '" autocomplete="off" spellcheck="false">';
    }
    return '<div class="plat-api__field"><label class="plat-api__label" for="' + id + '">' +
      esc(f.label) + '</label>' + input + help + '</div>';
  }

  function guideHtml(spec) {
    var g = spec.standardGuide;
    if (!g) return '';
    var cfg = spec.config || {};
    function fill(s) {
      return String(s == null ? '' : s)
        .replace(/\{origin\}/g, cfg.origin || '{origin}')
        .replace(/\{catalogPath\}/g, cfg.catalogPath || '{catalogPath}')
        .replace(/\{productsPath\}/g, cfg.productsPath || '{productsPath}')
        .replace(/\{categoriesPath\}/g, cfg.categoriesPath || '{categoriesPath}')
        .replace(/\{inventoryPath\}/g, cfg.inventoryPath || '{inventoryPath}')
        .replace(/\{inventoryMethod\}/g, cfg.inventoryMethod || '{inventoryMethod}')
        .replace(/\{tokenField\}/g, cfg.tokenField || '{tokenField}');
    }
    var steps = (g.steps || []).map(function (s, i) {
      return '<div class="plat-api__step"><b>' + (i + 1) + '. ' + esc(s.title) + '</b><span>' + esc(s.text) + '</span></div>';
    }).join('');
    var examples = (g.examples || []).map(function (e) {
      return '<div class="plat-api__example"><h4>' + esc(e.title) + '</h4>' +
        '<span class="plat-api__req">' + esc(fill(e.request)) + '</span>' +
        '<pre>' + esc(fill(e.response)) + '</pre>' +
        (e.note ? '<div class="plat-api__help">' + esc(e.note) + '</div>' : '') + '</div>';
    }).join('');
    var rules = (g.rules || []).map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('');
    return '<details class="card plat-api__guide" open><summary>' + esc(g.title) + '</summary>' +
      '<div class="plat-api__guidebody"><div class="sub">' + esc(g.intro) + '</div>' +
      '<div class="plat-api__steps">' + steps + '</div>' + examples +
      '<ul class="plat-api__help">' + rules + '</ul></div></details>';
  }

  function grouped(spec) {
    var fields = spec.fields || [], cfg = spec.config || {}, guides = spec.groupGuide || {};
    var groups = [], map = {};
    fields.forEach(function (f) {
      var g = f.group || '其他';
      if (!map[g]) { map[g] = []; groups.push(g); }
      map[g].push(f);
    });
    return groups.map(function (g, i) {
      var note = guides[g] ? '<div class="plat-api__groupnote">' + esc(guides[g]) + '</div>' : '';
      return '<div class="card plat-api__group">' +
        '<div class="panel-title"><h3>' + esc(g) + '</h3>' +
        '<span class="tag">' + (i + 1) + ' / ' + groups.length + '</span></div>' + note +
        '<div class="plat-api__grid">' + map[g].map(function (f) { return fieldHtml(f, cfg); }).join('') + '</div>' +
        '</div>';
    }).join('');
  }

  function statusHtml(spec) {
    var custom = spec.source === 'database';
    var tags = [
      '<span class="tag ' + (custom ? 'green' : '') + '">' + (custom ? '自定义配置已生效' : '系统内置默认') + '</span>',
      '<span class="tag ' + (spec.hasToken ? 'green' : '') + '">' +
        (spec.hasToken ? '已保存令牌' : (spec.envTokenFallback ? '使用服务器环境变量令牌' : '未配置令牌')) + '</span>'
    ];
    return '<div class="plat-api__status">' +
      '<div class="plat-api__statusline"><strong>当前生效地址</strong><code>' + esc(spec.config.origin) +
      '</code>' + tags.join('') + '</div>' +
      '<div class="sub">' + esc(spec.description) + '</div>' +
      '<div class="sub">配置来源：' + (custom ? '后台页面' : '代码内置默认') +
        (spec.updatedAt ? ' · 最近更新 ' + esc(spec.updatedAt) : '') +
        ' · 令牌只保存在服务端，本页永远只显示掩码。</div>' +
      '</div>';
  }

  function probeHtml(p) {
    if (!p) return '';
    var ok = !!p.ok;
    return '<div class="plat-api__probe ' + (ok ? 'is-ok' : 'is-bad') + '">' +
      '<div class="plat-api__probetitle">' + (ok ? '✓ 接口连通' : '✕ 连接失败') + '</div>' +
      (ok
        ? '<div class="plat-api__probemetrics">' +
            '<span>模式 <b>' + esc(p.source === 'b2b' ? '总目录接口' : '兼容接口') + '</b></span>' +
            '<span>商品 <b>' + (p.products || 0) + '</b></span>' +
            '<span>分类 <b>' + (p.categories || 0) + '</b></span>' +
            '<span>库存规格 <b>' + (p.inventory || 0) + '</b></span>' +
            '<span>耗时 <b>' + (p.elapsedMs || 0) + ' ms</b></span></div>'
        : '<div class="sub">' + esc(p.message || '未知错误') + '</div>') +
      '<div class="sub">测试地址：' + esc(p.origin) + ' · 只读取目录结构，不写入任何数据。</div>' +
      '</div>';
  }

  function render(spec) {
    var host = root();
    if (!host) return;
    state.spec = spec;
    host.innerHTML =
      '<div class="card plat-api__head">' + statusHtml(spec) +
        '<div class="plat-api__actions">' +
          '<button class="btn ghost" type="button" onclick="resetApiConfig()">恢复默认</button>' +
          '<button class="btn secondary" type="button" onclick="testApiConfig(this)">测试连接</button>' +
          '<button class="btn" type="button" onclick="saveApiConfig(this)">保存配置</button>' +
          '<button class="btn ghost" type="button" onclick="goPeaktraceSync()">去同步商品 →</button>' +
        '</div>' +
      '</div>' +
      '<div id="apiConfigProbe">' + probeHtml(state.probe) + '</div>' +
      guideHtml(spec) +
      grouped(spec) +
      '<div class="card plat-api__foot">' +
        '<div class="sub">保存后，手动点「同步 PEAKTRACE」或等待自动同步，都会按新配置执行；不需要改代码、不需要重新发布。</div>' +
        '<div class="plat-api__actions"><button class="btn" type="button" onclick="saveApiConfig(this)">保存配置</button>' +
        '<button class="btn secondary" type="button" onclick="testApiConfig(this)">测试连接</button></div>' +
      '</div>';
  }

  function collectDraft() {
    var draft = {};
    (state.spec.fields || []).forEach(function (f) {
      var el = q('apiCfg_' + f.key);
      if (!el) return;
      if (f.type === 'bool') draft[f.key] = !!el.checked;
      else if (f.type === 'number') draft[f.key] = Number(el.value || 0);
      else draft[f.key] = el.value;
    });
    return draft;
  }

  function busy(on, btn) {
    state.busy = on;
    var host = root();
    if (host) host.classList.toggle('is-busy', !!on);
    if (btn) btn.disabled = !!on;
  }
  function fail(e) {
    showAlert({ title: '操作失败', message: (e && e.message) || String(e) });
  }

  window.loadApiConfig = async function () {
    var host = root();
    if (!host) return;
    host.innerHTML = '<div class="card"><div class="sub">正在读取接口配置…</div></div>';
    try {
      var spec = await api(BASE);
      state.probe = null;
      render(spec);
    } catch (e) {
      host.innerHTML = '<div class="card"><h3>接口配置读取失败</h3><div class="sub">' +
        esc((e && e.message) || e) + '</div>' +
        '<button class="btn secondary" type="button" onclick="loadApiConfig()">重试</button></div>';
    }
  };

  window.saveApiConfig = async function (btn) {
    if (state.busy || !state.spec) return;
    busy(true, btn);
    try {
      var spec = await api(BASE, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectDraft())
      });
      state.probe = null;
      render(spec);
      toast('接口配置已保存，下次同步立即生效');
    } catch (e) { fail(e); }
    finally { busy(false, btn); }
  };

  window.testApiConfig = async function (btn) {
    if (state.busy || !state.spec) return;
    busy(true, btn);
    var box = q('apiConfigProbe');
    if (box) box.innerHTML = '<div class="card"><div class="sub">正在按当前表单测试连接…</div></div>';
    try {
      var p = await api(BASE + '/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ draft: collectDraft() })
      });
      state.probe = p;
      if (box) box.innerHTML = probeHtml(p);
      /* 测试失败时不要静默：给一条可读反馈，页面上的红卡片已经有细节 */
      if (!p.ok) toast('连接失败，请检查地址 / 路径 / 令牌');
    } catch (e) {
      state.probe = null;
      if (box) box.innerHTML = probeHtml({ ok: false, message: (e && e.message) || String(e) });
      fail(e);
    } finally { busy(false, btn); }
  };

  window.resetApiConfig = async function () {
    if (state.busy || !state.spec) return;
    var ok = await showConfirm({
      title: '恢复内置默认接口配置？',
      message: '会清除本页保存的所有自定义设置（含已保存的令牌），同步将回到系统内置地址。商品数据不会被删除。',
      confirmText: '恢复默认', danger: true
    });
    if (!ok) return;
    try {
      var spec = await api(BASE + '/reset', { method: 'POST' });
      state.probe = null;
      render(spec);
      toast('已恢复内置默认配置');
    } catch (e) { fail(e); }
  };

  /* 平台端没有 window.go（只有 club.js 定义了）——一律用导航按钮 click 切视图，
     直接改 location.hash 不会触发 navInit 的视图切换。 */
  window.openApiConfig = function () {
    var nav = document.querySelector('.nav button[data-view="api-config"]');
    if (nav) nav.click();
  };
  window.goPeaktraceSync = function () {
    var nav = document.querySelector('.nav button[data-view="products"]');
    if (nav) nav.click();
  };

  var prev = window.onView;
  window.onView = function (view) {
    if (view === 'api-config') {
      try { loadApiConfig(); } catch (e) { /* 页面还没就绪 */ }
    }
    return prev ? prev.call(window, view) : undefined;
  };
  try {
    if (location.hash.slice(1) === 'api-config' && document.getElementById(ROOT_ID)) {
      setTimeout(loadApiConfig, 120);
    }
  } catch (e) { /* 忽略 */ }
})();

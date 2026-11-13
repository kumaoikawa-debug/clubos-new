/* ClubOS NEW · 通知中心（P0-4 通知通道 + 模板）俱乐部后台。纯前端，走 club 鉴权。 */
(function () {
  'use strict';
  function getClubId() {
    const q = new URLSearchParams(location.search).get('club_id');
    if (q) return Number(q);
    if (window.CLUB) return Number(window.CLUB);
    return 1;
  }
  const CLUB = getClubId();
  const CH = `/api/club/${CLUB}/notification-channels`;
  const TP = `/api/club/${CLUB}/notification-templates`;
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const JSON_HEAD = { 'Content-Type': 'application/json' };

  async function req(method, url, body) {
    const opt = { method };
    if (body !== undefined) { opt.headers = JSON_HEAD; opt.body = JSON.stringify(body); }
    return api(url, opt);
  }
  function ok(out, msg) { $(out).textContent = msg || '已保存'; if (window.toast) window.toast('已保存'); }
  function fail(e, out) { const t = '操作失败：' + String((e && e.message) || e); if ($(out)) $(out).textContent = t; if (window.toast) window.toast(t); }
  const TYPE_CN = { wechat: '企业微信', email: '邮件', sms: '短信', inapp: '站内信' };

  /* ── 通道 ── */
  function cfgSummary(cfg) {
    try {
      const o = typeof cfg === 'string' ? JSON.parse(cfg || '{}') : (cfg || {});
      const keys = Object.keys(o);
      if (!keys.length) return '（空配置）';
      return keys.map(k => `${k}=…`).join(' ');
    } catch (_) { return '（配置 JSON 解析失败）'; }
  }
  async function loadChannels() {
    let list = [];
    try { list = await req('GET', CH); } catch (_) { list = []; }
    if (!Array.isArray(list)) list = [];
    $('#chList').innerHTML = list.length ? `<table><thead><tr><th>ID</th><th>类型</th><th>状态</th><th>配置</th><th>操作</th></tr></thead><tbody>${
      list.map(x => `<tr><td>${x.id}</td><td>${esc(TYPE_CN[x.type] || x.type)}</td><td>${x.enabled ? '<span class="badge on">启用</span>' : '<span class="badge off">停用</span>'}</td><td class="muted">${esc(cfgSummary(x.config_json || x.config))}</td><td><button class="btn sm ghost" onclick="ntFillChannel(${x.id})">编辑</button> <button class="btn sm ghost" onclick="ntToggleChannel(${x.id},${x.enabled ? 0 : 1})">${x.enabled ? '停用' : '启用'}</button></td></tr>`).join('')
    }</tbody></table>` : '<div class="empty">还没有通道，在下方新增（企业微信不填配置会自动回落俱乐部集成的企微 webhook）</div>';
    window.__ntChannels = list;
  }
  window.ntFillChannel = function (id) {
    const x = (window.__ntChannels || []).find(v => v.id === id); if (!x) return;
    $('#chEditId').value = id; $('#chFormTitle').textContent = `编辑通道 #${id}`;
    $('#chType').value = x.type; $('#chType').disabled = true;
    $('#chEnabled').checked = !!x.enabled;
    $('#chConfig').value = JSON.stringify(x.config_json || x.config || {}, null, 2);
  };
  window.ntToggleChannel = async function (id, enabled) {
    try { await req('PATCH', `${CH}/${id}`, { enabled: !!enabled }); ok('#chOut', enabled ? '通道已启用' : '通道已停用'); loadChannels(); }
    catch (e) { fail(e, '#chOut'); }
  };
  function chReset() {
    $('#chEditId').value = ''; $('#chFormTitle').textContent = '新增通道';
    $('#chType').disabled = false; $('#chType').value = 'wechat';
    $('#chEnabled').checked = true; $('#chConfig').value = ''; $('#chOut').textContent = '';
  }
  $('#chSave').onclick = async () => {
    const editId = Number($('#chEditId').value || 0);
    let cfg = {};
    const raw = ($('#chConfig').value || '').trim();
    if (raw) { try { cfg = JSON.parse(raw); } catch (e) { $('#chOut').textContent = '配置不是合法 JSON：' + e.message; return; } }
    const payload = { type: $('#chType').value, enabled: $('#chEnabled').checked, config_json: cfg };
    try {
      if (editId) await req('PATCH', `${CH}/${editId}`, payload);
      else await req('POST', CH, payload);
      ok('#chOut'); chReset(); loadChannels();
    } catch (e) { fail(e, '#chOut'); }
  };
  $('#chReset').onclick = chReset;

  /* ── 模板 ── */
  async function loadTemplates() {
    let list = [];
    try { list = await req('GET', TP); } catch (_) { list = []; }
    if (!Array.isArray(list)) list = [];
    $('#tplList').innerHTML = list.length ? `<table><thead><tr><th>ID</th><th>code</th><th>通道</th><th>标题</th><th>正文预览</th><th>状态</th><th>操作</th></tr></thead><tbody>${
      list.map(x => `<tr><td>${x.id}</td><td><code>${esc(x.code)}</code></td><td>${esc(TYPE_CN[x.channel] || x.channel)}</td><td>${esc(x.title || '')}</td><td class="muted">${esc(String(x.body || '').slice(0, 40))}${String(x.body || '').length > 40 ? '…' : ''}</td><td>${x.enabled ? '<span class="badge on">启用</span>' : '<span class="badge off">停用</span>'}</td><td><button class="btn sm ghost" onclick="ntFillTemplate(${x.id})">编辑</button> <button class="btn sm ghost" onclick="ntToggleTemplate(${x.id},${x.enabled ? 0 : 1})">${x.enabled ? '停用' : '启用'}</button> <button class="btn sm danger" onclick="ntDelTemplate(${x.id})">删除</button></td></tr>`).join('')
    }</tbody></table>` : '<div class="empty">还没有模板。code 是业务场景标识（如 order.paid），正文支持 {{变量}} 占位</div>';
    window.__ntTemplates = list;
  }
  window.ntFillTemplate = function (id) {
    const x = (window.__ntTemplates || []).find(v => v.id === id); if (!x) return;
    $('#tplEditId').value = id; $('#tplFormTitle').textContent = `编辑模板 #${id}`;
    $('#tplCode').value = x.code || ''; $('#tplChannel').value = x.channel || 'inapp';
    $('#tplEnabled').checked = !!x.enabled; $('#tplTitle').value = x.title || ''; $('#tplBody').value = x.body || '';
  };
  window.ntToggleTemplate = async function (id, enabled) {
    try { await req('PATCH', `${TP}/${id}`, { enabled: !!enabled }); ok('#tplOut', enabled ? '模板已启用' : '模板已停用'); loadTemplates(); }
    catch (e) { fail(e, '#tplOut'); }
  };
  window.ntDelTemplate = async function (id) {
    if (!confirm('删除该模板？已发出的历史消息不受影响。')) return;
    try { await req('DELETE', `${TP}/${id}`); ok('#tplOut', '已删除'); loadTemplates(); }
    catch (e) { fail(e, '#tplOut'); }
  };
  function tplReset() {
    $('#tplEditId').value = ''; $('#tplFormTitle').textContent = '新增模板';
    $('#tplCode').value = ''; $('#tplChannel').value = 'inapp'; $('#tplEnabled').checked = true;
    $('#tplTitle').value = ''; $('#tplBody').value = ''; $('#tplOut').textContent = '';
  }
  $('#tplSave').onclick = async () => {
    const editId = Number($('#tplEditId').value || 0);
    const payload = {
      code: ($('#tplCode').value || '').trim(),
      channel: $('#tplChannel').value,
      title: ($('#tplTitle').value || '').trim(),
      body: ($('#tplBody').value || '').trim(),
      enabled: $('#tplEnabled').checked
    };
    try {
      if (editId) await req('PATCH', `${TP}/${editId}`, payload);
      else await req('POST', TP, payload);
      ok('#tplOut'); tplReset(); loadTemplates();
    } catch (e) { fail(e, '#tplOut'); }
  };
  $('#tplReset').onclick = tplReset;

  loadChannels();
  loadTemplates();
})();

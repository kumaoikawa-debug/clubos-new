/* ClubOS NEW · 门店管理（P0-3）俱乐部后台编辑器。纯前端，走 club 鉴权。 */
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
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function load() {
    const box = $('#list');
    let list = [];
    try { list = await api(`/api/club/${CLUB}/stores`); } catch (_) { list = []; }
    if (!list.length) { box.innerHTML = '<div class="empty">还没有门店，点「新增门店」登记第一个（会自动成为默认门店）。</div>'; return; }
    box.innerHTML = list.map(s => `
      <div class="store" data-id="${s.id}">
        <div class="store__head">
          <div><span class="store__name">${esc(s.name)}</span>${s.isDefault ? '<span class="def">默认</span>' : ''} ${s.status === 'INACTIVE' ? '<span class="def" style="background:#f3f0e6;color:#92751f">停用</span>' : ''}</div>
          <div>
            ${s.isDefault ? '' : `<button class="btn sm ghost" data-act="default" data-id="${s.id}">设为默认</button>`}
            <button class="btn sm ghost" data-act="edit" data-id="${s.id}">编辑</button>
            <button class="btn sm danger" data-act="del" data-id="${s.id}">删除</button>
          </div>
        </div>
        ${s.address ? `<div class="row"><label>地址</label><span>${esc(s.address)}</span></div>` : ''}
        ${s.phone ? `<div class="row"><label>电话</label><span>${esc(s.phone)}</span></div>` : ''}
        ${s.geo ? `<div class="row"><label>坐标</label><span>${esc(s.geo.lat)}, ${esc(s.geo.lng)}</span></div>` : ''}
      </div>`).join('');
  }

  function formHTML(s) {
    s = s || {};
    return `
      <div class="store" data-id="${s.id || ''}">
        <div class="row"><label>名称 *</label><input data-f="name" value="${esc(s.name || '')}" placeholder="如：总部 / 城西活动基地"></div>
        <div class="row"><label>地址</label><input data-f="address" value="${esc(s.address || '')}"></div>
        <div class="row"><label>电话</label><input data-f="phone" value="${esc(s.phone || '')}"></div>
        <div class="row"><label>纬度</label><input data-f="lat" value="${s.geo ? esc(s.geo.lat) : ''}" placeholder="如 30.57"></div>
        <div class="row"><label>经度</label><input data-f="lng" value="${s.geo ? esc(s.geo.lng) : ''}" placeholder="如 104.06"></div>
        <div class="row"><label>状态</label>
          <select data-f="status"><option value="ACTIVE" ${s.status !== 'INACTIVE' ? 'selected' : ''}>启用</option><option value="INACTIVE" ${s.status === 'INACTIVE' ? 'selected' : ''}>停用</option></select>
        </div>
        <div class="row" style="justify-content:flex-end">
          <button class="btn sm" data-act="save" data-id="${s.id || ''}">保存</button>
          <button class="btn sm ghost" data-act="cancel">取消</button>
        </div>
      </div>`;
  }

  function readForm(el) {
    const g = k => el.querySelector(`[data-f="${k}"]`);
    const lat = g('lat').value.trim(), lng = g('lng').value.trim();
    const payload = {
      name: g('name').value.trim(),
      address: g('address').value.trim(),
      phone: g('phone').value.trim(),
      status: g('status').value,
    };
    if (lat && lng) payload.geo_json = { lat: Number(lat), lng: Number(lng) };
    return payload;
  }

  function bind() {
    $('#list').addEventListener('click', async e => {
      const b = e.target.closest('[data-act]'); if (!b) return;
      const act = b.dataset.act, id = b.dataset.id;
      if (act === 'add' || act === 'addBtn') {
        const box = $('#list');
        box.insertAdjacentHTML('afterbegin', formHTML({}));
      } else if (act === 'edit') {
        const card = b.closest('.store');
        const cur = await api(`/api/club/${CLUB}/stores/${id}`);
        card.outerHTML = formHTML(cur);
      } else if (act === 'cancel') {
        load();
      } else if (act === 'save') {
        const card = b.closest('.store');
        const payload = readForm(card);
        try {
          if (id) await api(`/api/club/${CLUB}/stores/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
          else await api(`/api/club/${CLUB}/stores`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
          toast('已保存'); load();
        } catch (err) { showAlert({ title: '保存失败', message: err.message }); }
      } else if (act === 'default') {
        try { await api(`/api/club/${CLUB}/stores/${id}/set-default`, { method: 'POST' }); toast('已设为默认'); load(); }
        catch (err) { showAlert({ title: '操作失败', message: err.message }); }
      } else if (act === 'del') {
        if (!confirm('确认删除该门店？')) return;
        try { await api(`/api/club/${CLUB}/stores/${id}`, { method: 'DELETE' }); toast('已删除'); load(); }
        catch (err) { showAlert({ title: '删除失败', message: err.message }); }
      }
    });
    $('#addBtn').addEventListener('click', () => {
      const box = $('#list');
      if (box.querySelector('.store[data-f], .store:not([data-id])')) return; // 已打开新建表单
      box.insertAdjacentHTML('afterbegin', formHTML({}));
    });
  }

  document.addEventListener('DOMContentLoaded', () => { bind(); load(); });
})();

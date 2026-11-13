/* ClubOS NEW · 拼团管理（P1-3）俱乐部后台。纯前端，走 club 鉴权。 */
(function () {
  'use strict';
  function getClubId() {
    const q = new URLSearchParams(location.search).get('club_id');
    if (q) return Number(q);
    if (window.CLUB) return Number(window.CLUB);
    return 1;
  }
  const CLUB = getClubId();
  const B = `/api/club/${CLUB}/group-buys`;
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : (d || 0); };
  const yuan = n => '¥' + Number(n || 0).toFixed(2);
  const CP_CLS = { ACTIVE: 'on', DRAFT: '', PAUSED: 'warn', ENDED: 'off' };
  const CP_CN = { ACTIVE: '已开启', DRAFT: '草稿', PAUSED: '暂停', ENDED: '已结束' };
  const TM_CLS = { OPEN: 'warn', SUCCESS: 'on', EXPIRED: 'off', CANCELLED: 'off' };
  const TM_CN = { OPEN: '进行中', SUCCESS: '已成团', EXPIRED: '已过期', CANCELLED: '已取消' };

  function resetForm() {
    $('#fId').value = '';
    $('#formTitle').textContent = '新建拼团活动';
    $('#fTitle').value = '';
    $('#fActivity').value = '';
    $('#fRequired').value = 3;
    $('#fPrice').value = '';
    $('#fOriginal').value = '';
    $('#fDuration').value = 24;
    $('#fMaxTeams').value = 0;
    $('#fStatus').value = 'DRAFT';
  }

  function readForm() {
    return {
      title: $('#fTitle').value.trim(),
      activityId: num($('#fActivity').value, 0),
      requiredPeople: num($('#fRequired').value, 3),
      groupPrice: num($('#fPrice').value, 0),
      originalPrice: num($('#fOriginal').value, 0),
      durationHours: num($('#fDuration').value, 24),
      maxTeams: num($('#fMaxTeams').value, 0),
      status: $('#fStatus').value,
    };
  }

  async function loadSummary() {
    let s = {};
    try { s = await api(`${B}/summary`); } catch (_) { s = {}; }
    const cells = [
      ['活动数', s.campaigns || 0], ['团总数', s.teams || 0],
      ['进行中', s.open || 0], ['已成团', s.success || 0],
      ['已过期', s.expired || 0], ['参团人次', s.members || 0],
    ];
    $('#stats').innerHTML = cells.map(([k, v]) =>
      `<div class="stat"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join('');
  }

  async function loadCampaigns() {
    let list = [];
    try { list = await api(`${B}/campaigns`); } catch (_) { list = []; }
    const box = $('#campaigns');
    if (!list.length) { box.innerHTML = '<div class="empty">还没有拼团活动，在下方新建一条。</div>'; return; }
    box.innerHTML = list.map(c => `
      <div class="item">
        <div class="item__head">
          <div><span class="item__title">#${c.id} · ${esc(c.title)}</span>
            <span class="badge ${CP_CLS[c.status] || ''}">${CP_CN[c.status] || esc(c.status)}</span></div>
          <div class="muted">活动 ${c.activityId || '未关联'} · 团数 ${c.teamCount != null ? c.teamCount : '-'}</div>
        </div>
        <div class="muted" style="margin-top:5px">
          ${c.requiredPeople} 人成团 · 拼团价 <b>${yuan(c.groupPrice)}</b>
          ${c.originalPrice ? `（原价 ${yuan(c.originalPrice)}，省 ${yuan(Math.max(0, c.originalPrice - c.groupPrice))}）` : ''}
          · ${c.durationHours}h 有效 · ${c.maxTeams ? '最多 ' + c.maxTeams + ' 团' : '开团数不限'}
        </div>
        <div class="row" style="justify-content:flex-end;margin-bottom:0">
          <button class="btn sm ghost" data-act="editCp" data-id="${c.id}">编辑</button>
          <button class="btn sm danger" data-act="delCp" data-id="${c.id}">停用</button>
        </div>
      </div>`).join('');
  }

  async function loadTeams() {
    const st = $('#teamStatus').value;
    let list = [];
    try { list = await api(`${B}/teams?limit=100${st ? '&status=' + encodeURIComponent(st) : ''}`); }
    catch (_) { list = []; }
    const box = $('#teams');
    if (!list.length) { box.innerHTML = '<div class="empty">暂无拼团记录。</div>'; return; }
    box.innerHTML = `<div class="scroll"><table><thead><tr>
      <th>#</th><th>活动</th><th>团长</th><th>人数</th><th>拼团价</th><th>状态</th><th>截止时间</th><th></th>
      </tr></thead><tbody>` + list.map(t => `<tr>
        <td>${t.id}</td><td>${esc(t.campaignTitle || '')} <span class="muted">#${t.campaignId}</span></td>
        <td>#${t.leaderUserId}</td><td>${t.memberCount}/${t.requiredPeople}</td>
        <td>${yuan(t.price)}</td>
        <td><span class="badge ${TM_CLS[t.status] || ''}">${TM_CN[t.status] || esc(t.status)}</span></td>
        <td class="muted">${t.expireAt ? new Date(Number(t.expireAt) * 1000).toLocaleString('zh-CN') : '-'}</td>
        <td><button class="btn sm ghost" data-act="detail" data-id="${t.id}">团员</button>
          ${t.status === 'OPEN' ? `<button class="btn sm danger" data-act="cancelTeam" data-id="${t.id}">取消</button>` : ''}</td>
      </tr>`).join('') + '</tbody></table></div>';
  }

  async function loadTeamDetail(id) {
    let d = null;
    try { d = await api(`${B}/teams/${id}`); } catch (_) { d = null; }
    const box = $('#teamDetail');
    if (!d) { box.innerHTML = '<div class="empty">团不存在。</div>'; return; }
    box.innerHTML = `<div class="form"><h4>团 #${d.id} 成员（${d.memberCount}/${d.requiredPeople}）</h4>
      <table><thead><tr><th>会员</th><th>角色</th><th>状态</th><th>价格快照</th><th>加入时间</th></tr></thead><tbody>` +
      (d.members || []).map(m => `<tr>
        <td>${esc(m.userName || '')} <span class="muted">#${m.userId}</span> ${esc(m.userPhone || '')}</td>
        <td>${m.role === 'LEADER' ? '团长' : '团员'}</td>
        <td>${esc(m.status)}</td><td>${yuan(m.price)}</td>
        <td class="muted">${esc(m.joinedAt || '')}</td></tr>`).join('') +
      '</tbody></table></div>';
  }

  function loadAll() { loadSummary(); loadCampaigns(); loadTeams(); }

  $('#saveCampaign').addEventListener('click', async () => {
    const p = readForm();
    if (!p.title || p.groupPrice <= 0) {
      showAlert({ title: '请填写', message: '需要活动标题与大于 0 的拼团价' });
      return;
    }
    const id = $('#fId').value;
    try {
      await api(id ? `${B}/campaigns/${id}` : `${B}/campaigns`, {
        method: id ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p),
      });
      toast(id ? '活动已更新' : '活动已创建');
      resetForm(); loadAll();
    } catch (err) { showAlert({ title: '保存失败', message: err.message }); }
  });

  $('#resetForm').addEventListener('click', resetForm);
  $('#reloadTeams').addEventListener('click', loadTeams);
  $('#teamStatus').addEventListener('change', loadTeams);

  document.addEventListener('click', async e => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const id = btn.dataset.id, act = btn.dataset.act;
    if (act === 'editCp') {
      let list = [];
      try { list = await api(`${B}/campaigns`); } catch (_) { list = []; }
      const c = list.find(x => String(x.id) === String(id));
      if (!c) return;
      $('#fId').value = c.id;
      $('#formTitle').textContent = `编辑活动 #${c.id}`;
      $('#fTitle').value = c.title || '';
      $('#fActivity').value = c.activityId || '';
      $('#fRequired').value = c.requiredPeople;
      $('#fPrice').value = c.groupPrice;
      $('#fOriginal').value = c.originalPrice;
      $('#fDuration').value = c.durationHours;
      $('#fMaxTeams').value = c.maxTeams;
      $('#fStatus').value = c.status;
      $('#fTitle').scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    if (act === 'delCp') {
      if (!confirm('停用后该活动不再可开团，已开的团不受影响。确认停用？')) return;
      try { await api(`${B}/campaigns/${id}`, { method: 'DELETE' }); toast('已停用'); loadAll(); }
      catch (err) { showAlert({ title: '操作失败', message: err.message }); }
      return;
    }
    if (act === 'detail') { loadTeamDetail(id); return; }
    if (act === 'cancelTeam') {
      if (!confirm('确认后台取消这个团？')) return;
      try {
        await api(`${B}/teams/${id}/cancel`, { method: 'POST' });
        toast('已取消'); loadTeams(); loadTeamDetail(id); loadSummary();
      } catch (err) { showAlert({ title: '操作失败', message: err.message }); }
    }
  });

  loadAll();
})();

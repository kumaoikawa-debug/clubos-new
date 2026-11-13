/* ClubOS NEW · 界面装修器 C 端渲染层（P0-2）
 * 把后端 /api/public/clubs/{clubId}/page/{pageKey} 返回的 components 渲染成页面区块。
 * 6 种组件：carousel / nav_grid / activity_list / marketing_entry / notice / free_container。
 * 仅作加法：组件为空或接口失败时整体不渲染，前端回落到默认 hero / 活动流（不破坏旧行为）。
 */
(function () {
  'use strict';
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  function activityHref(clubId, id) { return `/web?club_id=${clubId}&activity=${id}`; }
  function targetHref(clubId, t) {
    if (!t) return '#';
    // P1-5：装修器选了路由表页面时，用服务端解析好的 targetUrl；
    // 被关闭 / 隐藏的入口服务端不下发 URL，这里降级为不可点击。
    if (t.targetKey) return t.targetUrl || '#';
    if (!t.targetType) return '#';
    switch (t.targetType) {
      case 'activity': return activityHref(clubId, t.targetId);
      case 'product': return `/web?club_id=${clubId}#gear`;
      case 'page': return `/web?club_id=${clubId}#p${t.targetId}`;
      case 'external': return t.targetId || '#';
      case 'marketing': return `/web?club_id=${clubId}#marketing`;
      default: return '#';
    }
  }
  function linkAttrs(t) {
    return (t && t.targetKey && !t.targetUrl) ? 'aria-disabled="true" onclick="return false"' : '';
  }
  function cta(text, href, ext) {
    if (!text) return '';
    const a = ext
      ? `<a class="pl-btn" href="${esc(href)}" target="_blank" rel="noopener">${esc(text)}</a>`
      : `<a class="pl-btn" href="${esc(href)}">${esc(text)}</a>`;
    return a;
  }

  function renderCarousel(clubId, p) {
    const items = p.items || [];
    if (!items.length) return '';
    const slides = items.map(it => `
      <a class="pl-slide" href="${esc(targetHref(clubId, it))}" ${it.targetType === 'external' ? 'target="_blank" rel="noopener"' : ''} ${linkAttrs(it)}>
        ${it.image ? `<img class="pl-slide__img" src="${esc(it.image)}" alt="" loading="lazy" onerror="this.style.display='none'">` : ''}
        <div class="pl-slide__scrim"></div>
        <div class="pl-slide__copy">
          ${it.badge ? `<div class="pl-slide__badge">${esc(it.badge)}</div>` : ''}
          ${it.title ? `<h3>${esc(it.title)}</h3>` : ''}
          ${it.subtitle ? `<p>${esc(it.subtitle)}</p>` : ''}
          ${cta(it.buttonText, targetHref(clubId, it), it.targetType === 'external')}
        </div>
      </a>`).join('');
    return `<section class="pl-carousel"><div class="pl-carousel__track">${slides}</div></section>`;
  }

  function renderNavGrid(clubId, p) {
    const items = p.items || [];
    if (!items.length) return '';
    const tiles = items.map(it => {
      const isImg = /^\/static\/|^https?:\/\//.test(it.icon || '');
      const icon = isImg
        ? `<img class="pl-nav__icon" src="${esc(it.icon)}" alt="" onerror="this.style.display='none'">`
        : `<span class="pl-nav__icon pl-nav__icon--emoji">${esc(it.icon || '•')}</span>`;
      return `<a class="pl-nav" href="${esc(targetHref(clubId, it))}" ${it.targetType === 'external' ? 'target="_blank" rel="noopener"' : ''} ${linkAttrs(it)}>${icon}<span class="pl-nav__label">${esc(it.label)}</span></a>`;
    }).join('');
    return `<section class="pl-grid pl-grid--${Math.min(4, Math.max(2, Math.ceil(items.length / 2)))}">${tiles}</section>`;
  }

  async function renderActivityList(clubId, p) {
    const f = p.filter_json || {};
    const limit = Math.max(1, Math.min(12, Number(f.limit) || 6));
    let acts = [];
    try { acts = await api(`/api/public/clubs/${clubId}/activities`); } catch (_) { acts = []; }
    if (f.kind) acts = acts.filter(a => String(a.kind || '') === String(f.kind));
    if (f.tag) acts = acts.filter(a => (a.title || '').indexOf(f.tag) >= 0 || (a.categoryIds || []).indexOf(Number(f.tag)) >= 0);
    acts = (acts || []).slice(0, limit);
    if (!acts.length) return '';
    const cards = acts.map(a => `
      <a class="pl-act" href="${activityHref(clubId, a.id)}">
        <div class="pl-act__main"><div class="pl-act__title">${esc(a.title)}</div>
        <div class="pl-act__sub">${esc(a.event_date || '')} · ${esc(a.location || '')}</div></div>
        <div class="pl-act__price">${a.price ? '¥' + Number(a.price).toFixed(0) : '价格待定'}</div>
      </a>`).join('');
    return `<section class="pl-acts"><div class="pl-acts__title">${esc(p.title || '活动')}</div>${cards}</section>`;
  }

  function renderMarketingEntry(clubId, p) {
    const items = p.items || [];
    if (!items.length) return '';
    const map = { member: '#member', points: '#points', coupon: '#coupon', activity: '#activity', invite: '#invite' };
    const tiles = items.map(it => {
      const isImg = /^\/static\/|^https?:\/\//.test(it.icon || '');
      const icon = isImg
        ? `<img class="pl-mk__icon" src="${esc(it.icon)}" alt="" onerror="this.style.display='none'">`
        : `<span class="pl-mk__icon">${esc(it.icon || '★')}</span>`;
      return `<a class="pl-mk" href="/web?club_id=${clubId}${map[it.link_type] || '#'}">${icon}<span class="pl-mk__label">${esc(it.label)}</span></a>`;
    }).join('');
    return `<section class="pl-mkt"><div class="pl-mkt__title">${esc(p.title || '会员中心')}</div><div class="pl-mkt__grid">${tiles}</div></section>`;
  }

  function renderNotice(clubId, p) {
    const items = p.items || [];
    if (!items.length) return '';
    const rows = items.map(it => `<div class="pl-notice__row">${it.link ? `<a href="${esc(it.link)}">${esc(it.text)} ›</a>` : `<span>${esc(it.text)}</span>`}</div>`).join('');
    return `<section class="pl-notice">${rows}</section>`;
  }

  function renderFree(clubId, p) {
    if (!p.title && !p.content && !p.image) return '';
    return `<section class="pl-free">
      ${p.image ? `<img class="pl-free__img" src="${esc(p.image)}" alt="" loading="lazy" onerror="this.style.display='none'">` : ''}
      ${p.title ? `<h3>${esc(p.title)}</h3>` : ''}
      ${p.content ? `<p>${esc(p.content).replace(/\n/g, '<br>')}</p>` : ''}
      ${p.link ? `<a class="pl-btn" href="${esc(p.link)}">查看详情</a>` : ''}
    </section>`;
  }

  function injectStyles() {
    if (document.getElementById('pl-styles')) return;
    const s = document.createElement('style');
    s.id = 'pl-styles';
    s.textContent = `
      #homeDecorated:empty{display:none}
      #homeDecorated>*{margin:0 0 12px}
      .pl-carousel{overflow:hidden;border-radius:14px}
      .pl-carousel__track{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;gap:10px}
      .pl-slide{position:relative;flex:0 0 100%;scroll-snap-align:start;min-height:170px;display:block;color:#fff;text-decoration:none;border-radius:14px;overflow:hidden}
      .pl-slide__img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
      .pl-slide__scrim{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.05),rgba(0,0,0,.55))}
      .pl-slide__copy{position:absolute;left:16px;right:16px;bottom:16px}
      .pl-slide__badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;background:#fff;color:#16362b;margin-bottom:6px}
      .pl-slide__copy h3{margin:0 0 4px;font-size:19px}
      .pl-slide__copy p{margin:0 0 8px;font-size:13px;opacity:.9}
      .pl-btn{display:inline-block;padding:6px 14px;border-radius:20px;background:#fff;color:#16362b;font-size:13px;text-decoration:none;font-weight:600}
      .pl-grid{display:grid;gap:10px;padding:4px 2px}
      .pl-grid--2{grid-template-columns:repeat(2,1fr)}
      .pl-grid--3{grid-template-columns:repeat(3,1fr)}
      .pl-grid--4{grid-template-columns:repeat(4,1fr)}
      .pl-nav{display:flex;flex-direction:column;align-items:center;gap:6px;text-decoration:none;color:#16362b}
      .pl-nav__icon{width:46px;height:46px;border-radius:14px;background:#eef4f1;display:flex;align-items:center;justify-content:center;font-size:22px;object-fit:cover}
      .pl-nav__label{font-size:12px}
      .pl-acts__title,.pl-mkt__title{font-size:16px;font-weight:700;margin:2px 2px 8px}
      .pl-act{display:flex;align-items:center;justify-content:space-between;padding:12px;border:1px solid #eef0ee;border-radius:12px;margin-bottom:8px;text-decoration:none;color:#16362b;background:#fff}
      .pl-act__title{font-weight:600}.pl-act__sub{font-size:12px;opacity:.6}
      .pl-act__price{font-weight:700;color:#0a7d52}
      .pl-mkt__grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
      .pl-mk{display:flex;flex-direction:column;align-items:center;gap:6px;text-decoration:none;color:#16362b}
      .pl-mk__icon{width:46px;height:46px;border-radius:14px;background:#fff7e8;display:flex;align-items:center;justify-content:center;font-size:22px;object-fit:cover}
      .pl-mk__label{font-size:12px}
      .pl-notice{background:#fff8e8;border-radius:12px;padding:10px 14px}
      .pl-notice__row{font-size:13px;padding:4px 0}
      .pl-notice__row a{color:#a8650b;text-decoration:none}
      .pl-free{padding:14px;border:1px solid #eef0ee;border-radius:14px;background:#fff}
      .pl-free__img{width:100%;border-radius:10px;margin-bottom:8px}
      .pl-free h3{margin:0 0 6px}.pl-free p{margin:0 0 8px;font-size:14px;color:#444;line-height:1.5}
    `;
    document.head.appendChild(s);
  }

  // 主入口：C 端页面调用。clubId/pageKey/mount 均可选（默认 club_home / #homeDecorated）。
  window.ClubOSRenderPage = async function (clubId, pageKey, mount) {
    if (typeof clubId === 'undefined') clubId = window.CLUB;
    if (typeof pageKey === 'undefined') pageKey = 'club_home';
    if (!mount) mount = document.getElementById('homeDecorated');
    if (!mount) return;
    injectStyles();
    let data = null;
    try { data = await api(`/api/public/clubs/${clubId}/page/${pageKey}`); } catch (_) { return; }
    if (!data || !Array.isArray(data.components) || !data.components.length) { mount.innerHTML = ''; return; }
    const parts = [];
    for (const c of data.components) {
      let html = '';
      const p = c.props || {};
      switch (c.type) {
        case 'carousel': html = renderCarousel(clubId, p); break;
        case 'nav_grid': html = renderNavGrid(clubId, p); break;
        case 'activity_list': html = await renderActivityList(clubId, p); break;
        case 'marketing_entry': html = renderMarketingEntry(clubId, p); break;
        case 'notice': html = renderNotice(clubId, p); break;
        case 'free_container': html = renderFree(clubId, p); break;
      }
      if (html) parts.push(html);
    }
    mount.innerHTML = parts.join('');
  };
})();

/* ===== 渠道内容「成品」渲染层（俱乐部端）=====
   背景：AI 生成公众号 / 小红书 / 海报 / 回顾之后，过去只是把一个 JSON 丢进 <pre> 里给老板看，
   对经营没有任何用处——老板要的是「能直接发出去的东西」。
   这一层把同一份结构化内容渲染成成品：
     wechat → 公众号图文版式（可一键复制带格式的富文本，直接粘进公众号编辑器）
     xhs    → 3:4 图文卡片流（canvas 真实合成 1080×1440，可下载 PNG）+ 文案可复制
     poster → 1080×1440 招募海报（用真实封面 + AI 文案在 canvas 合成，可下载 PNG）
     recap  → 活动回顾图文；没有现场素材时如实说明缺什么，不编造
   约束：无构建、无第三方库；图片同源加载，canvas 不会被跨域污染。
   注意：本文件是 IIFE，不污染全局；对外只暴露 window.openChannelOutput / window.ChannelRender。 */

(function () {
  'use strict';

  var LABEL = { wechat: '微信公众号图文', longpic: 'AI 宣传长图', xhs: '小红书图文', poster: '活动招募海报', recap: '活动回顾' };
  var ICON = { wechat: '📰', longpic: '🖼', xhs: '📕', poster: '🎽', recap: '📷' };
  var _chSeq = 0;   /* 成品预览层标题 id 计数：叠开两层时 aria-labelledby 不能撞名 */
  var _posterCycle = 0;  /* 海报版式确定性循环计数：避免随机抽到最素版式 */
  var FONT = '"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif';
  var SERIF = 'Georgia,"Songti SC","Noto Serif SC",serif';

  /* ---------- 基础工具 ---------- */

  function abs(u) {
    u = String(u || '');
    if (!u) return '';
    if (/^(https?:|data:|blob:)/.test(u)) return u;
    return location.origin + (u[0] === '/' ? u : '/' + u);
  }

  function paras(t) {
    return String(t == null ? '' : t)
      .split('\n')
      .map(function (x) { return x.trim(); })
      .filter(function (x) { return !!x; });
  }

  function head(b) { return b.headline || b.title || b.kicker || b.eyebrow || ''; }
  function textOf(b) { return b.text || b.body || b.subtitle || b.summary || ''; }

  // 模型给 mediaRefs 的可能是 ref（img_01），也可能直接是 url
  /*上传素材不能直接吃 /static/uploads/* ——
     生产环境 security_v025 把那条路径整个封 404（俱乐部端也封），
     长图里塞裸链 = 5 张图全裂，且**预览不报错**，只是白框（2026-10-07 实测）。
     库里的 url 保持裸路径不动（代理路由的白名单就是拿它比对 的），
     改写只发生在这里（公开代理 / club 鉴权路由见下）。*/
  var _mediaActivityId = 0;
  /* club 端预览恒有登录会话 → 长图/海报里的图一律走带鉴权的 club 媒体路由。
     ★ 2026-10-09 用户截图实证的裂图根因：此前一律走公开代理，而公开代理按设计
       「不服务未发布活动」—— 草稿活动生成完长图一预览，所有图 404 全裂（预览还不报错）。
       club 路由对 draft / published 都放行（会话已把守），本文件只在俱乐部端加载，恒为 true。 */
  var _mediaClubScope = true;
  function setMediaActivityId(id) { _mediaActivityId = Number(id) || 0; }
  function mediaUrl(u) {
    var s = String(u || '');
    if (!/^\/static\/uploads\//.test(s)) return s;
    if (!_mediaActivityId) return s;          // 拿不到活动 id 就原样返回，别拼出坏链
    var rel = s.replace(/^\/static\//, '');
    var tail = rel.split('/').map(encodeURIComponent).join('/');
    if (_mediaClubScope)
      return '/api/club/' + CLUB + '/activities/' + _mediaActivityId + '/media/' + tail;
    return '/api/public/activities/' + _mediaActivityId + '/media/' + tail;
  }

  function toUrl(x, mm) {
    var s = String(x || '');
    if (!s) return '';
    if (mm && mm[s] && mm[s].url) return abs(mediaUrl(mm[s].url));
    if (/^(https?:|\/|data:|blob:)/.test(s)) return abs(mediaUrl(s));
    return '';
  }

  /* 封面兜底。媒体清单里混着品牌 logo / 空白幻灯片底图 / 地图截图，
     直接取「第一项」会得到一张几乎全白的首图 —— 2026-10-07 用户截图实证：
     推文顶部是一整块空白，看起来像生成坏了。所以先扫一遍 kind='photo' 的真实照片，
     确实一张都没有时，才退回清单里的任意一张。 */
  function firstMediaUrl(mm) {
    var k = Object.keys(mm || {});
    for (var pass = 0; pass < 2; pass++) {
      for (var i = 0; i < k.length; i++) {
        var m = mm[k[i]];
        if (!m || !m.url) continue;
        if (pass === 0 && (m.kind || 'photo') !== 'photo') continue;
        return abs(mediaUrl(m.url));
      }
    }
    return '';
  }

  /* ---------- 活动上下文（媒体清单 + 真实事实）---------- */

  var _ctxCache = {};
  function loadCtx(activityId) {
    if (_ctxCache[activityId]) return _ctxCache[activityId];
    // 媒体 url 要改写成公开代理地址（生产 /static/uploads/* 全 404），在这里记住活动 id
    setMediaActivityId(activityId);
    /* 品牌信息（俱乐部名 / 口号 / 品牌 logo）与活动资料一起取：海报顶部的品牌带要用。
       取不到就当"没设品牌"，只少一个 logo，不阻断渲染。 */
    var p = Promise.all([
      api('/api/club/' + CLUB + '/activities/' + activityId),
      api('/api/club/' + CLUB + '/settings').catch(function () { return null; })
    ]).then(function (res) {
      var a = res[0], st = res[1] || {};
      var master = a.activityMaster || {};
      var mm = mediaMap(master);
      /* 海报在俱乐部后台本地合成，媒体走 /api/club/ 鉴权路由：
         公开代理按设计**永不曝光 kind='logo'**、也不服务未发布活动 —— 用它加载 logo 会 404，
         俱乐部看自己的资料（含品牌 logo / 草稿）天经地义。白名单=该活动 master.media + cover。 */
      function clubMediaUrl(rawUrl) {
        var s = String(rawUrl || '');
        var mUp = s.match(/^\/static\/(uploads\/.+)$/);
        if (!mUp) return abs(s);   // demo 静态图等非 uploads 路径原样用
        return abs('/api/club/' + CLUB + '/activities/' + Number(activityId) + '/media/' +
          mUp[1].split('/').map(encodeURIComponent).join('/'));
      }
      function mediaClubUrl(ref) {
        var m = mm[ref];
        return m && m.url ? clubMediaUrl(m.url) : '';
      }
      function firstPhotoClubUrl() {
        var k = Object.keys(mm);
        for (var pass = 0; pass < 2; pass++)
          for (var i = 0; i < k.length; i++) {
            var m = mm[k[i]];
            if (!m || !m.url) continue;
            if (pass === 0 && (m.kind || 'photo') !== 'photo') continue;
            return clubMediaUrl(m.url);
          }
        return '';
      }
      /* 品牌 logo 只用「俱乐部设置 · 品牌」里老板自己上传的那张。
         ★ 不再回退到资料里 kind='logo' 的图：那通常是客户/场地/赞助方塞进 PPT 的标识
           （实测 2026-10-09：毕棚沟方案 img_01 是一张近乎全白的线条插画），
           盖进白胶囊里就是一团白斑，比不加 logo 还丑。没设品牌 logo 就只出纯文字品牌带。 */
      return {
        club: CLUB,
        activityId: Number(activityId),
        master: master,
        mm: mm,
        /* ?v= 时间戳：服务端 logo 固定文件名覆盖写、URL 不变，不禁缓存就永远吃旧图
           （2026-10-09 用户反馈「上传之后换不了」）—— 海报每次渲染强制拿最新。 */
        logoUrl: st.logoUrl ? abs(st.logoUrl) + (abs(st.logoUrl).indexOf('?') < 0 ? '?v=' + Date.now() : '') : '',
        brandName: String(st.name || ''),
        brandSlogan: String(st.slogan || ''),
        status: a.status || '',
        mediaClubUrl: mediaClubUrl,
        firstPhotoClubUrl: firstPhotoClubUrl,
        coverUrl: a.cover ? ('/api/club/' + CLUB + '/activities/' + activityId + '/cover') : '',
        title: a.title || master.title || '活动',
        date: master.date || a.event_date || '',
        location: master.location || a.location || '',
        price: (master.price != null && master.price !== '' ? master.price : a.price),
        capacity: master.capacity || a.capacity || ''
      };
    });
    _ctxCache[activityId] = p;
    return p;
  }
  // 活动被重新生成后要让缓存失效，避免一直拿旧媒体清单
  window.channelRenderResetCtx = function (activityId) {
    if (activityId) delete _ctxCache[activityId]; else _ctxCache = {};
  };

  /* ---------- 图片加载（canvas 合成用）---------- */

  var _imgCache = {};
  function loadImg(src) {
    src = abs(src);
    if (!src) return Promise.reject(new Error('no src'));
    if (_imgCache[src]) return _imgCache[src];
    var p = new Promise(function (res, rej) {
      var im = new Image();
      // 仅跨域时才声明 CORS，避免同源自带 cookie 的封面被额外预检拦掉
      if (src.indexOf(location.origin) !== 0) im.crossOrigin = 'anonymous';
      im.onload = function () { res(im); };
      im.onerror = function () { rej(new Error('图片加载失败：' + src)); };
      im.src = src;
    });
    p.catch(function () {});
    _imgCache[src] = p;
    return p;
  }
  function loadImgSafe(src) { return loadImg(src).catch(function () { return null; }); }

  /* ---------- 公众号图文：先建块模型，再分别序列化（预览用 class / 复制用内联样式）---------- */

  function wechatModel(data, ctx) {
    var mm = ctx.mm, out = [];
    var blocks = data.blocks || [];
    blocks.forEach(function (b) {
      var t = b.type || 'narrative';
      var h = head(b), tx = textOf(b);
      var imgs = (b.mediaRefs || []).map(function (r) { return toUrl(r, mm); }).filter(Boolean);
      if (t === 'hero') {
        var cover = imgs[0] || ctx.coverUrl || firstMediaUrl(mm);
        if (cover) out.push({ kind: 'cover', src: cover });
        if (h) out.push({ kind: 'h1', text: h });
        paras(tx).forEach(function (x) { out.push({ kind: 'lead', text: x }); });
      } else if (t === 'lead') {
        paras(tx).forEach(function (x) { out.push({ kind: 'lead', text: x }); });
      } else if (t === 'statement') {
        if (tx) out.push({ kind: 'statement', text: tx });
      } else if (t === 'narrative') {
        if (h) out.push({ kind: 'h2', text: h });
        paras(tx).forEach(function (x) { out.push({ kind: 'p', text: x }); });
        if (imgs.length === 1) out.push({ kind: 'img', src: imgs[0] });
        else if (imgs.length > 1) out.push({ kind: 'grid', srcs: imgs });
      } else if (t === 'media') {
        if (h) out.push({ kind: 'caption', text: h });
        imgs.forEach(function (s) { out.push({ kind: 'img', src: s }); });
        paras(tx).forEach(function (x) { out.push({ kind: 'p', text: x }); });
      } else if (t === 'gallery') {
        if (h) out.push({ kind: 'caption', text: h });
        if (imgs.length) out.push({ kind: 'grid', srcs: imgs });
        paras(tx).forEach(function (x) { out.push({ kind: 'p', text: x }); });
      } else if (t === 'facts') {
        if (h) out.push({ kind: 'h2', text: h });
        if (b.items && b.items.length) {
          out.push({ kind: 'facts', items: b.items.map(function (x) { return [x.label || '', x.value || x]; }) });
        }
        paras(tx).forEach(function (x) { out.push({ kind: 'p', text: x }); });
      } else if (t === 'include') {
        /* 费用包含：模块化分组卡（2026-10-11 用户反馈：整段流水句是文字墙）。
           新形状 groups=[{name,items[]}]；旧形状 items=[{label,value}] 兜底成一组一条。 */
        if (h) out.push({ kind: 'h2', text: h });
        var gs = (b.groups || []).map(function (g) {
          var name = g.name || g.title || g.label || '';
          var items = (g.items || g.points || []).map(function (x) {
            return typeof x === 'string' ? x : (x.text || x.value || x.label || '');
          }).filter(function (s) { return s; });
          return { name: name, items: items };
        }).filter(function (g) { return g.name && g.items.length; });
        if (!gs.length && b.items && b.items.length) {
          gs = b.items.map(function (x) {
            var name = (typeof x === 'object' && x) ? (x.label || '') : '';
            var v = typeof x === 'string' ? x : (x.value || x.text || '');
            return { name: name, items: v ? [v] : [] };
          }).filter(function (g) { return g.name && g.items.length; });
        }
        if (gs.length) out.push({ kind: 'include', groups: gs });
      } else if (t === 'info') {
        if (h) out.push({ kind: 'h2', text: h });
        paras(tx).forEach(function (x) { out.push({ kind: 'p', text: x }); });
      } else if (t === 'quote') {
        if (tx) out.push({ kind: 'quote', text: tx });
      } else if (t === 'timeline') {
        out.push({ kind: 'h2', text: h || '行程安排' });
        out.push({
          kind: 'timeline',
          items: (b.items || []).map(function (x) { return [x.time || '', x.text || x.content || '']; })
        });
      } else if (t === 'divider') {
        out.push({ kind: 'hr' });
      } else if (t === 'cta') {
        out.push({ kind: 'cta', headline: h || '立即报名', text: tx });
      } else {
        if (h) out.push({ kind: 'h2', text: h });
        paras(tx).forEach(function (x) { out.push({ kind: 'p', text: x }); });
        imgs.forEach(function (s) { out.push({ kind: 'img', src: s }); });
      }
    });
    if (!out.some(function (x) { return x.kind === 'cover'; })) {
      var c = ctx.coverUrl || firstMediaUrl(mm);
      if (c) out.unshift({ kind: 'cover', src: c });
    }
    // 一个块都没有（模型退化）时，至少把纯文本事实写出来，而不是空白页
    if (!out.length) {
      paras(data.summary || '').forEach(function (x) { out.push({ kind: 'lead', text: x }); });
      if (ctx.date || ctx.location || ctx.price) {
        out.push({
          kind: 'facts', items: [
            ['日期', ctx.date || '待定'], ['地点', ctx.location || '待定'], ['费用', '¥' + (ctx.price || 0)]
          ]
        });
      }
    }
    return { title: data.title || data.headline || ctx.title, summary: data.summary || '', blocks: out };
  }

  function previewHtml(model) {
    var h = '<article class="ch-wechat">';
    h += '<h1 class="ch-wx-title">' + esc(model.title) + '</h1>';
    if (model.summary) h += '<p class="ch-wx-summary">' + esc(model.summary) + '</p>';
    h += '<div class="ch-wx-body">';
    model.blocks.forEach(function (b) {
      if (b.kind === 'cover') h += '<figure class="ch-wx-cover"><img src="' + esc(abs(b.src)) + '" alt=""></figure>';
      else if (b.kind === 'h1') h += '<h2 class="ch-wx-h1">' + esc(b.text) + '</h2>';
      else if (b.kind === 'h2') h += '<h3 class="ch-wx-h2">' + esc(b.text) + '</h3>';
      else if (b.kind === 'lead') h += '<p class="ch-wx-lead">' + esc(b.text) + '</p>';
      else if (b.kind === 'p') h += '<p class="ch-wx-p">' + esc(b.text) + '</p>';
      else if (b.kind === 'caption') h += '<p class="ch-wx-caption">' + esc(b.text) + '</p>';
      else if (b.kind === 'img') h += '<figure class="ch-wx-img"><img src="' + esc(abs(b.src)) + '" alt="" loading="lazy"></figure>';
      else if (b.kind === 'grid') h += '<div class="ch-wx-grid">' + b.srcs.map(function (s) { return '<img src="' + esc(abs(s)) + '" alt="" loading="lazy">'; }).join('') + '</div>';
      else if (b.kind === 'facts') h += '<div class="ch-wx-facts">' + b.items.map(function (x) { return '<div><small>' + esc(x[0]) + '</small><strong>' + esc(String(x[1])) + '</strong></div>'; }).join('') + '</div>';
      else if (b.kind === 'include') h += '<div class="ch-wx-include">' + b.groups.map(function (g) {
        return '<div class="ch-wx-inc-card"><h4>' + esc(g.name) + '</h4><ul>' +
          g.items.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>';
      }).join('') + '</div>';
      else if (b.kind === 'statement') h += '<p class="ch-wx-statement">' + esc(b.text) + '</p>';
      else if (b.kind === 'quote') h += '<blockquote class="ch-wx-quote">' + esc(b.text) + '</blockquote>';
      else if (b.kind === 'timeline') h += '<div class="ch-wx-timeline">' + b.items.map(function (x) { return '<div><time>' + esc(x[0]) + '</time><p>' + esc(x[1]) + '</p></div>'; }).join('') + '</div>';
      else if (b.kind === 'hr') h += '<hr class="ch-wx-hr">';
      else if (b.kind === 'cta') h += '<div class="ch-wx-cta"><strong>' + esc(b.headline) + '</strong>' + (b.text ? '<p>' + esc(b.text) + '</p>' : '') + '</div>';
    });
    return h + '</div></article>';
  }

  // 公众号编辑器只认内联样式：这里输出可直接粘贴的富文本 HTML
  function inlineHtml(model, ctx) {
    var S = {
      title: 'font-size:22px;font-weight:700;line-height:1.45;color:#1a1a1a;margin:0 0 10px;letter-spacing:.4px',
      summary: 'font-size:14px;line-height:1.75;color:#8a8a8a;margin:0 0 18px;letter-spacing:.4px',
      h1: 'font-size:19px;font-weight:700;line-height:1.5;color:#1a1a1a;margin:30px 0 14px;letter-spacing:.4px',
      h2: 'font-size:18px;font-weight:700;line-height:1.5;color:#1a1a1a;margin:30px 0 14px;padding-left:11px;border-left:3px solid #245743;letter-spacing:.4px',
      p: 'font-size:16px;line-height:1.85;color:#3f3f3f;margin:0 0 18px;letter-spacing:.5px',
      lead: 'font-size:16px;line-height:1.9;color:#2f2f2f;margin:0 0 20px;letter-spacing:.5px',
      caption: 'font-size:13px;line-height:1.7;color:#9a9a9a;text-align:center;margin:6px 0 18px;letter-spacing:.4px',
      statement: 'font-size:17px;line-height:1.9;color:#1a1a1a;margin:26px 0;padding:18px 20px;background:#f4f0e8;border-radius:6px;letter-spacing:.5px',
      quote: 'font-size:16px;line-height:1.85;color:#3f3f3f;margin:0 0 22px;padding:14px 0 14px 16px;border-left:3px solid #d8d1c5;font-style:italic;letter-spacing:.5px',
      cta: 'margin:28px 0;padding:20px;background:#14201c;border-radius:8px;color:#ffffff',
      hr: 'border:0;border-top:1px solid #e6e6e6;margin:30px 0'
    };
    var img = 'display:block;width:100%;height:auto;margin:0 0 18px;border-radius:4px';
    var h = '';
    h += '<h1 style="' + S.title + '">' + esc(model.title) + '</h1>';
    if (model.summary) h += '<p style="' + S.summary + '">' + esc(model.summary) + '</p>';
    model.blocks.forEach(function (b) {
      if (b.kind === 'cover') h += '<p style="margin:0 0 20px"><img src="' + esc(abs(b.src)) + '" style="' + img + '"></p>';
      else if (b.kind === 'h1') h += '<h2 style="' + S.h1 + '">' + esc(b.text) + '</h2>';
      else if (b.kind === 'h2') h += '<h2 style="' + S.h2 + '">' + esc(b.text) + '</h2>';
      else if (b.kind === 'lead') h += '<p style="' + S.lead + '">' + esc(b.text) + '</p>';
      else if (b.kind === 'p') h += '<p style="' + S.p + '">' + esc(b.text) + '</p>';
      else if (b.kind === 'caption') h += '<p style="' + S.caption + '">' + esc(b.text) + '</p>';
      else if (b.kind === 'img') h += '<p style="margin:0 0 18px"><img src="' + esc(abs(b.src)) + '" style="' + img + '"></p>';
      else if (b.kind === 'grid') h += '<p style="margin:0 0 18px;font-size:0">' + b.srcs.map(function (s, i) {
        var half = b.srcs.length > 1;
        return '<img src="' + esc(abs(s)) + '" style="display:inline-block;vertical-align:top;width:' +
          (half ? (i % 2 === 0 ? '49%' : '49%') : '100%') + ';margin:' + (half && i % 2 === 0 ? '0 2% 8px 0' : '0 0 8px 0') + ';border-radius:4px">';
      }).join('') + '</p>';
      else if (b.kind === 'facts') h += '<section style="margin:0 0 22px;border-top:1px solid #eae5da">' + b.items.map(function (x) {
        return '<p style="font-size:15px;line-height:1.8;color:#3f3f3f;margin:0;padding:11px 0;border-bottom:1px solid #eae5da;letter-spacing:.4px">' +
          '<span style="display:inline-block;min-width:76px;color:#9a9a9a">' + esc(x[0]) + '</span><b>' + esc(String(x[1])) + '</b></p>';
      }).join('') + '</section>';
      else if (b.kind === 'include') h += '<section style="font-size:0;margin:0 0 6px">' + b.groups.map(function (g, i) {
        /* 公众号编辑器不认 class，全部内联样式；多组时双列卡片，单组整行 */
        var two = b.groups.length > 1;
        var box = 'display:inline-block;vertical-align:top;width:' + (two ? '48.5%' : '100%') + ';' +
          'margin:' + (two && i % 2 === 0 ? '0 3% 10px 0' : '0 0 10px 0') + ';' +
          'border:1px solid #e3ddd0;border-radius:8px;padding:13px 15px;background:#fbfaf7';
        return '<section style="' + box + '">' +
          '<p style="font-size:14.5px;font-weight:700;color:#245743;margin:0 0 8px;letter-spacing:.4px">' + esc(g.name) + '</p>' +
          g.items.map(function (x) {
            return '<p style="font-size:13.5px;line-height:1.75;color:#4a4a4a;margin:0 0 4px;letter-spacing:.3px">· ' + esc(x) + '</p>';
          }).join('') + '</section>';
      }).join('') + '</section>';
      else if (b.kind === 'statement') h += '<p style="' + S.statement + '">' + esc(b.text) + '</p>';
      else if (b.kind === 'quote') h += '<blockquote style="' + S.quote + '">' + esc(b.text) + '</blockquote>';
      else if (b.kind === 'timeline') h += '<section style="margin:0 0 22px">' + b.items.map(function (x) {
        return '<p style="font-size:15px;line-height:1.8;color:#3f3f3f;margin:0;padding:10px 0;border-bottom:1px solid #eee;letter-spacing:.4px">' +
          '<span style="display:inline-block;min-width:96px;color:#245743;font-weight:700">' + esc(x[0]) + '</span>' + esc(x[1]) + '</p>';
      }).join('') + '</section>';
      else if (b.kind === 'hr') h += '<hr style="' + S.hr + '">';
      else if (b.kind === 'cta') h += '<section style="' + S.cta + '"><p style="font-size:17px;font-weight:700;margin:0 0 8px;letter-spacing:.4px">' + esc(b.headline) + '</p>' +
        (b.text ? '<p style="font-size:14px;line-height:1.8;margin:0;color:#d1dfd9;letter-spacing:.4px">' + esc(b.text) + '</p>' : '') + '</section>';
    });
    // 报名/团期事实永远附在文末，避免内容里写错日期价格
    var facts = [];
    if (ctx.date) facts.push('日期：' + ctx.date);
    if (ctx.location) facts.push('地点：' + ctx.location);
    if (ctx.price || ctx.price === 0) facts.push('费用：¥' + ctx.price);
    if (facts.length) h += '<p style="font-size:14px;line-height:1.9;color:#8a8a8a;margin:26px 0 0;padding-top:14px;border-top:1px solid #eae5da;letter-spacing:.4px">' +
      esc(facts.join('　·　')) + '</p>';
    return h;
  }

  function plainText(model, ctx) {
    var out = [model.title, ''];
    if (model.summary) out.push(model.summary, '');
    model.blocks.forEach(function (b) {
      if (b.kind === 'h1' || b.kind === 'h2') out.push(b.text, '');
      else if (b.kind === 'p' || b.kind === 'lead') out.push(b.text, '');
      else if (b.kind === 'caption') out.push('（' + b.text + '）');
      else if (b.kind === 'statement') out.push(b.text, '');
      else if (b.kind === 'quote') out.push('「' + b.text + '」', '');
      else if (b.kind === 'facts') b.items.forEach(function (x) { out.push(x[0] + '：' + x[1]); });
      else if (b.kind === 'include') b.groups.forEach(function (g) { out.push(g.name + '：' + g.items.join('、'), ''); });
      else if (b.kind === 'timeline') b.items.forEach(function (x) { out.push(x[0] + '  ' + x[1]); });
      else if (b.kind === 'cta') out.push(b.headline, b.text || '');
      else if (b.kind === 'hr') out.push('———');
    });
    var facts = [];
    if (ctx.date) facts.push('日期：' + ctx.date);
    if (ctx.location) facts.push('地点：' + ctx.location);
    if (ctx.price || ctx.price === 0) facts.push('费用：¥' + ctx.price);
    if (facts.length) out.push('', facts.join('　·　'));
    return out.filter(function (x) { return x !== undefined && x !== null; }).join('\n');
  }

  /* ---------- canvas 绘制工具 ---------- */

  function rr(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // CJK 没有空格，靠逐字测量换行；遇到西文按空白切
  function wrap(ctx, str, maxW) {
    var lines = [];
    String(str == null ? '' : str).split('\n').forEach(function (seg) {
      seg = seg.trim();
      if (!seg) { lines.push(''); return; }
      var cur = '';
      var tokens = seg.match(/[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]|[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+|\s/g) || [];
      tokens.forEach(function (tk) {
        var t = tk.replace(/\s+/g, ' ');
        if (cur && ctx.measureText(cur + t).width > maxW) { lines.push(cur); cur = t.replace(/^\s/, ''); }
        else cur += t;
      });
      if (cur) lines.push(cur);
    });
    return lines;
  }

  function drawLines(ctx, lines, x, y, lh, max) {
    var n = max ? Math.min(lines.length, max) : lines.length;
    for (var i = 0; i < n; i++) {
      var s = lines[i];
      if (max && i === max - 1 && lines.length > max) s = s.slice(0, Math.max(1, s.length - 1)) + '…';
      ctx.fillText(s, x, y + i * lh);
    }
    return y + n * lh;
  }

  function drawCover(img, x, y, w, h) {
    var iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
    var s = Math.max(w / iw, h / ih);
    var dw = iw * s, dh = ih * s;
    this.drawImage(img, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
  }

  function downloadCanvas(cv, name) {
    return new Promise(function (res) {
      cv.toBlob(function (b) {
        if (!b) return res(false);
        var u = URL.createObjectURL(b);
        var a = document.createElement('a');
        a.href = u; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(u); }, 5000);
        res(true);
      }, 'image/png');
    });
  }

  function safeName(s) {
    return String(s || 'clubos').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40);
  }

  /* ---------- AI 宣传长图（longpic）：模型直出 HTML，沙箱里预览 + 导出 PNG ----------
     为什么单独一节（2026-10-07 用户反馈）：固定模板把 facts 排成 5 列网格，
     「费用包含 / 装备建议」是长串清单 → 窄列套长文本，页尾变成一张难看的电子表格。
     这个渠道让模型自己排版，所以这里**不再翻译成 block**，只做三件事：
       ① 把 {{media:ref}} 换成真实图片地址（ref 不在媒体清单里就整段丢掉，不留破图）
       ② 用 sandbox iframe 原样渲染模型的设计（不注入任何我们的样式，免得改坏它的版式）
       ③ 导出 750px 宽的整页 PNG（公众号长图的标准宽度），以及复制可粘贴的图文
     安全：html 在服务端已 sanitize_html 过（script、事件属性、javascript: 伪协议全剥掉），
     这里再做一层同源白名单过滤 —— 模型产物永远不该有能力跳出 iframe。 */

  var LONG_WIDTH = 750;

  function fitLongpicFrame(frame, holder, height) {
    if (!holder) return;
    var fit = function () {
      var scale = Math.min(1, holder.clientWidth / LONG_WIDTH);
      frame.style.width = LONG_WIDTH + 'px'; frame.style.maxWidth = 'none';
      frame.style.transformOrigin = 'top left'; frame.style.transform = 'scale(' + scale + ')';
      holder.style.height = Math.ceil(height * scale) + 'px';
    };
    fit();
    if (window.ResizeObserver) {
      var observer = new ResizeObserver(function () {
        if (!frame.isConnected) { observer.disconnect(); return; }
        fit();
      });
      observer.observe(holder);
    }
  }

  function longpicRichHtml(paint) {
    var doc = paint.frame.contentDocument;
    if (!doc || !doc.body) return paint.html;
    var clone = doc.body.cloneNode(true), source = doc.body.querySelectorAll('*'), target = clone.querySelectorAll('*');
    var properties = ['display', 'color', 'background-color', 'font-family', 'font-size', 'font-weight',
      'font-style', 'line-height', 'letter-spacing', 'text-align', 'width', 'max-width', 'height',
      'margin', 'padding', 'border', 'border-radius', 'vertical-align', 'object-fit', 'object-position',
      'background-image', 'background-size', 'background-position', 'position', 'left', 'right', 'top', 'bottom', 'flex', 'gap'];
    Array.prototype.forEach.call(source, function (el, i) {
      if (!target[i].style) return;
      var computed = doc.defaultView.getComputedStyle(el);
      properties.forEach(function (key) { target[i].style.setProperty(key, computed.getPropertyValue(key)); });
    });
    Array.prototype.slice.call(clone.querySelectorAll('style,script')).forEach(function (el) { el.remove(); });
    return '<section style="max-width:100%">' + clone.innerHTML + '</section>';
  }

  // 模型可能把占位符写成裸文本而不是 <img src="...">，两种都要能救回来
  function resolveLongpic(html, mm) {
    var t = String(html || '');
    // ① img 标签里的占位符：模型可能写 {{media:ref}} 或 {media:ref}，src 双/单引号均可
    //    ★ 2026-10-18 实坑：真模型偶尔直出单花括号 {media:ref}，旧正则只认 {{media:ref}}，
    //      导致整张长图预览与导出全白（占位符原样进 SVG，外链被 sanitize 成空白）。
    t = t.replace(/(<img\b[^>]*?src=(["']))\s*\{\{?\s*media\s*:\s*([A-Za-z0-9_-]+)\s*\}?\}\s*\2/gi,
      function (_, pre, q, ref) {
        var u = toUrl(ref, mm);
        return u ? pre + esc(u) + q : '';
      });
    // ② 没有任何 src 的 img（解析失败 / 模型只写了占位符）→ 丢掉，防止留下 <img src="">
    t = t.replace(/<img\b[^>]*>/gi, function (tag) {
      if (/\{\{?\s*media\s*:/.test(tag)) return '';
      return /\bsrc=("|')/i.test(tag) ? tag : '';
    });
    // ③ 裸占位符：不在 img 里的（实测 qwen3-max 会这么写）→ 去掉占位符本身（保留前后文字）
    t = t.replace(/\{\{?\s*media\s*:\s*([A-Za-z0-9_-]+)\s*\}?\}/g,
      function (whole, ref) { return toUrl(ref, mm) ? '' : whole; });
    return t;
  }

  /* 模型产物永远不该有能力跳出 iframe：只允许同源 / 数据 URL，其余 src 一律剥掉。
     服务端已 sanitize_html 过（script、on*、javascript: 全删），这里是第二道。 */
  function hardenLongpic(html) {
    return String(html || '').replace(/(src|href)\s*=\s*(["'])([^"']*)\2/gi,
      function (whole, attr, q, val) {
        var v = String(val || '').trim();
        if (!v) return '';
        if (/^data:image\//i.test(v)) return whole;
        /* 带 scheme 或协议相对：只放行同源 http(s)；其余（javascript:/mailto:/外链）剥掉 */
        if (/^[a-z][a-z0-9+.-]*:/i.test(v) || /^\/\//.test(v)) {
          if (/^(https?:)?\/\//i.test(v)) {
            try { if (new URL(v, location.href).origin === location.origin) return whole; } catch (e) {}
          }
          return '';
        }
        /* 相对路径（/api/…、/static/…、./x）＝同源，必须保留（与 hardenPromoHtml 同修） */
        return whole;
      });
  }

  function longpicDoc(html, ctx) {
    return '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">' +
      '<style>' +
      '*{box-sizing:border-box}' +
      'html,body{margin:0;padding:0;background:#fff}' +
      'body{width:' + LONG_WIDTH + 'px;font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;' +
      '-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}' +
      /* 这几条只是「地板」：模型自己写的行内样式优先级更高，这里只兜住它没写的情况 */
      'img{max-width:100%;height:auto;display:block}' +
      'p{margin:0 0 1em}' +
      'section{display:block}' +
      '</style></head><body>' + hardenLongpic(html) + '</body></html>';
  }

  async function paintLongpic(data, ctx, stage, resolver) {
    // 图片要改写成公开代理地址，先记住是哪场活动（toUrl 靠它拼）
    setMediaActivityId(ctx.activityId);
    var mm = ctx.mm;
    /* resolver：占位符 → 真实图片地址的替换函数。宣传长图用 resolveLongpic（活动媒体池）；
       活动回顾的图片来自回顾自己的照片池（rcap_*，走 /api/club/.../recap-media 白名单），
       复用同一套 iframe + 导出管线，只换解析器。 */
    var html = (resolver || resolveLongpic)(data.html || data.body || '', mm);
    var usedImgs = (html.match(/<img\b/gi) || []).length;
    var dropped = (data.droppedRefs || []).length || (data.usedRefs || []).length - usedImgs;

    stage.innerHTML = '<div class="ch-long-wrap">' +
      '<div class="ch-long-frame"><iframe id="chLongFrame" title="宣传长图预览" ' +
      'sandbox="allow-same-origin" referrerpolicy="no-referrer"></iframe></div>' +
      '<div class="ch-long-meta"><span>正在排版…</span></div></div>';

    var frame = stage.querySelector('#chLongFrame');
    var doc = longpicDoc(html, ctx);
    // 用 srcdoc 而不是 blob URL：blob 在部分环境下拿不到 document，会静默渲染成空白
    frame.srcdoc = doc;
    // 等图片都解码完再量高度，否则量到的是「图片还没撑开」的半成品高度
    try {
      await new Promise(function (res) {
        var done = false;
        var finish = function () { if (!done) { done = true; res(1); } };
        frame.onload = function () {
          var d = frame.contentDocument;
          if (!d) return finish();
          var imgs = Array.prototype.slice.call(d.images || []);
          if (!imgs.length) return setTimeout(finish, 300);
          var left = imgs.filter(function (i) { return !i.complete; }).length;
          if (!left) return setTimeout(finish, 200);
          var timer = setTimeout(finish, 4000);      // 慢图不许无限拖住预览
          imgs.forEach(function (i) {
            i.addEventListener('load', function () { if (--left <= 0) { clearTimeout(timer); finish(); } });
            i.addEventListener('error', function () { if (--left <= 0) { clearTimeout(timer); finish(); } });
          });
        };
        setTimeout(finish, 6000);
      });
    } catch (e) { /* 预览失败不该挡住「复制 / 下载」两条出路 */ }

    var h = 0;
    try { h = frame.contentDocument ? frame.contentDocument.body.scrollHeight : 0; } catch (e2) { h = 0; }
    frame.style.height = (h || 1200) + 'px';
    // 窄屏 CSS 把 iframe 按 .52 缩放，父容器要跟着缩高，否则下方留一大片空白
    var holder = stage.querySelector('.ch-long-frame');
    fitLongpicFrame(frame, holder, h || 1200);
    var meta = stage.querySelector('.ch-long-meta span');
    if (meta && h) meta.textContent = '宽度 ' + LONG_WIDTH + 'px（公众号标准宽度）· 实际高度约 ' + h + 'px';
    return { html: html, usedImgs: usedImgs, dropped: dropped, frame: frame };
  }

  /* 整页导出 750px 宽 PNG。
     ★ 2026-10-07 实测修掉的真bug：原实现是 `ctx.drawImage(doc.body, ...)`，
       浏览器直接抛 TypeError —— drawImage 只接受 canvas/img/video/ImageBitmap，
       **不接受 DOM 元素**（同源可读也不行）。按钮能点、能看见，但**从来没下载成功过**。
     正确做法：SVG `foreignObject` 承载序列化后的 DOM，先转成 <img> 再画进 canvas。
     图片必须先转成 data URI ——foreignObject 里引外链地址会让 SVG 光栅化失败（画出来全白）。 */
  /* 内联图片：foreignObject 里引外链地址会让 SVG 光栅化失败，必须转成 data URI。
     用 JPEG(0.92) 而不是 PNG —— 活动照片用 PNG 会让 SVG 膨胀到几MB（2026-10-07 实测 4 张图 6.9MB），
     白白拖慢导出；JPEG 在这种照片内容上肉眼无损，体积只有零头。 */
  var _INLINE_MIME = 'image/jpeg';
  var _INLINE_Q = 0.92;
  /* 内联一张图：先走 canvas 重编码（体积最小，保持既有行为），
     canvas 被判「污染」或内存不足（getContext/drawImage 直接抛）时，
     退回「直接取原始字节转 data URI」。
     ★ 2026-10-16 用户实测「下载后图片全白」后加的第二条路：
       原来只有 canvas 一条路，抛异常就保留外链 src，
       而 foreignObject 光栅化时外链图片不加载 → 那个位置就是一块背景色。
       两条路都走不通才算失败。 */
  function _canvasDataUri(im) {
    /* ★ 2026-11-04：手机直出原图动辄 4000×3000，按 naturalWidth 全尺寸 toDataURL
       会把单张撑到 3~6MB base64；十几张一起进 SVG 时 data URL 长达几十 MB，
       foreignObject 光栅化直接静默失败（用户实测「下载下来只有文字、照片全空」）。
       统一缩到最长边 1800px 再编码，750px 宽成品里肉眼无损。 */
    var cv2 = document.createElement('canvas');
    var nw = im.naturalWidth, nh = im.naturalHeight;
    var k = Math.min(1, 1800 / Math.max(nw, nh));
    cv2.width = Math.max(1, Math.round(nw * k));
    cv2.height = Math.max(1, Math.round(nh * k));
    cv2.getContext('2d').drawImage(im, 0, 0, cv2.width, cv2.height);
    return cv2.toDataURL(_INLINE_MIME, _INLINE_Q);
  }
  /* ★ 换过 src 之后必须等图片真正解码完，再去做「有没有内容」的自检。
     inlineImages 以前 setAttribute('src', dataUri) 之后**立刻**算 shots，
     此时 im 还在重新解码、naturalWidth=0 → _hasContent 全判 false →
     _paintShots 一张都不画（这正是「下载后没有图片」的直接原因）。 */
  function _waitDecoded(im) {
    return new Promise(function (res) {
      if (im.decode) { im.decode().then(function () { res(1); }, function () { res(1); }); return; }
      if (im.complete && im.naturalWidth > 0) return res(1);
      var t = setTimeout(function () { res(1); }, 1500);
      im.addEventListener('load', function () { clearTimeout(t); res(1); }, { once: true });
      im.addEventListener('error', function () { clearTimeout(t); res(1); }, { once: true });
    });
  }
  function _fetchDataUri(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.blob();
    }).then(function (b) {
      return new Promise(function (res, rej) {
        var fr = new FileReader();
        fr.onload = function () { res(String(fr.result || '')); };
        fr.onerror = function () { rej(new Error('图片读取失败')); };
        fr.readAsDataURL(b);
      });
    });
  }
  var _BLANK_PX = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

  /* 「这张图本身有没有内容」：纯色底图（白底 logo、纯色分割条）不该参与
     「到底画上去了没有」的自检，否则会把正常成品判成失败、白白重画一次。
     缩到 24×24 看灰度方差，有明暗变化的才算照片。
     ★ 2026-11-03：判定从「只看 R 通道」改为「亮度 + 饱和度双指标」——
       天空占大部的照片 R 通道几乎恒定（方差 <24），会被误判成纯色底图，
       于是不参与空白自检、也不参与导出后的盖回（Safari 下这张图丢了没人管）。 */
  function _hasContent(im) {
    try {
      if (!(im.naturalWidth > 0)) return false;
      var s = 24, cv = document.createElement('canvas');
      cv.width = s; cv.height = s;
      var ctx2 = cv.getContext('2d');
      ctx2.drawImage(im, 0, 0, s, s);
      var d = ctx2.getImageData(0, 0, s, s).data;
      var n = 0, mean = 0, i, v = 0, smean = 0, sv = 0;
      for (i = 0; i < d.length; i += 4) {
        var r = d[i], g = d[i + 1], b = d[i + 2];
        mean += (r + g + b) / 3;
        smean += Math.max(r, g, b) - Math.min(r, g, b);
        n++;
      }
      mean /= (n || 1); smean /= (n || 1);
      for (i = 0; i < d.length; i += 4) {
        var r2 = d[i], g2 = d[i + 1], b2 = d[i + 2];
        var l2 = (r2 + g2 + b2) / 3, sat2 = Math.max(r2, g2, b2) - Math.min(r2, g2, b2);
        v += (l2 - mean) * (l2 - mean);
        sv += (sat2 - smean) * (sat2 - smean);
      }
      return (v / (n || 1)) > 24 || (sv / (n || 1)) > 24;
    } catch (e) { return false; }
  }

  async function inlineImages(doc) {
    var imgs = Array.prototype.slice.call(doc.images || []);
    var failed = 0;
    await Promise.all(imgs.map(function (im) {
      if (!im.getAttribute('src')) return null;
      if (/^data:/i.test(im.getAttribute('src'))) return null;   // 已经是内联的跳过
      return new Promise(function (res) {
        var done = false;
        var finish = async function () {
          if (done) return; done = true;
          if (im.complete && im.naturalWidth > 0) {
            var raw = im.getAttribute('src'), uri = '';
            try { uri = _canvasDataUri(im); } catch (e) { uri = ''; }
            if (!uri) { try { uri = await _fetchDataUri(raw); } catch (e2) { uri = ''; } }
            if (uri) {
              /* ★ 2026-11-03（用户实测「Chrome 下载图里照片压扁变形」）：预览里
                 object-fit:cover 的裁切在 foreignObject 光栅化时会被 Chrome 忽略。
                 治本：序列化前就把每张图按「显示框尺寸」cover 裁好再换给 <img>，
                 让成品不再依赖 object-fit。 */
              try {
                var rect = im.getBoundingClientRect();
                if (rect.width > 20 && rect.height > 20) {
                  var cropped = _coverCropDataUri(im, rect.width, rect.height);
                  if (cropped) uri = cropped;
                }
              } catch (eR) { /* 量不到显示框就保留原图，退回旧行为 */ }
              im.setAttribute('src', uri);
            } else failed++;
          } else {
            failed++;                    // 压根没加载出来的图，也要如实计数，不许静默留白
          }
          /* ★ src 刚换成 data URI 时图片还在重新解码，必须等它真正可绘制，
             否则下面 shots 里的 _hasContent 会把每张照片都误判成「没内容」。 */
          await _waitDecoded(im);
          res(1);
        };
        im.addEventListener('load', finish, { once: true });
        im.addEventListener('error', function () {
          // 裂图换成 1×1 透明像素，别让 foreignObject 为一张挂掉的图整块失败
          if (!done) { done = true; im.setAttribute('src', _BLANK_PX); failed++; res(1); }
        }, { once: true });
        if (im.complete) finish();          // 已加载完的不会触发 load，得主动处理
      });
    }));
    /* 记下每张图在长图里的位置 + 「本身有没有内容」+ 内联后的干净 data URI，
       导出后要用它检查「这张图到底画上去了没有」，并把没画上的直接盖回去
       （2026-11-03：Safari foreignObject 光栅化会整批丢图，见 exportLongpicPng）。 */
    var shots = Array.prototype.slice.call(doc.images || []).map(function (im) {
      var r = im.getBoundingClientRect();
      var sx = doc.documentElement.scrollLeft || 0, sy = doc.documentElement.scrollTop || 0;
      return { x: Math.max(0, r.left + sx), y: Math.max(0, r.top + sy),
               w: Math.round(r.width), h: Math.round(r.height), hasContent: _hasContent(im),
               uri: (im.getAttribute('src') || '') };
    });
    return { total: imgs.length, failed: failed, shots: shots };
  }

  async function exportLongpicPng(paint, ctx, fileBase) {
    /* ★ 2026-11-04：原来直接 `paint.frame`，paint 为 undefined 时 WebKit 抛
       "undefined is not an object (evaluating 'paint.frame')"，而外层 catch 把它
       一律说成「浏览器不允许把预览转成图片」——把真 bug 伪装成环境问题，误导排查。
       现在先判空并抛出**可读的真实原因**，外层也照实显示。 */
    var frame = (paint && paint.frame) || null;
    if (!frame) throw new Error('预览还没准备好（没有可导出的预览文档），请重新生成一次再下载');
    var doc = null;
    try { doc = frame.contentDocument; } catch (e) { doc = null; }
    if (!doc) throw new Error('预览文档还没就绪，请等预览加载完再下载');
    var W = LONG_WIDTH;
    // 高度只能在**活的预览文档**里量：离屏副本 scrollHeight 恒为 0
    var H = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight, 1);

    var inlineStat = await inlineImages(doc);

    /* 复制到一张全新的离屏文档再序列化。
       ★ 2026-10-07 实测的坑：原先是把节点搬进一个 holder 再导出，
         foreignObject 光栅化会把页面上同层的浮层（圆角气泡里的「3万」、
         左右两侧的轮播箭头）一起烤进成品 —— 长图右上角凭空多一个东西。
         离屏文档里只有长图自己的节点，SVG 不可能画到别的东西。 */
    var clean = document.implementation.createHTMLDocument('longpic-export');
    // 序列化的是 body；head 中的全部基础样式也必须进入导出内容。
    Array.prototype.slice.call(doc.head.querySelectorAll('style')).forEach(function (style) {
      clean.body.appendChild(style.cloneNode(true));
    });
    clean.body.setAttribute('style', 'margin:0;padding:0;background:#fff;width:' + W + 'px');
    /* ★★ 2026-10-07 实测修掉的**第二个**真 bug（用户截图实证：「下载下来下面全是空白」）。
       原实现只克隆 `body.firstElementChild` —— 只取第一个顶层节点。
       而模型直出的 HTML 是**多个并列的顶层 section**（首屏 / 中段各节 / 收尾卡片），
       于是导出图里只剩首屏那一块，下面 5000+px 全是纯白；
       偏偏 canvas 高度取的是**完整** scrollHeight，文件名还写着 750x6709，
       尺寸看着完全正常、预览也完全正常 —— 只有下载出来的图是残的（内容只剩 1/9）。
       必须克隆 body 的**全部**子节点，一个不落。 */
    Array.prototype.slice.call(doc.body.childNodes).forEach(function (n) {
      clean.body.appendChild(n.cloneNode(true));
    });
    /* 回顾杂志长图：foreignObject 光栅化对内嵌 <img> 的排版不可靠（实测会把多张
       照片错误地叠到封面区域、连白色大标题都被盖掉），十几张原图内联后 SVG 更会
       膨胀到几十 MB、光栅化静默失败（用户实测「下载下来只有文字、照片全空」）。
       照片本来就由 _paintShots 按 shots 的精确坐标逐张画回，SVG 层只负责文字与色块
       —— 让 foreignObject 里的 img 不可见，避免两套渲染互相覆盖。**回顾一律生效**：
       模型自由排版（photoOverlayOnly）与固定模板（rc-doc）两条路线都走。 */
    if ((paint && paint.photoOverlayOnly) || /\brc-doc\b/.test(String(doc.body.className || ''))) {
      Array.prototype.slice.call(clean.body.querySelectorAll('img')).forEach(function (im, index) {
        var rect = doc.images[index].getBoundingClientRect();
        im.style.setProperty('width', rect.width + 'px', 'important');
        im.style.setProperty('height', rect.height + 'px', 'important');
        im.setAttribute('src', _BLANK_PX);   // 保留 CSS 宽高占位，只把照片源换成透明像素
      });
    }

    /*★ 必须用 XMLSerializer，不能用 innerHTML（2026-10-07 实测踩死在这）。
       foreignObject 里由 **XML 解析器**处理，而 innerHTML 是 **HTML 序列化**：
       <img src=x>、<br> 这类不闭合标签、以及 &nbsp; 在 XML 里都是**语法错误**，
       整个 SVG 解析失败 → img.onload 永远不来，只剩 onerror。 */
    var inner = new XMLSerializer().serializeToString(clean.body);
    // HTML 里定义过的实体在 XML 里未定义，序列化后仍会以实体名出现
    inner = inner.replace(/&nbsp;/gi, ' ');

    /* ★ 2026-11-04 修的导出黑底（用户实测「排版太差」附带发现）：
       SVG 作为 <img> 加载时，foreignObject 里**没有背景**的区域会被渲染成**黑色**。
       宣传长图一直是近黑底（--bg:#111312），所以这个毛病藏了很多年没人发现；
       活动回顾是米白底 → 导出成品整片黑底，深色正文几乎看不见。
       修法：SVG 里先铺一层底色矩形，底色取活文档 body 的 computed 背景（取不到就白）。 */
    var bgColor = '#ffffff';
    try {
      var cs = doc.defaultView && doc.defaultView.getComputedStyle
        ? doc.defaultView.getComputedStyle(doc.body) : null;
      var bc = cs && cs.backgroundColor;
      if (bc && bc !== 'transparent' && bc !== 'rgba(0, 0, 0, 0)') bgColor = bc;
    } catch (e3) { /* 取不到就用白底 */ }
    var svg = '<?xml version="1.0" encoding="UTF-8"?>' +
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H + '">' +
      '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="' + esc(bgColor) + '"/>' +
      '<foreignObject x="0" y="0" width="' + W + '" height="' + H + '">' + inner +
      '</foreignObject></svg>';

    /* ★ 必须用 data URI，**不能**用 Blob URL（2026-10-07 实测对照两条路径）：
         data URI → 图片加载正常，canvas.toBlob 出图成功；
         Blob URL → canvas 直接被判 Tainted，`toBlob` 抛 "Tainted canvases may not be exported"。
       原因是浏览器给 blob: 的 SVG 标了不透明来源，画进 canvas 就污染了。
       （先前误以为 data URI 会因长度失败而改用 Blob URL，正好踩中这个坑；
         内联图换成 JPEG 后 SVG 只有 ~1.5MB，data URI 完全够用。） */
    var cv = document.createElement('canvas');
    cv.width = W * 2; cv.height = H * 2;             // 2 倍图：公众号里缩排也不发虚
    var c = cv.getContext('2d');
    var shots = (inlineStat && inlineStat.shots) || [];
    var blanks = 0;
    /* ★★ 2026-11-03 双浏览器病灶根治（用户实测：Safari 下载图里照片全丢只剩文字 /
       Chrome 里照片压扁变形）：foreignObject 光栅化对内嵌图片不可靠 —— WebKit 整批丢图、
       Chrome 忽略 object-fit。压轴保险：光栅化完成后，按 shots 记录的位置用干净 data URI
       直接 drawImage 盖回去 —— 就算 SVG 那一层整张照片都没画出来，成品上也一定有图。
       变形的图在 inlineImages 里已按显示框裁好，这里盖回去的就是裁好的版本。 */
    var shotImgs = await Promise.all((shots || []).map(function (s) {
      /* hasContent 只用于「画完自检」，不再一票否决「要不要画」：
         手里有干净的 data URI 且尺寸够，就该画上去——画上总比整块空白强。 */
      if (!s.uri || s.w < 30 || s.h < 30 || !/^data:/i.test(s.uri)) return null;
      return new Promise(function (res) {
        var im = new Image();
        im.onload = function () { res(im); };
        im.onerror = function () { res(null); };
        im.src = s.uri;
      });
    }));
    var textLayer = photoTextLayer(doc, W, H, shots);
    var textImg = textLayer ? await _svgToImage(textLayer) : null;
    /* ★★ 画完自检 + 自动重画一次（2026-10-16 用户实测「下载后图片全白」）：
       drawImage 对「还没解码完的图片」不报错、也不画 —— 于是成品里那几张照片位置
       就是一块背景色，而 failed 计数是 0，用户拿不到任何提示。
       模型放照片的地方整块纯色是不可能的，所以直接按位置采样：纯色 = 没画上 = 重画。 */
    for (var attempt = 0; attempt < 2; attempt++) {
      var img = await _svgToImage(svg);
      c.fillStyle = '#fff'; c.fillRect(0, 0, cv.width, cv.height);
      c.drawImage(img, 0, 0, W * 2, H * 2);
      _paintShots(c, shots, shotImgs);            // 先盖回真图，再做空白自检
      if (textImg) c.drawImage(textImg, 0, 0, W * 2, H * 2);
      blanks = _blankShots(c, cv, shots);
      if (!blanks) break;
      await new Promise(function (r) { setTimeout(r, 600); });   // 给内嵌图片解码留时间，再来一次
    }

    /* ★★ 2026-10-08 兜底：**从底部自动裁掉纯白**。
       用户第二次截图仍显示「下面一大片白」。两种可能：
         (a) 浏览器还在跑修复前的旧 JS（只渲染首个顶层节点）—— 已在上面从代码层修掉；
         (b) 图片没载进来时 `width:100%;height:auto` 塌成 0 高，整篇内容比量到的
             `scrollHeight` 短，底下留白。
       不去猜是哪一种，直接**在导出后的画布上把底部纯白裁掉**：无论哪种根因都不会再出白边。
       只砍「末尾」空白（从底往上找最后一行非白像素），不会误伤中间的正常留白 —— 中间留白上方
       一定有内容，从底往上扫到的第一行非白就是整篇内容的底。 */
    var finalH = H;
    try {
      var rowStep = 4, lastContent = -1;
      for (var y = cv.height - 1; y >= 0; y -= rowStep) {
        var row = c.getImageData(0, y, cv.width, 1).data;
        for (var i = 0; i < row.length; i += 4) {
          if (row[i] < 250 || row[i + 1] < 250 || row[i + 2] < 250) { lastContent = y; break; }
        }
        if (lastContent >= 0) break;
      }
      if (lastContent >= 0) {
        var keepH = Math.min(cv.height, lastContent + 1 + 8);   // 底部留 8px(2x) 收边
        if (keepH < cv.height - 2) {
          var cv2 = document.createElement('canvas');
          cv2.width = cv.width; cv2.height = keepH;
          var c2 = cv2.getContext('2d');
          c2.fillStyle = '#fff'; c2.fillRect(0, 0, cv2.width, cv2.height);
          c2.drawImage(cv, 0, 0);
          cv = cv2;
          finalH = Math.round(keepH / 2);
        }
      }
    } catch (eTrim) { /* 读像素失败（极少数被判定污染的画布）就保持原样，不影响导出 */ }

    /* 导出 JPEG 而非 PNG：长图是照片为主，PNG 无损会把 750×5000 的图撑到 17MB，
       微信/邮件都发不动；JPEG(0.92) 肉眼几乎无损，体积降到 1MB 上下。 */
    var blob = await new Promise(function (res) { cv.toBlob(res, 'image/jpeg', 0.92); });
    if (!blob) throw new Error('浏览器拒绝生成图片');
    downloadBlob(blob, fileBase + '_长图_' + W + 'x' + finalH + '_2x.jpg');
    // 交给调用方如实告知「有几张图没能载入」：内联失败 + 画完自检仍空白的
    return { total: (inlineStat && inlineStat.total) || 0,
             failed: ((inlineStat && inlineStat.failed) || 0) + blanks };
  }

  // 照片补画后恢复与照片相交的文字，保留封面叠字的真实层级。
  function photoTextLayer(doc, width, height, shots) {
    var body = doc.body.cloneNode(true), count = 0;
    var original = Array.prototype.slice.call(doc.body.querySelectorAll('*'));
    var cloned = Array.prototype.slice.call(body.querySelectorAll('*'));
    original.forEach(function (el, index) {
      var target = cloned[index];
      if (!target || !target.style || /^(STYLE|SCRIPT)$/.test(el.tagName)) return;
      if (!el.hasAttribute('data-photo-overlay')) target.style.setProperty('background', 'transparent', 'important');
      target.style.setProperty('box-shadow', 'none', 'important');
      target.style.setProperty('border-color', 'transparent', 'important');
      target.style.setProperty('visibility', 'hidden', 'important');
      if (el.hasAttribute('data-photo-overlay')) {
        target.style.setProperty('visibility', 'visible', 'important'); count++;
      }
      if (el.tagName === 'IMG') {
        var rect = el.getBoundingClientRect();
        target.style.setProperty('width', rect.width + 'px', 'important');
        target.style.setProperty('height', rect.height + 'px', 'important');
        return;
      }
      var overlaps = Array.prototype.some.call(el.childNodes, function (node) {
        if (node.nodeType !== 3 || !node.textContent.trim()) return false;
        var range = doc.createRange(); range.selectNodeContents(node);
        return Array.prototype.some.call(range.getClientRects(), function (r) {
          return shots.some(function (s) {
            return r.right > s.x && r.left < s.x + s.w && r.bottom > s.y && r.top < s.y + s.h;
          });
        });
      });
      if (overlaps) { target.style.setProperty('visibility', 'visible', 'important'); count++; }
    });
    if (!count) return '';
    Array.prototype.slice.call(doc.head.querySelectorAll('style')).reverse().forEach(function (style) {
      body.insertBefore(style.cloneNode(true), body.firstChild);
    });
    body.style.setProperty('background', 'transparent', 'important');
    body.style.width = width + 'px';
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height + '">' +
      '<foreignObject width="100%" height="100%">' + new XMLSerializer().serializeToString(body) +
      '</foreignObject></svg>';
  }

  /* ---------- 导出兜底三件套（2026-11-03）----------
     _coverCropDataUri：把已加载的 <img> 按「显示框尺寸」cover 裁剪成 data URI
       （修 Chrome 光栅化忽略 object-fit 导致的压扁变形；比例基本一致时返回空串不裁）。
     _drawCover：cover 数学直接把图画进成品画布的指定矩形。
     _paintShots：把 shots 记录的照片位置全部用干净 data URI 重画一遍
       （修 Safari/WebKit foreignObject 光栅化整批丢图 —— 只把光栅化当文字底版，
        照片一律以画布 drawImage 为准）。 */
  function _coverCropDataUri(im, boxW, boxH) {
    try {
      var nw = im.naturalWidth, nh = im.naturalHeight;
      if (!nw || !nh || !boxW || !boxH) return '';
      var boxA = boxW / boxH, natA = nw / nh;
      if (Math.abs(boxA - natA) / natA < 0.02) return '';     // 比例基本一致，不裁
      var sw = nw, sh = nh, sx = 0, sy = 0;
      if (natA > boxA) { sw = nh * boxA; sx = (nw - sw) / 2; }
      else { sh = nw / boxA; sy = (nh - sh) / 2; }
      var cv = document.createElement('canvas');
      var ow = Math.min(Math.max(Math.round(boxW * 2), 200), 2000);
      var oh = Math.max(Math.round(ow / boxA), 200);
      cv.width = ow; cv.height = oh;
      var cx = cv.getContext('2d');
      cx.drawImage(im, sx, sy, sw, sh, 0, 0, ow, oh);
      return cv.toDataURL('image/jpeg', 0.9);
    } catch (e) { return ''; }
  }

  function _drawCover(c, image, x, y, w, h) {
    var iw = image.naturalWidth || image.width, ih = image.naturalHeight || image.height;
    if (!iw || !ih || !w || !h) return false;
    var boxA = w / h, natA = iw / ih;
    var sw = iw, sh = ih, sx = 0, sy = 0;
    if (natA > boxA) { sw = ih * boxA; sx = (iw - sw) / 2; }
    else { sh = iw / boxA; sy = (ih - sh) / 2; }
    c.drawImage(image, sx, sy, sw, sh, x, y, w, h);
    return true;
  }

  function _paintShots(c, shots, shotImgs) {
    (shots || []).forEach(function (s, i) {
      var im = shotImgs && shotImgs[i];
      /* 不再被 hasContent 一票否决：照片本来就由这里按坐标画回，
         hasContent 交由 _blankShots 做画完自检即可。 */
      if (!im || s.w < 30 || s.h < 30) return;
      try { _drawCover(c, im, s.x * 2, s.y * 2, s.w * 2, s.h * 2); } catch (e) { /* 单张失败不连坐 */ }
    });
  }

  /* SVG → 可绘制的 Image。
     ★ 关键：onload 只代表 SVG 文档本身加载完，foreignObject 里那些内联图片
       **可能还在解码**；页面不可见（用户切走标签页等下载）时这个窗口会明显拉长。
       以前 onload 后直接 drawImage，等于把「还没解码完的图」画进画布 → 静默全白。
       decode() 会等到图片真正可绘制，再补两帧让合成器跟上。 */
  function _svgToImage(svg) {
    return new Promise(function (res, rej) {
      var im = new Image();
      var settled = false;
      im.onload = function () {
        if (settled) return; settled = true;
        var go = function () {
          requestAnimationFrame(function () {
            requestAnimationFrame(function () { res(im); });
          });
        };
        if (im.decode) im.decode().then(go, go); else go();
      };
      im.onerror = function () {
        if (!settled) { settled = true; rej(new Error('浏览器无法把排版结果转成图片')); }
      };
      im.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    });
  }

  /* 在成品画布上按「照片原本的位置」判定是否整块没画上。
     ★ 2026-11-03 两代误报的血泪与终版方案：
       ① 原判据「5 个 8×8 小块的 R 通道极差 ≤4」——大片天空的照片 R 通道恒定，误报；
       ② 改「亮度+饱和度极差」后依旧误报——8×8 太小，雾面/纯色天空的**局部**就是均匀的
         （实测 img_04 雾山照 8×8 内极差 0~3，但整张照片灰度范围 136）；
       ③ 终版：把成品画布上整个 shot 区域缩画到 24×24 再统计**方差**（与 _hasContent 同口径）
         —— 真照片（哪怕低饱和雾面）缩略后亮度/饱和度方差必 >24；
         没画上的位置 = 整块纯背景色，方差 ≈0。 */
  function _blankShots(c, cv, shots) {
    var s2 = 24, out = 0;
    (shots || []).forEach(function (s) {
      if (!s.hasContent || s.w < 30 || s.h < 30) return;
      try {
        var cv2 = document.createElement('canvas');
        cv2.width = s2; cv2.height = s2;
        var cx = cv2.getContext('2d', { willReadFrequently: true });
        cx.drawImage(cv, s.x * 2, s.y * 2, s.w * 2, s.h * 2, 0, 0, s2, s2);
        var d = cx.getImageData(0, 0, s2, s2).data;
        var n = 0, mean = 0, smean = 0, i, v = 0, sv = 0;
        for (i = 0; i < d.length; i += 4) {
          var r = d[i], g = d[i + 1], b = d[i + 2];
          mean += (r + g + b) / 3;
          smean += Math.max(r, g, b) - Math.min(r, g, b);
          n++;
        }
        mean /= (n || 1); smean /= (n || 1);
        for (i = 0; i < d.length; i += 4) {
          var r2 = d[i], g2 = d[i + 1], b2 = d[i + 2];
          var l2 = (r2 + g2 + b2) / 3, sat2 = Math.max(r2, g2, b2) - Math.min(r2, g2, b2);
          v += (l2 - mean) * (l2 - mean);
          sv += (sat2 - smean) * (sat2 - smean);
        }
        var has = (v / (n || 1)) > 24 || (sv / (n || 1)) > 24;
        if (!has) out++;
      } catch (e) { /* 读不到就当画上了，不误报 */ }
    });
    return out;
  }

  function downloadBlob(blob, name) {
    var u = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = u; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(u); }, 5000);
  }

  /* ---------- 小红书：以「文案」为主，配一张首页海报 + 其它图片自动裁剪 3:4 ---------- */

  function xhsModel(data, ctx) {
    var mm = ctx.mm;
    var titles = (data.titleOptions || []).filter(Boolean);
    var body = data.body || data.summary || '';
    // 真实活动图片：模型指定的顺序优先，再补媒体清单里其余的；去重
    var seen = {}, all = [];
    (data.imageSequence || []).forEach(function (x) {
      var m = mm[x] || Object.keys(mm).map(function (k) { return mm[k]; }).find(function (m) { return m && m.url === x; });
      if (x !== ctx.coverUrl && (!m || (m.kind || 'photo') !== 'photo' || m.usable === false || m.source === 'recap')) return;
      var u = toUrl(x, mm); if (u && !seen[u]) { seen[u] = 1; all.push(u); }
    });
    Object.keys(mm).forEach(function (k) {
      var m = mm[k];
      if (!m || (m.kind || 'photo') !== 'photo' || m.usable === false || m.source === 'recap') return;
      var u = m.url ? abs(mediaUrl(m.url)) : '';
      if (u && !seen[u]) { seen[u] = 1; all.push(u); }
    });
    var hero = all[0] || ctx.coverUrl || '';   // 首页海报所用的那张
    // 「其它图片」：自动裁成 3:4 当作九宫格配图；上限 11 张（首页海报 + 11 ≈ 小红书单篇 12 图）
    var photos = all.slice(1, 12);
    /* ★ 2026-11-04 用户实锤「排得太差」：标签行里同一个话题出现两次（#icebreaker ×2）。
       这里做三件事：① 去重（大小写无关，保留首次出现的顺序）；
       ② 正文里已经写过的 # 话题不再进标签行（避免正文与标签行重复）；
       ③ 标签最多 8 个 —— 小红书标签堆到 10 个以上反而像 spam。 */
    var inBody = {};
    String(body || '').replace(/#[^\s#，。！？、]{1,20}/g, function (m) {
      inBody[m.slice(1).toLowerCase()] = 1; return m;
    });
    var seenTag = {}, tags = [];
    (data.tags || []).forEach(function (t) {
      var s = String(t || '').replace(/^#/, '').trim();
      if (!s) return;
      var k = s.toLowerCase();
      if (seenTag[k] || inBody[k]) return;
      seenTag[k] = 1; tags.push(s);
    });
    if (tags.length > 8) tags = tags.slice(0, 8);
    return {
      title: titles[0] || data.title || ctx.title,
      titleOptions: titles,
      hook: data.hook || '',
      body: body,
      tags: tags,
      heroSrc: hero,
      photos: photos
    };
  }

  // 首页海报：hero 背景 + 渐变压暗 + 标题/Hook（小红书风，1080×1440）
  function xhsPosterCanvas(model, ctx, heroImg) {
    var W = 1080, H = 1440, PAD = 78;
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    var c = cv.getContext('2d');
    if (heroImg) { c.save(); drawCover.call(c, heroImg, 0, 0, W, H); c.restore(); }
    else {
      var base = c.createLinearGradient(0, 0, W, H);
      base.addColorStop(0, '#e98a8a'); base.addColorStop(1, '#f4b9a0');
      c.fillStyle = base; c.fillRect(0, 0, W, H);
    }
    var g = c.createLinearGradient(0, H * 0.28, 0, H);
    g.addColorStop(0, 'rgba(20,10,12,0)');
    g.addColorStop(0.5, 'rgba(20,10,12,.5)');
    g.addColorStop(1, 'rgba(20,10,12,.9)');
    c.fillStyle = g; c.fillRect(0, 0, W, H);
    // 顶部小红书风小标签
    c.fillStyle = 'rgba(255,255,255,.95)';
    rr(c, PAD, 70, 306, 58, 29); c.fill();
    c.fillStyle = '#ff2741';
    c.font = '800 26px ' + FONT;
    c.fillText('小红书 · 活动招募', PAD + 32, 109);
    // 标题
    c.fillStyle = '#fff';
    c.font = '800 66px ' + FONT;
    var lines = wrap(c, model.title, W - PAD * 2).slice(0, 3);
    var lh = 88, y = H - PAD - 70 - (lines.length - 1) * lh - (model.hook ? 120 : 0);
    y = Math.max(y, 470);
    drawLines(c, lines, PAD, y, lh, 3);
    // hook
    if (model.hook) {
      c.fillStyle = 'rgba(255,255,255,.92)';
      c.font = '400 33px ' + FONT;
      drawLines(c, wrap(c, model.hook, W - PAD * 2).slice(0, 2), PAD, y + lines.length * lh + 26, 50, 2);
    }
    // 底部标签
    if (model.tags && model.tags.length) {
      c.fillStyle = 'rgba(255,255,255,.88)';
      c.font = '400 30px ' + FONT;
      c.fillText('#' + model.tags.slice(0, 4).join('  #'), PAD, H - 56);
    }
    return cv;
  }

  // 把任意真实照片居中裁剪为 3:4（小红书发布比例）
  function cropXhs(img) {
    var W = 1080, H = 1440;
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    var c = cv.getContext('2d');
    c.fillStyle = '#ececec'; c.fillRect(0, 0, W, H);
    drawCover.call(c, img, 0, 0, W, H);
    return cv;
  }

  async function paintXhs(model, ctx, stage) {
    var heroImg = model.heroSrc ? await loadImgSafe(model.heroSrc) : null;
    var poster = xhsPosterCanvas(model, ctx, heroImg);
    var photos = [];
    for (var i = 0; i < model.photos.length; i++) {
      var im = await loadImgSafe(model.photos[i]);
      photos.push(im ? cropXhs(im) : null);
    }
    var bodyParas = paras(model.body);
    var altTitles = model.titleOptions.slice(1, 4);
    stage.innerHTML =
      '<div class="ch-xhs">' +
        '<section class="ch-xhs-copy">' +
          '<div class="ch-xhs-sec-h">文案 · 直接复制粘贴到小红书</div>' +
          '<h3 class="ch-xhs-title">' + esc(model.title) + '</h3>' +
          (model.hook ? '<div class="ch-xhs-hook">' + esc(model.hook) + '</div>' : '') +
          '<div class="ch-xhs-body">' + (bodyParas.length ? bodyParas.map(function (p) { return '<p>' + esc(p) + '</p>'; }).join('') : '<p class="muted">（模型未返回正文）</p>') + '</div>' +
          (model.tags.length ? '<div class="ch-xhs-tags">' + model.tags.map(function (t) { return '<span>#' + esc(t) + '</span>'; }).join('') + '</div>' : '') +
          (altTitles.length ? '<div class="ch-xhs-alt">备选标题：' + altTitles.map(function (t) { return esc(t); }).join(' / ') + '</div>' : '') +
        '</section>' +
        '<section class="ch-xhs-poster-sec">' +
          '<div class="ch-xhs-sec-h">首页海报（1 张 · 发布时拖到第 1 位）</div>' +
          '<div class="ch-xhs-poster-hold"></div>' +
        '</section>' +
        '<section class="ch-xhs-gallery-sec">' +
          '<div class="ch-xhs-sec-h">配图（' + photos.length + ' 张 · 已自动裁剪为 3:4，按顺序补到海报后面）</div>' +
          '<div class="ch-xhs-gallery"></div>' +
        '</section>' +
      '</div>';
    stage.querySelector('.ch-xhs-poster-hold').appendChild(poster);
    var gal = stage.querySelector('.ch-xhs-gallery');
    photos.forEach(function (cv, idx) {
      if (!cv) return;
      var f = document.createElement('figure');
      f.className = 'ch-xhs-photo';
      f.appendChild(cv);
      var cap = document.createElement('figcaption');
      cap.textContent = '第 ' + (idx + 2) + ' 张';
      f.appendChild(cap);
      gal.appendChild(f);
    });
    if (!photos.length) {
      var empty = document.createElement('div');
      empty.className = 'ch-xhs-empty';
      empty.textContent = '本活动暂无其它照片，单独发首页海报即可。';
      gal.appendChild(empty);
    }
    return { poster: poster, photos: photos.filter(Boolean) };
  }

  /* ---------- 海报（canvas 1080×1440）----------
     ★ 2026-10-09 重做：用户拿 4 张参考图来，说旧版「一点设计感都没有」。
     旧版不是配色问题，是**没有设计系统**：没有品牌带、整张只有一个字号层级、
     事实平铺成一行灰字、二维码一个大白方块居中比正文还抢眼，而且三套版式是**随机**套的
     —— 老板抽到最素的那套，就是那张「标题 + 灰字 + 二维码」。
     新版按参考图的结构重建一套版式语言：
       品牌带（logo + 俱乐部名 + 口号 + 日期胶囊）→ 主标题（大字号 / 紧凑行距）
       → 副标（一句主张）→ 卖点条（白胶囊，参考图里最"设计"的一笔）→ 实拍主视觉（全幅）
       → 价格块（¥ 放大 + 单位缩小）→ 二维码卡（码 + 「扫码报名」+ 一圈细描边）
       → 底部图标行（地点 / 名额 / 日期 / 卖点各配一个线性图标，像参考图 1 底部那四格）。
     配色从主视觉照片推出来（秋色林场→琥珀色带、草甸雪山→墨绿色带），每一场活动自带一套色。
     三套版式都按这套语言实现：画报 / 深色杂志 / 极简影像。默认第一套，
     「换一套版式」循环切换（**不再随机** —— 随机等于把最差的一套推给老板）。 */

  /* --- 海报共用：颜色 / 字距 / 图标 / 日期 --- */

  function _rgb2hsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, h = 0, s = 0, l = (mx + mn) / 2;
    if (d) {
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      if (mx === r) h = ((g - b) / d + (g < b ? 6 : 0));
      else if (mx === g) h = ((b - r) / d + 2);
      else h = ((r - g) / d + 4);
      h /= 6;
    }
    return [h, s, l];
  }
  function _hsl2rgb(h, s, l) {
    function f(p, q, t) {
      if (t < 0) t += 1; if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    }
    if (s <= 0) { var v = Math.round(l * 255); return [v, v, v]; }
    var q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
    return [Math.round(f(p, q, h + 1 / 3) * 255), Math.round(f(p, q, h) * 255), Math.round(f(p, q, h - 1 / 3) * 255)];
  }
  function _rgba(cl, a) { return 'rgba(' + cl[0] + ',' + cl[1] + ',' + cl[2] + ',' + a + ')'; }
  /* 圆角矩形路径（不依赖 ctx.roundRect，兼容旧内核） */
  function rrPath(x, px, py, pw, ph, pr) {
    pr = Math.min(pr, pw / 2, ph / 2);
    x.beginPath();
    x.moveTo(px + pr, py);
    x.arcTo(px + pw, py, px + pw, py + ph, pr);
    x.arcTo(px + pw, py + ph, px, py + ph, pr);
    x.arcTo(px, py + ph, px, py, pr);
    x.arcTo(px, py, px + pw, py, pr);
    x.closePath();
  }
  function _rgb(cl) { return 'rgb(' + cl[0] + ',' + cl[1] + ',' + cl[2] + ')'; }

  var _TONE_DEFAULT = { deep: [16, 30, 25], deep2: [8, 16, 13], mid: [30, 58, 46], light: [44, 82, 66], accent: [226, 240, 232] };

  /* 从主视觉推「本场专属」配色。照片本身偏灰时强制补一点饱和度 ——
     不给色会让整张海报发灰，那正是「没设计感」的样子。 */
  function toneFromImage(img) {
    if (!img) return _TONE_DEFAULT;
    try {
      var S = 32, cv = document.createElement('canvas'), i;
      cv.width = S; cv.height = S;
      var x = cv.getContext('2d');
      x.drawImage(img, 0, 0, S, S);
      var d = x.getImageData(0, 0, S, S).data, r = 0, g = 0, b = 0, n = 0;
      for (i = 0; i < d.length; i += 4) { if (d[i + 3] < 8) continue; r += d[i]; g += d[i + 1]; b += d[i + 2]; n++; }
      if (!n) return _TONE_DEFAULT;
      var hsl = _rgb2hsl(r / n, g / n, b / n), h = hsl[0];
      var s = Math.min(0.55, Math.max(hsl[1], 0.30));
      return {
        deep: _hsl2rgb(h, s, 0.13), deep2: _hsl2rgb(h, s, 0.06), mid: _hsl2rgb(h, s, 0.25),
        light: _hsl2rgb(h, s, 0.36), accent: _hsl2rgb(h, Math.min(0.72, s * 1.6), 0.75)
      };
    } catch (e) { return _TONE_DEFAULT; }   /* 跨域图 getImageData 会抛 → 退回默认色 */
  }

  /* 字距文本：letterSpacing 只有很新的内核支持，逐字画最稳。
     海报的「设计感」有一大半来自字距 —— 参考图 3 / 4 的小字全是拉开的。 */
  function lsWidth(c, text, sp) {
    text = String(text == null ? '' : text);
    var w = 0, i;
    for (i = 0; i < text.length; i++) w += c.measureText(text[i]).width + (sp || 0);
    return Math.max(0, w - (sp || 0));
  }
  function lsText(c, text, x, y, sp, align) {
    text = String(text == null ? '' : text); sp = sp || 0;
    var w = lsWidth(c, text, sp), sx = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x, i, ch;
    for (i = 0; i < text.length; i++) { ch = text[i]; c.fillText(ch, sx, y); sx += c.measureText(ch).width + sp; }
    return w;
  }
  function lsFit(c, text, maxW, sp) {
    text = String(text == null ? '' : text);
    if (lsWidth(c, text, sp) <= maxW) return text;
    var out = '', i;
    for (i = 0; i < text.length; i++) {
      if (lsWidth(c, out + text[i] + '…', sp) > maxW) break;
      out += text[i];
    }
    return out ? out + '…' : text.slice(0, 1);
  }

  /* --- 线性图标（纯 canvas 路径，不依赖字体 / 外链） --- */
  function _pen(c, x, y, u) {
    function X(a) { return x + a * u; }
    function Y(b) { return y + b * u; }
    return {
      m: function (a, b) { c.moveTo(X(a), Y(b)); },
      l: function (a, b) { c.lineTo(X(a), Y(b)); },
      q: function (a, b, a2, b2) { c.quadraticCurveTo(X(a), Y(b), X(a2), Y(b2)); },
      circle: function (a, b, r) { c.moveTo(X(a) + r * u, Y(b)); c.arc(X(a), Y(b), r * u, 0, Math.PI * 2); },
      arc: function (a, b, r, s0, s1) { c.moveTo(X(a) + Math.cos(s0) * r * u, Y(b) + Math.sin(s0) * r * u); c.arc(X(a), Y(b), r * u, s0, s1); },
      rect: function (a, b, w, h, cr) {
        if (!cr) { c.moveTo(X(a), Y(b)); c.lineTo(X(a + w), Y(b)); c.lineTo(X(a + w), Y(b + h)); c.lineTo(X(a), Y(b + h)); c.closePath(); return; }
        c.moveTo(X(a + cr), Y(b));
        c.arcTo(X(a + w), Y(b), X(a + w), Y(b + h), cr * u);
        c.arcTo(X(a + w), Y(b + h), X(a), Y(b + h), cr * u);
        c.arcTo(X(a), Y(b + h), X(a), Y(b), cr * u);
        c.arcTo(X(a), Y(b), X(a + w), Y(b), cr * u);
        c.closePath();
      },
      close: function () { c.closePath(); }
    };
  }
  var ICONS = {
    camera: function (c, P) { P.rect(2.4, 7, 19.2, 13, 2.6); P.circle(12, 13.6, 3.7); P.m(8.4, 7); P.l(9.7, 3.9); P.l(14.3, 3.9); P.l(15.6, 7); },
    tent: function (c, P) { P.m(12, 3.4); P.l(21.4, 20.4); P.l(2.6, 20.4); P.close(); P.m(9.2, 20.4); P.l(12, 11.4); P.l(14.8, 20.4); },
    bed: function (c, P) { P.rect(2.6, 12.4, 18.8, 7.2, 1.6); P.m(2.6, 19.6); P.l(2.6, 8.4); P.m(5.4, 12.4); P.l(5.4, 9.6); P.l(16, 9.6); P.l(16, 12.4); },
    car: function (c, P) { P.m(2.6, 15.4); P.l(4.6, 9.4); P.l(19.4, 9.4); P.l(21.4, 15.4); P.m(2.6, 15.4); P.l(21.4, 15.4); P.circle(7, 17.6, 2.2); P.circle(17, 17.6, 2.2); },
    snow: function (c, P) { P.m(12, 3.2); P.l(12, 20.8); P.m(4.4, 7.6); P.l(19.6, 16.4); P.m(19.6, 7.6); P.l(4.4, 16.4); P.m(9.4, 5.6); P.l(12, 7.8); P.l(14.6, 5.6); P.m(9.4, 18.4); P.l(12, 16.2); P.l(14.6, 18.4); },
    leaf: function (c, P) { P.m(4.4, 19.6); P.q(4.6, 9.4, 12.2, 3.6); P.q(19.8, 9.6, 19.6, 19.6); P.q(12, 22.4, 4.4, 19.6); P.close(); P.m(6.6, 17.8); P.l(15.4, 8.6); },
    mountain: function (c, P) { P.m(2.2, 20.2); P.l(9, 5.8); P.l(13.6, 14.4); P.l(16.8, 9.2); P.l(21.8, 20.2); P.close(); },
    users: function (c, P) { P.circle(9.2, 8.2, 3.6); P.arc(9.2, 20.2, 6.6, Math.PI, Math.PI * 2); P.circle(17.6, 9.4, 2.8); P.arc(17.6, 20.2, 5, Math.PI, Math.PI * 2); },
    calendar: function (c, P) { P.rect(3, 5.4, 18, 15.6, 2.2); P.m(3, 10.4); P.l(21, 10.4); P.m(8, 3.2); P.l(8, 7.2); P.m(16, 3.2); P.l(16, 7.2); P.m(7.4, 14.6); P.l(9.6, 14.6); P.m(13.4, 14.6); P.l(15.6, 14.6); },
    bowl: function (c, P) { P.arc(12, 13.4, 7.8, 0, Math.PI); P.m(4.2, 13.4); P.l(19.8, 13.4); P.circle(12, 5.2, 1.5); },
    shield: function (c, P) { P.m(12, 3); P.l(19.4, 6); P.l(19.4, 12); P.q(19.4, 18.4, 12, 21.2); P.q(4.6, 18.4, 4.6, 12); P.l(4.6, 6); P.close(); },
    flag: function (c, P) { P.m(5.6, 3); P.l(5.6, 21.4); P.m(5.6, 4.8); P.l(19.4, 8); P.l(5.6, 11.4); },
    coin: function (c, P) { P.circle(12, 12, 8.8); P.m(14.8, 8.8); P.l(10.6, 8.8); P.l(10.6, 15.2); P.m(9.4, 12.2); P.l(14.6, 12.2); },
    star: function (c, P) { var r1 = 8.8, r2 = 3.7, i, a, rr2; for (i = 0; i < 10; i++) { a = -Math.PI / 2 + i * Math.PI / 5; rr2 = (i % 2) ? r2 : r1; if (i === 0) P.m(12 + Math.cos(a) * rr2, 12 + Math.sin(a) * rr2); else P.l(12 + Math.cos(a) * rr2, 12 + Math.sin(a) * rr2); } P.close(); },
    pin: function (c, P) { P.arc(12, 9.6, 6.4, Math.PI, Math.PI * 2); P.m(5.6, 9.6); P.l(12, 21.4); P.l(18.4, 9.6); P.circle(12, 9.6, 2.2); },
    clock: function (c, P) { P.circle(12, 12, 8.8); P.m(12, 6.8); P.l(12, 12); P.l(16.2, 14.2); }
  };
  function drawIcon(c, name, cx, cy, size, color, lw) {
    var u = size / 24;
    c.save();
    c.strokeStyle = color; c.lineWidth = lw || Math.max(1.3, size / 13);
    c.lineCap = 'round'; c.lineJoin = 'round';
    c.beginPath();
    (ICONS[name] || ICONS.star)(c, _pen(c, cx - 12 * u, cy - 12 * u, u));
    c.stroke();
    c.restore();
  }
  /* 卖点 → 图标的判据表。顺序即优先级：先把「酒店 / 相机 / 帐篷」这类硬特征挑出来，
     再谈宽泛的「山 / 林 / 景」（否则「3 晚高档酒店」会被「山」类规则抢走）。 */
  var ICON_RULES = [
    [/摄影|拍摄|跟拍|写真|航拍|无人机|相机|机位|大片/, 'camera'],
    [/露营|帐篷|营地|天幕|扎营/, 'tent'],
    [/酒店|住宿|民宿|客栈|入住|床位|温泉|房间/, 'bed'],
    [/包车|车|交通|自驾|接送|高铁|航班/, 'car'],
    [/雪|冰川|海拔|高原/, 'snow'],
    [/红叶|秋|花|草原|森林|云海|湖|风景|景观|自然/, 'leaf'],
    [/徒步|登山|穿越|溯溪|攀|山|野径|林间/, 'mountain'],
    [/名额|人数|限|小团|成行|队伍|向导|领队|老师|团体/, 'users'],
    [/天|晚|日期|出发|时间|行程|期数/, 'calendar'],
    [/餐|食|火锅|茶|美食|宴/, 'bowl'],
    [/保险|安全|保障|应急|医疗|救援/, 'shield'],
    [/证书|纪念|礼|赠|结营|手作|唐卡|绘制|课程/, 'flag'],
    [/费用|价|元|¥|包含/, 'coin'],
    [/独家|专属|优选|品质|升级|全含|无购物|纯玩|不赶|守护/, 'star']
  ];
  function iconFor(text, idx) {
    var t = String(text == null ? '' : text), i;
    for (i = 0; i < ICON_RULES.length; i++) if (ICON_RULES[i][0].test(t)) return ICON_RULES[i][1];
    return ['star', 'mountain', 'leaf', 'calendar'][(idx || 0) % 4];
  }

  /* 团期日期 → 海报上的短标签。资料里常见占位符（"2026 / 10.xx — 10.xx"）：
     把它印在海报上比不印更糟，所以占位符直接判空。 */
  function _dateParts(s) {
    s = String(s == null ? '' : s);
    if (!s || /[xX]{2}|[?？]/.test(s)) return [];
    var out = [], m, re = /(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})/g;
    while ((m = re.exec(s))) out.push({ m: +m[2], d: +m[3] });
    if (!out.length) {
      re = /(\d{1,2})\s*月\s*(\d{1,2})/g;
      while ((m = re.exec(s))) out.push({ m: +m[1], d: +m[2] });
    }
    return out;
  }
  function dateLabel(s) {
    var p = _dateParts(s);
    if (!p.length) return '';
    var f = function (x) { return x.m + '.' + x.d; };
    return p.length === 1 ? f(p[0]) : (f(p[0]) + ' — ' + f(p[p.length - 1]));
  }

  async function paintPoster(data, ctx, host) {
    var W = 1080, H = 1440, PAD = 72, CW = W - PAD * 2;
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    var c = cv.getContext('2d');

    /* 主视觉：AI 指定的 ref 优先，其次活动封面，最后清单里第一张真实照片。
       俱乐部端一律走带鉴权的 /api/club 媒体路由（草稿活动的公开代理按设计 404）。 */
    var pref = (data.preferredMediaRefs || [])[0];
    var bgUrl = (pref && ctx.mm[pref] && ctx.mm[pref].url) ? ctx.mediaClubUrl(pref)
      : (ctx.coverUrl || ctx.firstPhotoClubUrl());
    var img = bgUrl ? await loadImgSafe(bgUrl) : null;
    var logoImg = ctx.logoUrl ? await loadImgSafe(ctx.logoUrl) : null;
    var tone = toneFromImage(img);
    var ACCENT = data.style === 'outdoor-minimal-v1' ? '#D8AD72' : _rgb(tone.accent), INK = _rgb(tone.deep2);

    /* ---- 文案：只用真实资料，缺就少画一块，不编 ---- */
    function factText(x) {
      if (x == null) return '';
      if (typeof x === 'string') return x.trim();
      if (typeof x === 'number') return String(x);
      if (typeof x === 'object') {
        var a = x.label || x.name || x.title || '', b = (x.value != null ? x.value : (x.text || x.content || ''));
        return String(a && b ? (a + ' ' + b) : (a || b || '')).trim();
      }
      return '';
    }
    function usable(t, maxLen) {
      t = String(t == null ? '' : t).trim();
      if (!t || t.length > (maxLen || 26)) return '';
      if (/成本|毛利|进价|询价|档期/.test(t)) return '';   /* 内部口径的词一律不上海报 */
      return t;
    }
    var headline = usable(data.headline || data.title || ctx.title, 40) || String(ctx.title || '');
    var sub = usable(data.subheadline || data.subtitle || data.summary, 40);
    var points = [];
    (data.sellingPoints || data.highlights || []).forEach(function (x) {
      var t = usable(factText(x), 24);
      if (t && points.indexOf(t) < 0) points.push(t);
    });
    var facts = [];
    (data.facts || data.info || []).forEach(function (x) {
      var t = usable(factText(x), 20);
      /* 价格 / 名额 / 地点已经在版式里各有一处，别再用 facts 复制一遍 */
      if (!t || /[¥￥]|元\s*\/\s*人|^\s*限\s*\d+|^\s*\d+\s*人\s*$/.test(t)) return;
      if (facts.indexOf(t) < 0) facts.push(t);
    });
    var loc = usable(ctx.location, 24);
    var price = (ctx.price === 0 || ctx.price) ? String(ctx.price).replace(/\.0$/, '') : '';
    var cap = ctx.capacity ? String(ctx.capacity) : '';
    var dlab = dateLabel(ctx.date);
    var cta = usable(data.cta, 10);
    var qrLabel = cta || '扫码报名';

    /* 二维码：真实可扫，指向该活动的 C 端报名页（与「分享活动」弹窗同一条链接）。 */
    var qr = null;
    try {
      if (window.ClubOSQR && window.ClubOSQR.draw) {
        qr = document.createElement('canvas');
        window.ClubOSQR.draw(qr, location.origin + '/web?club_id=' + ctx.club + '&activity=' + ctx.activityId);
        if (!qr.width) qr = null;
      }
    } catch (e) { qr = null; }

    function shadow(on) {
      if (on) { c.shadowColor = 'rgba(0,0,0,.42)'; c.shadowBlur = 18; c.shadowOffsetY = 3; }
      else { c.shadowColor = 'transparent'; c.shadowBlur = 0; c.shadowOffsetY = 0; }
    }

    /* 二维码图章：白色圆角贴纸紧贴码面（静区≈2 模块，保证扫得动）+ 柔和投影，
       码中心嵌品牌 logo（抠底原色版，直径 ≤ 码宽 ~1/5，QR 纠错可承受），
       外圈确定性装饰点，码下白字标签。
       ★ 大白圆盘废弃 —— 圆包方天然浪费 27% 面积，白得像补丁（2026-10-09 用户反馈「不要白底」）；
         圆角贴纸是「设计过的贴纸」而不是「打的补丁」。 */
    function qrStamp(cx, cy, size) {
      if (!qr) return null;
      size = size || 170;
      var quiet = Math.max(12, Math.round(size * 0.085));
      var side = size + quiet * 2;
      var rad = data.style === 'outdoor-minimal-v1' ? 2 : Math.round(side * 0.22);
      c.save();
      c.shadowColor = 'rgba(0,0,0,.4)'; c.shadowBlur = data.style === 'outdoor-minimal-v1' ? 0 : 24; c.shadowOffsetY = 6;
      c.fillStyle = '#fff';
      rrPath(c, cx - side / 2, cy - side / 2, side, side, rad);
      c.fill();
      c.restore();
      /* 装饰点环：固定角度序列（不随机 —— 随机会一张一个样），大小/颜色疏密相间 */
      var dotAng = data.style === 'outdoor-minimal-v1' ? [] : [15, 55, 100, 150, 200, 235, 285, 330];
      var dotR = Math.round(side * 0.72);
      for (var di = 0; di < dotAng.length; di++) {
        var a = dotAng[di] * Math.PI / 180, dr2 = dotR + 12 + (di % 2) * 6;
        c.fillStyle = di % 2 ? _rgba(tone.accent, .9) : 'rgba(255,255,255,.85)';
        c.beginPath();
        c.arc(cx + Math.cos(a) * dr2, cy + Math.sin(a) * dr2, di % 2 ? 3.5 : 6, 0, Math.PI * 2);
        c.fill();
      }
      var qs2 = size;
      c.save();
      c.imageSmoothingEnabled = false;               /* 码模块要硬边，平滑会糊掉解码 */
      c.drawImage(qr, cx - qs2 / 2, cy - qs2 / 2, qs2, qs2);
      c.restore();
      /* 中心 logo：用抠底原色版（logoColor），直径 ≤ 码宽 ~1/5（实测 39% 扫不出、23% 可扫）。
         ★ 不再画原始 logoImg —— 带底色的 logo 缩成一颗色点，像补丁（2026-10-09 用户反馈）。
         白色贴纸底上白描边没有意义，去掉。 */
      if (logoColor) {
        var mr = Math.round(qs2 * 0.1) + 2;
        c.save();
        c.beginPath(); c.arc(cx, cy, mr, 0, Math.PI * 2); c.clip();
        var iw = logoColor.width || 1, ih = logoColor.height || 1;
        var fit = Math.max(mr * 2 / iw, mr * 2 / ih);
        c.drawImage(logoColor, cx - iw * fit / 2, cy - ih * fit / 2, iw * fit, ih * fit);
        c.restore();
      }
      /* 码下标签：白字 + 投影直接落在海报上，无卡片 */
      c.fillStyle = '#fff';
      c.font = '700 25px ' + FONT;
      shadow(true);
      lsText(c, qrLabel, cx, cy + side / 2 + 34, 2.2, 'center');
      shadow(false);
      return { x: cx - side / 2, y: cy - side / 2, w: side, h: side + 44 };
    }

    /* 品牌标识：logo 直接融入海报，不套任何容器（2026-10-09 用户反馈两轮）。
       处理管线：① 取四角+四边中点颜色，若为「均匀底色」（白底/浅蓝底都算 ——
       远拓新 logo 是浅蓝实底，只认近白会漏）则整片抠成透明并羽化边缘；
       ② 抠底后剩余笔迹偏暗 → 染白版（mono，适配深色海报），原色版（color）留给
       白色贴纸里的二维码中心章；③ 染白版画上海报时带轻投影。 */
    function logoStamp(img) {
      var iw = img.naturalWidth || img.width || 1, ih = img.naturalHeight || img.height || 1;
      if (iw < 2 || ih < 2) return null;
      var cv = document.createElement('canvas'); cv.width = iw; cv.height = ih;
      var x = cv.getContext('2d');
      if (!x) return null;
      x.drawImage(img, 0, 0);
      var d;
      try { d = x.getImageData(0, 0, iw, ih); } catch (e) { return null; }
      var p = d.data, n = iw * ih;
      /* ① 底色采样：四角 + 四边中点，取均值；散度小 = 均匀底色（不限白色） */
      var pts = [[0, 0], [iw - 1, 0], [0, ih - 1], [iw - 1, ih - 1],
                 [Math.floor(iw / 2), 0], [Math.floor(iw / 2), ih - 1], [0, Math.floor(ih / 2)], [iw - 1, Math.floor(ih / 2)]];
      var sr = 0, sg = 0, sb = 0;
      pts.forEach(function (q) { var o = (q[1] * iw + q[0]) * 4; sr += p[o]; sg += p[o + 1]; sb += p[o + 2]; });
      sr /= pts.length; sg /= pts.length; sb /= pts.length;
      var spread = 0;
      pts.forEach(function (q) {
        var o = (q[1] * iw + q[0]) * 4;
        spread = Math.max(spread, Math.abs(p[o] - sr) + Math.abs(p[o + 1] - sg) + Math.abs(p[o + 2] - sb));
      });
      var uniform = spread < 110;   /* 8 个采样点颜色接近 → 整图有均匀底 */
      var opaque = 0, sumLum = 0;
      for (var i = 0; i < p.length; i += 4) {
        if (uniform) {
          var dr = p[i] - sr, dg = p[i + 1] - sg, db = p[i + 2] - sb;
          var dist = Math.sqrt(dr * dr + dg * dg + db * db);
          if (dist < 34) { p[i + 3] = 0; continue; }                       /* 底色 → 透明 */
          if (dist < 100) { p[i + 3] = Math.round(p[i + 3] * dist / 100); } /* 边缘抗锯齿羽化 */
        }
        if (p[i + 3] > 40) { opaque++; sumLum += (p[i] + p[i + 1] + p[i + 2]) / 3; }
      }
      /* 抠完几乎不剩东西（整图就是一块底色）→ 放弃处理，原样返回 */
      if (opaque < n * 0.02) return null;
      var color = cv;
      x.putImageData(d, 0, 0);
      /* ② 剩余笔迹偏暗 = 深色字标 → 染白适配深色海报（用户允许改 logo 颜色） */
      var mono;
      if (opaque > 0 && sumLum / opaque < 150) {
        var mv = document.createElement('canvas'); mv.width = iw; mv.height = ih;
        var mx = mv.getContext('2d');
        if (!mx) return { mono: color, color: color };
        mx.putImageData(d, 0, 0);
        var md = mx.getImageData(0, 0, iw, ih), mp = md.data;
        for (i = 0; i < mp.length; i += 4) {
          if (!mp[i + 3]) continue;
          mp[i] = 255; mp[i + 1] = 255; mp[i + 2] = 255;
        }
        mx.putImageData(md, 0, 0);
        mono = mv;
      } else {
        mono = color;   /* 本身偏亮/彩色笔迹，原色即可 */
      }
      return { mono: mono, color: color };
    }
    var logoMono = logoImg ? logoStamp(logoImg) : null;
    var logoColor = logoMono ? logoMono.color : null;
    if (logoMono) logoMono = logoMono.mono;
    function logoW(h) {
      if (!logoMono) return 0;
      var iw = logoMono.width, ih = logoMono.height;
      return Math.max(1, Math.round(ih ? h * (iw / ih) : h));
    }
    function drawLogoBlend(x, y, h) {
      if (!logoMono) return null;
      var w = logoW(h);
      c.save();
      c.shadowColor = 'rgba(0,0,0,.35)'; c.shadowBlur = 16; c.shadowOffsetY = 3;
      c.drawImage(logoMono, x, y, w, h);
      c.restore();
      return { x: x, y: y, w: w, h: h };
    }
    /* 品牌带：有 logo 只出 logo（不叠加俱乐部名 —— 用户 2026-10-09 定）；
       没设 logo 才退回「俱乐部名 + 口号」纯文字带。返回下一行的 y。 */
    function brandBlock(mode, yTop, h, small) {
      var name = ctx.brandName, slo = ctx.brandSlogan;
      if (logoMono) {
        if (mode === 'center') { drawLogoBlend(Math.round(W / 2 - logoW(h) / 2), yTop, h); return yTop + h; }
        drawLogoBlend(PAD, yTop, h);
        return yTop + h;
      }
      if (mode === 'center') {
        var x = yTop, cx = W / 2;
        if (name) { c.fillStyle = '#fff'; c.font = (small ? '700 32px ' : '800 38px ') + FONT; lsText(c, name, cx, x + 30, 4, 'center'); x += 30; }
        if (slo) { c.fillStyle = 'rgba(255,255,255,.7)'; c.font = '500 19px ' + FONT; lsText(c, slo, cx, x + 30, 2.4, 'center'); x += 30; }
        return x;
      }
      var lx = PAD;
      var ny = yTop + ((slo && name) ? 32 : h / 2 + 13);
      if (name) { c.fillStyle = '#fff'; c.font = (small ? '700 32px ' : '800 38px ') + FONT; lsText(c, name, lx, ny, 3, 'left'); }
      if (slo) { c.fillStyle = 'rgba(255,255,255,.72)'; c.font = '500 20px ' + FONT; lsText(c, slo, lx + 1, ny + 30, 2.2, 'left'); }
      return yTop + h;
    }

    /* 主视觉铺满整张（照片比例不合时居中裁切，不拉变形） */
    function drawPhotoFull() {
      if (img) { c.save(); drawCover.call(c, img, 0, 0, W, H); c.restore(); return; }
      var g = c.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, _rgb(tone.mid)); g.addColorStop(1, _rgb(tone.deep2));
      c.fillStyle = g; c.fillRect(0, 0, W, H);
    }

    /* 底部图标行：卖点用剩的 + 真实事实（地点 / 名额 / 日期），互不重复。 */
    function rowItems() {
      var out = [], used = {};
      function push(t, ic) {
        t = usable(t, 14); if (!t || used[t]) return;
        used[t] = 1; out.push({ text: t, icon: ic || iconFor(t, out.length) });
      }
      points.slice(3).forEach(function (t) { push(t); });
      if (loc) push(loc, 'pin');
      if (cap) push('限 ' + cap + ' 人', 'users');
      if (dlab) push(dlab, 'calendar');
      facts.forEach(function (t) { if (out.length < 4) push(t); });
      return out.slice(0, 4);
    }
    function drawRow(items, topY, h) {
      if (!items.length) return;
      var cell = CW / items.length;
      for (var i = 0; i < items.length; i++) {
        var cx = PAD + cell * (i + 0.5);
        drawIcon(c, items[i].icon, cx, topY + h * 0.34, 30, _rgba(tone.accent, .95), 2);
        c.font = '500 23px ' + FONT;
        c.fillStyle = 'rgba(255,255,255,.92)';
        lsText(c, lsFit(c, items[i].text, cell - 26, 1), cx, topY + h * 0.72, 1, 'center');
      }
    }

    /* 版式：默认第一套；「换一套版式」循环切换（不随机 —— 随机会把最差的一套推给老板） */
    /* 版式家族（2026-10-30 扩展）：原来只有 magazine/noir/film 三套，
       老板给了 4 张参考海报，按「活动气质」再补 4 套：
         invite   电影感邀请函（全幅压暗 + 居中细线 + 仪式感衬线大标题）
         cosmic   极简宇宙（深空渐变 + 星点 + 极简居中白字）
         forest   暗色雾林（墨绿雾气 + 横向雾带 + 左下大标题）
         routemap 纪实路线图（牛皮纸网格 + 虚线路线 + 路点 + 纪实图版） */
    var FAM = data.style === 'outdoor-minimal-v1' ? ['film', 'routemap'] : ['magazine', 'invite', 'cosmic', 'forest', 'routemap', 'noir', 'film'];
    var ALIAS = { editor: 'magazine', pictorial: 'film', minimal: 'film', poster: 'magazine',
      invitation: 'invite', invite: 'invite', cinematic: 'invite', filmic: 'invite',
      cosmic: 'cosmic', space: 'cosmic', universe: 'cosmic', star: 'cosmic',
      forest: 'forest', mist: 'forest', misty: 'forest', fog: 'forest', woods: 'forest',
      route: 'routemap', routemap: 'routemap', map: 'routemap', documentary: 'routemap' };
    var forced = window.__posterFamily || data.posterFamily;
    if (ALIAS[forced]) forced = ALIAS[forced];
    var family = FAM.indexOf(forced) >= 0 ? forced : (data.style === 'outdoor-minimal-v1' ? 'film' : FAM[_posterCycle++ % FAM.length]);

    /* ═══ 版式一 magazine：画报（顶部色带 + 全幅主视觉 + 底部信息带，参考图 1/2）═══ */
    if (family === 'magazine') {
      c.textAlign = 'left';
      c.font = '900 100px ' + FONT;
      var tL = wrap(c, headline, CW - 20).slice(0, 2), TLH = 108;
      c.font = '400 30px ' + FONT;
      var sL = sub ? wrap(c, sub, CW - 40).slice(0, 2) : [];
      var strips = points.slice(0, 3);
      c.font = '600 24px ' + FONT;
      var stripT = strips.join('   ｜   ');
      if (strips.length > 2 && lsWidth(c, stripT, 1) > CW - 130) {
        strips = strips.slice(0, 2); stripT = strips.join('   ｜   ');
      }
      if (!strips.length) stripT = '';

      /* 顶部节奏：品牌带 → 主标题 → 副标题 → 卖点条，间距放宽给足呼吸感
         （2026-10-09 用户反馈「文案全部挤在一起」，旧值 64/52/44 太局促） */
      var yBrand = 58, brandH = 76;
      var yTitle = yBrand + brandH + (logoMono ? 100 : 88);
      var titleBottom = yTitle + (tL.length - 1) * TLH;
      var ySub = titleBottom + 64, subBottom = ySub + (sL.length - 1) * 46;
      var yStrip = subBottom + (sL.length ? 66 : 40), stripH = 62;
      var photoTop = Math.round(stripT ? (yStrip + stripH / 2 + 36) : (subBottom + 48));

      /* 主视觉在下、色带在上、交界处羽化 —— 参考图 1/2 就是照片融进色里的 */
      if (img) { c.save(); drawCover.call(c, img, 0, photoTop, W, H - photoTop); c.restore(); }
      var gb = c.createLinearGradient(0, 0, 0, photoTop);
      gb.addColorStop(0, _rgb(tone.deep)); gb.addColorStop(.5, _rgb(tone.mid)); gb.addColorStop(1, _rgb(tone.light));
      c.fillStyle = gb; c.fillRect(0, 0, W, photoTop);
      if (img) {
        var gf = c.createLinearGradient(0, photoTop, 0, photoTop + 130);
        gf.addColorStop(0, _rgba(tone.light, 1)); gf.addColorStop(.55, _rgba(tone.light, .42)); gf.addColorStop(1, _rgba(tone.light, 0));
        c.fillStyle = gf; c.fillRect(0, photoTop, W, 130);
      }
      /* 底部压暗：价格 / 二维码 / 图标行都落在这层上 */
      var gd = c.createLinearGradient(0, H - 640, 0, H);
      gd.addColorStop(0, _rgba(tone.deep2, 0)); gd.addColorStop(.46, _rgba(tone.deep2, .56));
      gd.addColorStop(1, _rgba(tone.deep2, .95));
      c.fillStyle = gd; c.fillRect(0, H - 640, W, 640);

      /* 顶部：品牌 + 日期胶囊 */
      brandBlock('left', yBrand, brandH);
      if (dlab) {
        c.font = '700 26px ' + FONT;
        var dw = lsWidth(c, dlab, 2) + 52, px = W - PAD - dw, py = yBrand + 12, ph = 52;
        c.save();
        c.fillStyle = 'rgba(255,255,255,.17)'; rr(c, px, py, dw, ph, ph / 2); c.fill();
        c.strokeStyle = 'rgba(255,255,255,.44)'; c.lineWidth = 1.5; rr(c, px, py, dw, ph, ph / 2); c.stroke();
        c.restore();
        c.fillStyle = '#fff';
        lsText(c, dlab, px + dw / 2, py + ph / 2 + 9, 2, 'center');
      }
      /* 主标题 */
      c.fillStyle = '#fff'; c.font = '900 100px ' + FONT;
      shadow(true);
      drawLines(c, tL, PAD, yTitle, TLH, 2);
      shadow(false);
      if (sL.length) {
        c.fillStyle = 'rgba(255,255,255,.88)'; c.font = '400 30px ' + FONT;
        drawLines(c, sL, PAD, ySub, 46, 2);
      }
      /* 卖点条（白胶囊 + 深色字 = 参考图里最"设计"的一笔） */
      if (stripT) {
        c.font = '600 24px ' + FONT;
        var sw = Math.min(CW, lsWidth(c, stripT, 1) + 96), sx = Math.round((W - sw) / 2);
        c.save();
        c.shadowColor = 'rgba(0,0,0,.28)'; c.shadowBlur = 20; c.shadowOffsetY = 6;
        c.fillStyle = 'rgba(255,255,255,.95)';
        rr(c, sx, yStrip - stripH / 2, sw, stripH, stripH / 2); c.fill();
        c.restore();
        c.fillStyle = INK;
        lsText(c, stripT, W / 2, yStrip + 9, 1, 'center');
      }
      /* 底部：价格块（左）+ 二维码（右）+ 图标行 */
      var y1 = H - 430;
      c.fillStyle = ACCENT; rr(c, PAD, y1, 56, 5, 2.5); c.fill();
      var bigPre = price ? '¥' : '', bigNum = price || (cap ? cap : '');
      var bigUnit = price ? '/ 人' : (cap ? '个名额' : '');
      var bigLabel = price ? '活动费用' : (cap ? '招募名额' : '');
      if (!bigNum) { bigNum = loc ? loc.slice(0, 6) : ''; bigLabel = '目的地'; bigUnit = ''; }
      c.font = '600 22px ' + FONT; c.fillStyle = 'rgba(255,255,255,.6)';
      if (bigLabel) lsText(c, bigLabel, PAD, y1 + 50, 3, 'left');
      if (bigNum) {
        var bx = PAD, by = y1 + 156;
        c.font = '700 40px ' + FONT;
        var pw3 = bigPre ? c.measureText(bigPre).width + 6 : 0;
        c.font = '900 92px ' + FONT;
        var nw = c.measureText(bigNum).width;
        if (bigPre) { c.fillStyle = ACCENT; c.font = '700 40px ' + FONT; c.fillText(bigPre, bx, by - 32); }
        c.fillStyle = '#fff'; c.font = '900 92px ' + FONT; c.fillText(bigNum, bx + pw3, by);
        if (bigUnit) { c.fillStyle = 'rgba(255,255,255,.7)'; c.font = '500 26px ' + FONT; c.fillText(bigUnit, bx + pw3 + nw + 14, by - 2); }
      }
      var cardA = qrStamp(W - PAD - 140, H - 380, 170);
      var rTop = H - 176;
      c.fillStyle = 'rgba(0,0,0,.28)'; c.fillRect(0, rTop, W, 176);
      c.fillStyle = _rgba(tone.accent, .38); c.fillRect(PAD, rTop, CW, 1.5);
      drawRow(rowItems(), rTop, 176);
    }

    /* ═══ 版式二 noir：深色杂志（居中字距标题 + 信息胶囊 + 居中二维码，参考图 3）═══ */
    /* ═══ 版式 invite：电影感邀请函（全幅压暗 + 居中细金线 + 仪式感大标题）═══ */
    else if (family === 'invite') {
      if (img) { c.save(); drawCover.call(c, img, 0, 0, W, H); c.restore(); }
      else { c.fillStyle = '#0d0f12'; c.fillRect(0, 0, W, H); }
      var iv1 = c.createLinearGradient(0, 0, 0, H);
      iv1.addColorStop(0, 'rgba(6,7,9,.93)');
      iv1.addColorStop(.42, 'rgba(6,7,9,.34)');
      iv1.addColorStop(.62, 'rgba(6,7,9,.30)');
      iv1.addColorStop(1, 'rgba(6,7,9,.95)');
      c.fillStyle = iv1; c.fillRect(0, 0, W, H);
      c.strokeStyle = 'rgba(214,178,110,.85)'; c.lineWidth = 2;
      c.strokeRect(PAD + 8, PAD + 8, W - (PAD + 8) * 2, H - (PAD + 8) * 2);
      c.textAlign = 'center';
      c.fillStyle = 'rgba(214,178,110,.95)'; c.font = '500 24px ' + FONT;
      c.fillText('诚 邀 · INVITATION', W / 2, 250);
      c.font = '700 88px ' + FONT;
      var ivL = wrap(c, headline, CW - 60).slice(0, 2);
      var ivY = 430;
      c.fillStyle = 'rgba(255,255,255,.97)';
      ivL.forEach(function (t, i) { c.fillText(t, W / 2, ivY + i * 104); });
      var ivEnd = ivY + (ivL.length - 1) * 104;
      c.strokeStyle = 'rgba(214,178,110,.75)'; c.lineWidth = 1.5;
      c.beginPath(); c.moveTo(W / 2 - 120, ivEnd + 56); c.lineTo(W / 2 + 120, ivEnd + 56); c.stroke();
      if (sub) {
        c.fillStyle = 'rgba(255,255,255,.82)'; c.font = '400 30px ' + FONT;
        wrap(c, sub, CW - 120).slice(0, 2).forEach(function (t, i) { c.fillText(t, W / 2, ivEnd + 116 + i * 46); });
      }
      if (points.length) {
        c.fillStyle = 'rgba(255,255,255,.9)'; c.font = '500 26px ' + FONT;
        points.slice(0, 3).forEach(function (t, i) { c.fillText(t, W / 2, H - 300 + i * 44); });
      }
      if (logoImg) { try { c.drawImage(logoImg, W / 2 - 34, H - 190, 68, 68); } catch (e) {} }
    }

    /* ═══ 版式 cosmic：极简宇宙（深空渐变 + 星点 + 极简居中白字）═══ */
    else if (family === 'cosmic') {
      var csR = c.createRadialGradient(W * 0.5, H * 0.34, 40, W * 0.5, H * 0.34, H * 0.78);
      csR.addColorStop(0, '#1b2440'); csR.addColorStop(.5, '#0d1226'); csR.addColorStop(1, '#04060d');
      c.fillStyle = csR; c.fillRect(0, 0, W, H);
      /* 星点用确定性公式，不用 Math.random —— 随机会让同一场活动每次生成都不一样 */
      for (var csI = 0; csI < 90; csI++) {
        var csX = (csI * 137) % W, csY = (csI * 271) % Math.round(H * 0.82);
        c.fillStyle = 'rgba(255,255,255,' + (0.16 + (csI % 5) * 0.06) + ')';
        c.beginPath(); c.arc(csX, csY, (csI % 3 === 0) ? 2.2 : 1.2, 0, Math.PI * 2); c.fill();
      }
      c.strokeStyle = 'rgba(150,190,255,.28)'; c.lineWidth = 1.5;
      c.beginPath(); c.arc(W / 2, 470, 250, 0, Math.PI * 2); c.stroke();
      c.strokeStyle = 'rgba(150,190,255,.16)';
      c.beginPath(); c.arc(W / 2, 470, 330, 0, Math.PI * 2); c.stroke();
      if (img) {
        var csW = 620, csH = 380, csX0 = (W - csW) / 2, csY0 = 900;
        c.save(); c.beginPath();
        if (c.roundRect) { c.roundRect(csX0, csY0, csW, csH, 18); } else { c.rect(csX0, csY0, csW, csH); }
        c.clip(); drawCover.call(c, img, csX0, csY0, csW, csH); c.restore();
        c.strokeStyle = 'rgba(255,255,255,.18)'; c.lineWidth = 1;
        c.strokeRect(csX0, csY0, csW, csH);
      }
      c.textAlign = 'center';
      c.fillStyle = 'rgba(180,210,255,.9)'; c.font = '500 22px ' + FONT;
      c.fillText('O U T D O O R   E X P E D I T I O N', W / 2, 250);
      c.fillStyle = '#fff'; c.font = '700 92px ' + FONT;
      var csL = wrap(c, headline, CW - 40).slice(0, 2);
      csL.forEach(function (t, i) { c.fillText(t, W / 2, 400 + i * 108); });
      if (sub) {
        c.fillStyle = 'rgba(255,255,255,.66)'; c.font = '400 28px ' + FONT;
        wrap(c, sub, CW - 160).slice(0, 2).forEach(function (t, i) { c.fillText(t, W / 2, 560 + i * 44); });
      }
      if (points.length) {
        c.fillStyle = 'rgba(255,255,255,.8)'; c.font = '500 25px ' + FONT;
        points.slice(0, 3).forEach(function (t, i) { c.fillText(t, W / 2, H - 150 + i * 42); });
      }
    }

    /* ═══ 版式 forest：暗色雾林（墨绿雾气 + 横向雾带 + 左下大标题）═══ */
    else if (family === 'forest') {
      if (img) { c.save(); drawCover.call(c, img, 0, 0, W, H); c.restore(); }
      else { c.fillStyle = '#101a14'; c.fillRect(0, 0, W, H); }
      c.fillStyle = 'rgba(12,28,20,.72)'; c.fillRect(0, 0, W, H);
      for (var frI = 0; frI < 7; frI++) {
        var frY = 180 + frI * 150;
        var frG = c.createLinearGradient(0, frY - 70, 0, frY + 70);
        frG.addColorStop(0, 'rgba(210,225,215,0)');
        frG.addColorStop(.5, 'rgba(210,225,215,' + (0.20 - frI * 0.02).toFixed(3) + ')');
        frG.addColorStop(1, 'rgba(210,225,215,0)');
        c.fillStyle = frG; c.fillRect(0, frY - 70, W, 140);
      }
      var frB = c.createLinearGradient(0, H - 520, 0, H);
      frB.addColorStop(0, 'rgba(6,14,10,0)'); frB.addColorStop(1, 'rgba(6,14,10,.9)');
      c.fillStyle = frB; c.fillRect(0, H - 520, W, 520);
      c.textAlign = 'left';
      c.fillStyle = 'rgba(150,190,160,.95)'; c.fillRect(PAD, H - 376, 96, 4);
      c.fillStyle = 'rgba(226,238,228,.95)'; c.font = '700 96px ' + FONT;
      var frL = wrap(c, headline, CW - 20).slice(0, 2);
      frL.forEach(function (t, i) { c.fillText(t, PAD, H - 330 + i * 108); });
      if (sub) {
        c.fillStyle = 'rgba(226,238,228,.78)'; c.font = '400 28px ' + FONT;
        wrap(c, sub, CW - 40).slice(0, 2).forEach(function (t, i) {
          c.fillText(t, PAD, H - 330 + frL.length * 108 + 6 + i * 44);
        });
      }
      if (points.length) {
        c.fillStyle = 'rgba(226,238,228,.72)'; c.font = '500 24px ' + FONT;
        points.slice(0, 2).forEach(function (t, i) { c.fillText(t, PAD, H - 90 + i * 38); });
      }
    }

    /* ═══ 版式 routemap：纪实路线图（牛皮纸网格 + 虚线路线 + 路点）═══ */
    else if (family === 'routemap') {
      drawPhotoFull();
      c.fillStyle = 'rgba(17,30,24,.38)'; c.fillRect(0, 0, W, H);
      var routeShade = c.createLinearGradient(0, H * .5, 0, H);
      routeShade.addColorStop(0, 'rgba(17,30,24,0)'); routeShade.addColorStop(1, 'rgba(17,30,24,.92)');
      c.fillStyle = routeShade; c.fillRect(0, H * .5, W, H * .5);
      brandBlock('left', 58, 64, true);
      c.textAlign = 'right'; c.fillStyle = '#FFFDF8'; c.font = '500 88px ' + SERIF;
      wrap(c, headline, CW * .78).slice(0, 2).forEach(function (t, i) {
        c.fillText(t, W - PAD, 300 + i * 108);
      });
      if (sub) { c.font = '400 28px ' + FONT; c.fillText(lsFit(c, sub, CW * .78, 1), W - PAD, 540); }
      var stops = (data.routeStops || []).slice(0, 4);
      if (stops.length >= 2) {
        c.strokeStyle = 'rgba(255,253,248,.8)'; c.lineWidth = 2; c.setLineDash([8, 12]); c.beginPath();
        stops.forEach(function (_, i) {
          var x = 110 + i * 720 / (stops.length - 1), y = 610 + i * 360 / (stops.length - 1);
          if (i) c.lineTo(x, y); else c.moveTo(x, y);
        });
        c.stroke(); c.setLineDash([]);
        stops.forEach(function (stop, i) {
          var x = 110 + i * 720 / (stops.length - 1), y = 610 + i * 360 / (stops.length - 1);
          c.fillStyle = '#FFFDF8'; c.beginPath(); c.arc(x, y, 8, 0, Math.PI * 2); c.fill();
          c.strokeStyle = '#FFFDF8'; c.lineWidth = 1; c.beginPath(); c.arc(x, y, 16, 0, Math.PI * 2); c.stroke();
          c.textAlign = i === stops.length - 1 ? 'right' : 'left';
          c.font = '500 26px ' + FONT; c.fillText(lsFit(c, stop, 190, 0), x, y - 32);
        });
        c.textAlign = 'left'; c.font = '400 18px ' + FONT;
        c.fillText('行程节点 · 顺序示意', PAD, 1040);
      }
      c.textAlign = 'left'; c.fillStyle = '#FFFDF8'; c.font = '500 54px ' + FONT;
      c.fillText(lsFit(c, dlab, CW - 240, 1), PAD, H - 230);
      c.font = '400 25px ' + FONT;
      c.fillText(lsFit(c, [loc, price ? '¥' + price + ' / 人' : ''].filter(Boolean).join(' · '), CW - 240, 0), PAD, H - 162);
      qrStamp(W - PAD - 90, H - 180, 140);
    }

    else if (family === 'noir') {
      drawPhotoFull();
      var gn = c.createLinearGradient(0, 0, 0, H);
      /* 中部压得更深一点：标题正下方的副标题 / 卖点行会落在照片最亮处（雪山/天空），
         只靠 .28 的浅遮罩会「白字压白雪」看不清（2026-10-09 实测 noir 的卖点行几乎糊掉）。 */
      gn.addColorStop(0, _rgba(tone.deep, .62)); gn.addColorStop(.42, _rgba(tone.deep, .44));
      gn.addColorStop(.66, _rgba(tone.deep, .68)); gn.addColorStop(1, _rgba(tone.deep2, .95));
      c.fillStyle = gn; c.fillRect(0, 0, W, H);

      c.textAlign = 'center';
      var y = brandBlock('center', 58, 68) + 40;
      c.font = '900 92px ' + FONT;
      var tL2 = wrap(c, headline, CW - 20).slice(0, 2), TLH2 = 104;
      c.fillStyle = '#fff'; shadow(true);
      y = drawLines(c, tL2, W / 2, y + 64, TLH2, 2);
      shadow(false);
      y += 24;
      c.fillStyle = _rgba(tone.accent, .55);
      c.fillRect(Math.round(W / 2 - 75), y, 150, 2);
      if (sub) {
        c.font = '500 27px ' + FONT; c.fillStyle = 'rgba(255,255,255,.94)';
        var sL2 = wrap(c, sub, CW - 120).slice(0, 2);
        shadow(true);
        sL2.forEach(function (t, i) { lsText(c, t, W / 2, y + 48 + i * 44, 2.6, 'center'); });
        shadow(false);
        y += 48 + (sL2.length - 1) * 44;
      }
      if (points.length) {
        c.font = '600 25px ' + FONT; c.fillStyle = 'rgba(255,255,255,.97)';
        shadow(true);
        lsText(c, lsFit(c, points.slice(0, 3).join('　·　'), CW - 60, 2), W / 2, y + 58, 2, 'center');
        shadow(false);
      }
      var info = [];
      if (dlab) info.push(['活动时间', dlab]);
      if (loc) info.push(['目的地', loc]);
      if (price) info.push(['活动费用', '¥' + price + ' / 人']);
      else if (cap) info.push(['招募名额', '限 ' + cap + ' 人']);
      else if (facts.length) info.push(['活动信息', facts[0]]);
      var iy = Math.max(y + 120, H * (info.length >= 3 ? .47 : .52));
      info.slice(0, 3).forEach(function (it) {
        c.font = '600 24px ' + FONT;
        var tw = Math.min(CW, lsWidth(c, it[0], 3) + 58), ph = 48, px2 = Math.round((W - tw) / 2);
        c.save();
        c.fillStyle = 'rgba(255,255,255,.15)'; rr(c, px2, iy, tw, ph, ph / 2); c.fill();
        c.strokeStyle = 'rgba(255,255,255,.3)'; c.lineWidth = 1.5; rr(c, px2, iy, tw, ph, ph / 2); c.stroke();
        c.restore();
        c.fillStyle = '#fff'; lsText(c, it[0], W / 2, iy + 33, 3, 'center');
        c.font = '600 31px ' + FONT; c.fillStyle = 'rgba(255,255,255,.96)';
        c.fillText(lsFit(c, it[1], CW - 40, 0), W / 2, iy + ph + 46);
        iy += ph + 96;
      });
      c.textAlign = 'left';
      var cardB = qrStamp(W / 2, H - 240, 170);
    }

    /* ═══ 版式三 film：极简影像（全幅照片主导 + 左下文本块，参考图 4）═══ */
    else {
      drawPhotoFull();
      var gtop = c.createLinearGradient(0, 0, 0, 250);
      gtop.addColorStop(0, _rgba(tone.deep, .55)); gtop.addColorStop(1, _rgba(tone.deep, 0));
      c.fillStyle = gtop; c.fillRect(0, 0, W, 250);
      var gcb = c.createLinearGradient(0, H * .36, 0, H);
      gcb.addColorStop(0, _rgba(tone.deep2, 0)); gcb.addColorStop(.5, _rgba(tone.deep2, .58));
      gcb.addColorStop(1, _rgba(tone.deep2, .92));
      c.fillStyle = gcb; c.fillRect(0, H * .36, W, H * .64);

      c.textAlign = 'left';
      brandBlock('left', 52, 60, true);

      var qrC = qrStamp(W - PAD - 125, H - 200, 165);
      var textW = CW - (qrC ? qrC.w + 30 : 0);
      c.font = '500 25px ' + FONT;
      var pL = points.slice(0, 3).map(function (t) { return lsFit(c, t, textW - 34, 1.2); });
      c.font = '400 29px ' + FONT;
      var sL3 = sub ? wrap(c, sub, textW - 10).slice(0, 2) : [];
      c.font = '400 96px ' + SERIF;
      var tL3 = wrap(c, headline, textW - 10).slice(0, 2), TLH3 = 106;
      var factsLine = [price ? ('¥' + price + ' / 人') : '', cap ? ('限 ' + cap + ' 人') : ''].filter(Boolean).join('     ·     ');
      var eyebrow = [dlab, loc].filter(Boolean).join('  ·  ');

      var hRule = 30, hEyebrow = eyebrow ? 46 : 0, hSub = sL3.length ? (sL3.length * 46 + 22) : 0;
      var yTop3 = Math.round(H - 150 - (hRule + hEyebrow + tL3.length * TLH3 + hSub + pL.length * 44));
      yTop3 = Math.max(yTop3, 230);

      c.fillStyle = ACCENT; rr(c, PAD, yTop3, 48, 5, 2.5); c.fill();
      var yy = yTop3 + hRule;
      if (eyebrow) {
        c.font = '500 23px ' + FONT; c.fillStyle = 'rgba(255,255,255,.76)';
        lsText(c, lsFit(c, eyebrow, textW, 2.6), PAD, yy + 30, 2.6, 'left');
        yy += 46;
      }
      c.fillStyle = '#fff'; c.font = '400 96px ' + SERIF;
      shadow(true); drawLines(c, tL3, PAD, yy + 78, TLH3, 2); shadow(false);
      yy += tL3.length * TLH3;
      if (sL3.length) {
        yy += 22;
        c.fillStyle = 'rgba(255,255,255,.86)'; c.font = '400 29px ' + FONT;
        drawLines(c, sL3, PAD, yy + 30, 46, 2);
        yy += sL3.length * 46;
      }
      yy += 18;
      pL.forEach(function (t) {
        c.fillStyle = ACCENT; c.fillRect(PAD, yy + 14, 18, 3);
        c.fillStyle = 'rgba(255,255,255,.9)'; c.font = '500 25px ' + FONT;
        lsText(c, t, PAD + 32, yy + 22, 1.2, 'left');
        yy += 44;
      });
      if (factsLine) {
        c.font = '600 24px ' + FONT; c.fillStyle = 'rgba(255,255,255,.88)';
        lsText(c, lsFit(c, factsLine, textW, 2.4), PAD, H - 88, 2.4, 'left');
      }
    }

    host.innerHTML = '<div class="ch-poster-hold"></div>';
    host.querySelector('.ch-poster-hold').appendChild(cv);
    cv.__posterFamily = family;
    return [cv];
  }

  /* ---------- 剪贴板 ---------- */

  async function copyRich(html, text) {
    try {
      if (navigator.clipboard && window.ClipboardItem) {
        await navigator.clipboard.write([new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' })
        })]);
        return true;
      }
    } catch (e) { /* 落到兜底 */ }
    try {
      var d = document.createElement('div');
      d.contentEditable = 'true';
      d.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0';
      d.innerHTML = html;
      document.body.appendChild(d);
      var r = document.createRange(); r.selectNodeContents(d);
      var s = getSelection(); s.removeAllRanges(); s.addRange(r);
      var ok = document.execCommand('copy');
      s.removeAllRanges(); d.remove();
      return ok;
    } catch (e2) { return false; }
  }

  async function copyText(t) {
    try { await navigator.clipboard.writeText(t); return true; }
    catch (e) {
      try {
        var ta = document.createElement('textarea');
        ta.value = t;
        ta.style.cssText = 'position:fixed;left:-9999px;top:0';
        document.body.appendChild(ta); ta.select();
        var ok = document.execCommand('copy');
        ta.remove(); return ok;
      } catch (e2) { return false; }
    }
  }

  /* ---------- 主入口：打开成品预览 ---------- */

  async function openChannelOutput(channel, data, opts) {
    opts = opts || {};
    data = data || {};
    var activityId = opts.activityId || data.activityId;
    /* 渠道 key 不在白名单里时直接拒绝：以前会渲染出「undefined xxx」这种半成品标题。 */
    if (!LABEL[channel]) { showAlert({ title: '无法渲染', message: '未知的内容渠道：' + channel + '。可用渠道为公众号 / 小红书 / 海报 / 回顾。' }); return; }
    var ctx = null;
    // 回顾分两种：绑定活动的（activityId>0，资料从活动取）与独立回顾（activityId=0，资料全在内容 JSON 里）
    if (channel === 'recap' && (!activityId || activityId === 0)) {
      // 独立回顾不依赖系统里的活动：品牌信息单独取，媒体清单从内容自身带（data.media）
      try {
        var _st = await api('/api/club/' + CLUB + '/settings').catch(function () { return null; });
        ctx = {
          club: CLUB, activityId: 0, master: {}, mm: {},
          logoUrl: _st && _st.logoUrl ? abs(_st.logoUrl) + (_st.logoUrl.indexOf('?') < 0 ? '?v=' + Date.now() : '') : '',
          brandName: _st ? String(_st.name || '') : '',
          brandSlogan: _st ? String(_st.slogan || '') : '',
          status: '', title: data.title || '活动回顾', date: '', location: '', price: '', capacity: ''
        };
      } catch (e) { ctx = { club: CLUB, activityId: 0, master: {}, mm: {}, logoUrl: '', brandName: '', brandSlogan: '', status: '', title: data.title || '活动回顾' }; }
    } else {
      if (!activityId) { showAlert({ title: '无法渲染', message: '缺少活动信息，无法渲染成品。' }); return; }
      try { ctx = await loadCtx(activityId); }
      catch (e) { showAlert({ title: '无法渲染', message: '读取活动资料失败：' + e.message }); return; }
    }

    var ov = document.createElement('div');
    ov.className = 'ch-overlay';
    var headTitle = data.title || (data.titleOptions || [])[0] || ctx.title;
    /* 成品预览是一层全屏 overlay：与 shared.js 的 uxDialog / clubos-ux 的 uxForm / 支付 sheet
       共用同一套弹窗行为（焦点进入并圈闭、Esc 只关最上层、锁背景滚动、关闭后归还焦点）。
       之前这里只有一条自己的 Esc 监听：打开后焦点仍在页面上、Tab 会跑到成品背后、背景能滚。 */
    var chTitleId = 'ch-title-' + (++_chSeq);
    ov.innerHTML =
      '<div class="ch-panel" role="dialog" aria-modal="true" aria-labelledby="' + chTitleId + '" tabindex="-1">' +
      '<div class="ch-head"><div><div class="eyebrow">AI CHANNEL OUTPUT · 成品预览</div>' +
      '<h2 id="' + chTitleId + '">' + ICON[channel] + ' ' + esc(LABEL[channel] || channel) + '</h2>' +
      '<div class="sub">' + esc(headTitle) + (ctx && ctx.activityId ? ' · 活动：' + esc(ctx.title) : ' · 独立回顾') + '</div></div>' +
      '<button class="ch-x" type="button" aria-label="关闭">×</button></div>' +
      '<div class="ch-tools" id="chTools"></div>' +
      '<div class="ch-stage" id="chStage"></div>' +
      '<details class="ch-raw"><summary>查看 AI 返回的原始结构化数据（不是成品，仅供排查）</summary>' +
      '<pre>' + esc(JSON.stringify(data, null, 2)) + '</pre></details>' +
      '</div>';
    document.body.appendChild(ov);
    var chSession = null;
    var close = function () { if (!ov.isConnected) return; if (chSession) chSession.release(); ov.remove(); };
    ov.querySelector('.ch-x').onclick = close;
    ov.onclick = function (e) { if (e.target === ov) close(); };
    /* 只在 shared.js 已就绪时接管；否则退化成以前只有 × / 点遮罩能关的行为，不会更差。 */
    if (window.uxDialogSession) chSession = window.uxDialogSession(ov, { onEscape: close, initialFocus: '.ch-x' });

    var stage = ov.querySelector('#chStage');
    var tools = ov.querySelector('#chTools');
    var btn = function (label, cls, fn) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn ' + cls;
      b.textContent = label;
      b.onclick = fn;
      tools.appendChild(b);
      return b;
    };
    var note = document.createElement('span');
    note.className = 'ch-note';
    tools.appendChild(note);

    // ---- 回顾但缺现场素材：如实说明，不编造 ----
    if (channel === 'recap' && (data.needsActualData || (!data.blocks && !data.body && data.message))) {
      stage.innerHTML = '<div class="ch-missing"><div class="ch-missing-mark">📷</div>' +
        '<h3>还没有现场素材，暂时无法生成真实回顾</h3>' +
        '<p>' + esc(data.message || '活动回顾只能叙述真正发生过的事。') + '</p>' +
        '<div class="notice">需要至少一张现场照片（或领队记录），ClubOS 才会基于真实素材写回顾；否则写出来的都是编的。</div>' +
        '<p class="sub">当前活动资料：' + esc(ctx.title) + (ctx.date ? ' · ' + esc(ctx.date) : '') + '</p></div>';
      note.textContent = 'ClubOS 不会替你把没发生过的事写成回顾。';
      return;
    }

    // ── 活动回顾：模型自由排版（主）/ 固定杂志模板（兜底）──
    // ★ 2026-11-04 之后反转：用户不要"每次都长一个样的固定模板"，要稳定杂志风格 + 每场不同版式。
    //   主路径 data.route==='model-html'：data.html 由模型在风格规范内自由排版，
    //   复用宣传长图的 iframe + 导出管线（换 resolveRecapHtml 解析回顾照片池）。
    //   回退路径：data.html 不存在（模型排版失败 / 离线）时，走固定杂志模板 paintRecap。
    if (channel === 'recap') {
      var isModelHtml = !!(data && data.html);
      var repaintRecap = async function () {
        if (isModelHtml) {
          // 回顾照片池来自 data.media（rcap_*，走俱乐部鉴权路由），不是活动媒体池
          ctx.mm = recapMediaMap(data);
          canvases = await paintLongpic(data, ctx, stage, resolveRecapHtml);
        } else {
          canvases = await paintRecap(data, ctx, stage);
        }
        return canvases;
      };
      canvases = await repaintRecap();
      /* ★ 2026-11-04 导出根治：模型自由排版的回顾，照片一律交给 _paintShots 按坐标画回，
         SVG 里只留文字与色块 —— 彻底绕开 foreignObject 内嵌大图光栅化不可靠这个老毛病。 */
      if (isModelHtml && canvases) canvases.photoOverlayOnly = true;
      /* 「换一版排版」入口：用同一批素材再跑一次 AI，版式重新自由组合。
         club.js 注入 window.clubRetypesetRecap；其他端没有这个钩子就不显示。 */
      if (window.clubRetypesetRecap) {
        btn('换一版排版（重新生成）', 'secondary', function () {
          window.clubRetypesetRecap({ close: close });
        });
      }
      var recapDownload = btn('下载长图（750px 宽 · 2倍图）', '', async function () {
        try {
          recapDownload.disabled = true;
          var st = await exportLongpicPng(canvases, ctx, safeName(data.title || '活动回顾'));
          if (st && st.failed > 0) {
            showAlert({ title: '长图已下载，但有照片没载进来', message: '共 ' + st.total + ' 张照片，其中 ' + st.failed + ' 张没能载入，这些位置在长图里是空白。请确认素材还在，然后再下载一次。' });
          } else { toast('长图已下载，可直接发公众号 / 朋友圈'); }
        } catch (e2) {
          showAlert({ title: '下载失败', message: (e2 && e2.message ? e2.message : '导出没成功。') +
            ' 重试仍不行的话，可以先在预览区按 Command+P 存成 PDF，或用系统截图。' });
        } finally { recapDownload.disabled = false; }
      });
      if (isModelHtml) {
        // 模型直出 HTML：直接拿渲染后的成品 DOM 复制（带行内样式），与宣传长图一致
        btn('复制图文（粘贴进公众号编辑器）', 'secondary', async function () {
          var docHtml = longpicRichHtml(canvases);
          var txt = canvases.frame.contentDocument ? canvases.frame.contentDocument.body.innerText : '';
          (await copyRich(docHtml, txt)) ? toast('已复制带格式图文，去公众号编辑器粘贴即可')
            : showAlert({ title: '复制失败', message: '浏览器拒绝了剪贴板权限，可改用「复制纯文本」。' });
        });
        btn('复制纯文本', 'secondary', async function () {
          var txt = canvases.frame.contentDocument ? canvases.frame.contentDocument.body.innerText : (data.title || '');
          (await copyText(txt)) ? toast('纯文本已复制') : showAlert({ title: '复制失败', message: '请手动选中文字复制。' });
        });
      } else {
        btn('复制图文（粘贴进公众号编辑器）', 'secondary', async function () {
          (await copyRich(recapRichHtml(data, ctx), recapPlainText(data, ctx)))
            ? toast('已复制带格式图文，去公众号编辑器粘贴即可')
            : showAlert({ title: '复制失败', message: '浏览器拒绝了剪贴板权限，可改用「复制纯文本」。' });
        });
        btn('复制纯文本', 'secondary', async function () {
          (await copyText(recapPlainText(data, ctx))) ? toast('纯文本已复制') : showAlert({ title: '复制失败', message: '请手动选中文字复制。' });
        });
      }
      note.textContent = data.route === 'model-html'
        ? '按这场活动的照片与故事自由编排，每场版式不同。'
        : data.route === 'offline'
          ? '演示模式：按提供的真实记录与照片整理，未调用模型。'
          : '已用户外简约版式整理本次素材。';
      return;
    }

    var canvases = [];

    // ══ AI 宣传长图：模型直出版式，沙箱预览 + 导出 PNG + 复制图文 ══
    if (channel === 'longpic') {
      if (!data.html) {
        stage.innerHTML = '<div class="ch-missing"><div class="ch-missing-mark">🖼</div>' +
          '<h3>这次没有拿到可用的排版内容</h3>' +
          '<p>模型没有返回长图 HTML，通常是模型服务临时异常。请再点一次「AI 生成」。</p></div>';
        note.textContent = '没有生成成功的内容不会计费以外的动作，也不会覆盖你已有的版本。';
        return;
      }
      var paint = await paintLongpic(data, ctx, stage);
      var longpicDownload = btn('下载长图（750px 宽 · 2倍图）', '', async function () {
        try {
          longpicDownload.disabled = true;
          var st = await exportLongpicPng(paint, ctx, safeName(ctx.title));
          /* 图片没载进来时**必须说**。原先裂图会被静默替换成透明像素，
             老板拿到一张"有些地方是白的"的成品，还以为是设计留白。 */
          if (st && st.failed > 0) {
            showAlert({ title: '长图已下载，但有照片没载进来',
              message: '共 ' + st.total + ' 张照片，其中 ' + st.failed + ' 张没能载入，这些位置在长图里是空白。' +
                '请确认活动素材还在，然后再下载一次。' });
          } else {
            toast('长图已下载，可直接发公众号');
          }
        } catch (e2) {
          showAlert({ title: '下载失败', message: (e2 && e2.message ? e2.message : '导出没成功。') +
            ' 重试仍不行的话，可以先在预览区按 Command+P 存成 PDF，或用系统截图。' });
        } finally { longpicDownload.disabled = false; }
      });
      btn('复制图文（粘贴进公众号编辑器）', 'secondary', async function () {
        var docHtml = longpicRichHtml(paint);
        var doc = paint.frame.contentDocument;
        var txt = doc && doc.body ? doc.body.innerText : '';
        (await copyRich(docHtml, txt)) ? toast('已复制带格式图文，去公众号编辑器粘贴即可')
          : showAlert({ title: '复制失败', message: '浏览器拒绝了剪贴板权限，可改用「复制纯文本」。' });
      });
      btn('复制纯文本', 'secondary', async function () {
        var txt = paint.frame.contentDocument ? paint.frame.contentDocument.body.innerText : (data.title || '');
        (await copyText(txt)) ? toast('纯文本已复制') : showAlert({ title: '复制失败', message: '请手动选中文字复制。' });
      });
      var route = (data && data.route) || 'model-html';
      var bits;
      if (route === 'mock') {
        bits = '演示模式（未接入模型，仅展示版式骨架）。';
      } else if (route === 'model-html') {
        bits = '按这场活动的主题、照片与信息量自由编排，每场版式不同。';
      } else {
        bits = '已用户外简约版式整理本次活动资料。';
      }
      if (paint.dropped > 0) bits += ' 有 ' + paint.dropped + ' 张配图被剔除（不是本活动的真实照片）。';
      note.textContent = bits;
      return;
    }

    if (channel === 'poster') {
      var repaintPoster = async function () { canvases = await paintPoster(data, ctx, stage); return canvases; };
      canvases = await repaintPoster();
      btn('下载海报 PNG', '', async function () {
        await downloadCanvas(canvases[0], safeName(ctx.title) + '_海报_1080x1440.png');
        toast('海报已下载（1080×1440 PNG）');
      });
      var swapBtn = btn('换一套版式', 'secondary', async function () {
        swapBtn.disabled = true;
        try {
          if (data.style === 'outdoor-minimal-v1') data.posterFamily = canvases[0].__posterFamily === 'film' && (data.routeStops || []).length >= 2 ? 'routemap' : 'film';
          await repaintPoster(); toast('已更新预览构图');
        }
        finally { swapBtn.disabled = false; }
      });
      var ptxt = [data.headline || ctx.title, data.subheadline || '', (data.facts || []).join(' · '), (data.sellingPoints || []).join('\n'), data.cta || ''].filter(Boolean).join('\n');
      btn('复制海报文案', 'secondary', async function () {
        (await copyText(ptxt)) ? toast('文案已复制') : showAlert({ title: '复制失败', message: '请手动选中文字复制。' });
      });
      note.textContent = '真实活动照片、简约大标题与清晰日期层级；有多站线路时可切换路线构图。二维码直达活动报名页' + (ctx.status === 'published' ? '。' : '（活动发布后才能打开）。');
      return;
    }

    if (channel === 'xhs') {
      var xm = xhsModel(data, ctx);
      var painted = await paintXhs(xm, ctx, stage);
      /* ★ 2026-11-04 排版修正（用户实锤「排得太差」）：
         ① 段与段之间用**空行**分隔（原来单个换行 → 段落全挤在一起，截图里就是那效果）；
         ② 模型若没按空行分段（body 里只有单个 \n），前端兜底把单个换行也升级成空行；
         ③ 标签行只出现一次且已去重（见 xhsModel 的标签去重）。 */
      var bodyText = String(xm.body || '').replace(/\r\n?/g, '\n');
      if (bodyText.indexOf('\n\n') < 0) bodyText = bodyText.replace(/\n/g, '\n\n');
      var caption = [xm.title, xm.hook, bodyText,
        xm.tags.length ? xm.tags.map(function (t) { return '#' + t; }).join(' ') : '']
        .filter(function (x) { return x !== undefined && x !== null && String(x).trim() !== ''; }).join('\n\n');
      btn('复制文案', '', async function () {
        (await copyText(caption)) ? toast('文案已复制，直接粘到小红书即可') : showAlert({ title: '复制失败', message: '请手动选中文字复制。' });
      });
      btn('下载首页海报 PNG', 'secondary', async function () {
        await downloadCanvas(painted.poster, safeName(ctx.title) + '_小红书首页海报_1080x1440.png');
        toast('首页海报已下载（1080×1440 PNG）');
      });
      if (painted.photos.length) {
        btn('下载全部配图（已裁剪 3:4，' + painted.photos.length + ' 张）', 'secondary', async function () {
          for (var i = 0; i < painted.photos.length; i++) {
            await downloadCanvas(painted.photos[i], safeName(ctx.title) + '_小红书配图_' + (i + 2) + '.png');
            await new Promise(function (r) { setTimeout(r, 320); });   // 连续下载需要间隔，否则浏览器只存第一张
          }
          toast('已下载 ' + painted.photos.length + ' 张 3:4 配图');
        });
      }
      note.textContent = '小红书以文案为主：复制上面的文案，配图用「首页海报 + 自动裁剪的活动照」。';
      return;
    }

    // wechat / recap（recap 有真实素材时与图文同构）
    var model = wechatModel(data, ctx);
    stage.innerHTML = previewHtml(model);
    btn('复制图文（可直接粘贴进公众号编辑器）', '', async function () {
      var ok = await copyRich(inlineHtml(model, ctx), plainText(model, ctx));
      ok ? toast('已复制带格式图文，去公众号编辑器粘贴即可') : showAlert({ title: '复制失败', message: '浏览器拒绝了剪贴板权限，可改用「复制纯文本」。' });
    });
    btn('复制纯文本', 'secondary', async function () {
      (await copyText(plainText(model, ctx))) ? toast('纯文本已复制') : showAlert({ title: '复制失败', message: '请手动选中文字复制。' });
    });
    note.textContent = '复制后到公众号编辑器直接粘贴，标题/小标题/图片排版都会保留。';
  }

  // ══ 活动回顾：杂志式长图（2026-11-04 按用户提供的手工参考长图彻底重做）══
  // 参考长图的节奏：封面满幅大图 → 极简序章 → 满幅过渡照 → 壹/贰/叁/肆 章节
  //（巨大中文序号 + 一句短文案 + 1~3 张大图）→ 合影金句页 → 品牌绿收尾页 + 二维码。
  // ★★ 排版由模板主导，绝不照抄模型返回的 blocks 形状。线上那份被用户批评的成品
  //    只有「一个 lead 塞 487 字整篇小红书文案 + 一个 gallery 塞 17 张照片」，
  //    旧模板照单全收 → 封面压长文、两屏纯文字、末尾一堵 2×2 图墙。现在无论 AI
  //    返回什么形状，都先拆成「短句池 + 照片池」，再按固定杂志节奏重新组装。
  var RC_NUM = ['壹','贰','叁','肆','伍','陆','柒','捌','玖','拾'];
  var RC_CH = [
    { c: '入山', e: 'Into the woods' }, { c: '林间', e: 'Among the trees' },
    { c: '相见', e: 'Gathering' }, { c: '手作', e: 'By hand' },
    { c: '静息', e: 'Stillness' }, { c: '同行', e: 'Side by side' },
    { c: '余韵', e: 'Afterglow' }, { c: '归途', e: 'Homeward' }
  ];
  function recapMediaMap(data) {
    var m = {}; (data.media || []).forEach(function (x) { if (x && x.ref) m[x.ref] = x; }); return m;
  }
  // 回顾照片走俱乐部鉴权路由（生产 /static/uploads/* 整体 404）；不依赖活动
  function recapMediaUrl(raw) {
    var s = String(raw || '');
    if (!/^\/static\/uploads\//.test(s)) return s;
    var rel = s.replace(/^\/static\//, '');
    return '/api/club/' + CLUB + '/recap-media/' + rel.split('/').map(encodeURIComponent).join('/');
  }
  function recapImg(ref, mm, cls) {
    var m = mm[ref] || {};
    var raw = m.url || '';
    var u = raw ? abs(recapMediaUrl(raw)) : '';
    if (!u) return '';
    var cap = m.desc ? '<div class="rk-cap">' + esc(m.desc) + '</div>' : '';
    return '<figure class="rk-fig ' + (cls || '') + '"><img src="' + esc(u) + '" alt="' + esc(m.name || '') + '">' + cap + '</figure>';
  }
  /* 回顾成品的照片池已经打进内容 JSON（data.media），不依赖活动资料。 */
  function rcUrl(ref, mm) {
    var m = (mm && mm[ref]) || {};
    var raw = m.url || '';
    if (!raw) return '';
    return abs(recapMediaUrl(raw));
  }
  function rcImgTag(ref, mm) {
    var m = (mm && mm[ref]) || {};
    var u = rcUrl(ref, mm);
    if (!u) return '';
    /* alt 用 AI 图注（desc），**不要**用文件名 —— 文件名是一串哈希，
       出现在图注位置就是用户截图里那行乱码。 */
    return '<img src="' + esc(u) + '" alt="' + esc((m.desc || '').trim()) + '">';
  }
  /* 兼容旧数据：老回顾若还带模型直出的 data.html，复制图文时仍可用（长图已不走它）。 */
  function resolveRecapHtml(html, mm) {
    var t = String(html || '');
    t = t.replace(/(<img\b[^>]*?src=(["']))\s*\{\{?\s*media\s*:\s*([A-Za-z0-9_-]+)\s*\}?\}\s*\2/gi,
      function (_, pre, q, ref) {
        var m = (mm && mm[ref]) || {};
        var u = m.url ? abs(recapMediaUrl(m.url)) : '';
        return u ? pre + esc(u) + q : '';
      });
    t = t.replace(/<img\b[^>]*>/gi, function (tag) {
      if (/\{\{?\s*media\s*:/.test(tag)) return '';
      return /\bsrc=("|')/i.test(tag) ? tag : '';
    });
    t = t.replace(/\{\{?\s*media\s*:\s*([A-Za-z0-9_-]+)\s*\}?\}/g, '');
    t += '<style>img{max-width:100%;height:auto}[style*="display:flex"],[style*="display: flex"]{flex-wrap:wrap}</style>';
    return t;
  }

  /* ── 短句池：把整篇渠道文案拆成「一句一页」的图上短语 ──
     去掉话题标签、emoji、栏目标题、重复句；长句按逗号再切。
     长图里每处最多放一句，绝不再出现整篇文案平铺两屏。 */
  function rcCleanLine(s) {
    var t = String(s || '');
    t = t.replace(/[\uD83C-\uDBFF][\uDC00-\uDFFF]|[\u2600-\u27BF\u2B00-\u2BFF]/g, '');
    t = t.replace(/#[^\s#]+/g, '');                 // #话题 整段去掉
    t = t.replace(/@(?=[\u4e00-\u9fa5A-Za-z])/g, ''); // @ 保留人名，去掉符号
    t = t.replace(/[「」"']/g, '').replace(/\s+/g, ' ').trim();
    t = t.replace(/^[，,、：:\-—\s]+|[，,、：:\-—\s]+$/g, '');
    return t;
  }
  function rcLinePool(data, mainTitle) {
    var raw = [];
    if (data.summary) raw.push(String(data.summary));
    (data.blocks || []).forEach(function (b) {
      if (head(b)) raw.push(String(head(b)));
      if (textOf(b)) raw.push(String(textOf(b)));
    });
    var base = String(mainTitle || '').split(/[｜|]/)[0].replace(/活动回顾$/, '').trim();
    var out = [], seen = {};
    function push(s) {
      var t = rcCleanLine(s);
      if (!t || t.length < 6) return;
      if (t.length > 54) {                 // 超过一行半就按逗号切开，只留前半
        var seg = t.split(/[，,]/);
        t = seg.slice(0, Math.max(2, Math.ceil(seg.length / 2))).join('，');
        if (t.length > 54) t = t.slice(0, 52);
      }
      if (!t || t.length < 6) return;
      if (/^(现场相册|活动回顾|精彩回顾|图文回顾|活动花絮|CLUB REVIEW)/i.test(t)) return;
      if (base && (t === base || base.indexOf(t) >= 0 || t.indexOf(base) >= 0)) return;
      if (/^(扫码|报名|咨询|关注|点击|长按|更多精彩)/.test(t)) return;
      var k = t.replace(/[\s，,。.、]/g, '');
      if (seen[k]) return;
      seen[k] = 1; out.push(t);
    }
    raw.join('\n').split(/[\n\r]+|[。！？!?；;]+/).forEach(push);
    return out;
  }
  /* 封面主标题断成两行（「以杉林为席，赴一场疗愈之约」→ 两行居中白字）。 */
  function rcTitleLines(title) {
    var t = String(title || '').split(/[｜|]/)[0].replace(/活动回顾$/, '').trim();
    if (!t) return ['活动回顾'];
    var parts = t.split(/[，,、\s]+/).filter(Boolean);
    if (parts.length >= 2) {
      var mid = Math.ceil(parts.length / 2);
      return [parts.slice(0, mid).join('，'), parts.slice(mid).join('，')].slice(0, 2);
    }
    if (t.length > 11) return [t.slice(0, 10), t.slice(10)];
    return [t];
  }
  /* ── 照片池：按上传顺序全部用上，绝不挤进一个「现场相册」 ──
     ① 封面 ② 序章后的满幅过渡照 ③ 结尾合影，其余均分成 2~6 个章节。 */
  function rcPhotoPlan(media, mm) {
    var refs = [];
    (media || []).forEach(function (m) {
      if (m && m.ref && rcUrl(m.ref, mm) && refs.indexOf(m.ref) < 0) refs.push(m.ref);
    });
    var cover = refs.shift() || '';
    var opener = refs.shift() || '';
    var finale = refs.length ? refs.pop() : '';
    var chapters = [];
    var n = Math.max(1, Math.min(6, Math.ceil(refs.length / 2.6)));
    for (var i = 0; i < n && refs.length; i++) {
      var take = Math.min(3, Math.ceil(refs.length / (n - i)));
      chapters.push(refs.splice(0, take));
    }
    while (refs.length) {                 // 照片特别多时，继续开新章而不是堆图墙
      var last = chapters.length ? chapters[chapters.length - 1] : [];
      if (last.length < 3) last.push(refs.shift());
      else chapters.push([refs.shift()]);
    }
    return { cover: cover, opener: opener, finale: finale, chapters: chapters };
  }
  /* 图注只用 AI 写的画面描述（desc）；没有描述就完全不写图注，
     绝不用文件名/哈希去凑（2026-11-04 用户截图里那行乱码就是这么来的）。 */
  function rcCaption(ref, mm) {
    var m = (mm && mm[ref]) || {};
    var d = String(m.desc || '').trim();
    return d ? '<figcaption class="rc-cap">' + esc(d) + '</figcaption>' : '';
  }
  function rcBleed(ref, mm) {
    if (!ref || !rcUrl(ref, mm)) return '';
    return '<figure class="rc-f-bleed">' + rcImgTag(ref, mm) + rcCaption(ref, mm) + '</figure>';
  }
  function rcBig(ref, mm) {
    if (!ref || !rcUrl(ref, mm)) return '';
    return '<figure class="rc-f-big">' + rcImgTag(ref, mm) + rcCaption(ref, mm) + '</figure>';
  }
  function rcDuo(refs, mm) {
    var list = (refs || []).filter(function (r) { return r && rcUrl(r, mm); });
    if (!list.length) return '';
    if (list.length === 1) return rcBig(list[0], mm);
    return '<div class="rc-duo">' + list.map(function (r) {
      return '<figure class="rc-f-pair">' + rcImgTag(r, mm) + rcCaption(r, mm) + '</figure>';
    }).join('') + '</div>';
  }
  /* 三种章节配图轮换：单张竖幅大图 / 双图并排 / 满幅出血 + 双图。 */
  function rcPhotos(refs, mm, variant) {
    var list = refs || [];
    if (!list.length) return '';
    if (variant === 2) return rcBleed(list[0], mm) + rcDuo(list.slice(1), mm);
    if (variant === 1) return rcDuo(list.slice(0, 2), mm) + rcBig(list[2], mm);
    return rcBig(list[0], mm) + rcDuo(list.slice(1), mm);
  }
  /* 收尾页二维码：父页面用本地 QR 库画好再内联成 data URI 塞进 srcdoc，
     iframe 是 sandbox（无 allow-scripts），不能在里面现画。 */
  async function rcQrDataUrl(ctx) {
    try {
      if (!window.ClubOSQR || !window.ClubOSQR.draw) return '';
      var cv = document.createElement('canvas');
      var link = location.origin + '/web?club_id=' + CLUB +
        (ctx && ctx.activityId ? '&activity=' + ctx.activityId : '');
      window.ClubOSQR.draw(cv, link);
      return cv.width ? cv.toDataURL('image/png') : '';
    } catch (e) { return ''; }
  }
  function recapHtml(data, mm, ctx, qr) {
    data = data || {}; ctx = ctx || {}; mm = mm || {};
    var mainTitle = String(data.title || ctx.title || '活动回顾');
    var titleLines = rcTitleLines(mainTitle);
    var lines = rcLinePool(data, mainTitle);
    var plan = rcPhotoPlan(data.media || [], mm);
    var brand = String(ctx.brandName || '').trim();
    var metaBits = [];
    if (ctx.date) metaBits.push(String(ctx.date));
    if (ctx.location) metaBits.push(String(ctx.location));

    // ① 封面：满幅照片 + 两行白色大标题，压暗只在上下两端，中间留给照片
    /* ★ 封面与合影页只用文档流 + 负 margin 叠字：foreignObject 光栅化会把
       position:absolute 的元素整批压到 (0,0)，实测导出成品封面变成三张照片叠在一起、
       白色大标题整个消失（预览里却是好的）。绝对定位在这条导出链里不可用。 */
    var coverTop = '<div class="rc-cover-top">' + esc(brand ? brand : 'CLUB REVIEW') + ' &nbsp;·&nbsp; ACTIVITY REVIEW</div>';
    var coverTx = '<div class="rc-cover-tx"><div class="rc-cover-rule"></div><h1 class="rc-cover-h1">' +
      titleLines.map(function (s) { return '<span>' + esc(s) + '</span>'; }).join('') +
      '</h1><div class="rc-cover-sub">' + esc(metaBits.join(' · ') || '活动回顾') + '</div></div>';
    var cover = plan.cover
      ? '<section class="rc-cover">' + coverTop +
        '<img class="rc-cover-img" src="' + esc(rcUrl(plan.cover, mm)) + '" alt="">' + coverTx + '</section>'
      : '<section class="rc-cover">' + coverTop + '<div class="rc-cover-ph"></div>' + coverTx + '</section>';

    // ② 序章：米白大留白，只放标题 + 最多两句短文案
    var lead = '<section class="rc-lead"><div class="rc-rule"></div>' +
      '<div class="rc-eyebrow">ACTIVITY REVIEW &nbsp;/&nbsp; 活动回顾</div>' +
      '<h2 class="rc-lead-h">' + esc(String(mainTitle).split(/[｜|]/)[0]) + '</h2>' +
      (lines[0] ? '<p class="rc-lead-p">' + esc(lines[0]) + '</p>' : '') +
      (lines[1] ? '<p class="rc-lead-p">' + esc(lines[1]) + '</p>' : '') +
      '</section>';

    // ③ 满幅过渡照 + 细分隔条
    var opener = plan.opener
      ? '<figure class="rc-opener"><img src="' + esc(rcUrl(plan.opener, mm)) + '" alt=""></figure>' +
        '<div class="rc-step">STEP 01 &nbsp;·&nbsp; ' + esc(RC_CH[0].e.toUpperCase()) + '</div>'
      : '';

    // ④ 章节页：巨大中文序号 + 章节名 + 一句短文案 + 1~3 张大图
    var chapters = plan.chapters.map(function (refs, i) {
      var ch = RC_CH[i % RC_CH.length];
      var no = RC_NUM[i % RC_NUM.length];
      var line = lines[2 + i] || '';
      var headHtml = '<div class="rc-ch-head">' +
        '<div class="rc-ch-no' + (i % 2 ? ' rc-ch-no--green' : '') + '">' + no + '</div>' +
        '<div class="rc-ch-name">' + esc(ch.c) + '</div>' +
        '<div class="rc-ch-en">' + esc(ch.e.toUpperCase()) + '</div>' +
        (line ? '<p class="rc-ch-line">' + esc(line) + '</p>' : '') + '</div>';
      var variant = i % 3;
      var photos = variant === 2
        ? rcBleed(refs[0], mm) + headHtml + rcDuo(refs.slice(1), mm)
        : headHtml + rcPhotos(refs, mm, variant);
      return '<section class="rc-chapter' + (i % 2 ? ' rc-chapter--alt' : '') + '">' + photos + '</section>';
    }).join('');

    // ⑤ 合影金句页
    var shorts = lines.filter(function (s) { return s.length <= 24; });
    var quote = shorts.length ? shorts[shorts.length - 1] : (lines.length ? lines[lines.length - 1] : '把这一天留在山野里');
    var finale = plan.finale
      ? '<section class="rc-finale"><img src="' + esc(rcUrl(plan.finale, mm)) + '" alt="">' +
        '<div class="rc-finale-tx"><div class="rc-finale-rule"></div>' +
        '<div class="rc-finale-q">' + esc(quote) + '</div>' +
        '<div class="rc-finale-s">' + esc(brand || '活动回顾') + '</div></div></section>'
      : '';

    // ⑥ 品牌绿收尾页 + 二维码
    var end = '<section class="rc-end">' +
      '<div class="rc-end-mark">' + esc(brand ? brand.toUpperCase() : 'CLUB REVIEW') + '</div>' +
      '<h2 class="rc-end-h"><span>山野不尽</span><span>我们一直在路上</span></h2>' +
      '<div class="rc-end-rule"></div>' +
      '<p class="rc-end-p">' + esc(brand ? '下一程，继续与 ' + brand + ' 同行' : '下一程山海，继续同行') + '</p>' +
      (qr ? '<div class="rc-qr"><img src="' + qr + '" alt="活动二维码"></div>' +
             '<div class="rc-qr-cap">扫码查看更多活动</div>' : '') +
      '<div class="rc-end-foot">ACTIVITY REVIEW &nbsp;·&nbsp; ' + esc(brand || 'CLUBOS') + '</div>' +
      '</section>';

    return cover + lead + opener + chapters + finale + end;
  }
  function recapDoc(html, ctx) {
    var css = [
      '*{box-sizing:border-box}',
      'html,body{margin:0;padding:0}',
      'body{width:750px;background:#F7F5EF;color:#2B2A26;' +
        'font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}',
      'img{display:block;max-width:100%}',
      'figure{margin:0}',
      'h1,h2,p{margin:0}',
      /* —— ① 封面 —— */
      '.rc-cover{background:#F7F5EF;overflow:hidden}',
      '.rc-cover-top{height:76px;line-height:76px;text-align:center;background:#1E4A43;color:rgba(255,255,255,.94);' +
        'font-size:17px;letter-spacing:7px;font-family:Georgia,serif}',
      '.rc-cover-img{width:750px;height:640px;object-fit:cover;display:block;max-width:none}',
      '.rc-cover-ph{width:750px;height:640px;background:linear-gradient(160deg,#22564C,#12332E)}',
      '.rc-cover-rule,.rc-finale-rule{width:64px;height:3px;background:#C8A86A;margin:0 auto 26px}',
      /* ★ 标题独立成深色块，不压在照片上：exportLongpicPng 最后会按显示框把照片
         逐张盖回画布，任何叠在照片上的文字都会被照片盖掉（实测封面白字整段消失）。 */
      '.rc-cover-tx{height:360px;padding-top:52px;text-align:center;color:#FFFDF7;background:#1E4A43}',
      '.rc-cover-h1{font-family:Georgia,"Songti SC","STSong","Noto Serif SC",serif;font-size:70px;line-height:1.34;' +
        'font-weight:600;letter-spacing:4px;text-shadow:0 4px 30px rgba(0,0,0,.5)}',
      '.rc-cover-h1 span{display:block;white-space:nowrap}',
      '.rc-cover-sub{margin-top:34px;font-size:19px;letter-spacing:5px;color:rgba(255,253,247,.86)}',
      /* —— ② 序章 —— */
      '.rc-lead{background:#F7F5EF;padding:96px 66px 104px}',
      '.rc-rule{width:64px;height:4px;background:#B99052;margin-bottom:34px}',
      '.rc-eyebrow{font-size:16px;letter-spacing:5px;color:#9A8B70;font-family:Georgia,serif;margin-bottom:26px}',
      '.rc-lead-h{font-family:Georgia,"Songti SC","STSong",serif;font-size:47px;line-height:1.42;' +
        'font-weight:600;color:#23221E;margin-bottom:46px}',
      '.rc-lead-p{font-family:Georgia,"Songti SC","STSong",serif;font-size:25px;line-height:2.05;' +
        'color:#4A463E;margin-bottom:22px}',
      /* —— ③ 满幅过渡照 —— */
      '.rc-opener{height:640px;overflow:hidden;background:#E5E1D6}',
      '.rc-opener img{width:750px;height:640px;object-fit:cover;max-width:none}',
      '.rc-step{height:104px;line-height:104px;text-align:center;background:#F7F5EF;color:#9A8B70;' +
        'font-size:16px;letter-spacing:6px;font-family:Georgia,serif}',
      /* —— ④ 章节页 —— */
      '.rc-chapter{background:#F7F5EF;padding:76px 0 92px}',
      '.rc-chapter--alt{background:#E9F0E9}',
      '.rc-ch-head{padding:0 66px}',
      '.rc-ch-no{font-family:Georgia,"Songti SC","STSong",serif;font-size:132px;line-height:.9;' +
        'color:#C4654A;margin-bottom:22px;font-weight:600}',
      '.rc-ch-no--green{color:#2E7D6B}',
      '.rc-ch-name{font-family:Georgia,"Songti SC","STSong",serif;font-size:43px;line-height:1.35;' +
        'font-weight:600;color:#23221E;letter-spacing:2px}',
      '.rc-ch-en{font-size:16px;letter-spacing:5px;color:#9A8B70;font-family:Georgia,serif;margin-top:10px}',
      '.rc-ch-line{font-family:Georgia,"Songti SC","STSong",serif;font-size:25px;line-height:2.0;' +
        'color:#4A463E;margin-top:30px;max-width:560px}',
      '.rc-f-big{margin:58px 56px 0;overflow:hidden;border-radius:10px;background:#E5E1D6}',
      '.rc-f-big img{width:638px;height:790px;object-fit:cover;max-width:none}',
      '.rc-f-bleed{margin:0 0 58px;overflow:hidden;background:#E5E1D6}',
      '.rc-f-bleed img{width:750px;height:620px;object-fit:cover;max-width:none}',
      '.rc-chapter .rc-f-bleed + .rc-ch-head{padding-top:0}',
      '.rc-duo{display:flex;flex-wrap:wrap;gap:16px;margin:22px 56px 0;padding:0}',
      '.rc-f-pair{flex:0 0 calc(50% - 8px);min-width:0;overflow:hidden;border-radius:10px;background:#E5E1D6}',
      '.rc-f-pair img{width:100%;height:430px;object-fit:cover}',
      '.rc-f-big + .rc-duo,.rc-f-bleed + .rc-duo{margin-top:16px}',
      '.rc-cap{font-size:17px;line-height:1.6;color:#7D7669;padding:12px 4px 0;font-family:Georgia,"Songti SC",serif}',
      /* —— ⑤ 合影金句页 —— */
      '.rc-finale{overflow:hidden;background:#17332E}',
      '.rc-finale>img{width:750px;height:620px;object-fit:cover;display:block;max-width:none}',
      '.rc-finale-tx{height:280px;padding-top:52px;text-align:center;color:#FFFDF7;background:#17332E}',
      '.rc-finale-q{font-family:Georgia,"Songti SC","STSong",serif;font-size:49px;line-height:1.5;' +
        'font-weight:600;letter-spacing:3px;text-shadow:0 3px 22px rgba(0,0,0,.55)}',
      '.rc-finale-s{margin-top:26px;font-size:18px;letter-spacing:6px;color:rgba(255,253,247,.8)}',
      /* —— ⑥ 品牌绿收尾 —— */
      '.rc-end{background:#2E8B7A;color:#FFFDF7;text-align:center;padding:116px 66px 92px}',
      '.rc-end-mark{font-size:16px;letter-spacing:7px;font-family:Georgia,serif;color:rgba(255,255,255,.78)}',
      '.rc-end-h{font-family:Georgia,"Songti SC","STSong",serif;font-size:52px;line-height:1.6;' +
        'font-weight:600;letter-spacing:4px;margin:52px 0 0}',
      '.rc-end-h span{display:block}',
      '.rc-end-rule{width:56px;height:2px;background:rgba(255,255,255,.6);margin:44px auto 34px}',
      '.rc-end-p{font-size:22px;line-height:1.9;color:rgba(255,255,255,.9);font-family:Georgia,"Songti SC",serif}',
      '.rc-qr{width:264px;height:264px;background:#fff;border-radius:14px;margin:62px auto 0;padding:22px}',
      '.rc-qr img{width:220px;height:220px}',
      '.rc-qr-cap{font-size:18px;letter-spacing:3px;color:rgba(255,255,255,.86);margin-top:22px}',
      '.rc-end-foot{font-size:15px;letter-spacing:5px;color:rgba(255,255,255,.66);margin-top:84px;font-family:Georgia,serif}'
    ].join('');
    /* ★★ 2026-11-04 导出实锤：exportLongpicPng 用 XMLSerializer 只序列化 clean.body，
       head 里的 <style> 根本进不了 foreignObject —— 模型直出 HTML 用行内样式所以一直没暴露，
       而本模板是类名 CSS，导出成品就只剩一堆原尺寸照片、文字与色块全丢。
       把 <style> 放进 body 的第一个子节点，序列化时才会跟着进 SVG 并生效。 */
    return '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"></head><body class="rc-doc">' +
      '<style>body{background:#F7F5EF}' + css + '</style>' + hardenLongpic(html) + '</body></html>';
  }
  async function paintRecap(data, ctx, stage) {
    var mm = recapMediaMap(data);
    var qr = await rcQrDataUrl(ctx);
    var html = recapHtml(data, mm, ctx, qr);
    stage.innerHTML = '<div class="ch-long-wrap"><div class="ch-long-frame"><iframe id="chRecapFrame" title="活动回顾预览" sandbox="allow-same-origin" referrerpolicy="no-referrer"></iframe></div><div class="ch-long-meta"><span>正在排版…</span></div></div>';
    var frame = stage.querySelector('#chRecapFrame');
    frame.srcdoc = recapDoc(html, ctx);
    try {
      await new Promise(function (res) {
        var done = false;
        var finish = function () { if (!done) { done = true; res(1); } };
        frame.onload = function () {
          var d = frame.contentDocument; if (!d) return finish();
          var imgs = Array.prototype.slice.call(d.images || []);
          if (!imgs.length) return setTimeout(finish, 300);
          var left = imgs.filter(function (i) { return !i.complete; }).length;
          if (!left) return setTimeout(finish, 200);
          var timer = setTimeout(finish, 4000);
          imgs.forEach(function (i) {
            i.addEventListener('load', function () { if (--left <= 0) { clearTimeout(timer); finish(); } });
            i.addEventListener('error', function () { if (--left <= 0) { clearTimeout(timer); finish(); } });
          });
        };
        setTimeout(finish, 6000);
      });
    } catch (e) { /* 预览失败不该挡住「复制 / 下载」两条出路 */ }
    var h = 0;
    try { h = frame.contentDocument ? frame.contentDocument.body.scrollHeight : 0; } catch (e2) { h = 0; }
    frame.style.height = (h || 1200) + 'px';
    var holder = stage.querySelector('.ch-long-frame');
    fitLongpicFrame(frame, holder, h || 1200);
    var meta = stage.querySelector('.ch-long-meta span');
    if (meta && h) meta.textContent = '宽度 ' + LONG_WIDTH + 'px（杂志式长图）· 实际高度约 ' + h + 'px';
    return { html: html, frame: frame };
  }
  function recapRichHtml(data, ctx) {
    data = data || {}; var mm = recapMediaMap(data);
    var out = '<div style="font-family:-apple-system,\'PingFang SC\',sans-serif;color:#2e2a24;max-width:680px">';
    out += '<h1 style="font-size:30px;margin:0 0 10px">' + esc(data.title || (ctx && ctx.title) || '活动回顾') + '</h1>';
    if (data.summary) out += '<p style="color:#5b554a;font-size:16px;line-height:1.7">' + esc(data.summary) + '</p>';
    (data.blocks || []).forEach(function (b) {
      var h = esc(head(b));
      var t = paras(textOf(b)).map(function (p) { return '<p style="font-size:15px;line-height:1.8">' + esc(p) + '</p>'; }).join('');
      var refs = (b.mediaRefs || []).filter(Boolean);
      var imgs = refs.map(function (r) {
        var m = mm[r] || {}; var raw = m.url || ''; var u = raw ? abs(recapMediaUrl(raw)) : '';
        return u ? '<img src="' + esc(u) + '" style="width:100%;margin:8px 0">' : '';
      }).join('');
      if (h) out += '<h2 style="font-size:20px;margin:16px 0 6px">' + h + '</h2>';
      out += t + imgs;
    });
    out += '</div>';
    return out;
  }
  function recapPlainText(data, ctx) {
    data = data || {}; var lines = [data.title || (ctx && ctx.title) || '活动回顾'];
    if (data.summary) lines.push(data.summary);
    (data.blocks || []).forEach(function (b) {
      var h = head(b); if (h) lines.push('\n' + h);
      paras(textOf(b)).forEach(function (p) { lines.push(p); });
    });
    return lines.join('\n');
  }

  window.openChannelOutput = openChannelOutput;
  window.ChannelRender = {
    label: function (c) { return LABEL[c] || c; },
    icon: function (c) { return ICON[c] || '📄'; }
  };
})();

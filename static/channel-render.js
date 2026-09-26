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

  var LABEL = { wechat: '微信公众号图文', xhs: '小红书图文', poster: '活动招募海报', recap: '活动回顾' };
  var ICON = { wechat: '📰', xhs: '📕', poster: '🖼', recap: '📷' };
  var _chSeq = 0;   /* 成品预览层标题 id 计数：叠开两层时 aria-labelledby 不能撞名 */
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
  function toUrl(x, mm) {
    var s = String(x || '');
    if (!s) return '';
    if (mm && mm[s] && mm[s].url) return abs(mm[s].url);
    if (/^(https?:|\/|data:|blob:)/.test(s)) return abs(s);
    return '';
  }

  function firstMediaUrl(mm) {
    var k = Object.keys(mm || {});
    for (var i = 0; i < k.length; i++) { if (mm[k[i]] && mm[k[i]].url) return abs(mm[k[i]].url); }
    return '';
  }

  /* ---------- 活动上下文（媒体清单 + 真实事实）---------- */

  var _ctxCache = {};
  function loadCtx(activityId) {
    if (_ctxCache[activityId]) return _ctxCache[activityId];
    var p = api('/api/club/' + CLUB + '/activities/' + activityId).then(function (a) {
      var master = a.activityMaster || {};
      var mm = mediaMap(master);
      return {
        club: CLUB,
        activityId: Number(activityId),
        master: master,
        mm: mm,
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

  /* ---------- 小红书：以「文案」为主，配一张首页海报 + 其它图片自动裁剪 3:4 ---------- */

  function xhsModel(data, ctx) {
    var mm = ctx.mm;
    var titles = (data.titleOptions || []).filter(Boolean);
    var body = data.body || data.summary || '';
    // 真实活动图片：模型指定的顺序优先，再补媒体清单里其余的；去重
    var seen = {}, all = [];
    (data.imageSequence || []).forEach(function (x) {
      var u = toUrl(x, mm); if (u && !seen[u]) { seen[u] = 1; all.push(u); }
    });
    Object.keys(mm).forEach(function (k) {
      var u = mm[k] && mm[k].url ? abs(mm[k].url) : '';
      if (u && !seen[u]) { seen[u] = 1; all.push(u); }
    });
    var hero = all[0] || ctx.coverUrl || '';   // 首页海报所用的那张
    // 「其它图片」：自动裁成 3:4 当作九宫格配图；上限 11 张（首页海报 + 11 ≈ 小红书单篇 12 图）
    var photos = all.slice(1, 12);
    return {
      title: titles[0] || data.title || ctx.title,
      titleOptions: titles,
      hook: data.hook || '',
      body: body,
      tags: (data.tags || []).map(function (t) { return String(t).replace(/^#/, ''); }),
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

  /* ---------- 海报（canvas 1080×1440）---------- */

  async function paintPoster(data, ctx, host) {
    var W = 1080, H = 1440, PAD = 84;
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    var c = cv.getContext('2d');

    var bgUrl = toUrl((data.preferredMediaRefs || [])[0], ctx.mm) || ctx.coverUrl || firstMediaUrl(ctx.mm);
    var img = bgUrl ? await loadImgSafe(bgUrl) : null;

    if (img) { c.save(); drawCover.call(c, img, 0, 0, W, H); c.restore(); }
    else {
      var base = c.createLinearGradient(0, 0, W, H);
      base.addColorStop(0, '#1f4a37'); base.addColorStop(1, '#2c755e');
      c.fillStyle = base; c.fillRect(0, 0, W, H);
    }
    var g = c.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, 'rgba(6,17,12,.72)');
    g.addColorStop(0.45, 'rgba(6,17,12,.42)');
    g.addColorStop(1, 'rgba(6,17,12,.93)');
    c.fillStyle = g; c.fillRect(0, 0, W, H);

    var y = PAD + 60;
    // 顶部：活动组织 + 招募
    c.font = '800 26px ' + FONT;
    c.fillStyle = 'rgba(214,235,224,.92)';
    c.fillText((data.brand || '活动招募').toUpperCase(), PAD, y);
    c.fillStyle = 'rgba(214,235,224,.55)';
    c.fillText(String(ctx.date || '').toUpperCase(), PAD, y + 44);
    y += 128;

    // 主标题
    c.font = '900 92px ' + SERIF;
    c.fillStyle = '#fff';
    var hl = wrap(c, data.headline || ctx.title, W - PAD * 2).slice(0, 3);
    y = drawLines(c, hl, PAD, y + 60, 112, 3) + 16;

    // 副标题
    if (data.subheadline) {
      c.font = '400 36px ' + FONT;
      c.fillStyle = 'rgba(238,246,242,.9)';
      y = drawLines(c, wrap(c, data.subheadline, W - PAD * 2).slice(0, 3), PAD, y + 30, 56) + 14;
    }

    // 关键事实
    var facts = (data.facts || []).filter(Boolean).slice(0, 4);
    var auto = [];
    if (ctx.location) auto.push(ctx.location);
    if (ctx.price || ctx.price === 0) auto.push('¥' + ctx.price + ' / 人');
    if (ctx.capacity) auto.push('限 ' + ctx.capacity + ' 人');
    if (!facts.length) facts = auto;
    if (facts.length) {
      y += 26;
      c.font = '700 34px ' + FONT;
      facts.forEach(function (f) {
        var t = String(f);
        var w = c.measureText(t).width + 52;
        w = Math.min(w, W - PAD * 2);
        c.fillStyle = 'rgba(255,255,255,.16)';
        rr(c, PAD, y - 34, w, 66, 33); c.fill();
        c.fillStyle = '#fff';
        c.fillText(t, PAD + 26, y + 10);
        y += 84;
      });
    }

    // 卖点（从底部往上排，避免长标题挤掉 CTA）
    var points = (data.sellingPoints || []).filter(Boolean).slice(0, 4);
    if (points.length) {
      var py = H - PAD - 150;
      for (var i = points.length - 1; i >= 0; i--) {
        c.font = '400 34px ' + FONT;
        var ls = wrap(c, String(points[i]), W - PAD * 2 - 46).slice(0, 3);
        py -= (ls.length - 1) * 50 + 46;
        c.fillStyle = '#8cc2ad';
        c.fillText('—', PAD, py + 10);
        c.fillStyle = 'rgba(255,255,255,.94)';
        drawLines(c, ls, PAD + 46, py + 10, 50);
        py -= 20;
        if (py < y + 40) break;
      }
    }

    // 底部 CTA
    c.fillStyle = '#fff';
    rr(c, PAD, H - PAD - 104, W - PAD * 2, 104, 52); c.fill();
    c.fillStyle = '#14201c';
    c.font = '800 38px ' + FONT;
    var cta = String(data.cta || '扫码报名').slice(0, 22);
    var cw = c.measureText(cta).width;
    c.fillText(cta, (W - cw) / 2, H - PAD - 38);

    host.innerHTML = '<div class="ch-poster-hold"></div>';
    host.querySelector('.ch-poster-hold').appendChild(cv);
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
    if (!activityId) { showAlert({ title: '无法渲染', message: '缺少活动信息，无法渲染成品。' }); return; }
    /* 渠道 key 不在白名单里时直接拒绝：以前会渲染出「undefined xxx」这种半成品标题。 */
    if (!LABEL[channel]) { showAlert({ title: '无法渲染', message: '未知的内容渠道：' + channel + '。可用渠道为公众号 / 小红书 / 海报 / 回顾。' }); return; }

    var ctx;
    try { ctx = await loadCtx(activityId); }
    catch (e) { showAlert({ title: '无法渲染', message: '读取活动资料失败：' + e.message }); return; }

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
      '<div class="sub">' + esc(headTitle) + ' · 活动：' + esc(ctx.title) + '</div></div>' +
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

    var canvases = [];

    if (channel === 'poster') {
      canvases = await paintPoster(data, ctx, stage);
      btn('下载海报 PNG', '', async function () {
        await downloadCanvas(canvases[0], safeName(ctx.title) + '_海报_1080x1440.png');
        toast('海报已下载（1080×1440 PNG）');
      });
      var ptxt = [data.headline || ctx.title, data.subheadline || '', (data.facts || []).join(' · '), (data.sellingPoints || []).join('\n'), data.cta || ''].filter(Boolean).join('\n');
      btn('复制海报文案', 'secondary', async function () {
        (await copyText(ptxt)) ? toast('文案已复制') : showAlert({ title: '复制失败', message: '请手动选中文字复制。' });
      });
      note.textContent = '海报用活动真实封面 + AI 文案在本地合成，不额外消耗 Credits。';
      return;
    }

    if (channel === 'xhs') {
      var xm = xhsModel(data, ctx);
      var painted = await paintXhs(xm, ctx, stage);
      var caption = [xm.title, '', xm.hook, '', xm.body, '', xm.tags.map(function (t) { return '#' + t; }).join(' ')].filter(function (x) { return x !== undefined && x !== null; }).join('\n');
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

  window.openChannelOutput = openChannelOutput;
  window.ChannelRender = {
    label: function (c) { return LABEL[c] || c; },
    icon: function (c) { return ICON[c] || '📄'; }
  };
})();

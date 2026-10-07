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
  /*上传素材一律走公开代理，不能直接吃 /static/uploads/* ——
     生产环境 security_v025 把那条路径整个封 404（俱乐部端也封），
     长图里塞裸链 = 5 张图全裂，且**预览不报错**，只是白框（2026-10-07 实测）。
     库里的 url 保持裸路径不动（代理路由的白名单就是拿它比对 的），
     改写只发生在这里。*/
  var _mediaActivityId = 0;
  function setMediaActivityId(id) { _mediaActivityId = Number(id) || 0; }
  function mediaUrl(u) {
    var s = String(u || '');
    if (!/^\/static\/uploads\//.test(s)) return s;
    if (!_mediaActivityId) return s;          // 拿不到活动 id 就原样返回，别拼出坏链
    var rel = s.replace(/^\/static\//, '');
    return '/api/public/activities/' + _mediaActivityId + '/media/' +
      rel.split('/').map(encodeURIComponent).join('/');
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

  // 模型可能把占位符写成裸文本而不是 <img src="...">，两种都要能救回来
  function resolveLongpic(html, mm) {
    var t = String(html || '');
    // ① img 标签里的占位符
    t = t.replace(/(<img\b[^>]*?src=["'])\s*\{\{\s*media\s*:\s*([A-Za-z0-9_-]+)\s*\}\}\s*(["'])/gi,
      function (_, pre, ref, post) {
        var u = toUrl(ref, mm);
        return u ? pre + esc(u) + post : '';
      });
    // ② 没有任何 src 的 img（模型只写了 {{media:xx}}）→ 丢掉，防止留下 <img src="">
    t = t.replace(/<img\b[^>]*src=["']\s*["'][^>]*>/gi, '');
    // ③ 裸占位符：不在 img 里的（实测 qwen3-max 会这么写）→ 保留原样，不当成 URL
    t = t.replace(/\s*\{\{\s*media\s*:\s*([A-Za-z0-9_-]+)\s*\}\}/g,
      function (whole, ref) { return toUrl(ref, mm) ? '' : whole; });
    return t;
  }

  /* 模型产物永远不该有能力跳出 iframe：只允许同源 / 数据 URL，其余 src 一律剥掉。
     服务端已 sanitize_html 过（script、on*、javascript: 全删），这里是第二道。 */
  function hardenLongpic(html) {
    return String(html || '').replace(/(src|href)\s*=\s*(["'])([^"']*)\2/gi,
      function (whole, attr, q, val) {
        var v = String(val || '');
        if (/^(https?:)?\/\//i.test(v)) {
          try { if (new URL(v, location.href).origin === location.origin) return whole; } catch (e) { return ''; }
        }
        if (/^data:image\//i.test(v)) return whole;
        return '';
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

  async function paintLongpic(data, ctx, stage) {
    // 图片要改写成公开代理地址，先记住是哪场活动（toUrl 靠它拼）
    setMediaActivityId(ctx.activityId);
    var mm = ctx.mm;
    var html = resolveLongpic(data.html || data.body || '', mm);
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
    var scaled = window.matchMedia && window.matchMedia('(max-width:800px)').matches ? 0.52 : 1;
    var holder = stage.querySelector('.ch-long-frame');
    if (holder && scaled !== 1) holder.style.height = Math.ceil((h || 1200) * scaled) + 'px';
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
  function _inlineOne(im) {
    var cv2 = document.createElement('canvas');
    cv2.width = im.naturalWidth; cv2.height = im.naturalHeight;
    cv2.getContext('2d').drawImage(im, 0, 0);
    im.setAttribute('src', cv2.toDataURL(_INLINE_MIME, _INLINE_Q));
  }

  async function inlineImages(doc) {
    var imgs = Array.prototype.slice.call(doc.images || []);
    var failed = 0;
    await Promise.all(imgs.map(function (im) {
      if (!im.getAttribute('src')) return null;
      if (/^data:/i.test(im.getAttribute('src'))) return null;   // 已经是内联的跳过
      return new Promise(function (res) {
        var finish = function () {
          if (im.complete && im.naturalWidth > 0) {
            try { _inlineOne(im); } catch (e) { failed++; /* canvas 被污染：留原 src */ }
          } else {
            failed++;                    // 压根没加载出来的图，也要如实计数，不许静默留白
          }
          res(1);
        };
        im.addEventListener('load', finish, { once: true });
        im.addEventListener('error', function () {
          // 裂图换成 1×1 透明像素，别让 foreignObject 为一张挂掉的图整块失败
          im.setAttribute('src', 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7');
          failed++;
          res(1);
        }, { once: true });
        if (im.complete) finish();          // 已加载完的不会触发 load，得主动处理
      });
    }));
    return { total: imgs.length, failed: failed };
  }

  async function exportLongpicPng(paint, ctx, fileBase) {
    var frame = paint.frame;
    var doc = null;
    try { doc = frame.contentDocument; } catch (e) { doc = null; }
    if (!doc) throw new Error('预览尚未就绪');
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
    var style = doc.querySelector('style');          // 地板样式带上，字距才不会变
    if (style) clean.head.appendChild(style.cloneNode(true));
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

    /*★ 必须用 XMLSerializer，不能用 innerHTML（2026-10-07 实测踩死在这）。
       foreignObject 里由 **XML 解析器**处理，而 innerHTML 是 **HTML 序列化**：
       <img src=x>、<br> 这类不闭合标签、以及 &nbsp; 在 XML 里都是**语法错误**，
       整个 SVG 解析失败 → img.onload 永远不来，只剩 onerror。 */
    var inner = new XMLSerializer().serializeToString(clean.body);
    // HTML 里定义过的实体在 XML 里未定义，序列化后仍会以实体名出现
    inner = inner.replace(/&nbsp;/gi, ' ');

    var svg = '<?xml version="1.0" encoding="UTF-8"?>' +
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H + '">' +
      '<foreignObject x="0" y="0" width="' + W + '" height="' + H + '">' + inner +
      '</foreignObject></svg>';

    /* ★ 必须用 data URI，**不能**用 Blob URL（2026-10-07 实测对照两条路径）：
         data URI → 图片加载正常，canvas.toBlob 出图成功；
         Blob URL → canvas 直接被判 Tainted，`toBlob` 抛 "Tainted canvases may not be exported"。
       原因是浏览器给 blob: 的 SVG 标了不透明来源，画进 canvas 就污染了。
       （先前误以为 data URI 会因长度失败而改用 Blob URL，正好踩中这个坑；
         内联图换成 JPEG 后 SVG 只有 ~1.5MB，data URI 完全够用。） */
    var img = await new Promise(function (res, rej) {
      var im = new Image();
      im.onload = function () { res(im); };
      im.onerror = function () { rej(new Error('浏览器无法把排版结果转成图片')); };
      im.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    });

    var cv = document.createElement('canvas');
    cv.width = W * 2; cv.height = H * 2;             // 2 倍图：公众号里缩排也不发虚
    var c = cv.getContext('2d');
    c.fillStyle = '#fff'; c.fillRect(0, 0, cv.width, cv.height);
    c.drawImage(img, 0, 0, W * 2, H * 2);

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
    return inlineStat;      // 交给调用方如实告知「有几张图没能载入」
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
      btn('下载长图（750px 宽 · 2倍图）', '', async function () {
        try {
          btn.disabled = true;
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
          showAlert({ title: '下载失败', message: '这个浏览器不允许把预览转成图片。' +
            '可以在预览区直接按 Command+P 存成 PDF，或用系统截图。' + (e2 && e2.message ? '（' + e2.message + '）' : '') });
        } finally { btn.disabled = false; }
      });
      btn('复制图文（粘贴进公众号编辑器）', 'secondary', async function () {
        var docHtml = '<div style="width:' + LONG_WIDTH + 'px;font-family:-apple-system,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;">' + paint.html + '</div>';
        var txt = doc.body ? doc.body.innerText : '';
        (await copyRich(docHtml, txt)) ? toast('已复制带格式图文，去公众号编辑器粘贴即可')
          : showAlert({ title: '复制失败', message: '浏览器拒绝了剪贴板权限，可改用「复制纯文本」。' });
      });
      btn('复制纯文本', 'secondary', async function () {
        var txt = paint.frame.contentDocument ? paint.frame.contentDocument.body.innerText : (data.title || '');
        (await copyText(txt)) ? toast('纯文本已复制') : showAlert({ title: '复制失败', message: '请手动选中文字复制。' });
      });
      var bits = '模型自己排的版式，直接按它的成品导出，不走固定模板。';
      if (paint.dropped > 0) bits += ' 有 ' + paint.dropped + ' 张配图被剔除（不是本活动的真实照片）。';
      note.textContent = bits;
      return;
    }

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

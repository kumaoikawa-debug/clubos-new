/* ===========================================================================
 * ClubOS 营地图片裁剪器（#813，2026-11-11）
 * 四类对象共用：营地档案 / 营位 / 装备租赁 / 小站商品。
 *
 * 交互模型：取景框固定居中，拖动 / 缩放照片决定保留哪一块。
 *   - 打开时先用「主体能量」算法自动把主体放进取景框（自动适配最佳显示范围）
 *   - 可手动拖拽、缩放、切换 4:3 / 1:1 / 16:9
 *   - 焦点十字 = 主体在成品图中的相对位置，C 端据此 object-position 二次裁切
 * 输出：{ratio, crop:[x,y,w,h]（源图归一化）, focalX, focalY}
 * 只产出参数，真正的裁剪由服务端 PIL 完成，保证 C 端视觉一致。
 * ========================================================================= */
(function () {
  var RATIOS = { '4:3': 4 / 3, '1:1': 1, '16:9': 16 / 9 };
  var SW = 460, SH = 320, PAD = 18;

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function loadImg(src) {
    return new Promise(function (res, rej) {
      var im = new Image();
      im.onload = function () { res(im); };
      im.onerror = function () { rej(new Error('图片加载失败')); };
      im.src = src;
    });
  }

  /* 主体检测：天空/白墙是平滑低纹理区，地景/建筑/人物梯度大。
     按行求能量质心并对下半区加权，与后端 _camp_auto_focal 同口径。 */
  function autoFocal(im) {
    try {
      var cv = document.createElement('canvas'); cv.width = 64; cv.height = 64;
      var cx = cv.getContext('2d', { willReadFrequently: true });
      cx.drawImage(im, 0, 0, 64, 64);
      var d = cx.getImageData(0, 0, 64, 64).data;
      function g(x, y) {
        var i = (y * 64 + x) * 4;
        return d[i] * .299 + d[i + 1] * .587 + d[i + 2] * .114;
      }
      var rows = [], cols = [], y, x, s;
      for (y = 0; y < 64; y++) { s = 1; for (x = 0; x < 63; x++) s += Math.abs(g(x, y) - g(x + 1, y)); rows.push(s); }
      for (x = 0; x < 64; x++) { s = 1; for (y = 0; y < 63; y++) s += Math.abs(g(x, y) - g(x, y + 1)); cols.push(s); }
      var rw = rows.map(function (v, i) { return v * v * (1 + .55 * (i / 63)); });
      var cw = cols.map(function (v) { return v * v; });
      var sr = rw.reduce(function (a, b) { return a + b; }, 0);
      var sc = cw.reduce(function (a, b) { return a + b; }, 0);
      var fy = rw.reduce(function (a, v, i) { return a + v * (i + .5); }, 0) / sr / 64;
      var fx = cw.reduce(function (a, v, i) { return a + v * (i + .5); }, 0) / sc / 64;
      if (!isFinite(fx)) fx = .5;
      if (!isFinite(fy)) fy = .5;
      return { x: clamp(fx, .2, .8), y: clamp(fy, .2, .82) };
    } catch (e) { return { x: .5, y: .5 }; }
  }

  /* 打开裁剪弹层。src 可为 blob: / data: / 同源代理 URL。
     resolve(null) = 取消；resolve({ratio,crop,focalX,focalY}) = 确定。 */
  window.campCropOpen = function (opt) {
    opt = opt || {};
    var src = opt.src || '', ratio = RATIOS[opt.ratio] ? opt.ratio : '4:3';
    if (!src) return Promise.resolve(null);
    return loadImg(src).then(function (im) {
      return new Promise(function (resolve) {
        var iw = im.naturalWidth || im.width, ih = im.naturalHeight || im.height;
        var body = '<div class="ccrop">'
          + '<div class="ccrop__stage" id="ccropStage">'
          + '<img id="ccropImg" src="' + String(src).replace(/"/g, '&quot;') + '" alt="">'
          + '<div class="ccrop__frame" id="ccropFrame"><span class="ccrop__cross" id="ccropCross"></span></div>'
          + '</div>'
          + '<div class="ccrop__bar"><span class="ccrop__lb">显示比例</span>'
          + Object.keys(RATIOS).map(function (r) { return '<button type="button" class="ccrop__ratio" data-r="' + r + '">' + r + '</button>'; }).join('')
          + '<button type="button" class="ccrop__auto" id="ccropAuto">自动适配最佳范围</button>'
          + '<button type="button" class="ccrop__reset" id="ccropReset">重置</button></div>'
          + '<div class="ccrop__bar"><span class="ccrop__lb">缩放</span>'
          + '<input type="range" id="ccropZoom" min="1" max="3" step="0.01" value="1">'
          + '<span class="ccrop__tip">拖动画面调整取景 · 十字为 C 端自动对齐的焦点</span></div>'
          + '<div class="ccrop__note" id="ccropNote"></div>'
          + '</div>';
        var settled = false;
        var ov = window.uxDialog({
          title: opt.title || '裁剪照片',
          desc: '拖动照片、调整缩放与比例，让主体落在取景框里；「自动适配」会识别主体并自动避开大片天空。',
          wide: true, body: body,
          foot: '<button class="btn ghost" type="button" data-cancel>取消</button>'
            + '<button class="btn" type="button" data-ok>使用这张取景</button>',
          onClose: function () { if (!settled) { settled = true; resolve(null); } }
        });
        if (!ov) { resolve(null); return; }
        var stage = ov.querySelector('#ccropStage'), img = ov.querySelector('#ccropImg');
        var frame = ov.querySelector('#ccropFrame'), cross = ov.querySelector('#ccropCross');
        var zoomEl = ov.querySelector('#ccropZoom'), note = ov.querySelector('#ccropNote');
        var focus = autoFocal(im);           // 主体在源图中的归一化位置
        var zoom = 1, left = 0, top = 0, dw = 0, dh = 0, fw = 0, fh = 0, fx0 = 0, fy0 = 0;
        var drag = null;

        function layout() {
          var ar = RATIOS[ratio];
          var availW = SW - PAD * 2, availH = SH - PAD * 2;
          fw = Math.min(availW, availH * ar); fh = fw / ar;
          if (fh > availH) { fh = availH; fw = fh * ar; }
          fx0 = (SW - fw) / 2; fy0 = (SH - fh) / 2;
          var base = Math.max(fw / iw, fh / ih) * zoom;
          dw = iw * base; dh = ih * base;
          frame.style.cssText = 'left:' + fx0 + 'px;top:' + fy0 + 'px;width:' + fw + 'px;height:' + fh + 'px';
          ov.querySelectorAll('.ccrop__ratio').forEach(function (b) {
            b.classList.toggle('on', b.getAttribute('data-r') === ratio);
          });
        }
        function keep() {
          var minL = fx0 + fw - dw, maxL = fx0;
          var minT = fy0 + fh - dh, maxT = fy0;
          left = clamp(left, minL, maxL); top = clamp(top, minT, maxT);
        }
        function paint() {
          img.style.width = dw + 'px'; img.style.height = dh + 'px';
          img.style.left = left + 'px'; img.style.top = top + 'px';
          var px = clamp((focus.x * dw - left - fx0) / fw, .02, .98);
          var py = clamp((focus.y * dh - top - fy0) / fh, .02, .98);
          cross.style.left = (px * 100) + '%'; cross.style.top = (py * 100) + '%';
          if (note) note.textContent = '焦点 ' + Math.round(px * 100) + '% / ' + Math.round(py * 100) + '%'
            + ' · 取景 ' + ratio + ' · 缩放 ' + zoom.toFixed(2) + '×';
          return { px: px, py: py };
        }
        function centerOnFocus() {
          var base = Math.max(fw / iw, fh / ih) * zoom;
          dw = iw * base; dh = ih * base;
          left = fx0 + fw / 2 - focus.x * dw;
          top = fy0 + fh / 2 - focus.y * dh;
          keep(); paint();
        }
        function reset() { zoom = 1; zoomEl.value = '1'; layout(); centerOnFocus(); }

        layout(); centerOnFocus();

        stage.addEventListener('pointerdown', function (e) {
          if (e.target.closest && e.target.closest('.ccrop__ratio')) return;
          drag = { x: e.clientX, y: e.clientY, l: left, t: top };
          try { stage.setPointerCapture(e.pointerId); } catch (err) { }
          e.preventDefault();
        });
        stage.addEventListener('pointermove', function (e) {
          if (!drag) return;
          left = drag.l + (e.clientX - drag.x); top = drag.t + (e.clientY - drag.y);
          keep(); paint();
        });
        ['pointerup', 'pointercancel'].forEach(function (ev) {
          stage.addEventListener(ev, function () { drag = null; });
        });
        zoomEl.addEventListener('input', function () {
          var cx = (fx0 + fw / 2 - left) / dw, cy = (fy0 + fh / 2 - top) / dh;
          zoom = parseFloat(zoomEl.value) || 1;
          var base = Math.max(fw / iw, fh / ih) * zoom;
          dw = iw * base; dh = ih * base;
          left = fx0 + fw / 2 - cx * dw; top = fy0 + fh / 2 - cy * dh;
          keep(); paint();
        });
        ov.querySelectorAll('.ccrop__ratio').forEach(function (b) {
          b.addEventListener('click', function () { ratio = b.getAttribute('data-r'); reset(); });
        });
        ov.querySelector('#ccropAuto').addEventListener('click', function () { reset(); });
        ov.querySelector('#ccropReset').addEventListener('click', function () { focus = { x: .5, y: .5 }; reset(); });
        ov.querySelector('[data-cancel]').addEventListener('click', function () { ov.uxClose(); });
        // uxDialog 给 foot 的 [data-ok] 预置了「点击即关闭」的 onclick，
        // 必须用 onclick 覆盖（addEventListener 会晚于它执行，导致关闭回调先 resolve(null)）。
        ov.querySelector('[data-ok]').onclick = function () {
          if (settled) return;
          settled = true;
          var f = paint();
          var cw = clamp(fw / dw, 0, 1), ch = clamp(fh / dh, 0, 1);
          var cxx = clamp((fx0 - left) / dw, 0, 1 - cw);
          var cyy = clamp((fy0 - top) / dh, 0, 1 - ch);
          resolve({ ratio: ratio, crop: [cxx, cyy, cw, ch], focalX: f.px, focalY: f.py, autoFit: false });
          ov.uxClose();
        };
      });
    }).catch(function (e) {
      if (window.toast) window.toast(e.message || '图片加载失败');
      return null;
    });
  };
})();

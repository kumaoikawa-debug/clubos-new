/* 商品图片管理器（总平台）：头图 + 详情图集
   ─────────────────────────────────────────────
   取代旧的「配图片地址」文本框（那个文本框必须先把图传到别处再粘地址，割裂）。
   现在：本地批量选文件 → 浏览器端自动裁到最佳比例（封面 1:1 / 详情 3:4）→ 直接落盘到
   服务端。每张图都能点「裁剪」手动微调（拖拽平移 + 缩放 + 比例预设）。

   后端配套：
   · POST /api/platform/products/{id}/cover        （单文件 → cover_url + image_url）
   · POST /api/platform/products/{id}/images/batch （多文件 → product_images，无头图时首张顶上）
   · GET  /api/platform/products/{id}/images       （→ {images, cover}）
   · PATCH /api/platform/products/{id}/images/{iid}（setCover / sort）
   · DELETE /api/platform/products/{id}/images/{iid}（删掉的是头图则回落） */
(function(){
  'use strict';

  const RATIOS={ '1':1, '0.75':3/4, '1.3333':4/3, '1.7777':16/9 };

  function loadImg(src){
    return new Promise((res,rej)=>{const im=new Image();im.onload=()=>res(im);im.onerror=()=>rej(new Error('图片加载失败'));im.src=src;});
  }
  /* 中心 cover 裁剪到指定比例，返回 jpeg Blob（无需服务端裁，省一跳且避开 EXIF 坑）。 */
  async function autoCrop(file, ratio){
    const im=await loadImg(URL.createObjectURL(file));
    const nW=im.naturalWidth, nH=im.naturalHeight;
    let sw,sh;
    if(nW/nH>ratio){ sh=nH; sw=Math.round(nH*ratio); } else { sw=nW; sh=Math.round(nW/ratio); }
    const sx=Math.round((nW-sw)/2), sy=Math.round((nH-sh)/2);
    const out=document.createElement('canvas'); out.width=sw; out.height=sh;
    out.getContext('2d').drawImage(im, sx,sy,sw,sh, 0,0,sw,sh);
    const b=await new Promise(r=>out.toBlob(r,'image/jpeg',0.92));
    URL.revokeObjectURL(im.src);
    return b;
  }

  /* 裁剪弹层：手动平移 / 缩放 / 比例预设。返回裁剪后的 Blob（取消返回 null）。 */
  window.uxCropImage=function({src, ratio=1, title='裁剪图片'}){
    return new Promise(resolve=>{
      const ov=uxDialog({title, wide:true,
        body:`<div class="pim-crop">
            <div class="pim-crop__stage" id="cropStage"><img id="cropImg" alt=""></div>
            <div class="pim-crop__row">
              <div class="pim-crop__ratio">
                <span>比例</span>
                <button data-r="1" class="${ratio===1?'is-on':''}">1:1</button>
                <button data-r="0.75" class="${Math.abs(ratio-3/4)<1e-3?'is-on':''}">3:4</button>
                <button data-r="1.3333" class="${Math.abs(ratio-4/3)<1e-3?'is-on':''}">4:3</button>
                <button data-r="1.7777" class="${Math.abs(ratio-16/9)<1e-3?'is-on':''}">16:9</button>
              </div>
              <div class="pim-crop__zoom">缩放 <input type="range" id="cropZoom" min="1" max="3" step="0.01" value="1"></div>
            </div>
            <div class="sub">拖动图片调整位置，拖动滑块缩放；虚线框内即为最终裁切区域。</div>
          </div>`,
        foot:`<button class="btn ghost" data-cancel>取消</button><button class="btn" data-ok>确定裁剪</button>`});
      const img=ov.querySelector('#cropImg'), stage=ov.querySelector('#cropStage');
      let nW,nH,base=1,zoom=1,dx=0,dy=0,curRatio=ratio;
      const SW=()=>parseFloat(stage.style.width), SH=()=>parseFloat(stage.style.height);
      function layout(){
        const maxW=Math.min(440, (stage.parentElement.clientWidth||440)-2);
        let w=maxW, h=Math.round(w/curRatio);
        if(h>480){ h=480; w=Math.round(h*curRatio); }
        stage.style.width=w+'px'; stage.style.height=h+'px';
        base=Math.max(w/nW, h/nH); apply();
      }
      function clamp(){
        const w=nW*base*zoom, h=nH*base*zoom, W=SW(),H=SH();
        dx=Math.min(0,Math.max(W-w,dx)); dy=Math.min(0,Math.max(H-h,dy));
      }
      function apply(){
        const w=nW*base*zoom, h=nH*base*zoom;
        img.style.width=w+'px'; img.style.height=h+'px';
        img.style.left=((SW()-w)/2+dx)+'px'; img.style.top=((SH()-h)/2+dy)+'px';
      }
      img.onload=()=>{ nW=img.naturalWidth; nH=img.naturalHeight; zoom=1; dx=0; dy=0; layout(); };
      img.src=src;
      let dragging=false,lx=0,ly=0;
      img.addEventListener('pointerdown',e=>{dragging=true;lx=e.clientX;ly=e.clientY;img.setPointerCapture(e.pointerId);});
      img.addEventListener('pointermove',e=>{if(!dragging)return;dx+=e.clientX-lx;dy+=e.clientY-ly;lx=e.clientX;ly=e.clientY;clamp();apply();});
      img.addEventListener('pointerup',()=>{dragging=false;});
      img.addEventListener('pointercancel',()=>{dragging=false;});
      ov.querySelector('#cropZoom').addEventListener('input',e=>{zoom=parseFloat(e.target.value);clamp();apply();});
      ov.querySelectorAll('.pim-crop__ratio button').forEach(b=>b.onclick=()=>{
        ov.querySelectorAll('.pim-crop__ratio button').forEach(x=>x.classList.remove('is-on'));
        b.classList.add('is-on'); curRatio=parseFloat(b.dataset.r); zoom=1; dx=0; dy=0; layout();
      });
      ov.querySelector('[data-cancel]').onclick=()=>{ov.uxClose();resolve(null);};
      ov.querySelector('[data-ok]').onclick=()=>{
        const W=SW(),H=SH(),w=nW*base*zoom,h=nH*base*zoom;
        const ox=(W-w)/2+dx, oy=(H-h)/2+dy;
        const sx=-ox*(nW/w), sy=-oy*(nH/h), sw=nW*(W/w), sh=nH*(H/h);
        const out=document.createElement('canvas'); out.width=Math.round(W); out.height=Math.round(H);
        out.getContext('2d').drawImage(img, sx,sy,sw,sh, 0,0,W,H);
        out.toBlob(b=>{ ov.uxClose(); resolve(b); }, 'image/jpeg', 0.92);
      };
    });
  };

  async function uploadBlobs(pid, blobs, mode){
    const fd=new FormData();
    blobs.forEach((b,i)=>{ if(mode==='cover') fd.append('file', b, 'cover.jpg'); else fd.append('files', b, 'img'+i+'.jpg'); });
    const ep = mode==='cover' ? 'cover' : 'images/batch';
    return api(`/api/platform/products/${pid}/${ep}`, {method:'POST', body:fd});
  }

  window.openProductImageManager=async function(pid){
    let prod=null;
    try{ const ps=await api('/api/platform/products'); prod=ps.find(x=>x.id===pid); }catch(e){}
    let cover=null, gallery=[];
    async function reload(){
      const d=await api(`/api/platform/products/${pid}/images`);
      cover=d.cover||null; gallery=(d.images||[]).slice();
    }
    try{ await reload(); }catch(e){ showAlert({title:'加载失败', message:e.message}); return; }

    const dlg=uxDialog({title:'商品图片'+(prod?(' · '+prod.name):''), wide:true,
      desc:'头图是 C 端卡片与详情页首图；详情图片在详情页轮播。支持本地批量上传，上传后自动裁到最佳比例（封面 1:1 / 详情 3:4），也可点「裁剪」手动微调。',
      body:`<div class="pim">
          <div class="pim__sec">
            <div class="pim__label">头图（主视觉）</div>
            <div id="pimCover" class="pim-coverwrap"></div>
          </div>
          <div class="pim__sec">
            <div class="pim__label">详情图片 <button class="btn" data-act="addgallery" style="margin-left:8px">＋ 批量添加</button></div>
            <div id="pimGrid" class="pim-grid"></div>
          </div>
        </div>`,
      foot:`<button class="btn ghost" data-act="done">完成</button>`});

    const coverBox=dlg.querySelector('#pimCover'), grid=dlg.querySelector('#pimGrid');

    function paint(){
      coverBox.innerHTML = cover
        ? `<div class="pim-cover"><img src="${esc(cover)}" alt=""><div class="pim-cover__btns">
             <button class="btn" data-act="recover">重新上传头图</button>
             <button class="btn ghost" data-act="cropcover">裁剪头图</button></div></div>`
        : `<button class="pim-cover pim-cover--empty" data-act="coverbtn">＋ 上传头图</button>`;
      grid.innerHTML = gallery.length
        ? gallery.map((g,i)=>`<div class="pim-thumb" data-id="${g.id}">
             <img src="${esc(g.url)}" alt="">
             ${g.url===cover?'<div class="pim-thumb__cover">头图</div>':''}
             <div class="pim-thumb__bar">
               ${g.url===cover?'':`<button class="btn ghost sm" data-act="setcover">设为主图</button>`}
               <button class="btn ghost sm" data-act="crop">裁剪</button>
               <button class="btn ghost sm" data-act="del">删除</button>
             </div>
             <div class="pim-thumb__idx">${i+1}</div>
           </div>`).join('')
        : '<div class="empty">还没有详情图片，点上方「＋ 批量添加」从本地上传。</div>';
    }
    paint();

    async function pickCover(){
      const input=document.createElement('input'); input.type='file'; input.accept='image/*';
      input.onchange=async()=>{
        const f=input.files[0]; if(!f)return;
        const blob=await uxCropImage({src:URL.createObjectURL(f), ratio:1, title:'裁剪头图（1:1）'});
        if(!blob)return;
        try{ toast('头图上传中…'); await uploadBlobs(pid,[blob],'cover'); await reload(); loadProducts(); paint(); toast('头图已更新'); }
        catch(e){ showAlert({title:'上传失败', message:e.message}); }
      };
      input.click();
    }
    async function recropCover(){
      const blob=await uxCropImage({src:cover, ratio:1, title:'裁剪头图（1:1）'});
      if(!blob)return;
      try{ toast('头图上传中…'); await uploadBlobs(pid,[blob],'cover'); await reload(); loadProducts(); paint(); toast('头图已更新'); }
      catch(e){ showAlert({title:'上传失败', message:e.message}); }
    }
    function openGalleryPicker(){
      const input=document.createElement('input'); input.type='file'; input.accept='image/*'; input.multiple=true;
      input.onchange=async()=>{
        const files=[...input.files]; if(!files.length)return;
        try{
          toast('详情图处理中…');
          const blobs=[];
          for(const f of files){ try{ blobs.push(await autoCrop(f, 3/4)); }catch(_){ blobs.push(f); } }
          await uploadBlobs(pid, blobs, 'gallery');
          await reload(); loadProducts(); paint();
          toast('已添加 '+blobs.length+' 张详情图');
        }catch(e){ showAlert({title:'上传失败', message:e.message}); }
      };
      input.click();
    }
    async function recropGallery(id, url){
      const blob=await uxCropImage({src:url, ratio:3/4, title:'裁剪详情图（3:4）'});
      if(!blob)return;
      const wasCover = (url===cover);
      try{
        await uploadBlobs(pid,[blob],'gallery');   // 先以新图追加到图集
        await api(`/api/platform/products/${pid}/images/${id}`, {method:'DELETE'});  // 再删旧图
        if(wasCover){ /* 旧图是头图，删后头图回落到首张，下面把它重新设成头图 */ }
        await reload(); loadProducts(); paint(); toast('已更新');
      }catch(e){ showAlert({title:'操作失败', message:e.message}); }
    }

    /* 监听绑到整个弹层 dlg 而不是 .pim：footer 里的「完成」在 .pim 外面，
       绑在 .pim 上会漏掉它（点了没反应）。所有 data-act 按钮都能冒泡到 dlg。 */
    dlg.addEventListener('click', async e=>{
      const btn=e.target.closest('[data-act]'); if(!btn)return;
      const act=btn.dataset.act;
      if(act==='done'){ dlg.uxClose(); return; }
      if(act==='coverbtn'||act==='recover'){ pickCover(); return; }
      if(act==='cropcover'){ recropCover(); return; }
      if(act==='addgallery'){ openGalleryPicker(); return; }
      const row=e.target.closest('.pim-thumb'); if(!row)return;
      const id=Number(row.dataset.id), url=row.querySelector('img').getAttribute('src');
      if(act==='setcover'){
        try{ await api(`/api/platform/products/${pid}/images/${id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({setCover:true})}); await reload(); loadProducts(); paint(); toast('已设为主图'); }
        catch(e){ showAlert({title:'操作失败', message:e.message}); }
      }else if(act==='crop'){
        recropGallery(id, url);
      }else if(act==='del'){
        const ok=await showConfirm({title:'删除这张图片？', message:'删除后 C 端详情页就不再显示它。', confirmText:'删除', danger:true});
        if(!ok)return;
        try{ await api(`/api/platform/products/${pid}/images/${id}`, {method:'DELETE'}); await reload(); loadProducts(); paint(); toast('已删除'); }
        catch(e){ showAlert({title:'删除失败', message:e.message}); }
      }
    });
  };
})();

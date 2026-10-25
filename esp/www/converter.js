// www/converter.js
(function(){
'use strict';

const W = 800, H = 480;
const FRAME_BYTES = 48000; // 1bpp：800 * 480 / 8
const THUMB_W = 200, THUMB_H = 120;
const THUMB_BYTES = (THUMB_W * THUMB_H) / 8; // 3000

const PAGE_ON_DEVICE = (location.protocol === 'http:' || location.protocol === 'https:');

// 4x4 Bayer 矩阵
const BAYER_4X4 = [
    [ 0,  8,  2, 10],
    [12,  4, 14,  6],
    [ 3, 11,  1,  9],
    [15,  7, 13,  5]
];

// 16x16 蓝噪声矩阵
const BLUE_NOISE_16X16 = [
  [15, 188, 40, 201, 56, 152, 90, 219, 13, 174, 44, 195, 62, 154, 86, 221],
  [229, 71, 115, 27, 111, 180, 251, 33, 233, 79, 121, 23, 109, 178, 253, 37],
  [50, 168, 6, 144, 74, 132, 42, 160, 54, 166, 4, 142, 76, 130, 46, 162],
  [193, 101, 231, 85, 213, 103, 185, 117, 197, 99, 235, 81, 215, 105, 181, 119],
  [38, 142, 58, 174, 8, 156, 66, 148, 30, 138, 60, 172, 10, 158, 68, 146],
  [217, 93, 163, 49, 189, 95, 225, 89, 209, 91, 161, 51, 191, 97, 227, 87],
  [72, 124, 20, 136, 80, 140, 28, 182, 64, 126, 18, 134, 82, 144, 26, 184],
  [247, 35, 205, 107, 243, 21, 119, 55, 239, 39, 207, 111, 245, 19, 123, 53],
  [11, 152, 46, 198, 14, 176, 42, 192, 9, 154, 48, 200, 12, 178, 44, 194],
  [223, 83, 133, 65, 235, 31, 117, 61, 221, 85, 131, 67, 237, 29, 115, 63],
  [52, 168, 4, 148, 62, 130, 44, 162, 54, 166, 6, 146, 64, 132, 40, 160],
  [197, 99, 229, 87, 211, 105, 183, 119, 195, 101, 231, 89, 209, 107, 185, 117],
  [34, 140, 60, 172, 10, 154, 68, 146, 36, 142, 58, 174, 8, 156, 66, 148],
  [213, 91, 163, 51, 187, 97, 225, 85, 215, 93, 161, 53, 189, 95, 227, 83],
  [70, 126, 18, 134, 82, 142, 26, 180, 72, 124, 20, 136, 80, 144, 28, 182],
  [241, 37, 205, 109, 249, 23, 121, 57, 243, 35, 207, 113, 247, 21, 119, 59]
];

const GRAY_PALETTE = [];
for(let i=0; i<16; i++) {
  const c = (i * 17).toString(16).padStart(2, '0');
  GRAY_PALETTE.push({name: String(i), color: '#' + c + c + c, deviceColor: '#' + c + c + c});
}

const KERNELS = {
  floydSteinberg:[[1,0,7/16],[-1,1,3/16],[0,1,5/16],[1,1,1/16]],
  atkinson:[[1,0,1/8],[2,0,1/8],[-1,1,1/8],[0,1,1/8],[1,1,1/8],[0,2,1/8]],
  falseFloydSteinberg:[[1,0,3/8],[0,1,3/8],[1,1,2/8]],
  burkes:[[1,0,8/32],[2,0,4/32],[-2,1,2/32],[-1,1,4/32],[0,1,8/32],[1,1,4/32],[2,1,2/32],[-2,2,1/32],[-1,2,2/32],[0,2,4/32],[1,2,2/32],[2,2,1/32]],
  stucki:[[1,0,8/42],[2,0,4/42],[-2,1,2/42],[-1,1,4/42],[0,1,8/42],[1,1,4/42],[2,1,2/42],[-2,2,1/42],[-1,2,2/42],[0,2,4/42],[1,2,2/42],[2,2,1/42]],
  jarvis:[[1,0,7/48],[2,0,5/48],[-2,1,3/48],[-1,1,5/48],[0,1,7/48],[1,1,5/48],[2,1,3/48],[-2,2,1/48],[-1,2,3/48],[0,2,5/48],[1,2,3/48],[2,2,1/48]],
  jarvisJudiceNinke:[[1,0,7/48],[2,0,5/48],[-2,1,3/48],[-1,1,5/48],[0,1,7/48],[1,1,5/48],[2,1,3/48],[-2,2,1/48],[-1,2,3/48],[0,2,5/48],[1,2,3/48],[2,2,1/48]],
  sierra3:[[1,0,5/32],[2,0,3/32],[-2,1,2/32],[-1,1,4/32],[0,1,5/32],[1,1,4/32],[2,1,2/32],[-1,2,2/32],[0,2,3/32],[1,2,2/32]],
  sierra2:[[1,0,4/16],[2,0,3/16],[-2,1,1/16],[-1,1,2/16],[0,1,3/16],[1,1,2/16],[2,1,1/16]],
  'sierra2-4a':[[1,0,2/4],[-1,1,1/4],[0,1,1/4]],
  fan:[[1,0,7/16],[-2,1,1/16],[-1,1,3/16],[0,1,5/16]],
  shiauFan:[[1,0,4/8],[-2,1,1/8],[-1,1,1/8],[0,1,2/8]],
  shiauFan2:[[1,0,7/14],[-3,1,1/14],[-2,1,1/14],[-1,1,2/14],[0,1,3/14]]
};

const PRESETS = {
  balanced:{exposure:0, saturation:0, contrast:0, clarity:0, shadow:0, highlight:0, rangeMode:'display', rangeStrength:0.75},
  dynamic:{exposure:0.1, saturation:0.3, contrast:0.15, clarity:0, shadow:10, highlight:20, rangeMode:'off', rangeStrength:0},
  vivid:{exposure:0.05, saturation:0.6, contrast:0.1, clarity:0, shadow:5, highlight:15, rangeMode:'off', rangeStrength:0},
  soft:{exposure:-0.02, saturation:-0.05, contrast:-0.1, clarity:0, shadow:0, highlight:5, rangeMode:'display', rangeStrength:0.85},
  grayscale:{exposure:0, saturation:-1, contrast:0.05, clarity:0, shadow:0, highlight:0, rangeMode:'display', rangeStrength:0.8},
  restore:{exposure:0.08, saturation:-0.1, contrast:0.2, clarity:0, shadow:25, highlight:30, rangeMode:'auto', rangeStrength:0.9},
  posterScan:{exposure:0.05, saturation:0.1, contrast:0.15, clarity:0, shadow:10, highlight:10, rangeMode:'auto', rangeStrength:0.9}
};

let srcImage = null;
let rotation = 0;
let palettes = { 'gray16': { label: '16级灰度', colors: GRAY_PALETTE } };
let currentKey = 'gray16';
let lastBlock = null;
let previewTimer = 0;
let lastMetrics = null;

let offsetX = 0;
let offsetY = 0;

const $ = id => document.getElementById(id);

function deviceUrl(path){
  if(PAGE_ON_DEVICE) return path;
  const raw = ($('deviceIp') && $('deviceIp').value || '').trim()
    .replace(/^https?:\/\//,'').replace(/\/+$/,'');
  if(!raw) throw new Error('请填写设备 IP');
  return 'http://' + raw + path;
}

function showHostInfo(){
  const el = $('hostInfo');
  if(PAGE_ON_DEVICE){
    el.textContent = '已连接 ' + location.host;
    el.className = 'host-info ok';
  } else {
    el.textContent = '本地文件模式';
    el.className = 'host-info err';
    $('ipFallback').style.display = '';
    $('uploadHint').textContent = '本地文件模式：需手动填写设备 IP。';
  }
}

function makeBayer(size){
  if(size <= 1) return [[0]];
  if(size === 2) return [[0,2],[3,1]];
  const half = size / 2;
  const sub = makeBayer(half);
  const m = Array.from({length: size}, () => new Array(size));
  for(let y = 0; y < half; y++){
    for(let x = 0; x < half; x++){
      const v = sub[y][x] * 4;
      m[y][x] = v;
      m[y][x + half] = v + 2;
      m[y + half][x] = v + 3;
      m[y + half][x + half] = v + 1;
    }
  }
  return m;
}

function normalizeBayerSize(v){
  const n = Math.max(2, Math.min(16, parseInt(v) || 4));
  if(n <= 2) return 2;
  if(n <= 4) return 4;
  if(n <= 8) return 8;
  return 16;
}

function getCurrentPalette(){ return palettes[currentKey]; }

function hexToRgb(hex){
  const h = hex.replace('#','');
  return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)];
}

function setStatus(text, cls){
  const el = $('status');
  el.textContent = text;
  el.className = 'status ' + (cls || '');
}

function analyzeImage(imgData){
  const d = imgData.data;
  const w = imgData.width, h = imgData.height;
  let n = 0;
  let sumLuma = 0, sumLuma2 = 0;
  let sumSat = 0, sumSat2 = 0;
  let dark = 0, light = 0, gray = 0, highSat = 0;
  const lumaValues = [];
  const colorSet = new Set();

  const stride = Math.max(1, Math.floor(Math.sqrt((w*h) / 9000)));
  for(let y = 0; y < h; y += stride){
    for(let x = 0; x < w; x += stride){
      const i = (y*w+x)*4;
      const r = d[i], g = d[i+1], b = d[i+2];
      const luma = 0.2126*r + 0.7152*g + 0.0722*b;
      const mx = Math.max(r,g,b), mn = Math.min(r,g,b);
      const sat = mx === 0 ? 0 : (mx-mn)/mx;
      n++;
      sumLuma += luma;
      sumLuma2 += luma*luma;
      sumSat += sat;
      sumSat2 += sat*sat;
      lumaValues.push(luma);
      if(luma < 36) dark++;
      if(luma > 220) light++;
      if(sat < 0.08) gray++;
      if(sat > 0.72) highSat++;
      colorSet.add(((r>>3)<<10) | ((g>>3)<<5) | (b>>3));
    }
  }

  if(n === 0) return null;

  const lumaMean = sumLuma / n;
  const lumaStd = Math.sqrt(Math.max(0, sumLuma2/n - lumaMean*lumaMean));
  const satMean = sumSat / n;
  const satStd = Math.sqrt(Math.max(0, sumSat2/n - satMean*satMean));

  lumaValues.sort((a,b) => a-b);
  const p05 = lumaValues[Math.floor(lumaValues.length*0.05)] || 0;
  const p95 = lumaValues[Math.floor(lumaValues.length*0.95)] || 0;

  const grayRatio = gray/n;
  const darkRatio = dark/n;
  const lightRatio = light/n;
  const highSatRatio = highSat/n;
  const uniqueRatio = colorSet.size/n;

  let kind = 'unknown';
  let confidence = 0.5;

  if(grayRatio > 0.75 && uniqueRatio < 0.15 && (darkRatio > 0.05 || lightRatio > 0.1)){
    kind = 'lineArt'; confidence = Math.min(1, grayRatio);
  } else if(grayRatio > 0.6 && uniqueRatio < 0.25 && darkRatio < 0.15){
    kind = 'textOrUi'; confidence = Math.min(1, grayRatio);
  } else if(satMean < 0.12 && grayRatio > 0.82){
    kind = 'grayscale'; confidence = Math.min(1, grayRatio);
  } else if(p95 - p05 < 90){
    kind = 'lowContrastPhoto'; confidence = Math.min(1, 1 - (p95-p05)/180);
  } else if(lumaStd > 70){
    kind = 'highContrastPhoto'; confidence = Math.min(1, lumaStd/100);
  } else {
    kind = 'photo'; confidence = 0.6;
  }

  return {
    kind, confidence,
    metrics: {
      lumaMean: lumaMean.toFixed(1),
      lumaStd: lumaStd.toFixed(1),
      lumaRange: (p95-p05).toFixed(1),
      satMean: (satMean*100).toFixed(0) + '%',
      satStd: (satStd*100).toFixed(0) + '%',
      grayRatio: (grayRatio*100).toFixed(0) + '%',
      darkRatio: (darkRatio*100).toFixed(0) + '%',
      lightRatio: (lightRatio*100).toFixed(0) + '%',
      highSatRatio: (highSatRatio*100).toFixed(0) + '%',
      uniqueRatio: (uniqueRatio*100).toFixed(0) + '%'
    }
  };
}

function renderImageStyle(result){
  if(!result){
    $('imageKind').textContent = '-';
    $('imageConf').textContent = '-';
    $('imageMeter').style.width = '0%';
    $('imageMetrics').innerHTML = '';
    return;
  }
  const kindNames = {
    photo:'照片', lowContrastPhoto:'低对比照片', highContrastPhoto:'高对比照片',
    flatIllustration:'扁平插画', lineArt:'线稿', textOrUi:'文字/UI',
    grayscale:'灰度图', pixelArt:'像素画', unknown:'未知'
  };
  $('imageKind').textContent = kindNames[result.kind] || result.kind;
  $('imageConf').textContent = Math.round(result.confidence*100) + '%';
  $('imageMeter').style.width = Math.round(result.confidence*100) + '%';

  const m = result.metrics;
  const rows = [
    ['亮度均值', m.lumaMean], ['亮度标准差', m.lumaStd], ['亮度范围', m.lumaRange],
    ['饱和度均值', m.satMean], ['饱和度标准差', m.satStd], ['灰色占比', m.grayRatio],
    ['暗部占比', m.darkRatio], ['亮部占比', m.lightRatio], ['高饱和占比', m.highSatRatio],
    ['唯一色占比', m.uniqueRatio]
  ];
  $('imageMetrics').innerHTML = rows.map(([k,v]) =>
    '<div><dt>' + k + '</dt><dd>' + v + '</dd></div>'
  ).join('');
}

function getAutoPreset(kind){
  switch(kind){
    case 'textOrUi': case 'lineArt': case 'grayscale': return 'balanced';
    case 'lowContrastPhoto': return 'restore';
    case 'highContrastPhoto': return 'soft';
    default: return 'balanced';
  }
}

function getAutoReasons(kind, opts){
  const reasons = [];
  if(kind === 'photo') reasons.push('照片类图像，使用均衡色调映射与适度对比度。');
  if(kind === 'lowContrastPhoto') reasons.push('低对比照片，先做范围扩展再进行抖动。');
  if(kind === 'highContrastPhoto') reasons.push('高对比照片，压缩亮度范围避免大面积纯黑纯白。');
  if(kind === 'lineArt') reasons.push('线稿内容，保留边缘锐度，避免过度抖动。');
  if(kind === 'textOrUi') reasons.push('文字/UI 内容，优先清晰可读性。');
  if(kind === 'grayscale') reasons.push('灰度图像，使用灰度调色板与中等对比度。');
  if(opts && opts.rangeMode && opts.rangeMode !== 'off') reasons.push('启用动态范围压缩以适配屏幕亮度范围。');
  if(reasons.length === 0) reasons.push('自动推荐基于图像分类与调色板亮度范围。');
  return reasons.slice(0, 3);
}

function getRotatedImage(){
  if(!srcImage) return null;
  if(rotation === 0) return srcImage;
  const c = document.createElement('canvas');
  const r = rotation % 360;
  if(r === 90 || r === 270){ c.width = srcImage.height; c.height = srcImage.width; }
  else { c.width = srcImage.width; c.height = srcImage.height; }
  const ctx = c.getContext('2d');
  ctx.translate(c.width/2, c.height/2);
  ctx.rotate(r * Math.PI / 180);
  ctx.drawImage(srcImage, -srcImage.width/2, -srcImage.height/2);
  return c;
}

function getScaledSize(){
  const rotated = getRotatedImage();
  if(!rotated) return null;
  const iw = rotated.width, ih = rotated.height;
  const fitMode = $('fitMode').value;
  const scale = fitMode === 'cover' ? parseInt($('scaleSlider').value) / 100 : 1;
  const baseScale = fitMode === 'cover' ? Math.max(W/iw, H/ih) : Math.min(W/iw, H/ih);
  const finalScale = baseScale * scale;
  return {
    rotated, iw, ih, baseScale, finalScale,
    nw: Math.max(1, Math.round(iw * finalScale)),
    nh: Math.max(1, Math.round(ih * finalScale))
  };
}

function updateOffsetRanges(resetToCenter){
  const sz = getScaledSize();
  if(!sz) return;

  const isCover = $('fitMode').value === 'cover';
  const xSlider = $('offsetXSlider');
  const ySlider = $('offsetYSlider');
  const scaleSlider = $('scaleSlider');

  scaleSlider.disabled = !isCover;
  $('scaleRow').classList.toggle('is-disabled', !isCover);

  if(!isCover){
    offsetX = 0; offsetY = 0;
    xSlider.max = '0'; ySlider.max = '0';
    xSlider.value = '0'; ySlider.value = '0';
    xSlider.disabled = true; ySlider.disabled = true;
    $('centerBtn').disabled = true;
    $('offsetXRow').classList.add('is-disabled');
    $('offsetYRow').classList.add('is-disabled');
    updateSliderLabels();
    return;
  }

  const maxX = Math.max(0, sz.nw - W);
  const maxY = Math.max(0, sz.nh - H);
  xSlider.max = String(maxX);
  ySlider.max = String(maxY);

  if(resetToCenter){
    offsetX = Math.floor(maxX / 2);
    offsetY = Math.floor(maxY / 2);
  } else {
    offsetX = Math.min(offsetX, maxX);
    offsetY = Math.min(offsetY, maxY);
  }
  xSlider.value = String(offsetX);
  ySlider.value = String(offsetY);

  xSlider.disabled = false; ySlider.disabled = false;
  $('centerBtn').disabled = false;
  $('offsetXRow').classList.remove('is-disabled');
  $('offsetYRow').classList.remove('is-disabled');
  updateSliderLabels();
}

function fitImage(sz, w, h, mode, xOffset, yOffset){
  const { rotated, nw, nh } = sz;
  const tmp = document.createElement('canvas');
  tmp.width = nw; tmp.height = nh;
  const tctx = tmp.getContext('2d');
  tctx.imageSmoothingEnabled = true;
  tctx.imageSmoothingQuality = 'high';
  tctx.drawImage(rotated, 0, 0, nw, nh);

  const out = document.createElement('canvas');
  out.width = w; out.height = h;
  const ctx = out.getContext('2d');

  if(mode === 'cover'){
    const maxX = Math.max(0, nw - w);
    const maxY = Math.max(0, nh - h);
    const ox = Math.max(0, Math.min(maxX, xOffset));
    const oy = Math.max(0, Math.min(maxY, yOffset));
    ctx.drawImage(tmp, -ox, -oy);
  } else {
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(tmp, Math.floor((w - nw) / 2), Math.floor((h - nh) / 2));
  }
  return out;
}

function makeNavImage(sz, mode, xOffset, yOffset, navMaxW, navMaxH){
  const { rotated, nw, nh } = sz;
  let fullW, fullH;
  if(mode === 'cover'){ fullW = Math.max(W, nw); fullH = Math.max(H, nh); }
  else { fullW = W; fullH = H; }

  const navScale = Math.min(navMaxW / fullW, navMaxH / fullH);
  const navW = Math.max(1, Math.round(fullW * navScale));
  const navH = Math.max(1, Math.round(fullH * navScale));

  const nav = document.createElement('canvas');
  nav.width = navW; nav.height = navH;
  const ctx = nav.getContext('2d');
  ctx.fillStyle = '#d2d2d2';
  ctx.fillRect(0, 0, navW, navH);

  if(mode === 'cover'){
    const tmp = document.createElement('canvas');
    tmp.width = Math.max(1, Math.round(nw * navScale));
    tmp.height = Math.max(1, Math.round(nh * navScale));
    tmp.getContext('2d').drawImage(rotated, 0, 0, tmp.width, tmp.height);
    ctx.drawImage(tmp, 0, 0);

    const viewX = Math.round(xOffset * navScale);
    const viewY = Math.round(yOffset * navScale);
    const viewW = Math.max(1, Math.round(W * navScale));
    const viewH = Math.max(1, Math.round(H * navScale));

    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.fillRect(0, 0, navW, viewY);
    ctx.fillRect(0, viewY + viewH, navW, navH);
    ctx.fillRect(0, viewY, viewX, viewH);
    ctx.fillRect(viewX + viewW, viewY, navW, viewH);

    ctx.strokeStyle = '#ff3232';
    ctx.lineWidth = 2;
    ctx.strokeRect(viewX, viewY, viewW, viewH);
  } else {
    const s = Math.min(W / rotated.width, H / rotated.height);
    const innerW = Math.round(rotated.width * s);
    const innerH = Math.round(rotated.height * s);
    const ox = Math.round((W - innerW) / 2);
    const oy = Math.round((H - innerH) / 2);
    const tmp = document.createElement('canvas');
    tmp.width = Math.max(1, Math.round(innerW * navScale));
    tmp.height = Math.max(1, Math.round(innerH * navScale));
    tmp.getContext('2d').drawImage(rotated, 0, 0, tmp.width, tmp.height);
    ctx.drawImage(tmp, Math.round(ox * navScale), Math.round(oy * navScale));
    ctx.strokeStyle = '#888';
    ctx.lineWidth = 1;
    ctx.strokeRect(0, 0, navW - 1, navH - 1);
  }
  return nav;
}

function drawCanvas(canvas, sourceCanvas){
  canvas.width = sourceCanvas.width;
  canvas.height = sourceCanvas.height;
  canvas.getContext('2d').drawImage(sourceCanvas, 0, 0);
}

function drawImageData(canvas, imgData){
  canvas.width = imgData.width;
  canvas.height = imgData.height;
  canvas.getContext('2d').putImageData(imgData, 0, 0);
}

function applyToneMapping(data, opts){
  const expMul = Math.pow(2, opts.exposure);
  const satMul = Math.max(0, 1 + opts.saturation);
  const contrastMul = opts.contrast < 0 ? Math.max(0.5, 1 + opts.contrast * 0.5) : 1 + opts.contrast;
  const shadow = opts.shadow, highlight = opts.highlight;
  const lut = new Uint8ClampedArray(256);
  for(let i = 0; i < 256; i++){
    let v = i/255;
    v = Math.min(1, Math.max(0, v * expMul));
    v = Math.min(1, Math.max(0, (v - 0.5) * contrastMul + 0.5));
    if(shadow > 0 || highlight > 0){
      if(v < 0.5){
        const s = v / 0.5;
        const exp = Math.max(0.15, 1 - shadow * 1.5);
        v = Math.pow(s, exp) * 0.5;
      } else {
        const hh = (v - 0.5) / 0.5;
        const exp = Math.max(0.15, 1 - highlight * 0.5);
        v = 0.5 + Math.pow(hh, exp) * 0.5;
      }
    }
    lut[i] = Math.round(Math.min(1, Math.max(0, v)) * 255);
  }
  for(let i = 0; i < data.length; i += 4){
    let r = lut[data[i]], g = lut[data[i+1]], b = lut[data[i+2]];
    if(satMul !== 1){
      const rn = r/255, gn = g/255, bn = b/255;
      const mx = Math.max(rn,gn,bn), mn = Math.min(rn,gn,bn);
      const l = (mx+mn)/2;
      if(mx !== mn){
        const delta = mx - mn;
        const sat = l > 0.5 ? delta / (2-mx-mn) : delta / Math.max(mx+mn, 1e-6);
        let hue;
        if(mx === rn) hue = ((gn-bn)/delta + (gn<bn?6:0))/6;
        else if(mx === gn) hue = ((bn-rn)/delta + 2)/6;
        else hue = ((rn-gn)/delta + 4)/6;
        const ns = Math.min(1, Math.max(0, sat * satMul));
        const c = (1 - Math.abs(2*l - 1)) * ns;
        const x = c * (1 - Math.abs((hue*6) % 2 - 1));
        const m = l - c/2;
        const sector = Math.floor(hue*6) % 6;
        let nr, ng, nb;
        if(sector === 0){ nr=c+m; ng=x+m; nb=m; }
        else if(sector === 1){ nr=x+m; ng=c+m; nb=m; }
        else if(sector === 2){ nr=m; ng=c+m; nb=x+m; }
        else if(sector === 3){ nr=m; ng=x+m; nb=c+m; }
        else if(sector === 4){ nr=x+m; ng=m; nb=c+m; }
        else { nr=c+m; ng=m; nb=x+m; }
        r = Math.round(Math.min(1, Math.max(0, nr))*255);
        g = Math.round(Math.min(1, Math.max(0, ng))*255);
        b = Math.round(Math.min(1, Math.max(0, nb))*255);
      }
    }
    data[i] = r; data[i+1] = g; data[i+2] = b;
  }
}

function applyClarity(data, amount){
  if(amount <= 0) return;
  const w = W, h = H;
  const src = new Uint8ClampedArray(data);
  const radius = 2, k = radius*2+1;
  const tmp = new Float32Array(w*h*3);
  for(let y = 0; y < h; y++){
    for(let x = 0; x < w; x++){
      let r=0,g=0,b=0;
      for(let dy=-radius; dy<=radius; dy++){
        for(let dx=-radius; dx<=radius; dx++){
          const nx = Math.min(w-1, Math.max(0, x+dx));
          const ny = Math.min(h-1, Math.max(0, y+dy));
          const idx = (ny*w+nx)*4;
          r += src[idx]; g += src[idx+1]; b += src[idx+2];
        }
      }
      const i = (y*w+x)*3;
      tmp[i] = r/(k*k); tmp[i+1] = g/(k*k); tmp[i+2] = b/(k*k);
    }
  }
  for(let i = 0, j = 0; i < data.length; i += 4, j += 3){
    const l = (data[i]*0.2126 + data[i+1]*0.7152 + data[i+2]*0.0722)/255;
    const mid = Math.pow(Math.max(0, 1 - Math.abs(2*l-1)), 1.2);
    const amt = amount * mid * 2;
    data[i]   = Math.min(255, Math.max(0, data[i]   + amt * (data[i]   - tmp[j])));
    data[i+1] = Math.min(255, Math.max(0, data[i+1] + amt * (data[i+1] - tmp[j+1])));
    data[i+2] = Math.min(255, Math.max(0, data[i+2] + amt * (data[i+2] - tmp[j+2])));
  }
}

function applyRangeFitting(data, opts){
  const mode = opts.rangeMode || 'off';
  const strength = (typeof opts.rangeStrength === 'number') ? opts.rangeStrength : 1;
  if(mode === 'off' || strength <= 0) return;

  const w = W, h = H, n = w*h;
  const lumas = new Float32Array(n);
  for(let i = 0; i < n; i++){
    lumas[i] = 0.2126*data[i*4] + 0.7152*data[i*4+1] + 0.0722*data[i*4+2];
  }

  let lo, hi;
  if(mode === 'auto'){
    const step = Math.max(1, Math.floor(n / 9000));
    const sample = [];
    for(let i = 0; i < n; i += step) sample.push(lumas[i]);
    sample.sort((a,b) => a-b);
    lo = sample[Math.floor(sample.length * (opts.lowPct || 0.01))] || 0;
    hi = sample[Math.floor(sample.length * (opts.highPct || 0.99))] || 255;
  } else {
    lo = 0; hi = 255;
  }

  const range = Math.max(1, hi - lo);
  for(let i = 0; i < n; i++){
    const l = lumas[i];
    const norm = Math.min(1, Math.max(0, (l - lo) / range));
    const target = norm * 255;
    const newL = l + (target - l) * strength;
    const ratio = (l > 0) ? (newL / l) : 0;
    const idx = i * 4;
    data[idx]   = Math.min(255, Math.max(0, data[idx]   * ratio));
    data[idx+1] = Math.min(255, Math.max(0, data[idx+1] * ratio));
    data[idx+2] = Math.min(255, Math.max(0, data[idx+2] * ratio));
  }
}

// 抖动算法
function dither(data, palette, opts){
  const w = W, h = H, n = w*h;
  const buf = new Float32Array(n);
  for(let i = 0; i < n; i++){
    buf[i] = 0.299*data[i*4] + 0.587*data[i*4+1] + 0.114*data[i*4+2];
  }
  const block = new Uint8Array(n);
  const BLACK = 0, WHITE = 15;
  const strength = (typeof opts.ditherStrength === 'number') ? opts.ditherStrength : 1.0;

  if(opts.type === 'quantizationOnly'){
    // 纯二值化，为了模拟灰阶，加入极微弱的随机噪声（强度 0.2）
    const jitter = strength * 30;
    for(let i = 0; i < n; i++) {
      const noise = (Math.random() - 0.5) * jitter;
      block[i] = (buf[i] + noise > 127) ? WHITE : BLACK;
    }
    return block;
  }

  if(opts.type === 'ordered'){
    const mw = normalizeBayerSize(opts.matrixW);
    const mh = normalizeBayerSize(opts.matrixH);
    const size = Math.max(mw, mh);
    const M = makeBayer(size);
    const m = M.length;
    const div = m * m;
    for(let y = 0; y < h; y++){
      for(let x = 0; x < w; x++){
        const i = y * w + x;
        // 阈值偏移量
        const t = (M[y % m][x % m] / div - 0.5) * 255 * strength;
        block[i] = (buf[i] + t > 127) ? WHITE : BLACK;
      }
    }
    return block;
  }

  if(opts.type === 'blueNoise'){
    const m = 16;
    for(let y = 0; y < h; y++){
      for(let x = 0; x < w; x++){
        const i = y * w + x;
        // 蓝噪声矩阵元素值域 0-255，平均值约 127.5
        const thresh = BLUE_NOISE_16X16[y % m][x % m];
        // 使用 strength 控制偏置程度
        const t = (thresh - 127.5) * strength;
        block[i] = (buf[i] + t > 127.5) ? WHITE : BLACK;
      }
    }
    return block;
  }

  if(opts.type === 'random'){
    for(let i = 0; i < n; i++){
      const r = buf[i] + (Math.random() - 0.5) * 255 * strength;
      block[i] = (r > 127) ? WHITE : BLACK;
    }
    return block;
  }

  // 误差扩散 (Error Diffusion)
  const kernel = KERNELS[opts.kernel] || KERNELS.floydSteinberg;
  for(let y = 0; y < h; y++){
    const reverse = opts.serpentine && (y % 2 === 1);
    for(let xi = 0; xi < w; xi++){
      const x = reverse ? (w - 1 - xi) : xi;
      const i = y * w + x;
      const v = Math.max(0, Math.min(255, buf[i]));
      const is_white = v > 127;
      block[i] = is_white ? WHITE : BLACK;
      const out_val = is_white ? 255 : 0;
      const err = (v - out_val) * strength; // 误差乘以强度

      if (strength > 0) {
        for(const [dx, dy, wt] of kernel){
          const nx = reverse ? x - dx : x + dx;
          const ny = y + dy;
          if(nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
          const ni = ny * w + nx;
          buf[ni] += err * wt;
        }
      }
    }
  }
  return block;
}

function blockToImageData(block, rgbList){
  const img = new ImageData(W, H);
  const d = img.data;
  for(let i = 0; i < block.length; i++){
    const c = rgbList[block[i]];
    d[i*4] = c[0]; d[i*4+1] = c[1]; d[i*4+2] = c[2]; d[i*4+3] = 255;
  }
  return img;
}

function pack(block, palette){
  const buf = new Uint8Array(FRAME_BYTES);
  let k = 0;
  for(let y = 0; y < H; y++){
    for(let x = 0; x < W; x += 8){
      let byte_val = 0;
      for(let bit = 0; bit < 8; bit++){
        const v = block[y * W + x + bit];
        if(v === 15){
          byte_val |= (0x80 >> bit);
        }
      }
      buf[k++] = byte_val;
    }
  }
  return buf;
}

function postProcessEdges(adjustedData, block, opts){
  const useEdgePreserve = opts.edgePreserve && opts.edgePreserveStrength > 0;
  const useEdgeAntiAlias = opts.edgeAntiAlias && opts.edgeAntiAliasStrength > 0;
  if(!useEdgePreserve && !useEdgeAntiAlias) return;

  const w = W, h = H;
  const srcLuma = new Float32Array(w*h);
  for(let i = 0; i < w*h; i++){
    srcLuma[i] = 0.2126*adjustedData[i*4] + 0.7152*adjustedData[i*4+1] + 0.0722*adjustedData[i*4+2];
  }

  function nearestGray(r,g,b){
    const v = 0.299*r + 0.587*g + 0.114*b;
    return v > 127 ? 15 : 0;
  }

  for(let y = 1; y < h-1; y++){
    for(let x = 1; x < w-1; x++){
      const dx = Math.abs(srcLuma[y*w+x+1] - srcLuma[y*w+x-1]);
      const dy = Math.abs(srcLuma[(y+1)*w+x] - srcLuma[(y-1)*w+x]);
      const mag = Math.sqrt(dx*dx + dy*dy);
      const idx = (y*w+x)*4;
      const pi = y*w+x;

      if(useEdgePreserve && mag > 42 && Math.random() < opts.edgePreserveStrength){
        block[pi] = nearestGray(adjustedData[idx], adjustedData[idx+1], adjustedData[idx+2]);
      } else if(useEdgeAntiAlias && mag > 20 && mag <= 42 && Math.random() < opts.edgeAntiAliasStrength){
        if(((x + y) & 1) === 0){
          block[pi] = nearestGray(adjustedData[idx], adjustedData[idx+1], adjustedData[idx+2]);
        }
      }
    }
  }
}

function drawToneCurve(canvas, opts){
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const pad = 16;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = '#34332f';
  ctx.lineWidth = 1;
  for(let i = 0; i <= 4; i++){
    const x = pad + (w - 2*pad)*i/4;
    const y = pad + (h - 2*pad)*i/4;
    ctx.beginPath(); ctx.moveTo(x, pad); ctx.lineTo(x, h-pad); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w-pad, y); ctx.stroke();
  }
  ctx.strokeStyle = '#a5a49d';
  ctx.setLineDash([4,4]);
  ctx.beginPath(); ctx.moveTo(pad, h-pad); ctx.lineTo(w-pad, pad); ctx.stroke();
  ctx.setLineDash([]);
  ctx.strokeStyle = '#c9cac5';
  ctx.lineWidth = 2;
  ctx.beginPath();
  for(let i = 0; i <= w - 2*pad; i++){
    const input = i / (w - 2*pad);
    let v = input;
    v = Math.min(1, Math.max(0, v * Math.pow(2, opts.exposure)));
    const contrastMul = opts.contrast < 0 ? Math.max(0.5, 1 + opts.contrast*0.5) : 1 + opts.contrast;
    v = Math.min(1, Math.max(0, (v - 0.5) * contrastMul + 0.5));
    if(opts.shadow > 0 || opts.highlight > 0){
      if(v < 0.5){
        v = Math.pow(v/0.5, Math.max(0.15, 1 - opts.shadow*1.5)) * 0.5;
      } else {
        v = 0.5 + Math.pow((v-0.5)/0.5, Math.max(0.15, 1 - opts.highlight*0.5)) * 0.5;
      }
    }
    const x = pad + i;
    const y = h - pad - v*(h - 2*pad);
    if(i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function drawRangeMap(canvas, opts){
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const pad = 16;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = '#34332f';
  ctx.lineWidth = 1;
  for(let i = 0; i <= 4; i++){
    const x = pad + (w - 2*pad)*i/4;
    const y = pad + (h - 2*pad)*i/4;
    ctx.beginPath(); ctx.moveTo(x, pad); ctx.lineTo(x, h-pad); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w-pad, y); ctx.stroke();
  }
  const barY = h - 24;
  const grad = ctx.createLinearGradient(pad, 0, w-pad, 0);
  grad.addColorStop(0, '#000');
  grad.addColorStop(1, '#fff');
  ctx.fillStyle = grad;
  ctx.fillRect(pad, barY, w-2*pad, 8);
  ctx.strokeStyle = '#a5a49d';
  ctx.strokeRect(pad, barY, w-2*pad, 8);

  const mode = opts.rangeMode || 'off';
  const strength = (typeof opts.rangeStrength === 'number') ? opts.rangeStrength : 1;
  if(mode !== 'off'){
    ctx.strokeStyle = '#c9cac5';
    ctx.lineWidth = 2;
    ctx.beginPath();
    for(let i = 0; i <= w - 2*pad; i++){
      const input = i/(w - 2*pad);
      let output = input;
      if(mode === 'auto'){
        const lowPct = opts.lowPct || 0.01;
        const highPct = opts.highPct || 0.99;
        const norm = Math.min(1, Math.max(0, (input - lowPct) / Math.max(0.0001, highPct - lowPct)));
        output = input + (norm - input) * strength;
      }
      const x = pad + i;
      const y = h - pad - 24 - output*(h - 2*pad - 40);
      if(i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
}

function drawHistogram(canvas, imgData){
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const pad = 16;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = '#34332f';
  ctx.lineWidth = 1;
  for(let i = 0; i <= 4; i++){
    const x = pad + (w-2*pad)*i/4;
    const y = pad + (h-2*pad)*i/4;
    ctx.beginPath(); ctx.moveTo(x, pad); ctx.lineTo(x, h-pad); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w-pad, y); ctx.stroke();
  }
  if(!imgData) return;
  const data = imgData.data;
  const bins = 64;
  const r = new Array(bins).fill(0), g = new Array(bins).fill(0), b = new Array(bins).fill(0), l = new Array(bins).fill(0);
  const step = Math.max(4, Math.floor(data.length / 4 / 9000) * 4);
  for(let i = 0; i < data.length; i += step){
    if(data[i+3] === 0) continue;
    r[Math.min(bins-1, Math.floor(data[i]/256*bins))]++;
    g[Math.min(bins-1, Math.floor(data[i+1]/256*bins))]++;
    b[Math.min(bins-1, Math.floor(data[i+2]/256*bins))]++;
    const lv = 0.2126*data[i]+0.7152*data[i+1]+0.0722*data[i+2];
    l[Math.min(bins-1, Math.floor(lv/256*bins))]++;
  }
  const max = Math.max(1, ...r, ...g, ...b, ...l);
  const barW = (w-2*pad)/bins;
  const draw = (arr, color) => {
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.28;
    for(let i = 0; i < bins; i++){
      const bh = arr[i]/max*(h-2*pad);
      ctx.fillRect(pad + i*barW, h-pad-bh, Math.max(1, barW-1), bh);
    }
    ctx.globalAlpha = 1;
  };
  draw(r, '#d64545');
  draw(g, '#3f9b62');
  draw(b, '#3d6fd8');
  ctx.strokeStyle = '#a5a49d';
  ctx.lineWidth = 2;
  ctx.beginPath();
  for(let i = 0; i < bins; i++){
    const x = pad + (i/(bins-1))*(w-2*pad);
    const y = h-pad-(l[i]/max)*(h-2*pad);
    if(i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function renderPaletteStrips(){
  const p = getCurrentPalette();
  const cal = $('calibratedPreview'), dev = $('devicePreview');
  cal.innerHTML = ''; dev.innerHTML = '';
  p.colors.forEach(c => {
    const s1 = document.createElement('div');
    s1.className = 'swatch'; s1.style.background = c.color; s1.title = c.color;
    cal.appendChild(s1);
    const s2 = document.createElement('div');
    s2.className = 'swatch'; s2.style.background = c.deviceColor; s2.title = c.deviceColor;
    dev.appendChild(s2);
  });
}

function renderPaletteEditor(){
  const p = getCurrentPalette();
  const rows = $('paletteRows');
  rows.innerHTML = '';
  p.colors.forEach((c, idx) => {
    const row = document.createElement('div');
    row.className = 'palette-row';
    const name = document.createElement('span');
    name.style.fontSize = '12px';
    name.style.color = 'var(--ink)';
    name.textContent = '灰度 ' + c.name;
    const cal = document.createElement('div');
    cal.style.background = c.color;
    cal.style.height = '16px';
    cal.style.borderRadius = '2px';
    row.append(name, cal);
    rows.appendChild(row);
  });
}

function populatePaletteSelect(){
  const sel = $('paletteSelect');
  sel.innerHTML = '';
  for(const key of Object.keys(palettes)){
    const opt = document.createElement('option');
    opt.value = key; opt.textContent = palettes[key].label;
    sel.appendChild(opt);
  }
  sel.value = currentKey;
}

function readSliderOptions(){
  return {
    exposure: ($('exposure').value/100),
    saturation: ($('saturation').value/100),
    contrast: ($('contrast').value/100),
    clarity: Math.min(1, Math.max(0, $('clarity').value/100)),
    shadow: Math.min(1, Math.max(0, $('shadow').value/100)),
    highlight: Math.min(1, Math.max(0, $('highlight').value/100)),
    rangeMode: $('rangeMode').value,
    rangeStrength: Math.min(1, Math.max(0, $('rangeStrength').value/100)),
    lowPct: parseFloat($('lowPct').value) || 0.01,
    highPct: parseFloat($('highPct').value) || 0.99,
    matrixW: parseInt($('matrixW').value) || 4,
    matrixH: parseInt($('matrixH').value) || 4,
    ditherStrength: $('ditherStrength').value / 100,
    edgePreserve: $('edgePreserve').checked,
    edgePreserveStrength: $('edgePreserveStr').value/100,
    edgeAntiAlias: $('edgeAntiAlias').checked,
    edgeAntiAliasStrength: $('edgeAntiAliasStr').value/100
  };
}

function getProcessingOptions(){
  const presetName = $('presetSelect').value;
  if(presetName === 'auto') return null;
  return readSliderOptions();
}

function applyPresetToUI(presetName){
  if(presetName === 'auto' || presetName === 'manual') return;
  const p = PRESETS[presetName];
  if(!p) return;
  $('exposure').value = Math.round(p.exposure*100);
  $('saturation').value = Math.round(p.saturation*100);
  $('contrast').value = Math.round(p.contrast*100);
  $('clarity').value = Math.round(p.clarity*100);
  $('shadow').value = Math.round(p.shadow);
  $('highlight').value = Math.round(p.highlight);
  $('rangeMode').value = p.rangeMode;
  $('rangeStrength').value = Math.round(p.rangeStrength*100);
  updateSliderLabels();
}

function getAutoOptions(metrics){
  const presetName = getAutoPreset(metrics.kind);
  const p = PRESETS[presetName];
  return {
    exposure: p.exposure,
    saturation: p.saturation,
    contrast: p.contrast,
    clarity: p.clarity,
    shadow: p.shadow/100,
    highlight: p.highlight/100,
    rangeMode: p.rangeMode,
    rangeStrength: p.rangeStrength,
    lowPct: 0.01,
    highPct: 0.99,
    matrixW: 4, matrixH: 4,
    ditherStrength: 1.0,
    edgePreserve: false, edgePreserveStrength: 0.65,
    edgeAntiAlias: false, edgeAntiAliasStrength: 0.75,
    presetName
  };
}

function updateAutoRecommendation(metrics, opts){
  const presetName = opts.presetName || getAutoPreset(metrics.kind);
  const presetNames = {
    balanced:'均衡', dynamic:'动态', vivid:'鲜艳', soft:'柔和',
    grayscale:'灰度', restore:'还原', posterScan:'扫描海报'
  };
  $('autoTitle').textContent = '自动推荐：' + (presetNames[presetName] || presetName);
  const reasons = getAutoReasons(metrics.kind, opts);
  $('autoReasons').innerHTML = reasons.map(r => '<li>' + r + '</li>').join('');
}

function refresh(){
  if(!srcImage) return;
  try{
    setStatus('处理中…', 'busy');

    const sz = getScaledSize();
    if(!sz) throw new Error('无法读取图像尺寸');

    const palette = getCurrentPalette();
    const fitMode = $('fitMode').value;

    updateOffsetRanges(false);

    const fitted = fitImage(sz, W, H, fitMode, offsetX, offsetY);
    drawCanvas($('canvasInput'), fitted);

    const nav = makeNavImage(sz, fitMode, offsetX, offsetY, 300, 200);
    const navCanvas = $('navCanvas');
    navCanvas.width = nav.width;
    navCanvas.height = nav.height;
    navCanvas.getContext('2d').drawImage(nav, 0, 0);

    const ctx = fitted.getContext('2d', { willReadFrequently: true });
    const imgData = ctx.getImageData(0, 0, W, H);

    let nonBlack = 0;
    for(let i = 0; i < imgData.data.length; i += 40){
      if(imgData.data[i] > 8 || imgData.data[i+1] > 8 || imgData.data[i+2] > 8) nonBlack++;
    }
    if(nonBlack < 5){
      setStatus('输入图像几乎全黑，请检查原图是否透明/全黑', 'err');
      return;
    }

    const metrics = analyzeImage(imgData);
    if(!metrics) throw new Error('图像分析失败');
    lastMetrics = metrics;
    renderImageStyle(metrics);

    let opts = getProcessingOptions();
    if(!opts){
      opts = getAutoOptions(metrics);
      updateAutoRecommendation(metrics, opts);
    } else {
      updateAutoRecommendation(metrics, {presetName: $('presetSelect').value});
    }

    applyToneMapping(imgData.data, opts);
    if(opts.clarity > 0) applyClarity(imgData.data, opts.clarity);
    applyRangeFitting(imgData.data, opts);

    const adjustedRaw = new Uint8ClampedArray(imgData.data);
    const adjustedCopy = new ImageData(new Uint8ClampedArray(imgData.data), W, H);
    drawImageData($('canvasAdjusted'), adjustedCopy);

    drawToneCurve($('toneCurve'), opts);
    drawRangeMap($('rangeMap'), opts);
    drawHistogram($('histogram'), adjustedCopy);

    const block = dither(imgData.data, palette, {
      type: $('ditherType').value,
      kernel: $('kernel').value,
      serpentine: $('serpentine').checked,
      matrixW: opts.matrixW,
      matrixH: opts.matrixH,
      ditherStrength: opts.ditherStrength
    });

    if(opts.edgePreserve || opts.edgeAntiAlias){
      postProcessEdges(adjustedRaw, block, opts);
    }

    lastBlock = block;

    const devRgb = palette.colors.map(c => hexToRgb(c.deviceColor));
    const deviceImageData = blockToImageData(block, devRgb);
    drawImageData($('canvasDevice'), deviceImageData);

    $('downloadBtn').disabled = false;
    $('uploadBtn').disabled = false;
    $('uploadToBinsBtn').disabled = false;
    setStatus('就绪（1bpp 抖动：屏幕效果 = 设备预览）', '');
  } catch(e){
    console.error('[E6] refresh failed:', e);
    setStatus('处理失败：' + (e.message || e), 'err');
  }
}

function scheduleRefresh(){
  if(!srcImage) return;
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refresh, 60);
}

function updateSliderLabels(){
  $('exposureVal').textContent = ($('exposure').value/100).toFixed(2);
  $('saturationVal').textContent = ($('saturation').value/100).toFixed(2);
  $('contrastVal').textContent = ($('contrast').value/100).toFixed(2);
  $('clarityVal').textContent = ($('clarity').value/100).toFixed(2);
  $('shadowVal').textContent = ($('shadow').value/100).toFixed(2);
  $('highlightVal').textContent = ($('highlight').value/100).toFixed(2);
  $('rangeStrengthVal').textContent = ($('rangeStrength').value/100).toFixed(2);
  $('edgePreserveStrVal').textContent = ($('edgePreserveStr').value/100).toFixed(2);
  $('edgeAntiAliasStrVal').textContent = ($('edgeAntiAliasStr').value/100).toFixed(2);
  $('scaleVal').textContent = (parseInt($('scaleSlider').value)/100).toFixed(2);
  $('offsetXVal').textContent = $('offsetXSlider').value;
  $('offsetYVal').textContent = $('offsetYSlider').value;
  $('ditherStrengthVal').textContent = ($('ditherStrength').value/100).toFixed(2);
}

function toggleDitherUI(){
  const type = $('ditherType').value;
  const isErrorDiff = (type === 'errorDiffusion');
  const isMatrix = (type === 'ordered' || type === 'blueNoise');

  $('kernel').parentElement.style.opacity = isErrorDiff ? 1 : 0.3;
  $('kernel').disabled = !isErrorDiff;
  $('serpentine').parentElement.style.opacity = isErrorDiff ? 1 : 0.3;
  $('serpentine').disabled = !isErrorDiff;

  document.querySelectorAll('[id^="matrix"]').forEach(el => {
    el.parentElement.style.opacity = isMatrix ? 1 : 0.3;
    el.disabled = !isMatrix;
  });

  const sRow = $('ditherStrengthRow');
  if (type === 'quantizationOnly') {
    sRow.style.opacity = 0.3;
    $('ditherStrength').disabled = true;
    $('ditherHint').textContent = '仅量化：纯黑白二值化，不保留灰阶过渡。';
  } else {
    sRow.style.opacity = 1;
    $('ditherStrength').disabled = false;
    $('ditherHint').textContent = '强度越高，灰阶过渡越细腻，但可能带来噪点感。';
  }
}

function askStorage(info){
  return new Promise(resolve => {
    const sdActive = info && info.sd_active;
    const dlg = $('storageDialog');
    const sdBtn = $('storageSdBtn');
    const flashBtn = $('storageFlashBtn');

    sdBtn.style.display = sdActive ? '' : 'none';
    if (sdActive){
      const free = info && info.free_sd != null
        ? '（剩余 ' + (info.free_sd/1024).toFixed(0) + ' KB）' : '';
      sdBtn.textContent = 'SD 卡' + free;
    }
    if (info && info.free_flash != null){
      flashBtn.textContent = 'Flash（剩余 ' + (info.free_flash/1024).toFixed(0) + ' KB）';
    } else {
      flashBtn.textContent = 'Flash';
    }

    dlg.classList.add('show');

    const handler = e => {
      if (e.target.tagName === 'BUTTON' && e.target.dataset.storage){
        dlg.classList.remove('show');
        dlg.removeEventListener('click', handler);
        const v = e.target.dataset.storage;
        resolve(v === 'cancel' ? null : v);
      }
    };
    dlg.addEventListener('click', handler);
  });
}

function generateThumb(bin){
  var tw = THUMB_W, th = THUMB_H;
  var thumb = new Uint8Array(tw * th / 8);
  for (var ty = 0; ty < th; ty++){
    var sy = ty * 4;
    var srcBase = sy * 100;
    var dstBase = ty * 25;
    for (var tx = 0; tx < tw; tx++){
      var sx = tx * 4;
      var b = bin[srcBase + (sx >> 3)];
      var is_white = (b >> (7 - (sx & 7))) & 1;
      if (is_white) {
        thumb[dstBase + (tx >> 3)] |= (0x80 >> (tx & 7));
      } else {
        thumb[dstBase + (tx >> 3)] &= ~(0x80 >> (tx & 7));
      }
    }
  }
  return thumb;
}

function bindEvents(){
  $('file').addEventListener('change', e => {
    const f = e.target.files[0];
    if(!f) return;
    const url = URL.createObjectURL(f);
    const img = new Image();
    img.onload = () => {
      srcImage = img;
      rotation = 0;
      $('rotationInfo').textContent = '旋转：0°';
      $('imgInfo').textContent = '原图 ' + img.width + ' × ' + img.height;
      $('scaleSlider').value = 100;
      updateOffsetRanges(true);
      refresh();
      setStatus('图片已加载', 'ok');
      URL.revokeObjectURL(url);
    };
    img.onerror = () => setStatus('图片加载失败', 'err');
    img.src = url;
  });

  ['exposure','saturation','contrast','clarity','shadow','highlight','rangeStrength',
   'edgePreserveStr','edgeAntiAliasStr','ditherStrength'].forEach(id => {
    $(id).addEventListener('input', () => {
      if($('presetSelect').value === 'auto') $('presetSelect').value = 'manual';
      updateSliderLabels();
      scheduleRefresh();
    });
  });

  $('presetSelect').addEventListener('change', () => {
    applyPresetToUI($('presetSelect').value);
    scheduleRefresh();
  });

  ['ditherType','kernel','serpentine','rangeMode',
   'edgePreserve','edgeAntiAlias'].forEach(id => {
    $(id).addEventListener('change', () => {
      if(id === 'ditherType') toggleDitherUI();
      scheduleRefresh();
    });
  });

  ['lowPct','highPct','matrixW','matrixH'].forEach(id => {
    $(id).addEventListener('input', scheduleRefresh);
  });

  $('fitMode').addEventListener('change', () => {
    if($('fitMode').value === 'contain') $('scaleSlider').value = 100;
    updateOffsetRanges(true);
    scheduleRefresh();
  });

  $('scaleSlider').addEventListener('input', () => {
    if($('fitMode').value !== 'cover') return;
    updateOffsetRanges(false);
    scheduleRefresh();
  });

  $('offsetXSlider').addEventListener('input', () => {
    if($('fitMode').value !== 'cover') return;
    offsetX = parseInt($('offsetXSlider').value) || 0;
    updateSliderLabels();
    scheduleRefresh();
  });

  $('offsetYSlider').addEventListener('input', () => {
    if($('fitMode').value !== 'cover') return;
    offsetY = parseInt($('offsetYSlider').value) || 0;
    updateSliderLabels();
    scheduleRefresh();
  });

  $('autoAdjustBtn').addEventListener('click', () => {
    if(!lastMetrics) return;
    const opts = getAutoOptions(lastMetrics);
    applyPresetToUI(opts.presetName);
    $('presetSelect').value = opts.presetName;
    updateAutoRecommendation(lastMetrics, opts);
    refresh();
    setStatus('已应用自动图像调整', 'ok');
  });

  $('resetAdjustBtn').addEventListener('click', () => {
    $('presetSelect').value = 'manual';
    $('exposure').value = 0;
    $('saturation').value = 0;
    $('contrast').value = 0;
    $('clarity').value = 0;
    $('shadow').value = 0;
    $('highlight').value = 0;
    $('rangeMode').value = 'off';
    $('rangeStrength').value = 100;
    $('lowPct').value = 0.01;
    $('highPct').value = 0.99;
    $('edgePreserve').checked = false;
    $('edgeAntiAlias').checked = false;
    $('ditherStrength').value = 100;
    updateSliderLabels();
    toggleDitherUI();
    scheduleRefresh();
    setStatus('已重置图像调整', 'ok');
  });

  $('rotateBtn').addEventListener('click', () => {
    rotation = (rotation + 90) % 360;
    $('rotationInfo').textContent = '旋转：' + rotation + '°';
    updateOffsetRanges(true);
    scheduleRefresh();
  });

  $('centerBtn').addEventListener('click', () => {
    if($('fitMode').value !== 'cover') return;
    updateOffsetRanges(true);
    scheduleRefresh();
  });

  $('resetCanvasBtn').addEventListener('click', () => {
    rotation = 0;
    $('rotationInfo').textContent = '旋转：0°';
    $('scaleSlider').value = 100;
    updateOffsetRanges(true);
    scheduleRefresh();
  });

  $('paletteSelect').addEventListener('change', () => {
    currentKey = $('paletteSelect').value;
    renderPaletteStrips();
    renderPaletteEditor();
    scheduleRefresh();
  });

  $('downloadBtn').addEventListener('click', () => {
    if(!lastBlock) return;
    try {
      const bin = pack(lastBlock, getCurrentPalette());
      const blob = new Blob([bin], { type: 'application/octet-stream' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'image_' + Date.now() + '.bin';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      setStatus('已下载 ' + bin.length + ' 字节', 'ok');
    } catch(e){
      setStatus('下载失败：' + e.message, 'err');
    }
  });

  $('clearGhostingBtn').addEventListener('click', async () => {
    const btn = $('clearGhostingBtn');
    btn.disabled = true;
    await window.E6Display.clearGhosting('full', setStatus);
    btn.disabled = false;
  });

  $('uploadBtn').addEventListener('click', async () => {
    if(!lastBlock) return;

    $('uploadBtn').disabled = true;
    $('downloadBtn').disabled = true;
    $('uploadToBinsBtn').disabled = true;

    let bin;
    try {
      bin = pack(lastBlock, getCurrentPalette());
    } catch(e){
      setStatus('生成失败：' + e.message, 'err');
      $('uploadBtn').disabled = false;
      $('downloadBtn').disabled = false;
      $('uploadToBinsBtn').disabled = false;
      return;
    }

    const mode = ($('refreshMode') && $('refreshMode').value) || 'full';
    const ok = await window.E6Display.uploadToScreen(bin, mode, setStatus);

    $('uploadBtn').disabled = false;
    $('downloadBtn').disabled = false;
    $('uploadToBinsBtn').disabled = false;
  });

  $('uploadToBinsBtn').addEventListener('click', async () => {
    if (!lastBlock) return;

    let listData = null;
    try {
      const r = await fetch('/bins/list', {cache:'no-store'});
      if (r.ok) listData = await r.json();
    } catch(e){}
    if (!listData){
      setStatus('读取 BIN 库失败', 'err');
      return;
    }

    const storage = await askStorage(listData);
    if (!storage) { setStatus('已取消上传', ''); return; }

    const storageText = storage === 'sd' ? 'SD' : 'Flash';

    let nextIdx = 0;
    (listData.slots || []).forEach(s => {
      if (s.storage === storage && s.index >= nextIdx) nextIdx = s.index + 1;
    });

    let bin;
    try {
      bin = pack(lastBlock, getCurrentPalette());
    } catch(e){
      setStatus('生成失败：' + e.message, 'err');
      return;
    }

    $('uploadToBinsBtn').disabled = true;
    $('downloadBtn').disabled = true;
    $('uploadBtn').disabled = true;
    setStatus('上传到 ' + storageText + ' 槽位 ' + (nextIdx + 1) + '…', 'busy');

    try {
      const r = await fetch('/bins/upload/' + nextIdx + '?storage=' + storage, {
        method: 'POST',
        headers: {'Content-Type': 'application/octet-stream'},
        body: bin
      });
      const text = await r.text();
      if (!r.ok) throw new Error('HTTP ' + r.status + ': ' + text);

      try {
        const thumbBuf = generateThumb(new Uint8Array(bin));
        await fetch('/bins/thumb_upload/' + nextIdx + '?storage=' + storage, {
          method: 'POST',
          headers: {'Content-Type':'application/octet-stream'},
          body: thumbBuf
        });
      } catch(te) {
        console.warn('thumb upload fail', te);
      }

      setStatus('已保存到 ' + storageText + ' 槽位 ' + (nextIdx + 1) +
                '（' + text + ' 字节）', 'ok');
    } catch(e) {
      setStatus('上传失败：' + e.message, 'err');
    } finally {
      $('uploadToBinsBtn').disabled = false;
      $('downloadBtn').disabled = false;
      $('uploadBtn').disabled = false;
    }
  });
}

function init(){
  populatePaletteSelect();
  renderPaletteStrips();
  renderPaletteEditor();
  showHostInfo();
  bindEvents();
  updateSliderLabels();
  toggleDitherUI();
  updateOffsetRanges(true);
  setStatus('就绪：请选择图片');
}

init();

})();
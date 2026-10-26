// www/bin_editor.js
(function(){
'use strict';

console.log('[BIN Editor] script start');

const W = 800, H = 480;
const FRAME_BYTES = 48000; // 1bpp
const TRANSPARENT = 255;

const PALETTE = [];
for(let i=0; i<16; i++) {
  const c = (i * 17).toString(16).padStart(2, '0');
  PALETTE.push('#' + c + c + c);
}

const BAYER = [
  [ 0,  8,  2, 10],
  [12,  4, 14,  6],
  [ 3, 11,  1,  9],
  [15,  7, 13,  5]
];

let binLayer = new Uint8Array(W * H);
let drawLayer = new Uint8Array(W * H);
binLayer.fill(TRANSPARENT);
drawLayer.fill(TRANSPARENT);

let bgColorIdx = TRANSPARENT;
let contentLocked = false;

let currentColor = 0;
let currentTool = 'pencil';
let brushSize = 4;
let textModeActive = false;

function snapshot() {
  return { bin: new Uint8Array(binLayer), draw: new Uint8Array(drawLayer), bg: bgColorIdx };
}

function restore(snap) {
  binLayer = new Uint8Array(snap.bin);
  drawLayer = new Uint8Array(snap.draw);
  bgColorIdx = snap.bg;
}

const undoStack = [snapshot()];
const redoStack = [];
const MAX_HISTORY = 30;

const $ = id => document.getElementById(id);
const canvasMain = $('canvasMain');
const canvasOverlay = $('canvasOverlay');
const ctxMain = canvasMain.getContext('2d', { willReadFrequently:true });
const ctxOverlay = canvasOverlay.getContext('2d');

let renderPending = false;
let drawing = false;
let lastX = -1, lastY = -1;
let currentPointerId = null;

function resolveIndex(x, y) {
  const i = y * W + x;
  let v = drawLayer[i];
  if (v === TRANSPARENT) v = binLayer[i];
  if (v === TRANSPARENT) v = bgColorIdx;
  return v;
}

function requestRender() {
  if (renderPending) return;
  renderPending = true;
  requestAnimationFrame(() => { renderPending = false; render(); });
}

function render() {
  const img = ctxMain.createImageData(W, H);
  const d = img.data;
  for (let i = 0; i < W * H; i++) {
    let v = drawLayer[i];
    if (v === TRANSPARENT) v = binLayer[i];
    if (v === TRANSPARENT) v = bgColorIdx;

    if (v === TRANSPARENT) {
      const x = i % W;
      const y = Math.floor(i / W);
      const isEven = ((x >> 3) + (y >> 3)) % 2 === 0;
      const c = isEven ? 32 : 20;
      d[i*4] = c; d[i*4+1] = c; d[i*4+2] = c; d[i*4+3] = 255;
    } else {
      const c = v * 17;
      d[i*4] = c; d[i*4+1] = c; d[i*4+2] = c; d[i*4+3] = 255;
    }
  }
  ctxMain.putImageData(img, 0, 0);
}

function unpack(buf) {
  const out = new Uint8Array(W * H);
  let k = 0;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x += 8) {
      const b = buf[k++];
      for (let bit = 0; bit < 8; bit++) {
        out[y*W + x + bit] = (b & (0x80 >> bit)) ? 15 : 0;
      }
    }
  }
  return out;
}

function packLayers() {
  const buf = new Uint8Array(FRAME_BYTES);
  let k = 0;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x += 8) {
      let byte_val = 0;
      for (let bit = 0; bit < 8; bit++) {
        let v = resolveIndex(x + bit, y);
        if (v === TRANSPARENT) v = 15;
        if (v * 17 > BAYER[y % 4][(x + bit) % 4] * 16 + 8) {
          byte_val |= (0x80 >> bit);
        }
      }
      buf[k++] = byte_val;
    }
  }
  return buf;
}

// ==== 单矩形打包 ====
function getBoundingBox() {
  let minX = W, minY = H, maxX = -1, maxY = -1;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let i = y * W + x;
      if (drawLayer[i] !== TRANSPARENT || binLayer[i] !== TRANSPARENT) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < minX) return null;
  return { x0: minX, y0: minY, x1: maxX, y1: maxY };
}

function packPartial(box) {
  const x0 = box.x0 & ~7;
  const x1 = (box.x1 | 7) >= W ? W - 1 : (box.x1 | 7);
  const y0 = box.y0;
  const y1 = box.y1;
  const w = x1 - x0 + 1;
  const h = y1 - y0 + 1;
  const rowBytes = w >> 3;
  const buf = new Uint8Array(rowBytes * h);

  let k = 0;
  // ★ 核心修复：数据倒序打包（从 y1 读到 y0），坐标完全不翻转
  for (let y = y1; y >= y0; y--) {
    for (let x = x0; x <= x1; x += 8) {
      let byte_val = 0;
      for (let bit = 0; bit < 8; bit++) {
        let px = x + bit;
        if (px > x1) break;
        let v = resolveIndex(px, y);
        if (v === TRANSPARENT) v = 15;
        if (v * 17 > BAYER[y % 4][px % 4] * 16 + 8) {
          byte_val |= (0x80 >> bit);
        }
      }
      buf[k++] = byte_val;
    }
  }
  return { buf, x0, y0, x1, y1 };
}

// ==== 多矩形打包 ====
function getMultiRects() {
  const CELL = 16;
  const cols = Math.ceil(W / CELL);
  const rows = Math.ceil(H / CELL);
  const grid = new Uint8Array(cols * rows);

  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let i = y * W + x;
      if (drawLayer[i] !== TRANSPARENT || binLayer[i] !== TRANSPARENT) {
        const cx = Math.floor(x / CELL);
        const cy = Math.floor(y / CELL);
        grid[cy * cols + cx] = 1;
      }
    }
  }

  const rects = [];
  for (let cy = 0; cy < rows; cy++) {
    for (let cx = 0; cx < cols; cx++) {
      if (grid[cy * cols + cx] !== 1) continue;

      let w_cells = 1;
      while (cx + w_cells < cols && grid[cy * cols + cx + w_cells] === 1) {
        w_cells++;
      }

      let h_cells = 1;
      let canExpandDown = true;
      while (cy + h_cells < rows && canExpandDown) {
        for (let i = 0; i < w_cells; i++) {
          if (grid[(cy + h_cells) * cols + cx + i] !== 1) {
            canExpandDown = false; break;
          }
        }
        if (canExpandDown) h_cells++;
      }

      for (let dy = 0; dy < h_cells; dy++) {
        for (let dx = 0; dx < w_cells; dx++) {
          grid[(cy + dy) * cols + cx + dx] = 0;
        }
      }

      const x0 = cx * CELL;
      const y0 = cy * CELL;
      const x1 = Math.min(W - 1, (cx + w_cells) * CELL - 1);
      const y1 = Math.min(H - 1, (cy + h_cells) * CELL - 1);

      rects.push({ x0, y0, x1, y1 });
    }
  }
  return rects;
}

function packMultiRects(rects) {
  const num_rects = rects.length;
  if (num_rects === 0) return null;

  const rectDatas = [];
  let totalDataLen = 0;

  for (const r of rects) {
    const x0 = r.x0 & ~7;
    const x1 = (r.x1 | 7) >= W ? W - 1 : (r.x1 | 7);
    const w = x1 - x0 + 1;
    const h = r.y1 - r.y0 + 1;
    const rowBytes = w >> 3;
    const len = rowBytes * h;

    const data = new Uint8Array(len);
    let k = 0;
    // ★ 核心修复：数据倒序打包（从 y1 读到 y0）
    for (let y = r.y1; y >= r.y0; y--) {
      for (let x = x0; x <= x1; x += 8) {
        let byte_val = 0;
        for (let bit = 0; bit < 8; bit++) {
          let px = x + bit;
          if (px > x1) break;
          let v = resolveIndex(px, y);
          if (v === TRANSPARENT) v = 15;
          if (v * 17 > BAYER[y % 4][px % 4] * 16 + 8) {
            byte_val |= (0x80 >> bit);
          }
        }
        data[k++] = byte_val;
      }
    }
    rectDatas.push(data);
    totalDataLen += len;
  }

  const headerLen = 4 + num_rects * 8;
  const buf = new Uint8Array(headerLen + totalDataLen);
  let idx = 0;

  buf[idx++] = (num_rects >> 8) & 0xFF;
  buf[idx++] = num_rects & 0xFF;
  buf[idx++] = (totalDataLen >> 8) & 0xFF;
  buf[idx++] = totalDataLen & 0xFF;

  for (let i = 0; i < num_rects; i++) {
    const r = rects[i];
    const x0 = r.x0 & ~7;
    const x1 = (r.x1 | 7) >= W ? W - 1 : (r.x1 | 7);

    buf[idx++] = (x0 >> 8) & 0xFF;
    buf[idx++] = x0 & 0xFF;
    buf[idx++] = (r.y0 >> 8) & 0xFF;   // 坐标不变
    buf[idx++] = r.y0 & 0xFF;
    buf[idx++] = (x1 >> 8) & 0xFF;
    buf[idx++] = x1 & 0xFF;
    buf[idx++] = (r.y1 >> 8) & 0xFF;   // 坐标不变
    buf[idx++] = r.y1 & 0xFF;
  }

  for (let i = 0; i < num_rects; i++) {
    buf.set(rectDatas[i], idx);
    idx += rectDatas[i].length;
  }

  return buf;
}

function getCanvasCoord(e) {
  const rect = canvasMain.getBoundingClientRect();
  const x = Math.floor((e.clientX - rect.left) * W / rect.width);
  const y = Math.floor((e.clientY - rect.top)  * H / rect.height);
  return { x: Math.max(0, Math.min(W - 1, x)), y: Math.max(0, Math.min(H - 1, y)) };
}

function saveHistory() {
  undoStack.push(snapshot());
  if (undoStack.length > MAX_HISTORY) undoStack.shift();
  redoStack.length = 0;
  updateHistoryButtons();
}

function updateHistoryButtons() {
  $('undoBtn').disabled = undoStack.length <= 1;
  $('redoBtn').disabled = redoStack.length === 0;
}

function undo() {
  if (undoStack.length <= 1) return;
  redoStack.push(snapshot());
  restore(undoStack.pop());
  render();
  updateHistoryButtons();
  setStatus('已撤销');
}

function redo() {
  if (redoStack.length === 0) return;
  undoStack.push(snapshot());
  restore(redoStack.pop());
  render();
  updateHistoryButtons();
  setStatus('已重做');
}

function paintPixelAt(x, y) {
  if (x < 0 || x >= W || y < 0 || y >= H) return;
  const i = y * W + x;

  if (currentTool === 'eraser') {
    drawLayer[i] = TRANSPARENT;
    if (!contentLocked) binLayer[i] = TRANSPARENT;
  } else {
    if (currentColor === TRANSPARENT) {
      drawLayer[i] = TRANSPARENT;
    } else {
      drawLayer[i] = currentColor;
    }
  }
}

function paintBrush(cx, cy) {
  const half = Math.floor(brushSize / 2);
  for (let dy = -half; dy < brushSize - half; dy++) {
    for (let dx = -half; dx < brushSize - half; dx++) {
      paintPixelAt(cx + dx, cy + dy);
    }
  }
}

function drawLine(x0, y0, x1, y1) {
  const dx = Math.abs(x1 - x0);
  const dy = Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1;
  const sy = y0 < y1 ? 1 : -1;
  let err = dx - dy;
  while (true) {
    paintBrush(x0, y0);
    if (x0 === x1 && y0 === y1) break;
    const e2 = 2 * err;
    if (e2 > -dy) { err -= dy; x0 += sx; }
    if (e2 <  dx) { err += dx; y0 += sy; }
  }
}

function pickColorAt(x, y) {
  const R = 2;
  const counts = new Array(17).fill(0);
  let total = 0;
  for (let dy = -R; dy <= R; dy++) {
    for (let dx = -R; dx <= R; dx++) {
      const px = x + dx, py = y + dy;
      if (px < 0 || px >= W || py < 0 || py >= H) continue;
      let v = resolveIndex(px, py);
      if (v === TRANSPARENT) v = 16;
      counts[v]++;
      total++;
    }
  }
  if (total === 0) return;

  const order = Array.from({length:17}, (_, i) => i).sort((a, b) => counts[b] - counts[a]);
  const mainColor = order[0] === 16 ? TRANSPARENT : order[0];

  currentColor = mainColor;
  updateColorUI();
  setStatus('已取色：' + (mainColor === TRANSPARENT ? '透明' : '灰度 ' + mainColor), 'ok');
}

function getTextMetrics() {
  const text = $('textInput').value;
  const font = $('fontSelect').value;
  const size = Math.max(6, parseInt($('fontSize').value) || 32);
  const bold = $('boldBtn').classList.contains('active');
  const italic = $('italicBtn').classList.contains('active');
  const underline = $('underlineBtn').classList.contains('active');
  const rotate = parseFloat($('rotateSlider').value) || 0;
  const align = $('alignSelect').value;
  const direction = $('directionSelect').value;
  const lineHeight = parseFloat($('lineHeightSlider').value) || 1.2;
  const letterSpacing = parseInt($('letterSpacingSlider').value, 10) || 0;

  const style = (italic ? 'italic ' : '') + (bold ? 'bold ' : '');
  const fontSpec = `${style}${size}px ${font}`;
  return { text, fontSpec, size, underline, rotate, align, direction,
           lineHeight, bold, italic, letterSpacing };
}

function drawHorizontalText(ctx, m) {
  const lines = m.text.split('\n');
  const lineHeightPx = m.size * m.lineHeight;
  const ls = m.letterSpacing || 0;
  const underlineThickness = Math.max(1, Math.round(m.size / 14));
  ctx.textBaseline = 'top';
  ctx.textAlign = 'left';

  for (let i = 0; i < lines.length; i++) {
    const chars = Array.from(lines[i]);
    if (chars.length === 0) continue;

    let lineW = 0;
    for (let j = 0; j < chars.length; j++) {
      lineW += ctx.measureText(chars[j]).width;
      if (j < chars.length - 1) lineW += ls;
    }

    let startX = 0;
    if (m.align === 'center') startX = -lineW / 2;
    else if (m.align === 'right') startX = -lineW;

    const ly = i * lineHeightPx;
    let cx = startX;
    for (let j = 0; j < chars.length; j++) {
      ctx.fillText(chars[j], cx, ly);
      cx += ctx.measureText(chars[j]).width + ls;
    }

    if (m.underline) {
      ctx.fillRect(startX, ly + m.size * 0.9, lineW, underlineThickness);
    }
  }
}

function drawVerticalText(ctx, m) {
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';

  const lines = m.text.split('\n');
  const lineHeightPx = m.size * m.lineHeight;
  const ls = m.letterSpacing || 0;
  const colWidth = Math.max(1, lineHeightPx + ls);
  const totalWidth = lines.length * colWidth;
  const underlineThickness = Math.max(1, Math.round(m.size / 14));

  let offsetX = 0;
  if (m.align === 'center') offsetX = -totalWidth / 2;
  else if (m.align === 'right') offsetX = -totalWidth;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const chars = Array.from(line);

    let colCenterX;
    if (m.direction === 'vertical-lr') {
      colCenterX = offsetX + colWidth * (i + 0.5);
    } else {
      colCenterX = offsetX + totalWidth - colWidth * (i + 0.5);
    }

    for (let j = 0; j < chars.length; j++) {
      const ch = chars[j];
      if (ch === ' ' || ch === '\t') continue;
      const cy = j * colWidth + colWidth / 2;
      ctx.fillText(ch, colCenterX, cy);
    }

    if (m.underline && chars.length > 0) {
      let maxCharW = 0;
      for (const ch of chars) {
        const w = ctx.measureText(ch).width;
        if (w > maxCharW) maxCharW = w;
      }
      const lineX = colCenterX + maxCharW / 2 + 1;
      const lineY1 = colWidth * 0.1;
      const lineY2 = chars.length * colWidth - colWidth * 0.1;
      ctx.fillRect(lineX, lineY1, underlineThickness, lineY2 - lineY1);
    }
  }
}

function drawTextOnCanvas(ctx, x, y, m, fillStyle) {
  ctx.save();
  ctx.font = m.fontSpec;
  ctx.fillStyle = fillStyle;

  ctx.translate(x, y);
  if (m.rotate !== 0) {
    ctx.rotate(m.rotate * Math.PI / 180);
  }

  if (m.direction === 'horizontal') {
    drawHorizontalText(ctx, m);
  } else {
    drawVerticalText(ctx, m);
  }

  ctx.restore();
}

function measureTextBBox(m) {
  const cvs = document.createElement('canvas');
  const ctx = cvs.getContext('2d');
  ctx.font = m.fontSpec;

  const lineHeightPx = m.size * m.lineHeight;
  const ls = m.letterSpacing || 0;
  const lines = m.text.split('\n');

  if (m.direction === 'horizontal') {
    let maxW = 0;
    for (const line of lines) {
      const chars = Array.from(line);
      let w = 0;
      for (let j = 0; j < chars.length; j++) {
        w += ctx.measureText(chars[j]).width;
        if (j < chars.length - 1) w += ls;
      }
      if (w > maxW) maxW = w;
    }
    return {
      w: Math.max(1, maxW),
      h: Math.max(1, lines.length * lineHeightPx)
    };
  } else {
    const colWidth = Math.max(1, lineHeightPx + ls);
    let maxChars = 0;
    for (const line of lines) {
      maxChars = Math.max(maxChars, Array.from(line).length);
    }
    return {
      w: Math.max(1, lines.length * colWidth),
      h: Math.max(1, maxChars * colWidth)
    };
  }
}

function renderTextPreviewBox() {
  const cvs = $('textPreviewCanvas');
  if (!cvs) return;
  const ctx = cvs.getContext('2d');
  const CW = cvs.width, CH = cvs.height;

  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, CW, CH);

  const m = getTextMetrics();
  if (!m.text.trim()) {
    ctx.fillStyle = '#555';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('（无文字）', CW / 2, CH / 2);
    return;
  }

  const bbox = measureTextBBox(m);
  if (bbox.w <= 0 || bbox.h <= 0) return;

  const rotRad = Math.abs(m.rotate) * Math.PI / 180;
  const cos = Math.abs(Math.cos(rotRad));
  const sin = Math.abs(Math.sin(rotRad));
  const boundW = bbox.w * cos + bbox.h * sin;
  const boundH = bbox.w * sin + bbox.h * cos;

  const pad = 8;
  const scale = Math.min(
    (CW - 2 * pad) / Math.max(boundW, 1),
    (CH - 2 * pad) / Math.max(boundH, 1),
    1
  );

  let cRelX = 0;
  if (m.align === 'left') cRelX = bbox.w / 2;
  else if (m.align === 'right') cRelX = -bbox.w / 2;
  const cRelY = bbox.h / 2;

  ctx.save();
  ctx.translate(CW / 2, CH / 2);
  ctx.scale(scale, scale);
  drawTextOnCanvas(ctx, -cRelX, -cRelY, m, '#fff');
  ctx.restore();
}

function renderTextPreview(x, y) {
  ctxOverlay.clearRect(0, 0, W, H);
  const m = getTextMetrics();
  if (!m.text) return;
  ctxOverlay.save();
  ctxOverlay.globalAlpha = 0.75;
  drawTextOnCanvas(ctxOverlay, x, y, m, 'rgba(117,199,189,1)');
  ctxOverlay.restore();
}

function drawTextToDrawLayer(x, y) {
  const m = getTextMetrics();
  if (!m.text) return false;

  const off = document.createElement('canvas');
  off.width = W; off.height = H;
  const octx = off.getContext('2d');

  drawTextOnCanvas(octx, x, y, m, '#fff');

  const img = octx.getImageData(0, 0, W, H);
  const d = img.data;
  let count = 0;
  const targetColor = currentColor;
  for (let i = 0; i < W * H; i++) {
    if (d[i*4 + 3] > 0) {
      drawLayer[i] = targetColor;
      count++;
    }
  }
  return count > 0;
}

function setStatus(text, cls) {
  const el = $('status');
  el.textContent = text;
  el.className = 'status' + (cls ? ' ' + cls : '');
  console.log('[BIN Editor] status:', text);
}

function buildSwatches() {
  const basic = $('graySwatches');
  basic.innerHTML = '';

  const transEl = document.createElement('div');
  transEl.className = 'swatch transparent';
  transEl.title = '透明';
  transEl.dataset.index = 'transparent';

  const transLabel = document.createElement('span');
  transLabel.textContent = 'T';
  transEl.appendChild(transLabel);

  transEl.addEventListener('click', () => {
    currentColor = TRANSPARENT;
    updateColorUI();
  });
  basic.appendChild(transEl);

  for(let i=0; i<16; i++) {
    const el = document.createElement('div');
    el.className = 'swatch';
    el.title = '灰度 ' + i;
    el.style.background = PALETTE[i];
    el.dataset.index = String(i);

    const label = document.createElement('span');
    label.textContent = String(i);
    el.appendChild(label);

    el.addEventListener('click', () => {
      currentColor = i;
      updateColorUI();
    });
    basic.appendChild(el);
  }

  const bg = $('bgSwatches');
  bg.innerHTML = '';

  const bgTransEl = document.createElement('div');
  bgTransEl.className = 'swatch transparent';
  bgTransEl.title = '背景透明';
  bgTransEl.dataset.kind = 'bg';
  bgTransEl.dataset.index = 'transparent';

  const bgTransLabel = document.createElement('span');
  bgTransLabel.textContent = 'T';
  bgTransEl.appendChild(bgTransLabel);

  bgTransEl.addEventListener('click', () => {
    bgColorIdx = TRANSPARENT;
    updateColorUI();
    render();
    setStatus('背景色：透明');
  });
  bg.appendChild(bgTransEl);

  for(let i=0; i<16; i++) {
    const el = document.createElement('div');
    el.className = 'swatch';
    el.title = '背景灰度 ' + i;
    el.style.background = PALETTE[i];
    el.dataset.kind = 'bg';
    el.dataset.index = String(i);

    const label = document.createElement('span');
    label.textContent = String(i);
    el.appendChild(label);

    el.addEventListener('click', () => {
      bgColorIdx = i;
      updateColorUI();
      render();
      setStatus('背景色：灰度 ' + i);
    });
    bg.appendChild(el);
  }
}

function updateColorUI() {
  document.querySelectorAll('.swatch').forEach(el => el.classList.remove('active'));

  document.querySelectorAll('.swatch[data-kind="bg"]').forEach(el => {
    const idx = el.dataset.index === 'transparent' ? TRANSPARENT : parseInt(el.dataset.index);
    if (idx === bgColorIdx) el.classList.add('active');
  });

  const targetIdx = currentColor === TRANSPARENT ? 'transparent' : String(currentColor);
  const el = document.querySelector('.swatch:not([data-kind="bg"])[data-index="' + targetIdx + '"]');
  if (el) el.classList.add('active');
}

function updateToolUI() {
  document.querySelectorAll('.tool-btn').forEach(el => {
    el.classList.toggle('active', el.dataset.tool === currentTool);
  });
  canvasMain.style.cursor = textModeActive ? 'text' : 'crosshair';
}

function updateLockUI() {
  const label = $('lockLabel');
  if (contentLocked) {
    label.textContent = '已锁定 · 橡皮只擦笔画和文字';
    label.className = 'locked';
  } else {
    label.textContent = '未锁定 · 橡皮可穿透擦除 BIN';
    label.className = 'unlocked';
  }
}

async function displayToScreen() {
  const btn = $('displayBtn');
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '上传中…';

  try {
    const mode = ($('refreshMode') && $('refreshMode').value) || 'full';
    let bin = null;
    let bounds = null;

    if (mode === 'partial') {
      bounds = getBoundingBox();
      if (!bounds) {
        setStatus('画布全透明，无内容可刷新', 'err');
        btn.disabled = false;
        btn.textContent = originalText;
        return;
      }
      const res = packPartial(bounds);
      bin = res.buf;
      bounds = { x0: res.x0, y0: res.y0, x1: res.x1, y1: res.y1 };
      setStatus(`单矩形: ${bounds.x1-bounds.x0+1}x${bounds.y1-bounds.y0+1}`, 'busy');
    } else if (mode === 'partial_multi') {
      const rects = getMultiRects();
      if (rects.length === 0) {
        setStatus('画布全透明，无内容可刷新', 'err');
        btn.disabled = false;
        btn.textContent = originalText;
        return;
      }
      bin = packMultiRects(rects);
      setStatus(`多矩形: ${rects.length} 块，约 ${bin.length} 字节`, 'busy');
    } else {
      bin = packLayers();
    }

    await window.E6Display.uploadToScreen(bin, mode, setStatus, bounds);
  } catch (e) {
    setStatus('显示失败：' + e.message, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

function bindEvents() {
  document.querySelectorAll('.tool-btn').forEach(el => {
    el.addEventListener('click', () => {
      currentTool = el.dataset.tool;
      if (textModeActive) {
        textModeActive = false;
        ctxOverlay.clearRect(0, 0, W, H);
      }
      updateToolUI();
    });
  });

  $('brushSize').addEventListener('input', () => {
    brushSize = parseInt($('brushSize').value) || 4;
    $('brushSizeVal').textContent = brushSize;
  });

  $('lockContent').addEventListener('change', () => {
    contentLocked = $('lockContent').checked;
    updateLockUI();
    setStatus(
      contentLocked ? '导入层已锁定：橡皮只擦笔画和文字' : '导入层已解锁：橡皮可穿透擦除 BIN 内容',
      contentLocked ? 'err' : 'ok'
    );
  });

  $('textInput').addEventListener('input', () => {
    renderTextPreviewBox();
    if (textModeActive) ctxOverlay.clearRect(0, 0, W, H);
  });
  $('fontSelect').addEventListener('change', renderTextPreviewBox);
  $('fontSize').addEventListener('input', renderTextPreviewBox);
  $('directionSelect').addEventListener('change', () => {
    renderTextPreviewBox();
    if (textModeActive) ctxOverlay.clearRect(0, 0, W, H);
  });
  $('alignSelect').addEventListener('change', () => {
    renderTextPreviewBox();
    if (textModeActive) ctxOverlay.clearRect(0, 0, W, H);
  });

  ['boldBtn','italicBtn','underlineBtn'].forEach(id => {
    $(id).addEventListener('click', () => {
      $(id).classList.toggle('active');
      renderTextPreviewBox();
      if (textModeActive) ctxOverlay.clearRect(0, 0, W, H);
    });
  });

  $('lineHeightSlider').addEventListener('input', () => {
    $('lineHeightVal').textContent = parseFloat($('lineHeightSlider').value).toFixed(1);
    renderTextPreviewBox();
    if (textModeActive) ctxOverlay.clearRect(0, 0, W, H);
  });

  $('letterSpacingSlider').addEventListener('input', () => {
    $('letterSpacingVal').textContent = $('letterSpacingSlider').value;
    renderTextPreviewBox();
    if (textModeActive) ctxOverlay.clearRect(0, 0, W, H);
  });

  $('rotateSlider').addEventListener('input', () => {
    $('rotateVal').textContent = $('rotateSlider').value + '°';
    renderTextPreviewBox();
    if (textModeActive) ctxOverlay.clearRect(0, 0, W, H);
  });

  $('undoBtn').addEventListener('click', undo);
  $('redoBtn').addEventListener('click', redo);

  $('clearBtn').addEventListener('click', () => {
    saveHistory();
    drawLayer.fill(TRANSPARENT);
    render();
    setStatus('已清空绘制层', 'ok');
  });

  $('clearAllBtn').addEventListener('click', () => {
    let unlockedMsg = '';
    if (contentLocked) {
      contentLocked = false;
      $('lockContent').checked = false;
      updateLockUI();
      unlockedMsg = '（已自动解锁导入层）';
    }
    saveHistory();
    drawLayer.fill(TRANSPARENT);
    binLayer.fill(TRANSPARENT);
    render();
    setStatus('已清空全部' + unlockedMsg, 'ok');
  });

  $('loadBtn').addEventListener('click', () => $('fileInput').click());

  $('fileInput').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const buf = new Uint8Array(await f.arrayBuffer());
      if (buf.length !== FRAME_BYTES) {
        setStatus('文件大小错误：' + buf.length + ' 字节，应为 ' + FRAME_BYTES, 'err');
        return;
      }
      saveHistory();
      binLayer = unpack(buf);
      drawLayer.fill(TRANSPARENT);
      render();
      $('fileInfo').textContent = '已加载：' + f.name + '（' + buf.length + ' 字节）';
      setStatus('BIN 已加载到导入层（绘制层已清空）', 'ok');
    } catch (err) {
      setStatus('加载失败：' + err.message, 'err');
    } finally {
      e.target.value = '';
    }
  });

  $('saveBtn').addEventListener('click', () => {
    try {
      const buf = packLayers();
      const blob = new Blob([buf], { type:'application/octet-stream' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'image_' + Date.now() + '.bin';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      setStatus('已保存 ' + buf.length + ' 字节（1bpp 抖动）', 'ok');
    } catch (e) {
      setStatus('保存失败：' + e.message, 'err');
    }
  });

  $('displayBtn').addEventListener('click', displayToScreen);

  $('clearGhostingBtn').addEventListener('click', async () => {
    const btn = $('clearGhostingBtn');
    btn.disabled = true;
    await window.E6Display.clearGhosting('full', setStatus);
    btn.disabled = false;
  });

  $('placeTextBtn').addEventListener('click', () => {
    const text = $('textInput').value;
    if (!text) { setStatus('请先输入文字', 'err'); return; }
    textModeActive = true;
    updateToolUI();
    setStatus('点击画布放置文字：' + (text.split('\n')[0].slice(0,20) || '…'));
  });

  canvasMain.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const { x, y } = getCanvasCoord(e);

    if (textModeActive) {
      saveHistory();
      const ok = drawTextToDrawLayer(x, y);
      if (ok) {
        textModeActive = false;
        ctxOverlay.clearRect(0, 0, W, H);
        render();
        updateToolUI();
        setStatus('文字已放置（在绘制层）', 'ok');
      } else {
        undoStack.pop();
        setStatus('文字未产生任何像素', 'err');
      }
      return;
    }

    if (currentTool === 'eyedropper') {
      pickColorAt(x, y);
      return;
    }

    saveHistory();
    drawing = true;
    currentPointerId = e.pointerId;
    try { canvasMain.setPointerCapture(e.pointerId); } catch(_) {}
    lastX = x; lastY = y;
    paintBrush(x, y);
    requestRender();
  });

  canvasMain.addEventListener('pointermove', (e) => {
    const { x, y } = getCanvasCoord(e);

    if (textModeActive && !drawing) {
      renderTextPreview(x, y);
      return;
    }
    if (!drawing) return;
    drawLine(lastX, lastY, x, y);
    lastX = x; lastY = y;
    requestRender();
  });

  const endDraw = () => {
    if (!drawing) return;
    drawing = false;
    if (currentPointerId !== null) {
      try { canvasMain.releasePointerCapture(currentPointerId); } catch(_) {}
      currentPointerId = null;
    }
    render();
  };
  canvasMain.addEventListener('pointerup', endDraw);
  canvasMain.addEventListener('pointercancel', endDraw);
  canvasMain.addEventListener('pointerleave', () => {
    if (textModeActive && !drawing) ctxOverlay.clearRect(0, 0, W, H);
    if (drawing) endDraw();
  });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
      if (e.shiftKey) redo(); else undo();
      e.preventDefault();
    } else if ((e.ctrlKey || e.metaKey) && e.key === 'y') {
      redo(); e.preventDefault();
    } else if (e.key === 'Escape' && textModeActive) {
      textModeActive = false;
      ctxOverlay.clearRect(0, 0, W, H);
      updateToolUI();
      setStatus('已取消文字放置');
    }
  });
}

function init() {
  buildSwatches();
  updateColorUI();
  updateToolUI();
  updateLockUI();
  updateHistoryButtons();
  bindEvents();
  render();
  renderTextPreviewBox();
  setStatus('就绪：打开 BIN 或直接在空白画布上绘制');
}

init();

})();
'use strict';

const $ = id => document.getElementById(id);
const voices = ['S', 'S2', 'A', 'A2', 'T', 'T2', 'B', 'B2', 'P'];
const voiceNames = {S:'女高', S2:'女高二', A:'女低', A2:'女低二', T:'男高', T2:'男高二', B:'男低', B2:'男低二', P:'钢琴'};
const divisiPartner = {S:'S2', A:'A2', T:'T2', B:'B2'};
let piece = null;
let page = 1;
let pollTimer = null;
let recognizing = false;

function status(text, bad = false) {
  $('status').textContent = text;
  $('status').classList.toggle('bad', bad);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败');
  return data;
}

function showPage() {
  if (!piece) return;
  const total = Math.max(1, Number(piece.pages) || 1);
  page = Math.min(Math.max(1, page), total);
  $('empty').hidden = true;
  let image = $('page');
  if (!image) {
    image = document.createElement('img');
    image.id = 'page';
    image.alt = '乐谱页面';
    $('stage').appendChild(image);
  }
  image.hidden = false;
  const mark = `${piece.id}:${page}`;
  if (image.dataset.page !== mark) {
    image.dataset.page = mark;
    image.src = `/api/pieces/${piece.id}/pages/${page}`;
    $('stage').scrollTop = 0;
  }
  $('pageLabel').textContent = `${page} / ${total}`;
  $('prev').disabled = page <= 1;
  $('next').disabled = page >= total;
}

function noteCount(voice, source = piece) {
  return (source?.voices?.[voice] || []).length;
}

function displayName(voice, counts) {
  const partner = divisiPartner[voice];
  const second = counts ? Number(counts[partner] || 0) : noteCount(partner);
  if (partner && second) return `${voiceNames[voice]}一`;
  return voiceNames[voice];
}

function hasNotes(source = piece) {
  return !!source && voices.some(voice => noteCount(voice, source));
}

function renderDesk() {
  const running = recognizing || piece?.job?.status === 'running';
  const button = $('recognize');
  const ready = !!piece && !running && hasNotes();
  const total = Number(piece?.job?.total) || 0;
  const done = Number(piece?.job?.done) || 0;
  const bar = $('recognizeProgress');
  const note = $('recognizeNote');
  button.disabled = !piece || running;
  button.setAttribute('aria-busy', running ? 'true' : 'false');
  if (running && total > 0) {
    const read = Math.min(done, total);
    bar.hidden = false;
    bar.max = total;
    bar.value = read;
    button.style.setProperty('--read', String(read / total));
    button.classList.add('has-progress');
    note.textContent = `已读 ${read} / ${total} 页`;
  } else if (running) {
    bar.hidden = false;
    bar.removeAttribute('value');
    button.style.setProperty('--read', '0.2');
    button.classList.remove('has-progress');
    note.textContent = '正在读谱';
  } else {
    bar.hidden = true;
    bar.removeAttribute('value');
    button.style.removeProperty('--read');
    button.classList.remove('has-progress');
    note.textContent = hasNotes() ? '再读一次' : '读这一份';
  }
  $('openMuseScore').disabled = !ready;
  $('exportScore').disabled = !ready;
  $('museNote').textContent = ready ? '打开去改音' : '先识别，再打开';
  const canImport = !!piece && !running;
  $('importScore').disabled = !canImport;
  $('importScore').closest('.act').classList.toggle('is-off', !canImport);
  button.classList.toggle('next', !!piece && !running && !hasNotes());
  $('openMuseScore').classList.toggle('next', ready);
}

function escapeText(value) {
  return String(value).replace(/[&<>"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[char]));
}

function renderHistory(rows) {
  if (!rows.length) {
    $('history').innerHTML = '<p class="hint">还没有上传过的谱。</p>';
    return;
  }
  $('history').innerHTML = rows.map(row => {
    const counts = row.counts || {};
    const bits = voices.filter(voice => counts[voice]).map(voice => `${displayName(voice, counts)} ${counts[voice]}`);
    const detail = `${row.pages} 页${bits.length ? '，' + bits.join('，') : ''}`;
    return `<div class="past"><button type="button" class="past-open" data-open="${row.id}"><strong>${escapeText(row.name)}</strong><span>${detail}</span></button><button type="button" class="quiet past-delete" data-delete="${row.id}">删除</button></div>`;
  }).join('');
}

function showLibrary(open) {
  $('home').classList.toggle('library', open);
  $('history').inert = !open;
  $('libraryToggle').setAttribute('aria-pressed', open ? 'true' : 'false');
}

function showHome() {
  recognizing = false;
  clearTimeout(pollTimer);
  piece = null;
  page = 1;
  showLibrary(false);
  $('work').hidden = true;
  $('home').hidden = false;
  history.replaceState(null, '', '/');
  status('');
  startStaff();
}

function showWork() {
  $('home').hidden = true;
  $('work').hidden = false;
  stopStaff();
}

function setPiece(next) {
  showWork();
  piece = next;
  $('pieceName').textContent = next.name;
  $('pieceName').title = next.name;
  page = Math.min(Math.max(page, 1), next.pages);
  recognizing = next.job?.status === 'running';
  history.replaceState(null, '', `/?piece=${next.id}`);
  showPage();
  renderDesk();
  clearTimeout(pollTimer);
  if (next.job?.status === 'running') {
    pollTimer = setTimeout(async () => {
      try { setPiece(await api(`/api/pieces/${next.id}`)); }
      catch (error) { status(error.message, true); }
    }, 2000);
  }
  if (next.job?.status === 'done') status('');
  else if (next.job?.status === 'failed') status(next.job.message, true);
}

async function refreshLibrary() {
  const rows = await api('/api/pieces');
  renderHistory(rows);
}

async function openFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const form = new FormData();
  files.forEach(file => form.append('files', file));
  status('正在放入谱页…');
  page = 1;
  setPiece(await api('/api/pieces', {method:'POST', body:form}));
  await refreshLibrary();
  status('谱放好了。点「识别」。');
}

async function download(url, filename) {
  const response = await fetch(url);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || '导出失败');
  }
  const link = document.createElement('a');
  link.href = URL.createObjectURL(await response.blob());
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

$('file').onchange = async () => {
  try { await openFiles($('file').files); $('file').value = ''; }
  catch (error) { status(error.message, true); }
};
$('fileAgain').onchange = async () => {
  try { await openFiles($('fileAgain').files); $('fileAgain').value = ''; }
  catch (error) { status(error.message, true); }
};
$('back').onclick = () => { showHome(); refreshLibrary().catch(error => status(error.message, true)); };
$('libraryToggle').onclick = () => showLibrary(!$('home').classList.contains('library'));
$('history').onclick = async event => {
  const remove = event.target.closest('[data-delete]');
  if (remove) {
    const name = remove.closest('.past')?.querySelector('strong')?.textContent || '这份谱';
    if (!window.confirm(`删除「${name}」？谱和识别结果会一起去掉。`)) return;
    try {
      await api(`/api/pieces/${remove.dataset.delete}`, {method:'DELETE'});
      if (piece?.id === remove.dataset.delete) showHome();
      await refreshLibrary();
      status('已删除。');
    } catch (error) { status(error.message, true); }
    return;
  }
  const button = event.target.closest('[data-open]');
  if (!button) return;
  try {
    page = 1;
    setPiece(await api(`/api/pieces/${button.dataset.open}`));
    if (piece.job?.status === 'running') status('正在读谱。这一步会久一点，请留在这个页面。');
    else if (piece.job?.status !== 'failed') status(hasNotes() ? '已打开。可以进 MuseScore 改。' : '已打开。点「识别」。');
  }
  catch (error) { status(error.message, true); }
};
$('home').ondragover = event => { event.preventDefault(); $('home').classList.add('over'); };
$('home').ondragleave = event => {
  if (event.relatedTarget && $('home').contains(event.relatedTarget)) return;
  $('home').classList.remove('over');
};
$('home').ondrop = async event => {
  event.preventDefault();
  $('home').classList.remove('over');
  try { await openFiles(event.dataTransfer.files); }
  catch (error) { status(error.message, true); }
};
$('stage').ondragover = event => { event.preventDefault(); $('stage').classList.add('over'); };
$('stage').ondragleave = () => $('stage').classList.remove('over');
$('stage').ondrop = async event => {
  event.preventDefault();
  $('stage').classList.remove('over');
  try { await openFiles(event.dataTransfer.files); }
  catch (error) { status(error.message, true); }
};
$('prev').onclick = () => { page -= 1; showPage(); };
$('next').onclick = () => { page += 1; showPage(); };
$('recognize').onclick = async () => {
  if (!piece || recognizing || piece.job?.status === 'running') return;
  recognizing = true;
  renderDesk();
  $('recognize').blur();
  try {
    setPiece(await api(`/api/pieces/${piece.id}/recognize`, {method:'POST'}));
    status('正在读谱。这一步会久一点，请留在这个页面。');
  } catch (error) {
    const busy = String(error.message).includes('正在识别');
    if (busy && piece) {
      status(error.message, true);
      try { setPiece(await api(`/api/pieces/${piece.id}`)); }
      catch (pollError) {
        recognizing = false;
        renderDesk();
        status(pollError.message, true);
      }
      return;
    }
    recognizing = false;
    renderDesk();
    status(error.message, true);
  }
};
document.addEventListener('keydown', event => {
  if ($('work').hidden || event.altKey || event.ctrlKey || event.metaKey || event.repeat) return;
  const tag = event.target.tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
  if (event.key === 'ArrowLeft' && !$('prev').disabled) {
    event.preventDefault();
    page -= 1;
    showPage();
  } else if (event.key === 'ArrowRight' && !$('next').disabled) {
    event.preventDefault();
    page += 1;
    showPage();
  }
});
$('exportScore').onclick = async () => {
  try {
    const stem = (piece.name || 'score').replace(/\.[^.]+$/, '');
    await download(`/api/pieces/${piece.id}/score.musicxml`, `${stem}.musicxml`);
    status('电子谱已下载。');
  } catch (error) { status(error.message, true); }
};
$('openMuseScore').onclick = async () => {
  try {
    status('正在打开 MuseScore…');
    await api(`/api/pieces/${piece.id}/musescore`, {method: 'POST'});
    status('已在 MuseScore 打开。改完后从 MuseScore 导出 MusicXML，再点「导入」。');
  } catch (error) { status(error.message, true); }
};
$('importScore').onchange = async () => {
  const file = $('importScore').files[0];
  $('importScore').value = '';
  if (!file || !piece) return;
  const form = new FormData();
  form.append('file', file);
  try {
    status('正在读改过的谱…');
    setPiece(await api(`/api/pieces/${piece.id}/score`, {method:'POST', body:form}));
    status('已导入电子谱。');
  } catch (error) { status(error.message, true); }
};

const staffMotion = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const staffPointer = { x: 0.62, y: 0.46 };
const staffFollow = { x: 0.62, y: 0.46, vx: 0, vy: 0 };
let staffRaf = 0;
const openingHue = Number(document.documentElement.dataset.hue) || 0;
const phrases = [
  [2, 4, 5, 4, 7, 5, 3],
  [1, 3, 6, 5, 4, 6, 8],
  [4, 4, 2, 5, 7, 3, 1],
  [0, 2, 4, 7, 5, 4, 2],
  [3, 5, 8, 6, 4, 2, 5],
];
const phrase = phrases[openingHue % phrases.length];
const echo = phrases[(openingHue + 2) % phrases.length];

function spring(value, velocity, target, dt) {
  const accel = -180 * (value - target) - 24 * velocity;
  velocity += accel * dt;
  value += velocity * dt;
  return [value, velocity];
}

function fitStaff() {
  const canvas = $('staff');
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width * dpr));
  const height = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return { dpr, width: rect.width, height: rect.height };
}

function inkColors() {
  const style = getComputedStyle(document.documentElement);
  return {
    ink: style.getPropertyValue('--paper').trim() || '#e7e4d8',
    mark: style.getPropertyValue('--mark').trim() || '#d24a30',
  };
}

function drawStaff() {
  const canvas = $('staff');
  const { dpr, width, height } = fitStaff();
  const ctx = canvas.getContext('2d');
  const { ink, mark } = inkColors();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  placeHomeTitle();
  const homeRect = $('home').getBoundingClientRect();
  const dropRect = $('historyDrop').getBoundingClientRect();
  const titleRect = $('homeTitle').getBoundingClientRect();
  const bandTop = Math.max(48, titleRect.bottom - homeRect.top + 12);
  const bandBottom = Math.max(bandTop + 96, dropRect.top - homeRect.top - 28);
  const span = bandBottom - bandTop;
  const gap = Math.min(14, span / 12);
  const center = bandTop + span * 0.62 + (staffFollow.y - 0.5) * span * 0.16;
  const shift = staffMotion ? Math.round((0.5 - staffFollow.y) * 3) : 0;
  const wave = x => {
    if (!staffMotion) return 0;
    const t = x / Math.max(width, 1);
    return Math.sin(t * Math.PI) * (staffFollow.y - 0.5) * 32
      + Math.sin(t * Math.PI * 1.4 + staffFollow.x * 5) * 7;
  };
  const lineY = (base, x) => base + wave(x);
  function system(origin, lineGap, alpha) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = ink;
    ctx.lineWidth = 1;
    for (let line = 0; line < 5; line += 1) {
      const base = origin + (line - 2) * lineGap;
      ctx.beginPath();
      for (let x = 0; x <= width; x += 8) {
        const y = lineY(base, x);
        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    ctx.restore();
  }
  function bloom(x, y, radius, color, alpha) {
    const paint = ctx.createRadialGradient(x, y, 0, x, y, radius);
    paint.addColorStop(0, color);
    paint.addColorStop(1, 'transparent');
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.fillStyle = paint;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
  bloom(width * 0.18, bandTop + span * 0.22, Math.min(width, span) * 0.42, mark, 0.22);
  bloom(width * 0.78, bandTop + span * 0.48, Math.min(width, span) * 0.36, ink, 0.1);
  if (span > 180) system(center - gap * 7.2, gap * 0.82, 0.22);
  system(center, gap, 0.55);
  ctx.strokeStyle = ink;
  ctx.fillStyle = ink;
  ctx.lineWidth = 1.2;
  [0.14, 0.5, 0.86].forEach((place, index) => {
    const x = width * place;
    const top = lineY(center - 2 * gap, x);
    const bottom = lineY(center + 2 * gap, x);
    ctx.globalAlpha = 0.55;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    if (index === 2) {
      ctx.moveTo(x + 5, top);
      ctx.lineTo(x + 5, bottom);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  });
  const notes = [];
  phrase.forEach((step, index) => {
    const x = width * (0.22 + index * 0.07) + (staffFollow.x - 0.62) * width * 0.16;
    const placed = Math.max(0, Math.min(8, step + shift));
    const y = lineY(center + 2 * gap - placed * (gap / 2), x);
    notes.push({ x, y, placed });
    ctx.beginPath();
    ctx.ellipse(x, y, 7, 5, -0.45, 0, Math.PI * 2);
    ctx.fill();
    const stemUp = placed < 4;
    ctx.beginPath();
    ctx.moveTo(x + (stemUp ? 6.2 : -6.2), y);
    ctx.lineTo(x + (stemUp ? 6.2 : -6.2), y + (stemUp ? -30 : 30));
    ctx.stroke();
    if (index % 2 === 0) {
      ctx.beginPath();
      ctx.arc(x, y + (stemUp ? 12 : -12), 1.7, 0, Math.PI * 2);
      ctx.fill();
    }
  });
  if (notes.length > 2) {
    const first = notes[0];
    const last = notes[notes.length - 1];
    const mid = notes[Math.floor(notes.length / 2)];
    ctx.globalAlpha = 0.75;
    ctx.beginPath();
    ctx.moveTo(first.x, first.y - 22);
    ctx.quadraticCurveTo(mid.x, mid.y - 40, last.x, last.y - 22);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  if (span > 220) {
    const lower = bandTop + span * 0.9;
    system(lower, gap * 0.72, 0.28);
    ctx.save();
    ctx.globalAlpha = 0.8;
    ctx.fillStyle = mark;
    ctx.strokeStyle = mark;
    echo.forEach((step, index) => {
      if (index > 4) return;
      const x = width * (0.58 - index * 0.07);
      const placed = Math.max(0, Math.min(8, step));
      const y = lineY(lower + gap * 0.72 - placed * (gap * 0.36), x);
      ctx.beginPath();
      ctx.ellipse(x, y, 5.2, 3.8, -0.4, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.restore();
  }
  if (span > 180) {
    ctx.globalAlpha = 0.45;
    echo.forEach((step, index) => {
      const x = width * (0.3 + index * 0.06);
      const placed = Math.max(0, Math.min(8, step));
      const origin = center - gap * 7.2;
      const y = lineY(origin + 2 * gap * 0.82 - placed * (gap * 0.41), x);
      ctx.beginPath();
      ctx.ellipse(x, y, 5.5, 4, -0.4, 0, Math.PI * 2);
      ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }
}

function placeHomeTitle() {
  const title = $('homeTitle');
  if (!staffMotion) {
    title.style.transform = 'none';
    return;
  }
  const dx = Math.max(-40, Math.min(40, (staffFollow.x - 0.62) * 100));
  const dy = Math.max(-24, Math.min(24, (staffFollow.y - 0.46) * 80));
  title.style.transform = `translate(${dx}px, ${dy}px)`;
}

function stopStaff() {
  cancelAnimationFrame(staffRaf);
  staffRaf = 0;
}

function startStaff() {
  if ($('home').hidden) return;
  stopStaff();
  drawStaff();
  if (!staffMotion) return;
  let last = performance.now();
  const frame = now => {
    if ($('home').hidden || document.hidden) {
      staffRaf = 0;
      return;
    }
    const dt = Math.min(0.032, (now - last) / 1000);
    last = now;
    [staffFollow.x, staffFollow.vx] = spring(staffFollow.x, staffFollow.vx, staffPointer.x, dt);
    [staffFollow.y, staffFollow.vy] = spring(staffFollow.y, staffFollow.vy, staffPointer.y, dt);
    drawStaff();
    staffRaf = requestAnimationFrame(frame);
  };
  staffRaf = requestAnimationFrame(frame);
}

$('home').addEventListener('pointermove', event => {
  const rect = $('home').getBoundingClientRect();
  staffPointer.x = (event.clientX - rect.left) / Math.max(rect.width, 1);
  staffPointer.y = (event.clientY - rect.top) / Math.max(rect.height, 1);
  if (!staffRaf && !$('home').hidden) startStaff();
});
$('home').addEventListener('pointerleave', () => {
  staffPointer.x = 0.62;
  staffPointer.y = 0.46;
});
window.addEventListener('resize', () => { if (!$('home').hidden) drawStaff(); });
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && !$('home').hidden) startStaff();
});

(async () => {
  try {
    startStaff();
    await refreshLibrary();
    const id = new URLSearchParams(location.search).get('piece');
    if (!id) return;
    setPiece(await api(`/api/pieces/${id}`));
    if (piece.job?.status === 'idle') status('谱放好了。点「识别」。');
  } catch (error) { status(error.message, true); }
})();

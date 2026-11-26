const $ = id => document.getElementById(id);
const SLICES = 48;
let KEY = 18;
let BEAT = 96;
let PX = BEAT / SLICES;
const LOW = 21;
const HIGH = 108;
const VOICES = ['S', 'S2', 'A', 'A2', 'T', 'T2', 'B', 'B2', 'P'];
const LABELS = {
  S: '女高一', S2: '女高二', A: '女低一', A2: '女低二',
  T: '男高一', T2: '男高二', B: '男低一', B2: '男低二', P: '钢琴',
};
const RHYTHMS = [
  ['十六分', 12],
  ['八分', 24],
  ['三连音八分', 16],
  ['四分', 48],
  ['附点八分', 36],
  ['附点四分', 72],
  ['三连音四分', 32],
  ['二分', 96],
  ['附点二分', 144],
  ['全音符', 192],
];
const TOKENS = {
  12: '0.25', 24: '0.5', 16: '1/3', 48: '1', 36: '0.75',
  72: '1.5', 32: '2/3', 96: '2', 144: '3', 192: '4',
};
const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const BLACK = new Set([1, 3, 6, 8, 10]);

const params = new URLSearchParams(location.search);
const pieceId = params.get('piece') || '';
const notes = Object.fromEntries(VOICES.map(voice => [voice, []]));
let piece = null;
let active = 'S';
let grid = 48;
let selected = -1;
let showGhosts = true;
let drag = null;
let framed = false;
const channel = 'BroadcastChannel' in window ? new BroadcastChannel('choirian-tune') : null;

function gcd(left, right) {
  let a = Math.abs(left);
  let b = Math.abs(right);
  while (b) {
    const next = a % b;
    a = b;
    b = next;
  }
  return a || 1;
}

function pitchName(pitch) {
  return NAMES[((pitch % 12) + 12) % 12] + (Math.floor(pitch / 12) - 1);
}

function beatToken(slices) {
  if (TOKENS[slices]) return TOKENS[slices];
  const divisor = gcd(slices, SLICES);
  return `${slices / divisor}/${SLICES / divisor}`;
}

function rhythmName(slices) {
  const found = RHYTHMS.find(([, size]) => size === slices);
  return found ? found[0] : '';
}

function phrase(slices) {
  if (!Number.isFinite(slices) || slices < 1) return '时值太短';
  const name = rhythmName(slices);
  const token = beatToken(slices);
  return name ? `${token} 拍 · ${name}` : `${token} 拍，不是标准节奏`;
}

function parseSlices(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return null;
  let beats;
  if (raw.includes('/')) {
    const [left, right] = raw.split('/');
    const numerator = Number(left);
    const denominator = Number(right);
    if (!denominator || !Number.isFinite(numerator) || !Number.isFinite(denominator)) return null;
    beats = numerator / denominator;
  } else {
    beats = Number(raw);
  }
  if (!Number.isFinite(beats)) return null;
  return Math.round(beats * SLICES);
}

function snap(beats) {
  return Math.round(Number(beats) * SLICES);
}

function status(text, bad = false) {
  const node = $('status');
  node.textContent = text || '';
  node.classList.toggle('bad', Boolean(bad));
}

function beatsPerBar() {
  return [2, 3, 4, 6].includes(piece?.beats_per_bar) ? piece.beats_per_bar : 4;
}

function span() {
  let end = 0;
  for (const voice of VOICES) {
    for (const note of notes[voice]) end = Math.max(end, note.start + note.duration);
  }
  const fromNotes = Math.ceil(end / (beatsPerBar() * SLICES) - 1e-9);
  const beatWidth = beatsPerBar() * BEAT;
  const visible = Math.ceil(($('timeCol').clientWidth || beatWidth * 4) / beatWidth);
  const bars = Math.min(400, Math.max(piece?.bars || 1, fromNotes, visible));
  return {
    bars,
    width: bars * beatsPerBar() * BEAT,
    height: (HIGH - LOW + 1) * KEY,
  };
}

function pairKey(a, b) {
  const swap = b.start < a.start || (b.start === a.start && b.duration < a.duration);
  const left = swap ? b : a;
  const right = swap ? a : b;
  return `${left.start},${left.duration},${right.start},${right.duration}`;
}

function overlapCounts(list) {
  const counts = new Map();
  const ordered = [...list].sort((a, b) => a.start - b.start || a.duration - b.duration);
  for (let index = 0; index < ordered.length; index += 1) {
    const earlier = ordered[index];
    const end = earlier.start + earlier.duration;
    for (let laterIndex = index + 1; laterIndex < ordered.length; laterIndex += 1) {
      const later = ordered[laterIndex];
      if (later.start >= end) break;
      const key = pairKey(earlier, later);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  return counts;
}

function addsOverlap(before, after) {
  for (const [key, count] of after) {
    if (count > (before.get(key) || 0)) return true;
  }
  return false;
}

function clashes(voice, note, ignore) {
  if (voice === 'P') return false;
  const current = notes[voice];
  const proposed = current.map((item, index) => index === ignore ? note : item);
  if (ignore < 0) proposed.push(note);
  return addsOverlap(overlapCounts(current), overlapCounts(proposed));
}

function place(node, note) {
  node.style.left = `${note.start * PX}px`;
  node.style.top = `${(HIGH - note.pitch) * KEY + 1}px`;
  node.style.width = `${Math.max(note.duration * PX - 2, 4)}px`;
  node.style.height = `${KEY - 2}px`;
  node.textContent = note.duration * PX >= 36 ? pitchName(note.pitch) : '';
  node.title = `${LABELS[node.dataset.voice]} ${pitchName(note.pitch)} · ${phrase(note.duration)}`;
}

function showDuration(slices) {
  if (!slices) return;
  $('duration').value = beatToken(slices);
  $('readout').textContent = phrase(slices);
}

function paintChoices() {
  for (const button of $('voices').querySelectorAll('button')) {
    button.setAttribute('aria-pressed', button.dataset.voice === active ? 'true' : 'false');
  }
  for (const button of $('rhythms').querySelectorAll('button')) {
    button.setAttribute('aria-pressed', Number(button.dataset.slices) === grid ? 'true' : 'false');
  }
  $('remove').disabled = selected < 0;
}

function render() {
  const box = span();
  const left = $('timeCol').scrollLeft;
  const top = $('timeCol').scrollTop;
  const lane = $('lane');
  lane.style.width = `${box.width}px`;
  lane.style.height = `${box.height}px`;
  lane.style.setProperty('--bar', `${beatsPerBar() * BEAT}px`);
  lane.style.setProperty('--beat', `${BEAT}px`);
  lane.style.setProperty('--cell', `${Math.max(grid, 1) * PX}px`);
  lane.style.setProperty('--key', `${KEY}px`);
  lane.replaceChildren();
  VOICES.forEach(voice => {
    if (voice !== active && !showGhosts) return;
    notes[voice].forEach((note, index) => {
      const mine = voice === active;
      const node = document.createElement(mine ? 'button' : 'div');
      node.className = mine ? 'blob' : 'blob ghost';
      if (mine) node.type = 'button';
      node.dataset.voice = voice;
      node.dataset.index = String(index);
      if (mine && index === selected) {
        node.classList.add('on');
        node.setAttribute('aria-pressed', 'true');
      }
      place(node, note);
      if (mine) node.addEventListener('pointerdown', event => beginDrag(event, index));
      lane.appendChild(node);
    });
  });
  const ruler = $('rulerInner');
  ruler.style.width = `${box.width}px`;
  ruler.replaceChildren();
  for (let bar = 1; bar <= box.bars; bar += 1) {
    const mark = document.createElement('button');
    mark.type = 'button';
    mark.className = 'bar-no';
    mark.style.left = `${(bar - 1) * beatsPerBar() * BEAT}px`;
    mark.style.width = `${beatsPerBar() * BEAT}px`;
    mark.textContent = String(bar);
    mark.addEventListener('click', () => seekBeats((bar - 1) * beatsPerBar()));
    ruler.appendChild(mark);
  }
  const line = document.createElement('div');
  line.id = 'playhead';
  line.className = 'playhead';
  lane.appendChild(line);
  for (const row of $('keys').children) row.style.height = `${KEY}px`;
  $('keys').style.height = `${box.height}px`;
  $('timeCol').scrollLeft = left;
  $('timeCol').scrollTop = top;
  syncScroll();
  paintChoices();
  paintPlayhead();
  if (!framed) frameVoice();
}

function frameVoice() {
  framed = true;
  const list = notes[active];
  if (!list.length) return;
  const pitches = list.map(note => note.pitch).sort((a, b) => a - b);
  const mid = pitches[Math.floor(pitches.length / 2)];
  $('timeCol').scrollTop = Math.max(0, (HIGH - mid) * KEY - $('timeCol').clientHeight / 2 + KEY);
  syncScroll();
}

function syncScroll() {
  $('keys').style.transform = `translateY(${-$('timeCol').scrollTop}px)`;
  $('rulerInner').style.transform = `translateX(${-$('timeCol').scrollLeft}px)`;
}

function beginDrag(event, index) {
  if (event.button !== 0) return;
  event.preventDefault();
  event.stopPropagation();
  const note = notes[active][index];
  const node = event.currentTarget;
  selected = index;
  paintChoices();
  showDuration(note.duration);
  node.classList.add('on');
  const edge = event.clientX > node.getBoundingClientRect().right - 10;
  drag = {
    index,
    mode: edge ? 'duration' : 'move',
    x: event.clientX,
    y: event.clientY,
    start: note.start,
    duration: note.duration,
    pitch: note.pitch,
  };
  node.setPointerCapture(event.pointerId);
  const follow = moveEvent => moveDrag(moveEvent);
  const finish = upEvent => {
    node.removeEventListener('pointermove', follow);
    node.removeEventListener('pointerup', finish);
    node.removeEventListener('pointercancel', finish);
    endDrag(upEvent);
  };
  node.addEventListener('pointermove', follow);
  node.addEventListener('pointerup', finish);
  node.addEventListener('pointercancel', finish);
  status('');
}

function moveDrag(event) {
  if (!drag || event.currentTarget.dataset.index !== String(drag.index)) return;
  const dx = event.clientX - drag.x;
  const dy = event.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  const next = {start: drag.start, duration: drag.duration, pitch: drag.pitch};
  const steps = Math.round(dx / (grid * PX));
  if (drag.mode === 'duration') {
    next.duration = Math.max(1, drag.duration + steps * grid);
  } else {
    next.start = Math.max(0, drag.start + steps * grid);
    next.pitch = Math.min(HIGH, Math.max(LOW, drag.pitch - Math.round(dy / KEY)));
  }
  if (clashes(active, next, drag.index)) {
    status('同一声部的音符重叠了', true);
    return;
  }
  notes[active][drag.index] = next;
  place(event.currentTarget, next);
  showDuration(next.duration);
  status('');
}

function endDrag(event) {
  if (!drag || event.currentTarget.dataset.index !== String(drag.index)) return;
  moveDrag(event);
  const note = notes[active][drag.index];
  const changed = note.start !== drag.start || note.duration !== drag.duration || note.pitch !== drag.pitch;
  drag = null;
  if (changed) save().catch(error => status(error.message, true));
  else render();
}

function addAt(event) {
  if (event.button !== 0 || event.target.closest('.blob')) return;
  const rect = $('lane').getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const pitch = Math.min(HIGH, Math.max(LOW, HIGH - Math.floor(y / KEY)));
  const start = Math.max(0, Math.floor(x / (grid * PX)) * grid);
  const note = {start, duration: grid, pitch};
  if (clashes(active, note, -1)) {
    status('同一声部的音符重叠了', true);
    return;
  }
  notes[active].push(note);
  selected = notes[active].length - 1;
  showDuration(grid);
  status('');
  save().catch(error => status(error.message, true));
}

function applySlices(slices) {
  if (!Number.isFinite(slices) || slices < 1) {
    status('时值无效', true);
    return;
  }
  grid = slices;
  showDuration(slices);
  if (selected < 0) {
    render();
    return;
  }
  const current = notes[active][selected];
  const next = {start: current.start, duration: slices, pitch: current.pitch};
  if (clashes(active, next, selected)) {
    status('同一声部的音符重叠了', true);
    render();
    return;
  }
  notes[active][selected] = next;
  status('');
  save().catch(error => status(error.message, true));
}

function nudge(pitchBy, startBy) {
  if (selected < 0) return;
  const current = notes[active][selected];
  const next = {
    start: Math.max(0, current.start + startBy),
    duration: current.duration,
    pitch: Math.min(HIGH, Math.max(LOW, current.pitch + pitchBy)),
  };
  if (next.start === current.start && next.pitch === current.pitch) return;
  if (clashes(active, next, selected)) {
    status('同一声部的音符重叠了', true);
    return;
  }
  notes[active][selected] = next;
  showDuration(next.duration);
  status('');
  save().catch(error => status(error.message, true));
}

function removeSelected() {
  if (selected < 0) return;
  notes[active].splice(selected, 1);
  selected = -1;
  status('');
  save().catch(error => status(error.message, true));
}

function jump() {
  const number = Math.round(Number($('bar').value));
  if (!Number.isFinite(number) || number < 1) return;
  seekBeats((number - 1) * beatsPerBar());
}

const pendingVoices = new Set();
let saving = null;

async function save() {
  const voice = active;
  pendingVoices.add(voice);
  if (saving) return saving;
  saving = (async () => {
    while (pendingVoices.size) {
      const savingVoice = pendingVoices.values().next().value;
      pendingVoices.delete(savingVoice);
      const mark = selected >= 0 && savingVoice === active ? {...notes[savingVoice][selected]} : null;
      const response = await fetch(`/api/pieces/${pieceId}/voice`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          voice: savingVoice,
          notes: notes[savingVoice].map(note => ({
            start: note.start / SLICES,
            duration: note.duration / SLICES,
            pitch: note.pitch,
          })),
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = body.detail;
        throw new Error(typeof detail === 'string' ? detail : '没有保存');
      }
      piece.bars = body.bars;
      piece.bpm = body.bpm;
      piece.updated = body.updated;
      piece.tuned = true;
      dropTuneAudio();
      notes[savingVoice] = (body.voices[savingVoice] || []).map(note => ({
        start: snap(note.start),
        duration: snap(note.duration),
        pitch: note.pitch,
      }));
      if (mark && savingVoice === active) {
        selected = notes[savingVoice].findIndex(item => item.start === mark.start && item.duration === mark.duration && item.pitch === mark.pitch);
      }
      channel?.postMessage({id: pieceId});
    }
    if (!drag) render();
  })();
  try {
    await saving;
  } catch (error) {
    await reload();
    throw error;
  } finally {
    saving = null;
  }
}

function take(source) {
  piece = source;
  for (const voice of VOICES) {
    notes[voice] = (source.voices?.[voice] || []).map(note => ({
      start: snap(note.start),
      duration: Math.max(1, snap(note.duration)),
      pitch: note.pitch,
    }));
  }
}

async function reload() {
  const response = await fetch(`/api/pieces/${pieceId}`);
  if (!response.ok) throw new Error('曲目没有读出来');
  take(await response.json());
  selected = -1;
  render();
}

function buildChrome() {
  const voices = $('voices');
  for (const voice of VOICES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'quiet';
    button.dataset.voice = voice;
    button.textContent = LABELS[voice];
    button.setAttribute('aria-pressed', voice === active ? 'true' : 'false');
    button.addEventListener('click', () => {
      chooseVoice(voice);
    });
    voices.appendChild(button);
  }
  const rhythms = $('rhythms');
  for (const [name, slices] of RHYTHMS) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'quiet';
    button.dataset.slices = String(slices);
    button.textContent = name;
    button.addEventListener('click', () => applySlices(slices));
    rhythms.appendChild(button);
  }
  const keys = $('keys');
  for (let pitch = HIGH; pitch >= LOW; pitch -= 1) {
    const row = document.createElement('div');
    row.className = BLACK.has(pitch % 12) ? 'pkey black' : 'pkey';
    row.style.height = `${KEY}px`;
    if (pitch % 12 === 0) row.textContent = pitchName(pitch);
    keys.appendChild(row);
  }
}

let audioContext = null;
let buffers = {};
let sources = [];
let playing = false;
let offset = 0;
let startedAt = 0;
let audioStamp = '';
let parked = true;
let playRaf = 0;
let pan = null;

function clock(seconds) {
  const whole = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

function playPosition() {
  if (!playing || !audioContext) return offset;
  return offset + (audioContext.currentTime - startedAt);
}

function audioLength() {
  return buffers[active]?.duration || 0;
}

function paintPlayhead() {
  const line = $('playhead');
  if (!line || !piece) return;
  const beats = playPosition() * (piece.bpm || 72) / 60;
  line.style.left = `${beats * BEAT}px`;
  line.hidden = parked && !playing;
  $('now').textContent = clock(playPosition());
  $('play').textContent = playing ? '暂停' : '播放';
  $('play').classList.toggle('on', playing);
  $('play').disabled = !notes[active].length;
}

function stopPlayback() {
  cancelAnimationFrame(playRaf);
  for (const source of sources) {
    source.onended = null;
    try { source.stop(); } catch { /* already stopped */ }
  }
  sources = [];
  playing = false;
}

function dropTuneAudio() {
  stopPlayback();
  buffers = {};
  audioStamp = '';
  parked = true;
  offset = 0;
  paintPlayhead();
}

function viewStart() {
  return $('timeCol').scrollLeft / BEAT * 60 / (piece?.bpm || 72);
}

async function prepareTuneAudio() {
  const bpm = piece?.bpm || 72;
  const stamp = `${piece.updated || ''}:${bpm}`;
  if (audioStamp !== stamp) {
    buffers = {};
    audioStamp = stamp;
  }
  if (buffers[active]) return;
  if (!notes[active].length) return;
  const context = audioContext || (audioContext = new AudioContext());
  const response = await fetch(`/api/pieces/${pieceId}/voices/${active}.wav?bpm=${bpm}`);
  if (!response.ok) throw new Error('音频没有准备好');
  buffers[active] = await context.decodeAudioData(await response.arrayBuffer());
}

function playFrom(seconds) {
  const context = audioContext;
  const total = audioLength();
  if (!context || !total) return;
  stopPlayback();
  offset = Math.min(Math.max(0, seconds), total);
  if (offset >= total - 0.05) offset = 0;
  startedAt = context.currentTime;
  const buffer = buffers[active];
  const amount = 0.9;
  if (buffer && offset < buffer.duration - 0.01) {
    const source = context.createBufferSource();
    const gain = context.createGain();
    source.buffer = buffer;
    gain.gain.value = amount;
    source.connect(gain).connect(context.destination);
    source.start(0, offset);
    sources.push(source);
  }
  if (!sources.length) {
    paintPlayhead();
    return;
  }
  playing = true;
  parked = false;
  const follow = () => {
    if (!playing) return;
    if (playPosition() >= audioLength() - 0.05) {
      stopPlayback();
      offset = 0;
      parked = true;
      paintPlayhead();
      return;
    }
    paintPlayhead();
    const col = $('timeCol');
    const head = playPosition() * (piece.bpm || 72) / 60 * BEAT;
    if (head > col.scrollLeft + col.clientWidth - 40) col.scrollLeft = head - 40;
    playRaf = requestAnimationFrame(follow);
  };
  playRaf = requestAnimationFrame(follow);
}

function seekBeats(beats) {
  const bpm = piece?.bpm || 72;
  const seconds = Math.max(0, beats) * 60 / bpm;
  const wasPlaying = playing;
  offset = seconds;
  parked = false;
  $('timeCol').scrollLeft = Math.max(0, beats * BEAT);
  $('bar').value = String(Math.max(1, Math.floor(beats / beatsPerBar()) + 1));
  syncScroll();
  paintPlayhead();
  if (wasPlaying) playFrom(seconds);
}

async function chooseVoice(voice) {
  if (voice === active) return;
  const at = playPosition();
  const wasPlaying = playing;
  stopPlayback();
  active = voice;
  selected = -1;
  framed = false;
  status('');
  render();
  if (wasPlaying && notes[voice].length) {
    try {
      $('play').disabled = true;
      $('play').textContent = '准备…';
      await prepareTuneAudio();
      if (audioContext?.state === 'suspended') await audioContext.resume();
      playFrom(at);
    } catch (error) {
      status(error.message, true);
      paintPlayhead();
    }
    return;
  }
  paintPlayhead();
}

async function togglePlay() {
  if (!notes[active].length) return;
  if (playing) {
    offset = playPosition();
    stopPlayback();
    parked = false;
    paintPlayhead();
    return;
  }
  const start = parked ? viewStart() : offset;
  $('play').disabled = true;
  $('play').textContent = '准备…';
  try {
    await prepareTuneAudio();
    if (audioContext.state === 'suspended') await audioContext.resume();
    playFrom(start);
  } catch (error) {
    status(error.message, true);
    paintPlayhead();
  }
}

function zoomAt(clientX, clientY, factor) {
  const col = $('timeCol');
  const rect = col.getBoundingClientRect();
  const localX = clientX - rect.left + col.scrollLeft;
  const localY = clientY - rect.top + col.scrollTop;
  const nextBeat = Math.min(512, Math.max(24, BEAT * factor));
  const nextKey = Math.min(42, Math.max(8, KEY * factor));
  if (nextBeat === BEAT && nextKey === KEY) return;
  const beatRatio = nextBeat / BEAT;
  const keyRatio = nextKey / KEY;
  BEAT = nextBeat;
  KEY = nextKey;
  PX = BEAT / SLICES;
  render();
  col.scrollLeft = localX * beatRatio - (clientX - rect.left);
  col.scrollTop = localY * keyRatio - (clientY - rect.top);
  syncScroll();
  paintPlayhead();
}

$('timeCol').addEventListener('scroll', syncScroll);
const roll = document.querySelector('.roll');
roll.addEventListener('pointerdown', event => {
  if (event.button !== 1) return;
  event.preventDefault();
  pan = {x: event.clientX, y: event.clientY, left: $('timeCol').scrollLeft, top: $('timeCol').scrollTop};
  roll.classList.add('panning');
  try { roll.setPointerCapture(event.pointerId); } catch { /* the button was already released */ }
});
roll.addEventListener('pointermove', event => {
  if (!pan) return;
  $('timeCol').scrollLeft = pan.left - (event.clientX - pan.x);
  $('timeCol').scrollTop = pan.top - (event.clientY - pan.y);
  syncScroll();
});
roll.addEventListener('pointerup', event => {
  if (event.button !== 1) return;
  pan = null;
  roll.classList.remove('panning');
});
roll.addEventListener('pointercancel', () => {
  pan = null;
  roll.classList.remove('panning');
});
roll.addEventListener('auxclick', event => {
  if (event.button === 1) event.preventDefault();
});
roll.addEventListener('wheel', event => {
  if (!event.ctrlKey) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
  zoomAt(event.clientX, event.clientY, factor);
}, {passive: false});
$('play').addEventListener('click', () => {
  togglePlay().catch(error => status(error.message, true));
});
$('lane').addEventListener('pointerdown', addAt);
$('ghosts').addEventListener('change', () => {
  showGhosts = $('ghosts').checked;
  render();
});
$('go').addEventListener('click', jump);
$('bar').addEventListener('keydown', event => {
  if (event.key === 'Enter') jump();
});
$('duration').addEventListener('input', () => {
  const slices = parseSlices($('duration').value);
  $('readout').textContent = slices == null ? '看不懂这个时值' : phrase(slices);
});
$('duration').addEventListener('keydown', event => {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  applySlices(parseSlices($('duration').value));
});
$('apply').addEventListener('click', () => applySlices(parseSlices($('duration').value)));
$('remove').addEventListener('click', removeSelected);
document.addEventListener('keydown', event => {
  const tag = event.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  if (event.code === 'Space') {
    if (tag === 'BUTTON' && event.target.id !== 'play') return;
    event.preventDefault();
    togglePlay().catch(error => status(error.message, true));
    return;
  }
  if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault();
    removeSelected();
    return;
  }
  if (event.key === 'ArrowUp') nudge(1, 0);
  else if (event.key === 'ArrowDown') nudge(-1, 0);
  else if (event.key === 'ArrowLeft') nudge(0, -grid);
  else if (event.key === 'ArrowRight') nudge(0, grid);
  else return;
  event.preventDefault();
});

buildChrome();
reload().then(() => {
  $('pieceName').textContent = piece.name || '微调';
  document.title = `${piece.name || '微调'} · 微调`;
  const withNotes = VOICES.find(voice => notes[voice].length);
  if (withNotes) {
    active = withNotes;
    framed = false;
    render();
  }
}).catch(error => status(error.message, true));

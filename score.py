"""Piece model, MusicXML exchange, and piano WAV rendering. Durations are in quarter-note beats."""
import array
import io
import math
import re
import wave
import zipfile
from xml.etree import ElementTree as ET

VOICES = ("S", "A", "T", "B")
DIVISI = ("S2", "A2", "T2", "B2")
SECOND_OF = {"S": "S2", "A": "A2", "T": "T2", "B": "B2"}
PIANO = "P"
PARTS = ("S", "S2", "A", "A2", "T", "T2", "B", "B2", PIANO)
VOICE_NAMES = {
    "S": "女高",
    "S2": "女高二",
    "A": "女低",
    "A2": "女低二",
    "T": "男高",
    "T2": "男高二",
    "B": "男低",
    "B2": "男低二",
    "P": "钢琴",
}


def part_name(voice, voices=None):
    """A divided part is 女高一 and 女高二. An undivided part stays 女高."""
    second = SECOND_OF.get(voice)
    if second and voices and (voices.get(second) or []):
        return f"{VOICE_NAMES[voice]}一"
    return VOICE_NAMES[voice]
DURATIONS = (0.5, 1, 1.5, 2, 3, 4)


def new_piece(piece_id, name, pages):
    return {
        "id": piece_id,
        "name": name,
        "pages": pages,
        "bpm": 72,
        "beats_per_bar": 4,
        "bars": 1,
        "voices": {voice: [] for voice in PARTS},
        "report": "",
        "job": {"status": "idle", "message": ""},
        "tenor_sounding": True,
    }


def apply_recognition(piece, voices, bpm, beats_per_bar, report):
    piece["voices"] = {voice: voices.get(voice) or [] for voice in PARTS}
    piece["bpm"] = bpm if 24 <= bpm <= 220 else 72
    piece["beats_per_bar"] = beats_per_bar if beats_per_bar in (2, 3, 4, 6) else 4
    end = 0.0
    for notes in piece["voices"].values():
        for note in notes:
            end = max(end, note["start"] + note["duration"])
    piece["bars"] = max(1, math.ceil(end / piece["beats_per_bar"] - 1e-6))
    piece["report"] = report
    piece["job"] = {"status": "done", "message": report}
    piece["tenor_sounding"] = True
    return piece


def _sounds_during(note, others):
    start = float(note["start"])
    end = start + float(note["duration"])
    for other in others:
        other_start = float(other["start"])
        other_end = other_start + float(other["duration"])
        if start < other_end - 0.02 and other_start < end - 0.02:
            return True
    return False


def sing_together(voices):
    """A divided part sings the same note until the two voices actually split."""
    for role, second in SECOND_OF.items():
        divided = [dict(note) for note in voices.get(second) or []]
        if not divided:
            continue
        merged = list(divided)
        for note in voices.get(role) or []:
            if _sounds_during(note, divided):
                continue
            merged.append({"start": note["start"], "duration": note["duration"], "pitch": note["pitch"]})
        merged.sort(key=lambda item: (item["start"], item["pitch"]))
        voices[second] = merged
    return voices


def prepare_piece(piece):
    """Old scores stored tenor at the written treble pitch, and a second voice only where it split."""
    voices = piece.setdefault("voices", {})
    for role in PARTS:
        voices.setdefault(role, [])
    if not piece.get("tenor_sounding"):
        for role in ("T", "T2"):
            for note in voices[role]:
                note["pitch"] = int(note["pitch"]) - 12
        piece["tenor_sounding"] = True
    sing_together(voices)
    return piece


def _number(value, lo, hi, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}无效")
    if not lo <= value <= hi:
        raise ValueError(f"{label}超出范围")
    return float(value)


def clean_voice(notes, beats_per_bar, bars):
    if not isinstance(notes, list):
        raise ValueError("音符列表无效")
    if len(notes) > 4000:
        raise ValueError("音符太多")
    limit = beats_per_bar * bars
    cleaned = []
    for note in notes:
        if not isinstance(note, dict):
            raise ValueError("音符无效")
        start = _number(note.get("start"), 0, limit, "起点")
        duration = _number(note.get("duration"), 0.5, 4, "时值")
        if duration not in DURATIONS:
            raise ValueError("时值只能是八分、四分、附点四分、二分、附点二分或全音符")
        pitch = note.get("pitch")
        if type(pitch) is not int or not 36 <= pitch <= 96:
            raise ValueError("音高超出合唱常用范围")
        if start + duration > limit + 1e-6:
            raise ValueError("音符超出了当前小节数")
        cleaned.append({"start": round(start, 4), "duration": duration, "pitch": pitch})
    cleaned.sort(key=lambda item: (item["start"], item["pitch"]))
    for previous, current in zip(cleaned, cleaned[1:]):
        if current["start"] < previous["start"] + previous["duration"] - 1e-6:
            raise ValueError("同一声部的音符重叠了")
    return cleaned


def update_piece(piece, bpm, beats_per_bar, bars, voice, notes):
    if voice not in VOICES:
        raise ValueError("声部无效")
    if beats_per_bar not in (2, 3, 4, 6):
        raise ValueError("拍号只支持 2/4、3/4、4/4、6/4")
    if type(bars) is not int or not 1 <= bars <= 80:
        raise ValueError("小节数应为 1–80")
    bpm_value = _number(bpm, 24, 220, "速度")
    piece["bpm"] = int(round(bpm_value))
    piece["beats_per_bar"] = beats_per_bar
    piece["bars"] = bars
    piece["voices"][voice] = clean_voice(notes, beats_per_bar, bars)
    for other in VOICES:
        if other != voice:
            piece["voices"][other] = clean_voice(piece["voices"].get(other) or [], beats_per_bar, bars)
    return piece


_TONE_CACHE = {}


def _tone(pitch, seconds, sample_rate):
    """A piano string: the wire is slightly stiff, the hammer clicks, and high partials die first."""
    count = max(1, int(seconds * sample_rate))
    key = (int(pitch), sample_rate)
    cached = _TONE_CACHE.get(key)
    if cached is not None and len(cached) >= count:
        return cached[:count]
    frequency = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
    stiffness = 0.0002 + 0.0003 * max(0.0, (64 - pitch) / 40.0)
    samples = array.array("h")
    for index in range(count):
        time = index / sample_rate
        attack = 1.0 - math.exp(-800.0 * time)
        wave_sum = 0.0
        for number in range(1, 7):
            stretched = frequency * number * math.sqrt(1.0 + stiffness * number * number)
            weight = 1.0 / (number ** 0.85)
            decay = math.exp(-time * (0.9 + 1.8 * number * number))
            wave_sum += weight * decay * math.sin(2.0 * math.pi * stretched * time)
        hammer = math.exp(-350.0 * time) * math.sin(2.0 * math.pi * (frequency * 8.0 + 1400.0) * time)
        felt = math.exp(-220.0 * time) * math.sin(2.0 * math.pi * (frequency * 13.7 + 2400.0) * time)
        body = attack * wave_sum + 0.22 * hammer + 0.05 * felt
        samples.append(int(max(-32767, min(32767, 7800 * body))))
    _TONE_CACHE[key] = samples
    return samples


def render_wav(notes, bpm, sample_rate=22050):
    if not notes:
        raise ValueError("这一声部还没有音符")
    seconds_per_beat = 60.0 / bpm
    end = max(note["start"] + note["duration"] for note in notes) * seconds_per_beat + 0.35
    total = int(end * sample_rate)
    mix = array.array("h", [0] * total)
    for note in notes:
        start = int(note["start"] * seconds_per_beat * sample_rate)
        tone = _tone(note["pitch"], max(0.05, note["duration"] * seconds_per_beat), sample_rate)
        for offset, sample in enumerate(tone):
            index = start + offset
            if index >= total:
                break
            mixed = mix[index] + sample
            mix[index] = 32767 if mixed > 32767 else -32767 if mixed < -32767 else mixed
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(mix.tobytes())
    return buffer.getvalue()


def render_zip(piece, bpm=None):
    tempo = piece["bpm"] if bpm is None else bpm
    buffer = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for voice in PARTS:
            notes = piece["voices"].get(voice) or []
            if not notes:
                continue
            archive.writestr(f"{voice}-{part_name(voice, piece['voices'])}.wav", render_wav(notes, tempo))
            written += 1
    if not written:
        raise ValueError("还没有可以导出的声部")
    return buffer.getvalue()


_DIVISION = 10000
_NOTE_UNITS = (
    (192, "whole", 0, None),
    (144, "half", 1, None),
    (96, "half", 0, None),
    (72, "quarter", 1, None),
    (64, "half", 0, (3, 2)),
    (48, "quarter", 0, None),
    (36, "eighth", 1, None),
    (32, "quarter", 0, (3, 2)),
    (24, "eighth", 0, None),
    (18, "16th", 1, None),
    (16, "eighth", 0, (3, 2)),
    (12, "16th", 0, None),
    (8, "16th", 0, (3, 2)),
    (6, "32nd", 0, None),
    (4, "32nd", 0, (3, 2)),
    (3, "64th", 0, None),
)
_UNIT_BY_TICKS = {ticks: (kind, dots, ratio) for ticks, kind, dots, ratio in _NOTE_UNITS}
_STEP_NAMES = ("C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B")
_STEP_ALTERS = (0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0)


def musicxml_name(name):
    stem = re.sub(r'[\\/:*?"<>|]+', "", (name or "score").rsplit(".", 1)[0]).strip()
    return f"{stem or 'score'}.musicxml"


def _ticks(beats):
    return int(round(float(beats) * _DIVISION))


def _overlaps(earlier, later):
    if earlier["start"] == later["start"] and earlier["duration"] == later["duration"]:
        return False
    return earlier["start"] < later["start"] + later["duration"] and later["start"] < earlier["start"] + earlier["duration"]


def _layers(notes):
    layers = []
    for note in sorted(notes, key=lambda item: (item["start"], -item["pitch"])):
        for layer in layers:
            if any(_overlaps(note, other) for other in layer):
                continue
            layer.append(note)
            break
        else:
            layers.append([note])
    return layers


def _atoms(notes, bar_ticks):
    """Split a note at the barline and tie the pieces, so the bar itself stays put."""
    layers = []
    for layer_notes in _layers(notes):
        atoms = []
        for note in layer_notes:
            cursor = note["start"]
            end = note["start"] + note["duration"]
            pieces = []
            while cursor < end:
                room = bar_ticks if cursor % bar_ticks == 0 else bar_ticks - (cursor % bar_ticks)
                take = min(end - cursor, room)
                pieces.append({"start": cursor, "duration": take, "pitch": note["pitch"], "tie_start": False, "tie_stop": False})
                cursor += take
            for index, piece in enumerate(pieces):
                piece["tie_start"] = index < len(pieces) - 1
                piece["tie_stop"] = index > 0
            atoms.extend(pieces)
        layers.append(atoms)
    return layers


def _pitch_element(parent, pitch):
    element = ET.SubElement(parent, "pitch")
    ET.SubElement(element, "step").text = _STEP_NAMES[pitch % 12]
    alter = _STEP_ALTERS[pitch % 12]
    if alter:
        ET.SubElement(element, "alter").text = str(alter)
    ET.SubElement(element, "octave").text = str(pitch // 12 - 1)


def _add_note(parent, atom, voice, staff, chord):
    note = ET.SubElement(parent, "note")
    if chord:
        ET.SubElement(note, "chord")
    _pitch_element(note, atom["pitch"])
    ET.SubElement(note, "duration").text = str(atom["duration"])
    for kind in ("start", "stop"):
        if atom[f"tie_{kind}"]:
            ET.SubElement(note, "tie", {"type": kind})
    ET.SubElement(note, "voice").text = str(voice)
    appearance = _UNIT_BY_TICKS.get(atom["duration"])
    if appearance is not None and appearance[2] is None:
        kind, dots, _ratio = appearance
        ET.SubElement(note, "type").text = kind
        for _ in range(dots):
            ET.SubElement(note, "dot")
    if staff:
        ET.SubElement(note, "staff").text = str(staff)
    if atom["tie_start"] or atom["tie_stop"]:
        notations = ET.SubElement(note, "notations")
        for kind in ("start", "stop"):
            if atom[f"tie_{kind}"]:
                ET.SubElement(notations, "tied", {"type": kind})
    return note


def _add_rest(parent, ticks, voice, staff):
    note = ET.SubElement(parent, "note")
    ET.SubElement(note, "rest")
    ET.SubElement(note, "duration").text = str(ticks)
    ET.SubElement(note, "voice").text = str(voice)
    if staff:
        ET.SubElement(note, "staff").text = str(staff)


def _write_voice(measure, atoms, voice, staff, bar_start, bar_ticks):
    cursor = bar_start
    groups = {}
    for atom in atoms:
        if not bar_start <= atom["start"] < bar_start + bar_ticks:
            continue
        groups.setdefault(atom["start"], []).append(atom)
    for start in sorted(groups):
        if start > cursor:
            _add_rest(measure, start - cursor, voice, staff)
        for index, atom in enumerate(sorted(groups[start], key=lambda item: -item["pitch"])):
            _add_note(measure, atom, voice, staff, chord=index > 0)
        cursor = start + max(atom["duration"] for atom in groups[start])
    if cursor < bar_start + bar_ticks:
        _add_rest(measure, bar_start + bar_ticks - cursor, voice, staff)
        cursor = bar_start + bar_ticks
    return cursor - bar_start


def _part_notes(piece, role):
    notes = []
    for note in piece["voices"].get(role) or []:
        duration = _ticks(note["duration"])
        if duration <= 0:
            continue
        notes.append({"start": _ticks(note["start"]), "duration": duration, "pitch": int(note["pitch"])})
    return notes


def write_musicxml(piece):
    """Write the sounding parts as one editable score. Playback of a re-import follows this file."""
    if not any(piece["voices"].get(role) for role in PARTS):
        raise ValueError("还没有可以导出的声部")
    beats = piece["beats_per_bar"] if piece.get("beats_per_bar") in (2, 3, 4, 6) else 4
    bar_ticks = beats * _DIVISION
    end = 0
    for role in PARTS:
        for note in _part_notes(piece, role):
            end = max(end, note["start"] + note["duration"])
    bars = max(1, math.ceil(end / bar_ticks))
    root = ET.Element("score-partwise", {"version": "3.1"})
    work = ET.SubElement(root, "work")
    ET.SubElement(work, "work-title").text = musicxml_name(piece.get("name") or "score").removesuffix(".musicxml")
    listing = ET.SubElement(root, "part-list")
    roles = [role for role in PARTS if role not in DIVISI or piece["voices"].get(role)]
    identities = [(role, f"P{index}", part_name(role, piece["voices"])) for index, role in enumerate(roles, start=1)]
    for _role, part_id, name in identities:
        score_part = ET.SubElement(listing, "score-part", {"id": part_id})
        ET.SubElement(score_part, "part-name").text = name
    clefs = {"S": "G", "S2": "G", "A": "G", "A2": "G", "T": "G", "T2": "G", "B": "F", "B2": "F"}
    for role, part_id, _name in identities:
        part = ET.SubElement(root, "part", {"id": part_id})
        notes = _part_notes(piece, role)
        if role == "P":
            staves = {
                1: [note for note in notes if note["pitch"] >= 60],
                2: [note for note in notes if note["pitch"] < 60],
            }
        else:
            staves = {None: notes}
        voiced = {staff: _atoms(staff_notes, bar_ticks) for staff, staff_notes in staves.items()}
        for bar_index in range(bars):
            measure = ET.SubElement(part, "measure", {"number": str(bar_index + 1)})
            if bar_index == 0:
                attributes = ET.SubElement(measure, "attributes")
                ET.SubElement(attributes, "divisions").text = str(_DIVISION)
                time = ET.SubElement(attributes, "time")
                ET.SubElement(time, "beats").text = str(beats)
                ET.SubElement(time, "beat-type").text = "4"
                if role == "P":
                    ET.SubElement(attributes, "staves").text = "2"
                    for number, sign, line in ((1, "G", "2"), (2, "F", "4")):
                        clef = ET.SubElement(attributes, "clef", {"number": str(number)})
                        ET.SubElement(clef, "sign").text = sign
                        ET.SubElement(clef, "line").text = line
                else:
                    clef = ET.SubElement(attributes, "clef")
                    ET.SubElement(clef, "sign").text = clefs[role]
                    ET.SubElement(clef, "line").text = "2" if clefs[role] == "G" else "4"
                    if role in ("T", "T2"):
                        ET.SubElement(clef, "clef-octave-change").text = "-1"
                if role == "S":
                    direction = ET.SubElement(measure, "direction")
                    ET.SubElement(direction, "sound", {"tempo": str(int(piece.get("bpm") or 72))})
            bar_start = bar_index * bar_ticks
            previous = 0
            first_voice = True
            for staff, layers in voiced.items():
                targets = list(enumerate(layers, start=1)) or [(1, [])]
                for index, atoms in targets:
                    if not first_voice:
                        backup = ET.SubElement(measure, "backup")
                        ET.SubElement(backup, "duration").text = str(previous)
                    previous = _write_voice(measure, atoms, index, staff, bar_start, bar_ticks)
                    first_voice = False
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")

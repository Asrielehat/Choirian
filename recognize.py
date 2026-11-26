"""Turn a scan into SATB notes.

homr (https://github.com/liebharc/homr) reads the page images. Audiveris is the
fallback, and when it exports several movements only the substantial one is kept.
"""
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from score import PARTS, VOICES, part_name, sing_together

STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ROLE_WORDS = {
    "S": ("soprano", "sopran", "女高"),
    "A": ("alto", "contralto", "女低"),
    "T": ("tenor", "男高"),
    "B": ("bass", "男低"),
}
ACCOMPANIMENT = ("piano", "keyboard", "accomp", "pno", "钢琴", "伴奏")
_SECOND_VOICE = re.compile(r"(?<![a-z0-9])(?:ii|2nd|second|2)(?![a-z0-9])|二")
OTHER_PARTS = (
    "solo", "独唱", "独奏", "sax", "萨克斯", "violin", "小提琴", "cello", "大提琴",
    "flute", "长笛", "clarinet", "单簧管", "trumpet", "小号", "horn", "圆号",
    "oboe", "双簧管", "viola", "中提琴", "organ", "管风琴", "guitar", "吉他",
)


def audiveris_path():
    candidates = [
        os.environ.get("AUDIVERIS_CMD", ""),
        str(Path(__file__).resolve().parent / "tools" / "Audiveris" / "Audiveris.exe"),
        str(Path(__file__).resolve().parent.parent / "ChoirLab" / "tools" / "Audiveris" / "Audiveris.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    found = shutil.which("audiveris")
    return Path(found) if found else None


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _read_xml(path):
    raw = path.read_bytes()
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path) as archive:
            rootfile = next(name for name in archive.namelist() if name.lower().endswith((".xml", ".musicxml")) and not name.startswith("META-INF/"))
            raw = archive.read(rootfile)
    root = ET.fromstring(raw)
    for element in root.iter():
        element.tag = _local(element.tag)
    if root.tag != "score-partwise":
        raise ValueError("识谱结果不是分声部乐谱")
    return root


def _curve_marks(item):
    """Ties and same-pitch slurs hold a note. homr often writes the tie as a slur."""
    tie_start = tie_stop = False
    slur_start, slur_stop = [], []
    for element in item.iter():
        kind = element.get("type")
        if element.tag in ("tie", "tied"):
            tie_start = tie_start or kind == "start"
            tie_stop = tie_stop or kind == "stop"
        elif element.tag == "slur":
            number = element.get("number") or "1"
            if kind == "start":
                slur_start.append(number)
            elif kind == "stop":
                slur_stop.append(number)
    return tie_start, tie_stop, slur_start, slur_stop


def _midi(pitch):
    step = pitch.findtext("step", "C")
    octave = int(pitch.findtext("octave", "4"))
    alter = int(float(pitch.findtext("alter", "0") or 0))
    return (octave + 1) * 12 + STEPS[step] + alter


def _system_groups(part):
    groups = []
    current = []
    for measure in part.findall("measure"):
        printed = measure.find("print")
        if current and printed is not None and printed.get("new-system") == "yes":
            groups.append(current)
            current = []
        current.append(measure)
    if current:
        groups.append(current)
    return groups


def _group_clef(measures, previous):
    for measure in measures:
        sign = measure.findtext(".//clef/sign")
        if sign:
            return sign
    return previous


def _clef_shift(clef):
    raw = clef.findtext("clef-octave-change")
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _group_octave(measures, previous):
    for measure in measures:
        clef = measure.find(".//clef")
        if clef is not None and clef.findtext("sign"):
            return _clef_shift(clef)
    return previous


# A bracketed choir that homr wrote as one part: four staves, two staves, or those plus piano.
CHOIR_STACKS = {("G", "F"), ("G", "G", "G", "F"), ("G", "G", "G", "F", "G", "F")}


def _stack_period(clefs):
    """Unstack only a serialized choir. A piano's own clef changes stay in order."""
    for period in (2, 3, 4, 5, 6):
        if len(clefs) < period or len(clefs) % period:
            continue
        pattern = tuple(clefs[:period])
        if pattern not in CHOIR_STACKS:
            continue
        if all(clefs[index] == pattern[index % period] for index in range(len(clefs))):
            return period
    return 0


def _first_explicit_clef(groups):
    for group in groups:
        for measure in group:
            sign = measure.findtext(".//clef/sign")
            if sign:
                return sign
    return "G"


def _first_octave(groups):
    for group in groups:
        for measure in group:
            clef = measure.find(".//clef")
            if clef is not None and clef.findtext("sign"):
                return _clef_shift(clef)
    return 0


def _changes_to_bass(measures, opening):
    """A staff that starts in treble and later switches to bass is not only a singer."""
    current = opening or "G"
    for measure in measures:
        sign = measure.findtext(".//clef/sign")
        if not sign:
            continue
        if current != "F" and sign == "F":
            return True
        current = sign
    return False


def _already_together(part):
    """A grand staff already plays its two hands together. Later systems follow it."""
    for measure in part.findall("measure"):
        staves = measure.findtext("attributes/staves")
        if staves and staves.isdigit() and int(staves) >= 2:
            return True
    return False


def _measure_has_pitch(measure):
    return any(note.find("pitch") is not None for note in measure.findall("note"))


def _every_slot_has_a_note(groups, period):
    """A rest written where a staff is missing is not a second singer."""
    rows = len(groups) // period
    for slot in range(period):
        if not any(_measure_has_pitch(measure) for row in range(rows) for measure in groups[row * period + slot]):
            return False
    return True


def _quarter_meter(time_element):
    """Bar length in quarter notes. 12/8 and 6/4 are both 6; a one-off 5/4 is not a real change."""
    raw = float(time_element.findtext("beats")) * 4 / float(time_element.findtext("beat-type") or 4)
    snapped = int(round(raw))
    if abs(raw - snapped) < 0.05 and snapped in (2, 3, 4, 6):
        return float(snapped)
    return None


def _measure_state(part, division=1.0, beats=4.0):
    """Division and meter in effect at the start of each measure.

    A time change printed on one staff applies to every staff after it.
    The values returned last are the meter the next page should start from.
    """
    state = {}
    for measure in part.findall("measure"):
        state[id(measure)] = (division, beats)
        attributes = measure.find("attributes")
        if attributes is not None and attributes.findtext("divisions"):
            division = float(attributes.findtext("divisions"))
        time = measure.find("attributes/time")
        if time is not None and time.findtext("beats"):
            meter = _quarter_meter(time)
            if meter is not None:
                beats = meter
    return state, division, beats


def _expand_part(part, division=1.0, beats=4.0, name=""):
    groups = _system_groups(part)
    state, end_division, end_beats = _measure_state(part, division, beats)
    if not groups:
        return [], state, end_division, end_beats
    clefs = []
    octaves = []
    previous = "G"
    previous_octave = 0
    for group in groups:
        previous = _group_clef(group, previous)
        previous_octave = _group_octave(group, previous_octave)
        clefs.append(previous)
        octaves.append(previous_octave)
    period = 0 if _is_accompaniment(name) or _already_together(part) else _stack_period(clefs)
    if period and not _every_slot_has_a_note(groups, period):
        period = 0

    def pack(groups_for_voice, hint, clef, octave):
        measures = [measure for group in groups_for_voice for measure in group]
        return measures, hint, clef, octave

    if not period:
        return [pack(groups, None, _first_explicit_clef(groups), _first_octave(groups))], state, end_division, end_beats
    hints = [None] * period
    if period == 6 and clefs[:6] == ["G", "G", "G", "F", "G", "F"]:
        hints = [None, None, None, None, "Piano", "Piano"]
    rows = len(groups) // period
    packs = [
        pack([groups[row * period + voice] for row in range(rows)], hints[voice], clefs[voice], octaves[voice])
        for voice in range(period)
    ]
    return packs, state, end_division, end_beats


def _tuplet_ratio(item):
    mod = item.find("time-modification")
    if mod is None:
        return None
    try:
        actual = int(mod.findtext("actual-notes") or 0)
        normal = int(mod.findtext("normal-notes") or 0)
    except ValueError:
        return None
    if actual < 2 or normal < 1 or actual == normal:
        return None
    return actual, normal


def _tuplet_bracket(item):
    kinds = {
        tuplet.get("type")
        for tuplet in item.findall("./notations/tuplet")
        if tuplet.get("type") in ("start", "stop")
    }
    if kinds == {"start", "stop"}:
        return "both"
    if "start" in kinds:
        return "start"
    if "stop" in kinds:
        return "stop"
    return ""


def _mark_written_tuplets(slots):
    """A printed bracket whose notes already share one length is the triplet. Leave it alone."""
    start = None
    for index, slot in enumerate(slots):
        bracket = slot.get("bracket")
        if bracket in ("start", "both"):
            start = index
        if bracket in ("stop", "both") and start is not None:
            span = slots[start:index + 1]
            durations = [item["duration"] for item in span]
            if durations and max(durations) - min(durations) <= 0.05:
                for item in span:
                    item["stable"] = True
            start = None


def _repair_tuplets(slots):
    """A 3 printed on one note can leave the first of three quarters at full length.

    Shorten that first note only when it is a quarter and a single marked note follows it.
    An eighth before a triplet eighth keeps its written length: 0.5 is also 1.5 times a
    triplet eighth, and shortening it makes the triplet rush in. Two or more notes already
    marked 3:2 at the same length are the triplet, and so is a bracket whose notes already agree.
    """
    _mark_written_tuplets(slots)
    index = 1
    while index < len(slots):
        slot = slots[index]
        unit = slot["duration"]
        if slot.get("stable") or slot["ratio"] != (3, 2) or unit <= 0:
            index += 1
            continue
        run = 1
        while index + run < len(slots) and _same_marked_triplet(slots[index + run], unit):
            run += 1
        if run >= 2:
            index += run
            continue
        previous = slots[index - 1]
        same_kind = not slot.get("kind") or not previous.get("kind") or slot["kind"] == previous["kind"]
        if (
            previous.get("stable")
            or previous["ratio"] is not None
            or previous.get("kind") != "quarter"
            or not same_kind
            or abs(previous["duration"] - unit * 1.5) > 0.05
        ):
            index += 1
            continue
        group = [index - 1, index]
        if index + 1 < len(slots):
            nxt = slots[index + 1]
            if nxt["ratio"] == (3, 2) and not nxt.get("stable") and (
                abs(nxt["duration"] - unit) <= 0.05 or abs(nxt["duration"] - unit / 2) <= 0.05
            ):
                group.append(index + 1)
        for chosen in group:
            slots[chosen]["duration"] = unit
        index = group[-1] + 1


def _same_marked_triplet(slot, unit):
    return (
        not slot.get("stable")
        and slot["ratio"] == (3, 2)
        and abs(slot["duration"] - unit) <= 0.05
    )


def _measures(measure_list, state=None):
    """Place notes by the notes and rests actually written.

    A time signature does not stretch a bar. A 4/4 bar that still carries a 12/8
    signature stays four quarters long when its notes and rests already add up to four.
    """
    measures = []
    division = 1.0
    state = state or {}
    for measure in measure_list:
        if id(measure) in state:
            division = state[id(measure)][0]
        attributes = measure.find("attributes")
        if attributes is not None and attributes.findtext("divisions"):
            division = float(attributes.findtext("divisions"))
        cursor = 0.0
        furthest = 0.0
        events = []
        run = []

        def flush():
            nonlocal cursor, furthest
            if not run:
                return
            _repair_tuplets(run)
            for slot in run:
                start = max(0.0, cursor)
                for event in slot["events"]:
                    event["start"] = round(start, 4)
                    event["duration"] = round(slot["duration"], 4)
                    events.append(event)
                cursor = start + slot["duration"]
                furthest = max(furthest, cursor)
            run.clear()

        for item in measure:
            if item.tag in ("backup", "forward"):
                flush()
                delta = float(item.findtext("duration", "0")) / division
                cursor += delta if item.tag == "forward" else -delta
                furthest = max(furthest, cursor)
                continue
            if item.tag != "note" or item.find("grace") is not None:
                continue
            duration = float(item.findtext("duration", "0")) / division
            if item.find("chord") is not None:
                if run:
                    for event in _note_events(item, duration):
                        event["chord"] = True
                        run[-1]["events"].append(event)
                continue
            run.append({
                "duration": duration,
                "ratio": _tuplet_ratio(item),
                "kind": item.findtext("type") or "",
                "bracket": _tuplet_bracket(item),
                "events": _note_events(item, duration),
            })
        flush()
        measures.append((events, furthest))
    return measures


def _note_events(item, duration):
    pitch = item.find("pitch")
    if pitch is None or duration <= 0:
        return []
    midi = _midi(pitch)
    if not 0 <= midi <= 127:
        return []
    tie_start, tie_stop, slur_start, slur_stop = _curve_marks(item)
    return [{
        "start": 0.0,
        "duration": duration,
        "pitch": midi,
        "staff": item.findtext("staff", "1"),
        "voice": item.findtext("voice") or "1",
        "tie_start": tie_start,
        "tie_stop": tie_stop,
        "slur_start": slur_start,
        "slur_stop": slur_stop,
        "chord": False,
    }]


def _merge_holds(notes):
    """A tie, or a slur between the same pitch, lengthens one note. The second attack is not sung."""
    if not notes:
        return []
    notes = sorted(notes, key=lambda note: (note["start"], note["pitch"]))
    parent = list(range(len(notes)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def abuts(earlier, later):
        return notes[later]["start"] > notes[earlier]["start"] + 0.02 and abs(
            notes[earlier]["start"] + notes[earlier]["duration"] - notes[later]["start"]
        ) < 0.02

    def shares_the_attack(index):
        start = notes[index]["start"]
        pitch = notes[index]["pitch"]
        return any(
            other != index
            and notes[other]["pitch"] != pitch
            and abs(notes[other]["start"] - start) < 0.02
            for other in range(len(notes))
        )

    def previous_same(later, limit):
        if shares_the_attack(later):
            return None
        pitch = notes[later]["pitch"]
        for earlier in range(later - 1, limit - 1, -1):
            if notes[earlier]["pitch"] != pitch:
                continue
            if abuts(earlier, later):
                return earlier
            if notes[earlier]["start"] < notes[later]["start"] - 0.02:
                return None
        return None

    def link(earlier, later):
        if earlier is None:
            return
        head = find(earlier)
        if head == find(later):
            return
        notes[head]["duration"] = round(notes[later]["start"] + notes[later]["duration"] - notes[head]["start"], 4)
        parent[later] = head

    claimed = set()
    for later, note in enumerate(notes):
        earlier = None
        pitch = note["pitch"]
        for candidate in range(later - 1, -1, -1):
            if candidate in claimed or notes[candidate]["pitch"] != pitch:
                continue
            if abuts(candidate, later):
                earlier = candidate
                break
            if notes[candidate]["start"] < note["start"] - 0.02:
                break
        if shares_the_attack(later):
            continue
        if earlier is not None and (note.get("tie_stop") or notes[earlier].get("tie_start")):
            link(earlier, later)
            claimed.add(earlier)

    open_slur = {}
    for index, note in enumerate(notes):
        for number in note.get("slur_stop") or []:
            start = open_slur.pop(number, None)
            if start is None:
                continue
            for later in range(start + 1, index + 1):
                link(previous_same(later, start), later)
        for number in note.get("slur_start") or []:
            open_slur[number] = index

    return [
        {
            "start": note["start"],
            "duration": note["duration"],
            "pitch": note["pitch"],
            "chord": bool(note.get("chord")),
        }
        for index, note in enumerate(notes)
        if find(index) == index
    ]


def _absolute(measures, starts):
    grouped = {}
    for index, (events, _) in enumerate(measures):
        for event in events:
            grouped.setdefault(event["staff"], {}).setdefault(event["voice"], []).append({
                "start": round(starts[index] + event["start"], 4),
                "duration": event["duration"],
                "pitch": event["pitch"],
                "tie_start": event["tie_start"],
                "tie_stop": event["tie_stop"],
                "slur_start": event["slur_start"],
                "slur_stop": event["slur_stop"],
                "chord": bool(event.get("chord")),
            })
    return grouped


def _staff_lines(grouped, label, clef, octave_shift=0, bass_clef_change=False):
    """Two sung voices on one staff stay apart. S.A. shares the treble, T.B. shares the bass.

    The second voice keeps its own layer, so a divided part can still be heard.
    """
    lines = []
    piano = _is_accompaniment(label)
    for staff in sorted(grouped, key=lambda value: int(value) if str(value).isdigit() else 0):
        layers = grouped[staff]
        ranked = sorted(layers, key=lambda voice: (-len(layers[voice]), int(voice) if str(voice).isdigit() else 0))
        main = ranked[0]
        selected = [main]
        extra = []
        for voice in ranked[1:]:
            notes = layers[voice]
            if not piano and len(notes) >= 2 and len(notes) * 5 >= len(layers[main]):
                selected.append(voice)
            else:
                extra.extend(_merge_holds(notes))
        ordered = sorted(selected, key=lambda value: int(value) if str(value).isdigit() else 0)
        for index, voice in enumerate(ordered):
            notes = _merge_holds(list(layers[voice]))
            if voice == main:
                notes.extend(extra)
            if not notes:
                continue
            if piano or len(selected) > 1:
                upper, lower = notes, []
            else:
                upper, lower = _split_sung(notes)
            lines.append({
                "name": label,
                "clef": clef,
                "octave_shift": octave_shift,
                "notes": upper,
                "layer": index if len(selected) > 1 else 0,
                "bass_clef_change": bass_clef_change,
            })
            if lower:
                lines.append({
                    "name": label,
                    "clef": clef,
                    "octave_shift": octave_shift,
                    "notes": lower,
                    "layer": 1,
                    "bass_clef_change": bass_clef_change,
                })
    return lines


def _lead(notes):
    """The note on the stem stays with the first voice. A chord that has no stem note keeps the top one."""
    stems = [note for note in notes if not note.get("chord")]
    if len(stems) == 1:
        return stems[0]
    return max(notes, key=lambda note: note["pitch"])


def _split_sung(notes):
    """Enough stacked noteheads on one staff are the two singers there.

    The note on the stem stays on the upper line. Every other pitch stays on the
    lower line, so a three-note stack keeps the middle pitch with the top when the
    top is the stem. One stack, or a rare one, stays together and is divided later.
    """
    by_start = {}
    for note in notes:
        by_start.setdefault(note["start"], []).append(note)
    chords = [
        group for group in by_start.values()
        if len({item["pitch"] for item in group}) >= 2
    ]
    if len(chords) < 2 or len(chords) * 5 < len(by_start):
        return notes, []
    upper, lower = [], []
    for start in sorted(by_start):
        ordered = sorted(by_start[start], key=lambda note: note["pitch"])
        unique = []
        for note in ordered:
            if unique and unique[-1]["pitch"] == note["pitch"]:
                continue
            unique.append(note)
        lead = _lead(unique)
        upper.append({key: lead[key] for key in ("start", "duration", "pitch")})
        for note in unique:
            if note is lead:
                continue
            lower.append({key: note[key] for key in ("start", "duration", "pitch")})
    return upper, lower


def _named_divisi(name):
    """Soprano II and 女高二 are the second voice of that part."""
    value = name.lower()
    role = _role(value)
    if role is None:
        return None
    rest = value
    for word in ROLE_WORDS[role]:
        if word in rest:
            rest = rest.replace(word, " ", 1)
            break
    if _SECOND_VOICE.search(rest):
        return f"{role}2"
    return None


def _is_other(name):
    value = name.lower()
    return any(word in value for word in OTHER_PARTS)


def _as_partwise(root):
    """MuseScore can export either partwise or timewise MusicXML. The reader walks parts."""
    if root.tag == "score-partwise":
        return root
    if root.tag != "score-timewise":
        raise ValueError("请用 MusicXML 导出，不要用 MIDI 或其他格式")
    partwise = ET.Element("score-partwise")
    listing = root.find("part-list")
    if listing is not None:
        partwise.append(listing)
    parts = {}
    for measure in root.findall("measure"):
        for part in measure.findall("part"):
            copied = ET.Element("measure", {"number": measure.get("number") or "1"})
            for child in list(part):
                copied.append(child)
            parts.setdefault(part.get("id"), []).append(copied)
    for part_id, measures in parts.items():
        element = ET.Element("part", {"id": part_id or "P1"})
        element.extend(measures)
        partwise.append(element)
    return partwise


def read_score(path, division=1.0, beats=4.0):
    root = _as_partwise(_read_xml(path))
    names = {item.get("id"): item.findtext("part-name", item.get("id")) for item in root.findall("./part-list/score-part")}
    tempos = [float(item.get("tempo")) for item in root.findall(".//sound") if item.get("tempo")]
    bpm = int(tempos[0]) if tempos and 24 <= tempos[0] <= 220 else 72
    parsed = []
    ending = (division, beats)
    for part in root.findall("part"):
        packs, state, end_division, end_beats = _expand_part(part, division, beats, names.get(part.get("id"), ""))
        if (end_division, end_beats) != (division, beats):
            ending = (end_division, end_beats)
        label = names.get(part.get("id"), part.get("id"))
        for measures, hint, clef, octave in packs:
            parsed.append((hint or label, clef, _measures(measures, state), octave, _changes_to_bass(measures, clef)))
    count = max((len(measures) for _, _, measures, _octave, _changed in parsed), default=0)
    starts = []
    cursor = 0.0
    for index in range(count):
        starts.append(cursor)
        entries = [
            (measures[index][1], bool(measures[index][0]))
            for _, _, measures, _octave, _changed in parsed
            if index < len(measures)
        ]
        cursor += _agreed_length(entries) if entries else beats
    lines = []
    for label, clef, measures, octave, changed in parsed:
        lines.extend(_staff_lines(_absolute(measures, starts), label, clef, octave, changed))
    meter_beats = int(round(ending[1]))
    return {
        "bpm": bpm,
        "beats_per_bar": meter_beats if meter_beats in (2, 3, 4, 6) else 4,
        "lines": lines,
        "division": ending[0],
        "meter": ending[1],
        "timeline": cursor,
        "tempo_written": bool(tempos),
    }


def _agreed_length(entries):
    """One long staff must not open a rest before the next bar.

    A staff that holds only rests does not vote. The bar follows the length
    the pitched staves already fill, and when they split, the shorter one
    wins so the others do not sit in silence.
    """
    pitched = [length for length, has_pitch in entries if has_pitch and length > 0.05]
    pool = pitched or [length for length, _has_pitch in entries if length > 0.05]
    if not pool:
        return 0.0
    pool.sort()
    middle = len(pool) // 2
    if len(pool) % 2:
        return pool[middle]
    return min(pool[middle - 1], pool[middle])


def _role(name):
    value = name.lower()
    for role, words in ROLE_WORDS.items():
        if any(word in value for word in words):
            return role
    return None


def _is_accompaniment(name):
    value = name.lower()
    return any(word in value for word in ACCOMPANIMENT)


def _singles(notes):
    """One note per beat, the higher one, so a written F5 is what lines up with the others."""
    singles = []
    for note in sorted(notes, key=lambda item: (item["start"], -item["pitch"])):
        if singles and abs(note["start"] - singles[-1]["start"]) < 0.02:
            continue
        singles.append(note)
    return singles


def _shares_the_beat(note, notes, partner):
    """A beat homr wrote as two pitches stays put, even after the lower one is the second voice."""
    pool = list(notes) + list(partner or [])
    return any(
        other is not note
        and other["pitch"] != note["pitch"]
        and abs(other["start"] - note["start"]) < 0.02
        for other in pool
    )


def _motives(notes, size=3):
    singles = _singles(notes)
    groups = []
    for index in range(len(singles) - size + 1):
        group = singles[index:index + size]
        if any(group[i + 1]["start"] < group[i]["start"] + group[i]["duration"] - 0.02 for i in range(size - 1)):
            continue
        groups.append(group)
    return groups


def _triplet_lengths(group):
    """Eighth-note and quarter-note triplets already occupy their own time. Do not slide them."""
    def tuplet_length(duration):
        return abs(duration - 1 / 3) <= 0.05 or abs(duration - 2 / 3) <= 0.05

    return all(tuplet_length(note["duration"]) for note in group)


def _wait_with_the_others(voices):
    """One voice sang the same three notes early and then rested. That is a rushed reading.

    Hold the previous note and start the three notes with the other voices.
    """
    repairs = []
    used = set()
    for role in VOICES:
        notes = voices[role]
        for group in _motives(notes):
            if _triplet_lengths(group) or any(id(note) in used for note in group):
                continue
            if any(_shares_the_beat(note, notes, voices.get(f"{role}2")) for note in group):
                continue
            classes = [note["pitch"] % 12 for note in group]
            start = group[0]["start"]
            finish = group[-1]["start"] + group[-1]["duration"]
            best = None
            for other in VOICES:
                if other == role:
                    continue
                for candidate in _motives(voices[other]):
                    if [note["pitch"] % 12 for note in candidate] != classes:
                        continue
                    delay = candidate[0]["start"] - start
                    if not 0.4 <= delay <= 2.0:
                        continue
                    other_end = candidate[-1]["start"] + candidate[-1]["duration"]
                    if any(finish - 0.02 <= note["start"] < other_end - 0.02 for note in notes if note not in group):
                        continue
                    if best is None or delay < best[0]:
                        best = (delay, candidate)
            if best is None:
                continue
            repairs.append((role, notes, group, best[1]))
            used.update(id(note) for note in group)
    for role, notes, group, candidate in repairs:
        start = group[0]["start"]
        previous = None
        for note in notes:
            if note in group or note["start"] > start:
                continue
            if abs(note["start"] + note["duration"] - start) <= 0.05 and (previous is None or note["start"] > previous["start"]):
                previous = note
        for note, target in zip(group, candidate):
            old_start = note["start"]
            new_start = round(target["start"], 4)
            note["start"] = new_start
            note["duration"] = round(target["duration"], 4)
            for sibling in notes:
                if sibling is not note and abs(sibling["start"] - old_start) <= 0.02:
                    sibling["start"] = new_start
            for sibling in voices.get(f"{role}2", []):
                if abs(sibling["start"] - old_start) <= 0.02:
                    sibling["start"] = new_start
        if previous is not None:
            previous["duration"] = round(group[0]["start"] - previous["start"], 4)
    for role in PARTS:
        voices[role].sort(key=lambda note: (note["start"], note["pitch"]))


def _sounding(line, role):
    """Tenor written in treble sounds an octave lower. A clef octave mark is already sounding."""
    shift = -12 if role in ("T", "T2") and line.get("clef") == "G" and not line.get("octave_shift") else 0
    return [
        {"start": note["start"], "duration": note["duration"], "pitch": int(note["pitch"]) + shift, "chord": bool(note.get("chord"))}
        for note in line["notes"]
    ]


def _split_divisi(voices):
    """Two pitches at one moment inside a part are its first and second voices.

    The note on the stem stays. Every other pitch goes to the second voice and still
    sounds. A chord with no stem note keeps the top one. The same pitch written twice stays one note.
    """
    for role in VOICES:
        grouped = {}
        for note in voices[role]:
            grouped.setdefault(note["start"], []).append(note)
        kept = []
        extra = []
        for start in sorted(grouped):
            ordered = sorted(grouped[start], key=lambda note: note["pitch"])
            unique = []
            for note in ordered:
                if unique and unique[-1]["pitch"] == note["pitch"]:
                    continue
                unique.append(note)
            lead = _lead(unique)
            kept.append(lead)
            extra.extend(note for note in unique if note is not lead)
        voices[role] = kept
        if extra:
            voices[f"{role}2"].extend(extra)
            voices[f"{role}2"].sort(key=lambda note: (note["start"], note["pitch"]))


def _group_count(group):
    return sum(len(line.get("notes") or []) for line in group)


def _keyboard_group(groups):
    """A staff much busier than the others, and either very wide or switching into bass, is the piano."""
    if len(groups) < 2:
        return None
    counts = [_group_count(group) for group in groups]
    chosen = None
    for index, group in enumerate(groups):
        others = sorted(count for other, count in enumerate(counts) if other != index)
        median = others[len(others) // 2]
        if counts[index] < 3 * max(median, 1):
            continue
        pitches = [int(note["pitch"]) for line in group for note in (line.get("notes") or []) if "pitch" in note]
        wide = bool(pitches) and min(pitches) < 48 and max(pitches) >= 60
        changed = any(line.get("bass_clef_change") for line in group)
        if not (wide or changed):
            continue
        if chosen is None or counts[index] > counts[chosen]:
            chosen = index
    return chosen


def _place_group(voices, group, role):
    """One staff group is one singer. A second layer stays with that singer, not the next part."""
    primary = [line for line in group if line.get("layer", 0) == 0] or group[:1]
    for line in primary:
        voices[role] = list(voices[role]) + _sounding(line, role)
    second = f"{role}2"
    for line in group:
        if line in primary:
            continue
        voices[second] = list(voices[second]) + _sounding(line, second)


def assign_voices(lines, trust=False):
    other = [line for line in lines if _is_other(line["name"])]
    piano = [line for line in lines if line not in other and _is_accompaniment(line["name"])]
    vocal = [line for line in lines if line not in other and line not in piano]
    voices = {role: [] for role in PARTS}
    claimed = set()
    pending = []
    for line in vocal:
        named = _named_divisi(line["name"])
        if named:
            voices[named].extend(_sounding(line, named))
            continue
        role = _role(line["name"])
        if role and line.get("layer", 0) >= 1:
            voices[f"{role}2"].extend(_sounding(line, f"{role}2"))
            continue
        if role and role not in claimed:
            voices[role] = _sounding(line, role)
            claimed.add(role)
            continue
        pending.append(line)
    kept = []
    outside = 0
    seen_bass = False
    for line in pending:
        if line.get("clef") == "F":
            seen_bass = True
            kept.append(line)
        elif seen_bass and line.get("clef") == "G":
            outside += 1
        else:
            kept.append(line)
    pending = kept
    groups = []
    for line in pending:
        previous = groups[-1] if groups else None
        if (
            previous
            and line.get("layer", 0) >= 1
            and previous[0].get("layer", 0) == 0
            and previous[-1].get("clef") == line.get("clef")
            and previous[-1].get("name") == line.get("name")
        ):
            previous.append(line)
        else:
            groups.append([line])
    if not claimed and len(groups) == 2 and groups[0][0].get("clef") == "G" and groups[1][0].get("clef") == "F":
        role_plan = [("S", "A"), ("T", "B")]
    elif not claimed and [group[0].get("clef") for group in groups] == ["G", "G", "F"]:
        role_plan = [("S",), ("A",), ("T", "B")]
    elif not claimed and [group[0].get("clef") for group in groups] == ["G", "G", "G", "F"]:
        role_plan = [("S",), ("A",), ("T",), ("B",)]
    else:
        role_plan = None
    if role_plan:
        for group, roles in zip(groups, role_plan):
            if len(roles) > 1:
                for index, line in enumerate(group):
                    role = roles[min(index, len(roles) - 1)]
                    voices[role] = list(voices[role]) + _sounding(line, role)
                continue
            role = roles[0]
            primary = [line for line in group if line.get("layer", 0) == 0] or group[:1]
            for line in primary:
                voices[role] = list(voices[role]) + _sounding(line, role)
            for line in group:
                if line in primary:
                    continue
                voices[f"{role}2"] = list(voices[f"{role}2"]) + _sounding(line, f"{role}2")
        pending = []
    else:
        keyboard = _keyboard_group(groups)
        vocal_groups = []
        for index, group in enumerate(groups):
            if index == keyboard:
                piano.extend(group)
            else:
                vocal_groups.append(group)
        open_roles = [role for role in VOICES if role not in claimed]
        for group, role in zip(vocal_groups, open_roles):
            _place_group(voices, group, role)
        pending = [line for group in vocal_groups[len(open_roles):] for line in group]
    piano_notes = [note for line in piano for note in line["notes"]]
    piano_notes.sort(key=lambda note: (note["start"], note["pitch"]))
    voices["P"] = piano_notes
    if not trust:
        _wait_with_the_others(voices)
    _split_divisi(voices)
    sing_together(voices)
    for role in PARTS:
        voices[role].sort(key=lambda note: (note["start"], note["pitch"]))
        for note in voices[role]:
            note.pop("chord", None)
    extra = ""
    if pending:
        extra += f"还有 {len(pending)} 条谱表没有放进四个声部。"
    if outside:
        extra += f"其他 {outside} 条没有放进声部。"
    if other:
        extra += f"其他 {len(other)} 条没有放进声部。"
    return voices, extra


def voices_from_file(path):
    """Read an edited MusicXML file as written. Do not move a voice to match another."""
    parsed = read_score(path)
    voices, extra = assign_voices(parsed["lines"], trust=True)
    cleaned = {}
    for role in PARTS:
        cleaned[role] = [
            {"start": note["start"], "duration": note["duration"], "pitch": int(note["pitch"])}
            for note in voices[role]
        ]
        cleaned[role].sort(key=lambda note: (note["start"], note["pitch"]))
    return cleaned, parsed["bpm"], parsed["beats_per_bar"], extra, parsed["tempo_written"]


def join_scores(parsed_list):
    """Place later pages after earlier ones. Each file's timeline starts at beat 0."""
    voices = {role: [] for role in PARTS}
    warnings = []
    bpm, beats = 72, 4
    offset = 0.0
    timed = False
    vocal_counts = []
    for parsed in parsed_list:
        assigned, extra = assign_voices(parsed["lines"])
        vocal_counts.append(sum(1 for role in VOICES if assigned[role]))
        if not any(assigned[role] for role in PARTS):
            warnings.append("有一段没有读出音符。")
            continue
        end = offset
        for role, notes in assigned.items():
            for note in notes:
                start = round(float(note["start"]) + offset, 4)
                duration = round(float(note["duration"]), 4)
                voices[role].append({"start": start, "duration": duration, "pitch": int(note["pitch"])})
                end = max(end, start + duration)
        if extra and extra not in warnings:
            warnings.append(extra)
        # A note read past the bar may stick out. It must not push the next page back.
        offset = offset + float(parsed["timeline"]) if parsed.get("timeline") else end
        if not timed:
            bpm = parsed["bpm"]
            beats = parsed["beats_per_bar"]
            timed = True
    if len({count for count in vocal_counts if count}) > 1:
        warnings.append("各页声部数量不一致，后面的页可能对不齐。")
    return voices, bpm, beats, "".join(warnings)


def prefer_main_score(parsed_list):
    """Audiveris sometimes exports a tiny broken movement beside the real score."""
    if len(parsed_list) <= 1:
        return parsed_list
    counts = [sum(len(line["notes"]) for line in parsed["lines"]) for parsed in parsed_list]
    total = sum(counts) or 1
    best = max(counts)
    if best >= total * 0.8:
        return [parsed_list[counts.index(best)]]
    return parsed_list


def homr_python():
    root = Path(__file__).resolve().parent
    candidates = [
        os.environ.get("HOMR_PYTHON", ""),
        str(root / "runtime" / "python.exe"),
        str(root / ".venv-homr" / "Scripts" / "python.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def homr_site(python):
    python = Path(python).resolve()
    sites = [python.parent / "Lib" / "site-packages" / "homr"]
    if len(python.parents) > 1:
        sites.append(python.parents[1] / "Lib" / "site-packages" / "homr")
    for site in sites:
        if site.is_dir():
            return site
    return sites[0]


def homr_ready():
    python = homr_python()
    if python is None:
        return False
    site = homr_site(python)

    def ready(paths):
        return any(path.is_file() and "fp16" not in path.name and path.stat().st_size > 1_000_000 for path in paths)

    return ready((site / "segmentation").glob("segnet_*.onnx")) and ready(
        (site / "transformer").glob("encoder_*.onnx")
    ) and ready((site / "transformer").glob("decoder_*.onnx"))


def engine_name():
    if homr_ready():
        return "homr"
    if audiveris_path():
        return "Audiveris"
    if homr_python():
        return "homr"
    return ""


def _exports(output):
    exports = list(output.rglob("*.mxl")) + list(output.rglob("*.musicxml"))
    exports += [path for path in output.rglob("*.xml") if path.name not in ("book.xml", "sheet.xml")]
    exports = [path for path in exports if "homr" not in path.parts]
    return sorted(exports, key=lambda path: str(path).lower())


def _read_exports(paths):
    parsed = []
    problems = []
    division, beats = 1.0, 4.0
    for path in paths:
        try:
            score = read_score(path, division, beats)
            parsed.append(score)
            division, beats = score.get("division", division), score.get("meter", beats)
        except Exception as error:
            problems.append(f"{path.name} 读不了：{error}")
    return parsed, problems


def _describe(engine, voices, extra, detail=""):
    shown = []
    for voice in VOICES:
        shown.append(voice)
        if voices.get(f"{voice}2"):
            shown.append(f"{voice}2")
    if voices.get("P"):
        shown.append("P")
    counts = "，".join(f"{part_name(voice, voices)} {len(voices[voice])} 个音" for voice in shown)
    return f"{engine}：{counts}。{extra}{detail}"


def transcribe(pdf_path, output, timeout=900):
    exe = audiveris_path()
    if exe is None:
        raise RuntimeError("没有找到 Audiveris。把 Audiveris.exe 路径写到环境变量 AUDIVERIS_CMD。")
    output.mkdir(parents=True, exist_ok=True)
    command = [str(exe), "-batch", "-transcribe", "-export", "-output", str(output), "--", str(pdf_path)]
    log_path = output / "audiveris.log"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            subprocess.run(command, cwd=exe.parent, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("识别超时。先用页数少一点的谱试。") from None
    exports = _exports(output)
    if not exports:
        text = log_path.read_text(encoding="utf-8", errors="replace")[-500:]
        raise RuntimeError(f"没有识别出乐谱。{text}")
    return exports


def transcribe_homr(pages, output, timeout, progress=None):
    python = homr_python()
    if python is None:
        raise RuntimeError("没有找到 homr。先运行 setup-homr.bat。")
    work = output / "homr"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for page in pages:
        shutil.copy(page, work / page.name)
    log_path = output / "homr.log"
    log_path.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONWARNINGS"] = "ignore"
    # homr's folder scan matches both *.png and *.PNG. On Windows those are the same files, so each page would run twice.
    deadline = time.monotonic() + timeout
    images = sorted(path for path in work.iterdir() if path.suffix.lower() == ".png")
    total = len(images)
    if progress and total:
        progress(0, total, f"正在读第 1 / {total} 页")
    for index, image in enumerate(images, start=1):
        remaining = int(deadline - time.monotonic())
        if remaining < 30:
            break
        command = [str(python), str(Path(__file__).resolve().parent / "homr_keep.py"), str(image), "--gpu", "no"]
        try:
            with log_path.open("a", encoding="utf-8") as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=remaining, env=env)
        except subprocess.TimeoutExpired:
            break
        if progress:
            progress(index, total, f"正在读第 {index} / {total} 页")
    exports = sorted(work.glob("*.musicxml"))
    if not exports:
        text = log_path.read_text(encoding="utf-8", errors="replace")[-500:]
        raise RuntimeError(f"homr 没有写出乐谱。{text}")
    return exports


def reload_homr(folder):
    """Read the MusicXML homr already wrote for this piece. This does not run homr again."""
    exports = sorted((Path(folder) / "omr" / "homr").glob("*.musicxml"), key=lambda path: path.name.lower())
    if not exports:
        return None
    parsed, problems = _read_exports(exports)
    detail = f" {' '.join(problems)}" if problems else ""
    return _from_parsed("homr", parsed, detail)


def _from_parsed(engine, parsed, detail=""):
    voices, bpm, beats, extra = join_scores(parsed)
    if not any(voices.values()):
        return None
    return {"voices": voices, "bpm": bpm, "beats_per_bar": beats, "report": _describe(engine, voices, extra, detail)}


def recognize_piece(folder, piece, pdf_path, timeout, progress=None):
    output = Path(folder) / "omr"
    output.mkdir(parents=True, exist_ok=True)
    pages = [Path(folder) / f"page-{number:02d}.png" for number in range(1, piece["pages"] + 1)]
    pages = [path for path in pages if path.is_file()]
    errors = []
    homr_result = None
    if homr_python() and not homr_ready():
        errors.append("homr 模型还在下载")
    elif homr_ready() and pages:
        try:
            exports = transcribe_homr(pages, output, timeout, progress)
            parsed, problems = _read_exports(exports)
            errors.extend(problems)
            missed = len(pages) - len(exports)
            detail = f" {missed} 页没有结果。" if missed else ""
            homr_result = _from_parsed("homr", parsed, detail)
            if homr_result and len(exports) * 2 >= len(pages):
                return homr_result
            errors.append(f"homr 只完成 {len(exports)}/{len(pages)} 页")
        except Exception as error:
            errors.append(str(error)[:300])
    exports = _exports(output)
    if not exports and audiveris_path() and pdf_path and Path(pdf_path).is_file():
        if progress:
            progress(0, 0, "正在用 Audiveris 读整份谱")
        exports = transcribe(pdf_path, output, timeout)
    if exports:
        parsed, problems = _read_exports(exports)
        errors.extend(problems)
        result = _from_parsed("Audiveris", prefer_main_score(parsed))
        if result:
            if errors:
                result["report"] = "homr 没有用上，已改用 Audiveris。" + result["report"]
            return result
    if homr_result:
        return homr_result
    raise RuntimeError("；".join(errors) or "没有识别出音符")

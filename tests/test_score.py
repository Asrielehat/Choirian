import io
import os
import tempfile
import unittest
import unittest.mock
import wave
import zipfile
from pathlib import Path

os.environ["FENBU_DATA"] = tempfile.mkdtemp()
import app
import recognize
import score
from fastapi.testclient import TestClient


SATB = """<score-partwise>
<part-list>
<score-part id="P1"><part-name>Voice</part-name></score-part>
<score-part id="P2"><part-name>Voice</part-name></score-part>
<score-part id="P3"><part-name>Voice</part-name></score-part>
<score-part id="P4"><part-name>Voice</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration><tie type="start"/></note><note><rest/><duration>3</duration></note></measure><measure number="2"><note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration><tie type="stop"/></note></measure></part>
<part id="P2"><measure number="1"><note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration></note></measure><measure number="2"><note><rest/><duration>4</duration></note></measure></part>
<part id="P3"><measure number="1"><note><pitch><step>E</step><octave>4</octave></pitch><duration>2</duration></note><note><pitch><step>F</step><octave>4</octave></pitch><duration>2</duration></note></measure><measure number="2"><note><rest/><duration>4</duration></note></measure></part>
<part id="P4"><measure number="1"><note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration></note></measure><measure number="2"><note><rest/><duration>4</duration></note></measure></part>
</score-partwise>"""


class ScoreTests(unittest.TestCase):
    def test_overlap_and_overflow_are_rejected(self):
        notes = [{"start": 0, "duration": 1, "pitch": 72}, {"start": 0.5, "duration": 1, "pitch": 74}]
        with self.assertRaises(ValueError):
            score.clean_voice(notes, 4, 2)
        late = [{"start": 7.5, "duration": 1, "pitch": 72}]
        with self.assertRaises(ValueError):
            score.clean_voice(late, 4, 2)

    def test_wav_and_zip(self):
        notes = [{"start": 0, "duration": 1, "pitch": 72}, {"start": 1, "duration": 0.5, "pitch": 74}]
        raw = score.render_wav(notes, 80)
        self.assertTrue(raw.startswith(b"RIFF"))
        piece = score.new_piece("a" * 32, "demo", 1)
        piece["voices"]["S"] = notes
        archive = zipfile.ZipFile(io.BytesIO(score.render_zip(piece)))
        self.assertEqual(archive.namelist(), ["S-女高.wav"])
        piece["voices"]["S2"] = [{"start": 0, "duration": 1, "pitch": 67}]
        divided = zipfile.ZipFile(io.BytesIO(score.render_zip(piece)))
        self.assertEqual(divided.namelist(), ["S-女高一.wav", "S2-女高二.wav"])
        self.assertTrue(divided.read("S2-女高二.wav").startswith(b"RIFF"))

    def test_a_faster_tempo_shortens_the_note_and_keeps_the_pitch(self):
        note = [{"start": 0, "duration": 1, "pitch": 69}]

        def samples(bpm):
            opened = wave.open(io.BytesIO(score.render_wav(note, bpm)))
            return memoryview(opened.readframes(opened.getnframes())).cast("h").tolist()

        slow = samples(60)
        fast = samples(120)
        self.assertLess(len(fast), len(slow))
        self.assertEqual(fast[:1000], slow[:1000])

    def test_tempo_thirty_stays_and_the_pitch_does_not_change(self):
        piece = score.new_piece("d" * 32, "慢.musicxml", 1)
        score.apply_recognition(piece, {"S": [{"start": 0, "duration": 1, "pitch": 60}]}, 30, 4, "")
        self.assertEqual(piece["bpm"], 30)
        note = [{"start": 0, "duration": 1, "pitch": 69}, {"start": 1, "duration": 1, "pitch": 71}]
        slow = wave.open(io.BytesIO(score.render_wav(note, 30)))
        fast = wave.open(io.BytesIO(score.render_wav(note, 72)))
        self.assertGreater(slow.getnframes(), fast.getnframes())
        self.assertEqual(slow.readframes(1000), fast.readframes(1000))
        piece["voices"]["S"] = note
        slow_zip = zipfile.ZipFile(io.BytesIO(score.render_zip(piece, 30)))
        fast_zip = zipfile.ZipFile(io.BytesIO(score.render_zip(piece, 72)))
        self.assertGreater(len(slow_zip.read("S-女高.wav")), len(fast_zip.read("S-女高.wav")))


class RecognizeTests(unittest.TestCase):
    def test_a_written_tempo_of_thirty_is_kept(self):
        path = Path(tempfile.mkdtemp()) / "slow.musicxml"
        path.write_text(
            "<score-partwise><part-list><score-part id=\"P1\"><part-name>Soprano</part-name></score-part></part-list>"
            "<part id=\"P1\"><measure number=\"1\"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>"
            "<direction><sound tempo=\"30\"/></direction><note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration></note></measure></part></score-partwise>",
            encoding="utf-8",
        )
        self.assertEqual(recognize.read_score(path)["bpm"], 30)

    def test_four_unnamed_parts_become_satb(self):
        path = Path(tempfile.mkdtemp()) / "score.musicxml"
        path.write_text(SATB, encoding="utf-8")
        parsed = recognize.read_score(path)
        voices, extra = recognize.assign_voices(parsed["lines"])
        self.assertEqual(extra, "")
        self.assertEqual(voices["S"][0]["pitch"], 72)
        self.assertEqual(voices["A"][0]["pitch"], 67)
        self.assertEqual(voices["T"][0]["duration"], 2)
        self.assertEqual(voices["B"][0]["pitch"], 48)
        self.assertEqual(parsed["beats_per_bar"], 4)
        stacked = """<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><print new-system="yes"/><note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="3"><print new-system="yes"/><note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="4"><print new-system="yes"/><clef><sign>F</sign><line>4</line></clef><note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration></note></measure>
<measure number="5"><print new-system="yes"/><clef><sign>G</sign><line>2</line></clef><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration></note></measure>
<measure number="6"><print new-system="yes"/><note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="7"><print new-system="yes"/><note><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="8"><print new-system="yes"/><clef><sign>F</sign><line>4</line></clef><note><pitch><step>D</step><octave>3</octave></pitch><duration>4</duration></note></measure>
</part></score-partwise>"""
        stacked_path = path.with_name("stacked.musicxml")
        stacked_path.write_text(stacked, encoding="utf-8")
        stacked_score = recognize.read_score(stacked_path)
        stacked_voices, stacked_extra = recognize.assign_voices(stacked_score["lines"])
        self.assertEqual(stacked_extra, "")
        self.assertEqual([note["pitch"] for note in stacked_voices["S"]], [72, 74])
        self.assertEqual(stacked_voices["S"][1]["start"], 4)
        self.assertEqual(stacked_voices["A"][0]["pitch"], 67)
        self.assertEqual(stacked_voices["B"][1]["pitch"], 50)
        scaled = """<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>2</duration></note></measure>
<measure number="2"><print new-system="yes"/><note><pitch><step>G</step><octave>4</octave></pitch><duration>2</duration></note></measure>
<measure number="3"><print new-system="yes"/><note><pitch><step>E</step><octave>4</octave></pitch><duration>2</duration></note></measure>
<measure number="4"><print new-system="yes"/><clef><sign>F</sign><line>4</line></clef><note><pitch><step>C</step><octave>3</octave></pitch><duration>2</duration></note></measure>
</part></score-partwise>"""
        scaled_path = path.with_name("scaled.musicxml")
        scaled_path.write_text(scaled, encoding="utf-8")
        scaled_voices, _ = recognize.assign_voices(recognize.read_score(scaled_path)["lines"])
        self.assertEqual([note["duration"] for note in scaled_voices["A"]], [0.5])
        self.assertEqual([note["duration"] for note in scaled_voices["B"]], [0.5])
        continued = """<score-partwise><part-list><score-part id="P1"><part-name>Soprano</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign><line>2</line></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><print new-system="yes"/><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration></note></measure>
</part></score-partwise>"""
        continued_path = path.with_name("continued.musicxml")
        continued_path.write_text(continued, encoding="utf-8")
        continued_score = recognize.read_score(continued_path)
        self.assertEqual(len(continued_score["lines"]), 1)
        self.assertEqual(continued_score["lines"][0]["notes"][1]["start"], 4)
        self.assertEqual(recognize.prefer_main_score([
            {"lines": [{"name": "Piano", "notes": [{}] * 10}]},
            {"lines": [{"name": "Voice", "notes": [{}] * 100}]},
        ])[0]["lines"][0]["name"], "Voice")
        voices, extra = recognize.assign_voices([
            {"name": "Soprano", "notes": [{"start": 0, "duration": 1, "pitch": 72}]},
            {"name": "Alto", "notes": [{"start": 0, "duration": 1, "pitch": 67}]},
            {"name": "Piano", "notes": [{"start": 0, "duration": 1, "pitch": 60}]},
        ])
        self.assertEqual(voices["T"], [])
        self.assertEqual(voices["P"][0]["pitch"], 60)
        self.assertEqual(extra, "")
        piano_only, piano_extra = recognize.assign_voices([
            {"name": "Piano", "notes": [{"start": 0, "duration": 1, "pitch": 48}, {"start": 0, "duration": 1, "pitch": 64}]},
        ])
        self.assertEqual(piano_only["S"], [])
        self.assertEqual([note["pitch"] for note in piano_only["P"]], [48, 64])
        self.assertEqual(piano_only["S2"], [])
        self.assertEqual(piano_extra, "")
        joined, bpm, beats, warning = recognize.join_scores([
            {"bpm": 80, "beats_per_bar": 4, "lines": [{"name": "Soprano", "notes": [{"start": 0, "duration": 4, "pitch": 72}]}]},
            {"bpm": 90, "beats_per_bar": 3, "lines": [{"name": "Soprano", "notes": [{"start": 0, "duration": 1, "pitch": 74}]}]},
        ])
        self.assertEqual(joined["S"][1]["start"], 4)
        self.assertEqual((bpm, beats, warning), (80, 4, ""))
        piece = score.new_piece("b" * 32, "demo", 1)
        score.apply_recognition(piece, voices, parsed["bpm"], parsed["beats_per_bar"], "ok")
        self.assertGreater(len(score.render_wav(piece["voices"]["S"], piece["bpm"])), 44)
        self.assertEqual(piece["voices"]["P"][0]["pitch"], 60)

    def test_piano_label_and_same_pitch_curve_hold(self):
        path = Path(tempfile.mkdtemp()) / "piano.musicxml"
        path.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Soprano</part-name></score-part>
<score-part id="P2"><part-name>Piano</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration><notations><slur type="start" number="1"/></notations></note>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>2</duration><notations><slur type="stop" number="1"/></notations></note>
<note><pitch><step>D</step><octave>5</octave></pitch><duration>1</duration><notations><slur type="start" number="1"/></notations></note>
<note><pitch><step>E</step><octave>5</octave></pitch><duration>1</duration><notations><slur type="stop" number="1"/></notations></note>
<note><pitch><step>F</step><octave>5</octave></pitch><duration>1</duration><notations><tied type="start"/></notations></note>
<note><pitch><step>F</step><octave>5</octave></pitch><duration>1</duration><notations><tied type="stop"/></notations></note>
</measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions><staves>2</staves></attributes>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><staff>1</staff></note>
<backup><duration>4</duration></backup>
<note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><staff>2</staff></note>
</measure></part></score-partwise>""", encoding="utf-8")
        voices, extra = recognize.assign_voices(recognize.read_score(path)["lines"])
        self.assertEqual(extra, "")
        self.assertEqual([(note["pitch"], note["duration"]) for note in voices["S"]], [(72, 3), (74, 1), (76, 1), (77, 2)])
        self.assertEqual(voices["A"], [])
        self.assertEqual([note["pitch"] for note in voices["P"]], [48, 67])

    def test_a_dense_wide_staff_becomes_piano_and_the_bass_stays(self):
        def sung(pitches, layer=0):
            return {
                "name": "Voice",
                "clef": "G",
                "layer": layer,
                "notes": [{"start": index, "duration": 1, "pitch": pitch} for index, pitch in enumerate(pitches)],
            }

        piano = [{"start": index, "duration": 1, "pitch": pitch} for index, pitch in enumerate(
            [36, 48, 60, 72, 36, 48, 60, 72, 40, 52, 64, 76]
        )]
        voices, extra = recognize.assign_voices([
            sung([72, 74]),
            sung([67, 69]),
            sung([64, 65, 67]),
            sung([60, 62], layer=1),
            sung([48, 50, 47]),
            {"name": "Voice", "clef": "G", "layer": 0, "bass_clef_change": True, "notes": piano},
        ])
        self.assertEqual(extra, "")
        self.assertEqual([note["pitch"] for note in voices["S"]], [72, 74])
        self.assertEqual([note["pitch"] for note in voices["T"]], [52, 53, 55])
        self.assertEqual([note["pitch"] for note in voices["B"]], [48, 50, 47])
        self.assertIn(48, [note["pitch"] for note in voices["T2"]])
        self.assertNotIn(47, [note["pitch"] for note in voices["T2"]])
        self.assertIn(36, [note["pitch"] for note in voices["P"]])
        self.assertIn(76, [note["pitch"] for note in voices["P"]])


class MeterTests(unittest.TestCase):
    def test_meter_change_reaches_every_staff_and_the_next_page(self):
        folder = Path(tempfile.mkdtemp())
        changed = folder / "changed.musicxml"
        changed.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><print new-system="yes"/><note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="3"><print new-system="yes"/><note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="4"><print new-system="yes"/><clef><sign>F</sign></clef><note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration></note></measure>
<measure number="5"><print new-system="yes"/><attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes><clef><sign>G</sign></clef><note><pitch><step>D</step><octave>5</octave></pitch><duration>3</duration></note></measure>
<measure number="6"><print new-system="yes"/><note><pitch><step>A</step><octave>4</octave></pitch><duration>3</duration></note></measure>
<measure number="7"><print new-system="yes"/><note><pitch><step>F</step><octave>4</octave></pitch><duration>3</duration></note></measure>
<measure number="8"><print new-system="yes"/><clef><sign>F</sign></clef><note><pitch><step>D</step><octave>3</octave></pitch><duration>3</duration></note></measure>
<measure number="9"><print new-system="yes"/><clef><sign>G</sign></clef><note><pitch><step>E</step><octave>5</octave></pitch><duration>3</duration></note></measure>
<measure number="10"><print new-system="yes"/><note><pitch><step>B</step><octave>4</octave></pitch><duration>3</duration></note></measure>
<measure number="11"><print new-system="yes"/><note><pitch><step>G</step><octave>4</octave></pitch><duration>3</duration></note></measure>
<measure number="12"><print new-system="yes"/><clef><sign>F</sign></clef><note><pitch><step>E</step><octave>3</octave></pitch><duration>3</duration></note></measure>
</part></score-partwise>""", encoding="utf-8")
        voices, _ = recognize.assign_voices(recognize.read_score(changed)["lines"])
        self.assertEqual([note["start"] for note in voices["S"]], [0, 4, 7])
        self.assertEqual([note["start"] for note in voices["A"]], [0, 4, 7])
        odd = folder / "odd.musicxml"
        odd.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration></note></measure>
<measure number="2"><print new-system="yes"/><attributes><time><beats>5</beats><beat-type>4</beat-type></time></attributes><note><pitch><step>D</step><octave>5</octave></pitch><duration>1</duration></note></measure>
<measure number="3"><print new-system="yes"/><note><pitch><step>E</step><octave>5</octave></pitch><duration>1</duration></note></measure>
</part></score-partwise>""", encoding="utf-8")
        odd_line = recognize.read_score(odd)["lines"][0]["notes"]
        self.assertEqual([note["start"] for note in odd_line], [0, 1, 2])
        twelve = folder / "twelve.musicxml"
        twelve.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><time><beats>12</beats><beat-type>8</beat-type></time><clef><sign>G</sign></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration></note></measure>
</part></score-partwise>""", encoding="utf-8")
        first = recognize.read_score(twelve)
        self.assertEqual(first["meter"], 6)
        nxt = folder / "next.musicxml"
        nxt.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes><note><pitch><step>D</step><octave>5</octave></pitch><duration>1</duration></note></measure>
<measure number="2"><note><pitch><step>E</step><octave>5</octave></pitch><duration>1</duration></note></measure>
</part></score-partwise>""", encoding="utf-8")
        second = recognize.read_score(nxt, first["division"], first["meter"])
        self.assertEqual([note["start"] for note in second["lines"][0]["notes"]], [0, 1])
        filled = folder / "filled.musicxml"
        filled.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><time><beats>12</beats><beat-type>8</beat-type></time><clef><sign>G</sign></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration></note><note><rest/><duration>3</duration></note></measure>
<measure number="2"><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration></note></measure>
</part></score-partwise>""", encoding="utf-8")
        filled_line = recognize.read_score(filled)["lines"][0]["notes"]
        self.assertEqual([note["start"] for note in filled_line], [0, 4])
        triplet = folder / "triplet.musicxml"
        triplet.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>3</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>3</duration><type>quarter</type></note>
<note><pitch><step>D</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification></note>
<note><pitch><step>E</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification></note>
<note><pitch><step>F</step><octave>5</octave></pitch><duration>3</duration><type>quarter</type></note>
</measure></part></score-partwise>""", encoding="utf-8")
        triplet_line = recognize.read_score(triplet)["lines"][0]["notes"]
        self.assertEqual([round(note["duration"], 4) for note in triplet_line], [0.6667, 0.6667, 0.6667, 1])
        self.assertEqual([round(note["start"], 4) for note in triplet_line], [0, 0.6667, 1.3333, 2])
        shared = folder / "shared.musicxml"
        shared.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration><voice>1</voice></note><backup><duration>4</duration></backup><note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><voice>2</voice></note></measure>
<measure number="2"><print new-system="yes"/><clef><sign>F</sign></clef><note><pitch><step>E</step><octave>3</octave></pitch><duration>4</duration><voice>1</voice></note><backup><duration>4</duration></backup><note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><voice>2</voice></note></measure>
<measure number="3"><print new-system="yes"/><clef><sign>G</sign></clef><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration><voice>1</voice></note><backup><duration>4</duration></backup><note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration><voice>2</voice></note></measure>
<measure number="4"><print new-system="yes"/><clef><sign>F</sign></clef><note><pitch><step>F</step><octave>3</octave></pitch><duration>4</duration><voice>1</voice></note><backup><duration>4</duration></backup><note><pitch><step>D</step><octave>3</octave></pitch><duration>4</duration><voice>2</voice></note></measure>
</part></score-partwise>""", encoding="utf-8")
        shared_voices, _ = recognize.assign_voices(recognize.read_score(shared)["lines"])
        self.assertEqual([note["pitch"] for note in shared_voices["S"]], [72, 74])
        self.assertEqual([note["pitch"] for note in shared_voices["A"]], [67, 69])
        self.assertEqual(shared_voices["S"][1]["start"], 4)
        self.assertEqual([note["pitch"] for note in shared_voices["T"]], [52, 53])
        self.assertEqual([note["pitch"] for note in shared_voices["B"]], [48, 50])

    def test_a_longer_staff_does_not_open_a_rest(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "ahead.musicxml"
        path.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Voice</part-name></score-part>
<score-part id="P2"><part-name>Voice</part-name></score-part>
<score-part id="P3"><part-name>Piano</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>6</beats><beat-type>8</beat-type></time><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>3</duration></note></measure>
<measure number="2"><note><pitch><step>D</step><octave>5</octave></pitch><duration>3</duration></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions><time><beats>8</beats><beat-type>8</beat-type></time><clef><sign>G</sign></clef></attributes>
<note><rest/><duration>4</duration></note></measure>
<measure number="2"><note><rest/><duration>4</duration></note></measure></part>
<part id="P3"><measure number="1"><attributes><divisions>1</divisions><staves>2</staves><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note>
<note><pitch><step>E</step><octave>4</octave></pitch><duration>2</duration></note></measure>
<measure number="2"><note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
</score-partwise>""", encoding="utf-8")
        voices, _ = recognize.assign_voices(recognize.read_score(path)["lines"])
        self.assertEqual([note["start"] for note in voices["S"]], [0, 3])
        self.assertEqual(voices["P"][1]["start"], 3)
        first = {"bpm": 72, "beats_per_bar": 4, "timeline": 3, "lines": [
            {"name": "Voice", "clef": "G", "layer": 0, "notes": [{"start": 0, "duration": 3, "pitch": 72}]},
            {"name": "Piano", "clef": "G", "layer": 0, "notes": [{"start": 0, "duration": 6, "pitch": 60}]},
        ]}
        second = {"bpm": 72, "beats_per_bar": 4, "timeline": 3, "lines": [
            {"name": "Voice", "clef": "G", "layer": 0, "notes": [{"start": 0, "duration": 3, "pitch": 74}]},
        ]}
        joined, _, _, _ = recognize.join_scores([first, second])
        self.assertEqual([note["start"] for note in joined["S"]], [0, 3])

    def test_a_voice_that_sings_the_same_notes_early_waits(self):
        voices, _ = recognize.assign_voices([
            {"name": "Soprano", "clef": "G", "layer": 0, "notes": [
                {"start": 0, "duration": 4.5, "pitch": 67},
                {"start": 4.5, "duration": 0.5, "pitch": 67},
                {"start": 5.0, "duration": 0.5, "pitch": 65},
                {"start": 5.5, "duration": 0.5, "pitch": 64},
            ]},
            {"name": "Bass", "clef": "F", "layer": 0, "notes": [
                {"start": 0, "duration": 3.5, "pitch": 55},
                {"start": 3.5, "duration": 0.25, "pitch": 55},
                {"start": 3.75, "duration": 0.25, "pitch": 53},
                {"start": 4.0, "duration": 0.5, "pitch": 52},
                {"start": 6.0, "duration": 1, "pitch": 58},
            ]},
        ])
        self.assertEqual([note["start"] for note in voices["B"][:4]], [0, 4.5, 5.0, 5.5])
        self.assertEqual([note["duration"] for note in voices["B"][:4]], [4.5, 0.5, 0.5, 0.5])
        self.assertEqual(voices["B"][4]["start"], 6)

    def test_a_high_f_that_shares_its_beat_stays_where_it_was_written(self):
        voices, _ = recognize.assign_voices([
            {"name": "Soprano", "clef": "G", "layer": 0, "notes": [
                {"start": 1, "duration": 1, "pitch": 67},
                {"start": 2, "duration": 1, "pitch": 65},
                {"start": 3, "duration": 1, "pitch": 64},
            ]},
            {"name": "Tenor", "clef": "G", "layer": 0, "notes": [
                {"start": 0, "duration": 1, "pitch": 67},
                {"start": 1, "duration": 1, "pitch": 65},
                {"start": 1, "duration": 1, "pitch": 77},
                {"start": 2, "duration": 1, "pitch": 64},
            ]},
        ])
        self.assertEqual([note["start"] for note in voices["T"]], [0, 1, 2])
        self.assertEqual(voices["T"][1]["pitch"], 65)
        self.assertEqual([note["pitch"] for note in voices["T2"] if note["start"] == 1], [53])

    def test_a_high_f_written_on_the_upper_layer_stays_on_that_beat(self):
        voices, _ = recognize.assign_voices([
            {"name": "Soprano", "clef": "G", "layer": 0, "notes": [
                {"start": 1, "duration": 1, "pitch": 67},
                {"start": 2, "duration": 1, "pitch": 65},
                {"start": 3, "duration": 1, "pitch": 64},
            ]},
            {"name": "Tenor", "clef": "G", "layer": 0, "notes": [
                {"start": 0, "duration": 1, "pitch": 67},
                {"start": 1, "duration": 1, "pitch": 77},
                {"start": 2, "duration": 1, "pitch": 64},
            ]},
            {"name": "Tenor", "clef": "G", "layer": 1, "notes": [
                {"start": 1, "duration": 1, "pitch": 65},
            ]},
        ])
        self.assertEqual([note["start"] for note in voices["T"]], [0, 1, 2])
        self.assertEqual(voices["T"][1]["pitch"], 65)
        self.assertEqual([note["pitch"] for note in voices["T2"] if abs(note["start"] - 1) < 0.02], [53])

    def test_the_higher_pitch_is_the_tenor_and_the_lower_is_the_second(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "tenor-f.musicxml"
        path.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Tenor</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>2</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type></note>
<note><pitch><step>F</step><octave>4</octave></pitch><duration>1</duration><type>eighth</type></note>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>1</duration><type>eighth</type></note>
<note><pitch><step>F</step><octave>4</octave></pitch><duration>1</duration><type>eighth</type></note>
<note><chord/><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type></note>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>1</duration><type>eighth</type></note>
<note><chord/><pitch><step>D</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type></note>
<note><pitch><step>F</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type></note>
<note><chord/><pitch><step>F</step><octave>4</octave></pitch><duration>1</duration><type>eighth</type></note>
</measure>
<measure number="2">
<note><pitch><step>G</step><octave>4</octave></pitch><duration>3</duration><type>quarter</type></note>
<note><chord/><pitch><step>E</step><octave>5</octave></pitch><duration>3</duration><type>quarter</type></note>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>3</duration><type>quarter</type></note>
<note><chord/><pitch><step>C</step><octave>5</octave></pitch><duration>3</duration><type>quarter</type></note>
</measure></part></score-partwise>""", encoding="utf-8")
        voices, _ = recognize.assign_voices(recognize.read_score(path)["lines"])
        self.assertEqual(
            [(note["start"], note["pitch"]) for note in voices["T"]],
            [(0.0, 60), (0.5, 53), (1.0, 55), (1.5, 60), (2.0, 62), (2.5, 65), (3.0, 64), (4.5, 60)],
        )
        self.assertEqual([note["pitch"] for note in voices["T2"] if note["start"] == 1.5], [53])
        self.assertEqual([note["pitch"] for note in voices["T2"] if note["start"] == 2.5], [53])
        self.assertEqual([note["pitch"] for note in voices["T2"] if note["start"] == 3.0], [55])

    def test_a_higher_second_voice_becomes_the_first_for_soprano_and_bass(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "above.musicxml"
        path.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Soprano</part-name></score-part>
<score-part id="P2"><part-name>Bass</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note>
<backup><duration>4</duration></backup>
<note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration><voice>2</voice></note></measure>
<measure number="2">
<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration><voice>1</voice></note>
<backup><duration>4</duration></backup>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><voice>2</voice></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions><clef><sign>F</sign></clef></attributes>
<note><pitch><step>G</step><octave>2</octave></pitch><duration>4</duration><voice>1</voice></note>
<backup><duration>4</duration></backup>
<note><pitch><step>F</step><octave>3</octave></pitch><duration>4</duration><voice>2</voice></note></measure>
<measure number="2">
<note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><voice>1</voice></note>
<backup><duration>4</duration></backup>
<note><pitch><step>A</step><octave>2</octave></pitch><duration>4</duration><voice>2</voice></note></measure></part>
</score-partwise>""", encoding="utf-8")
        voices, extra = recognize.assign_voices(recognize.read_score(path)["lines"])
        self.assertEqual([note["pitch"] for note in voices["S"]], [69, 72])
        self.assertEqual([note["pitch"] for note in voices["S2"]], [65, 67])
        self.assertEqual([note["pitch"] for note in voices["B"]], [53, 48])
        self.assertEqual([note["pitch"] for note in voices["B2"]], [43, 45])
        self.assertEqual(extra, "")

    def test_chord_on_a_shared_staff_splits_into_the_two_singers(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "chord.musicxml"
        path.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Voice</part-name></score-part>
<score-part id="P2"><part-name>Voice</part-name></score-part>
<score-part id="P3"><part-name>Piano</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>E</step><octave>5</octave></pitch><duration>4</duration><voice>1</voice></note>
<note><chord/><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note></measure>
<measure number="2"><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration><voice>1</voice></note>
<note><chord/><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions><clef><sign>F</sign></clef></attributes>
<note><pitch><step>B</step><octave>3</octave></pitch><duration>4</duration><voice>1</voice></note>
<note><chord/><pitch><step>E</step><octave>3</octave></pitch><duration>4</duration><voice>1</voice></note></measure>
<measure number="2"><note><pitch><step>A</step><octave>3</octave></pitch><duration>4</duration><voice>1</voice></note>
<note><chord/><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><voice>1</voice></note></measure></part>
<part id="P3"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note>
<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note>
<note><chord/><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note></measure>
<measure number="2"><note><pitch><step>D</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note>
<note><chord/><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note>
<note><chord/><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note></measure></part>
</score-partwise>""", encoding="utf-8")
        voices, _ = recognize.assign_voices(recognize.read_score(path)["lines"])
        self.assertEqual([note["pitch"] for note in voices["S"]], [76, 74])
        self.assertEqual([note["pitch"] for note in voices["A"]], [67, 69])
        self.assertEqual([note["pitch"] for note in voices["T"]], [59, 57])
        self.assertEqual([note["pitch"] for note in voices["B"]], [52, 48])
        self.assertEqual(sorted(note["pitch"] for note in voices["P"]), [60, 62, 64, 65, 67, 69])
        self.assertEqual(voices["S2"], [])
        self.assertEqual(voices["A2"], [])

    def test_four_staves_keep_their_own_singers_when_one_staff_has_a_chord(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "four.musicxml"
        path.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Voice</part-name></score-part>
<score-part id="P2"><part-name>Voice</part-name></score-part>
<score-part id="P3"><part-name>Voice</part-name></score-part>
<score-part id="P4"><part-name>Voice</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration></note>
<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration></note>
<note><chord/><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><note><pitch><step>B</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
<part id="P3"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
<part id="P4"><measure number="1"><attributes><divisions>1</divisions><clef><sign>F</sign></clef></attributes>
<note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><note><pitch><step>D</step><octave>3</octave></pitch><duration>4</duration></note></measure></part>
</score-partwise>""", encoding="utf-8")
        voices, extra = recognize.assign_voices(recognize.read_score(path)["lines"])
        self.assertEqual([note["pitch"] for note in voices["S"]], [72, 74])
        self.assertEqual([note["pitch"] for note in voices["S2"]], [64, 65])
        self.assertEqual([note["pitch"] for note in voices["A"]], [69, 71])
        self.assertEqual([note["pitch"] for note in voices["T"]], [55, 57])
        self.assertEqual([note["pitch"] for note in voices["B"]], [48, 50])
        self.assertNotIn("还有", extra)

    def test_piano_lines_follow_each_other(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "interlude.musicxml"
        path.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><staves>2</staves><clef><sign>G</sign></clef></attributes><note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><print new-system="yes"/><clef><sign>F</sign></clef><note><pitch><step>D</step><octave>3</octave></pitch><duration>4</duration></note></measure>
<measure number="3"><print new-system="yes"/><clef><sign>G</sign></clef><note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="4"><print new-system="yes"/><note><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration></note></measure>
</part></score-partwise>""", encoding="utf-8")
        voices, _ = recognize.assign_voices(recognize.read_score(path)["lines"])
        self.assertEqual([note["start"] for note in voices["P"]], [0, 4, 8, 12])
        self.assertEqual(voices["S"], [])
        choir = folder / "choir.musicxml"
        choir.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Voice</part-name></score-part>
<score-part id="P2"><part-name>Voice</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><print new-system="yes"/><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration></note></measure>
<measure number="3"><print new-system="yes"/><note><pitch><step>E</step><octave>5</octave></pitch><duration>4</duration></note></measure>
<measure number="4"><print new-system="yes"/><note><pitch><step>F</step><octave>5</octave></pitch><duration>4</duration></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions></attributes><note><rest/><duration>4</duration></note></measure>
<measure number="2"><print new-system="yes"/><note><rest/><duration>4</duration></note></measure>
<measure number="3"><print new-system="yes"/><clef><sign>F</sign></clef><note><pitch><step>E</step><octave>3</octave></pitch><duration>4</duration></note></measure>
<measure number="4"><print new-system="yes"/><note><pitch><step>D</step><octave>3</octave></pitch><duration>4</duration></note></measure></part>
</score-partwise>""", encoding="utf-8")
        choir_voices, _ = recognize.assign_voices(recognize.read_score(choir)["lines"])
        self.assertEqual([note["start"] for note in choir_voices["T"]], [8, 12])
        self.assertEqual(choir_voices["B"], [])
        gap = folder / "gap.musicxml"
        gap.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions></attributes><note><rest/><duration>4</duration></note></measure>
<measure number="2"><print new-system="yes"/><clef><sign>F</sign></clef><note><pitch><step>E</step><octave>3</octave></pitch><duration>4</duration></note></measure>
</part></score-partwise>""", encoding="utf-8")
        gap_line = recognize.read_score(gap)["lines"][0]["notes"]
        self.assertEqual([note["start"] for note in gap_line], [4])

    def test_treble_staff_below_the_bass_is_not_a_voice(self):
        def line(clef, pitch, layer=0):
            return {"name": "Voice", "clef": clef, "layer": layer, "notes": [{"start": 0, "duration": 1, "pitch": pitch}]}
        voices, extra = recognize.assign_voices([
            line("G", 72),
            line("G", 67),
            line("F", 59),
            line("F", 48, 1),
            line("G", 70),
        ])
        self.assertEqual([note["pitch"] for note in voices["S"]], [72])
        self.assertEqual([note["pitch"] for note in voices["A"]], [67])
        self.assertEqual([note["pitch"] for note in voices["T"]], [59])
        self.assertEqual([note["pitch"] for note in voices["B"]], [48])
        self.assertIn("其他 1 条", extra)

    def test_a_plain_eighth_before_a_written_triplet_keeps_its_length(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "beside.musicxml"
        path.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>12</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign></clef></attributes>
<note><pitch><step>D</step><octave>3</octave></pitch><duration>6</duration><type>eighth</type></note>
<note><pitch><step>D</step><octave>6</octave></pitch><duration>4</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification></note>
<note><pitch><step>A</step><octave>5</octave></pitch><duration>4</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification></note>
<note><pitch><step>F</step><octave>4</octave></pitch><duration>6</duration><type>eighth</type></note>
</measure></part></score-partwise>""", encoding="utf-8")
        notes = recognize.read_score(path)["lines"][0]["notes"]
        self.assertEqual([round(note["duration"], 4) for note in notes], [0.5, 0.3333, 0.3333, 0.5])
        self.assertEqual([round(note["start"], 4) for note in notes], [0, 0.5, 0.8333, 1.1667])

    def test_a_bracketed_triplet_keeps_the_quarter_before_it(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "bracket.musicxml"
        path.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>12</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign></clef></attributes>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>12</duration><type>quarter</type></note>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><notations><tuplet type="start"/></notations></note>
<note><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification></note>
<note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><notations><tuplet type="stop"/></notations></note>
</measure></part></score-partwise>""", encoding="utf-8")
        notes = recognize.read_score(path)["lines"][0]["notes"]
        self.assertEqual([round(note["duration"], 4) for note in notes], [1, 0.3333, 0.3333, 0.3333])
        self.assertEqual([round(note["start"], 4) for note in notes], [0, 1, 1.3333, 1.6667])

    def test_two_eighths_keep_their_length_before_a_triplet(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "measure10.musicxml"
        path.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Voice</part-name></score-part>
<score-part id="P2"><part-name>Piano</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>6</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign></clef></attributes>
<note><pitch><step>F</step><alter>1</alter><octave>4</octave></pitch><duration>3</duration><type>eighth</type></note>
<note><pitch><step>F</step><alter>1</alter><octave>4</octave></pitch><duration>3</duration><type>eighth</type></note>
<note><pitch><step>F</step><alter>1</alter><octave>4</octave></pitch><duration>2</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><notations><tuplet type="start"/></notations></note>
<note><pitch><step>E</step><octave>4</octave></pitch><duration>2</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification></note>
<note><pitch><step>D</step><octave>4</octave></pitch><duration>2</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification><notations><tuplet type="stop"/></notations></note>
<note><pitch><step>F</step><alter>1</alter><octave>4</octave></pitch><duration>3</duration><type>eighth</type></note>
<note><pitch><step>F</step><alter>1</alter><octave>4</octave></pitch><duration>3</duration><type>eighth</type></note>
<note><pitch><step>D</step><octave>4</octave></pitch><duration>6</duration><type>quarter</type></note>
</measure><measure number="2"><note><rest/><duration>24</duration></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>6</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>D</step><octave>6</octave></pitch><duration>24</duration><type>whole</type></note>
</measure><measure number="2"><note><pitch><step>C</step><octave>4</octave></pitch><duration>6</duration><type>quarter</type></note></measure></part>
</score-partwise>""", encoding="utf-8")
        voices, _ = recognize.assign_voices(recognize.read_score(path)["lines"])
        tenor = voices["S"]
        self.assertEqual([round(note["duration"], 4) for note in tenor], [0.5, 0.5, 0.3333, 0.3333, 0.3333, 0.5, 0.5, 1])
        self.assertEqual([round(note["start"], 4) for note in tenor[:5]], [0, 0.5, 1, 1.3333, 1.6667])
        self.assertEqual(voices["P"][1]["start"], 4)

    def test_a_plain_eighth_is_not_the_first_note_of_a_quarter_triplet(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "one-mark.musicxml"
        path.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>6</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>F</step><alter>1</alter><octave>4</octave></pitch><duration>3</duration><type>eighth</type></note>
<note><pitch><step>F</step><alter>1</alter><octave>4</octave></pitch><duration>3</duration><type>eighth</type></note>
<note><pitch><step>E</step><octave>4</octave></pitch><duration>2</duration><type>eighth</type><time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification></note>
</measure></part></score-partwise>""", encoding="utf-8")
        notes = recognize.read_score(path)["lines"][0]["notes"]
        self.assertEqual([round(note["duration"], 4) for note in notes], [0.5, 0.5, 0.3333])
        self.assertEqual([round(note["start"], 4) for note in notes], [0, 0.5, 1])


class DivisiTests(unittest.TestCase):
    def test_one_chord_and_a_three_note_stack_stay_inside_the_part(self):
        folder = Path(tempfile.mkdtemp())
        once = folder / "once.musicxml"
        once.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Voice</part-name></score-part>
<score-part id="P2"><part-name>Voice</part-name></score-part>
<score-part id="P3"><part-name>Voice</part-name></score-part>
<score-part id="P4"><part-name>Voice</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration></note>
<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><note><pitch><step>B</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
<part id="P3"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
<part id="P4"><measure number="1"><attributes><divisions>1</divisions><clef><sign>F</sign></clef></attributes>
<note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><note><pitch><step>D</step><octave>3</octave></pitch><duration>4</duration></note></measure></part>
</score-partwise>""", encoding="utf-8")
        once_voices, extra = recognize.assign_voices(recognize.read_score(once)["lines"])
        self.assertEqual([note["pitch"] for note in once_voices["S"]], [72, 74])
        self.assertEqual([note["pitch"] for note in once_voices["S2"]], [64, 74])
        self.assertEqual([note["pitch"] for note in once_voices["A"]], [69, 71])
        self.assertNotIn("还有", extra)
        stack = folder / "stack.musicxml"
        stack.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Soprano</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration></note>
<note><chord/><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration></note>
<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note>
<note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration></note></measure></part>
</score-partwise>""", encoding="utf-8")
        stack_voices, _ = recognize.assign_voices(recognize.read_score(stack)["lines"])
        self.assertEqual([note["pitch"] for note in stack_voices["S"]], [72, 74])
        self.assertEqual([note["pitch"] for note in stack_voices["S2"]], [64, 67, 74])
        self.assertGreater(len(score.render_wav(stack_voices["S2"], 72)), 44)
        self.assertEqual(stack_voices["A"], [])

    def test_a_written_second_voice_and_its_name_stay_with_the_part(self):
        folder = Path(tempfile.mkdtemp())
        named = folder / "named.musicxml"
        named.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Soprano</part-name></score-part>
<score-part id="P2"><part-name>Soprano II</part-name></score-part>
<score-part id="P3"><part-name>Alto</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
<part id="P3"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
</score-partwise>""", encoding="utf-8")
        named_voices, extra = recognize.assign_voices(recognize.read_score(named)["lines"])
        self.assertEqual([note["pitch"] for note in named_voices["S"]], [72])
        self.assertEqual([note["pitch"] for note in named_voices["S2"]], [67])
        self.assertEqual([note["pitch"] for note in named_voices["A"]], [64])
        self.assertEqual(extra, "")
        chinese = folder / "chinese.musicxml"
        chinese.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>女高</part-name></score-part>
<score-part id="P2"><part-name>女高二</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>B</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
</score-partwise>""", encoding="utf-8")
        chinese_voices, _ = recognize.assign_voices(recognize.read_score(chinese)["lines"])
        self.assertEqual([note["pitch"] for note in chinese_voices["S"]], [74])
        self.assertEqual([note["pitch"] for note in chinese_voices["S2"]], [71])
        self.assertEqual(chinese_voices["A"], [])
        layers = folder / "layers.musicxml"
        layers.write_text("""<score-partwise><part-list>
<score-part id="P1"><part-name>Soprano</part-name></score-part>
<score-part id="P2"><part-name>Alto</part-name></score-part>
</part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration><voice>1</voice></note>
<backup><duration>4</duration></backup>
<note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><voice>2</voice></note></measure>
<measure number="2"><note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration><voice>1</voice></note>
<backup><duration>4</duration></backup>
<note><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration><voice>2</voice></note></measure></part>
<part id="P2"><measure number="1"><attributes><divisions>1</divisions><clef><sign>G</sign></clef></attributes>
<note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><note><pitch><step>B</step><octave>4</octave></pitch><duration>4</duration></note></measure></part>
</score-partwise>""", encoding="utf-8")
        layer_voices, layer_extra = recognize.assign_voices(recognize.read_score(layers)["lines"])
        self.assertEqual([note["pitch"] for note in layer_voices["S"]], [72, 74])
        self.assertEqual([note["pitch"] for note in layer_voices["S2"]], [64, 65])
        self.assertEqual([note["pitch"] for note in layer_voices["A"]], [69, 71])
        self.assertEqual(layer_extra, "")

    def test_divided_parts_come_back_from_the_exported_score(self):
        piece = score.new_piece("d" * 32, "分声.pdf", 1)
        piece["bpm"] = 80
        piece["beats_per_bar"] = 4
        piece["voices"]["S"] = [{"start": 0, "duration": 1, "pitch": 72}]
        piece["voices"]["S2"] = [
            {"start": 0, "duration": 1, "pitch": 64},
            {"start": 0, "duration": 1, "pitch": 67},
        ]
        piece["voices"]["A"] = [{"start": 0, "duration": 1, "pitch": 60}]
        path = Path(tempfile.mkdtemp()) / "divided.musicxml"
        written = score.write_musicxml(piece)
        path.write_text(written, encoding="utf-8")
        self.assertIn("女高一", written)
        self.assertIn("女高二", written)
        voices, bpm, beats, extra, tempo_written = recognize.voices_from_file(path)
        self.assertEqual((bpm, beats, extra, tempo_written), (80, 4, "", True))
        self.assertEqual(voices["S"], piece["voices"]["S"])
        self.assertEqual(voices["S2"], piece["voices"]["S2"])
        self.assertEqual(voices["A"], piece["voices"]["A"])
        self.assertEqual(voices["A2"], [])

    def test_the_second_voice_sings_the_unison_and_tenor_stays_sounding(self):
        piece = score.new_piece("e" * 32, "男高.pdf", 1)
        piece["voices"]["T"] = [{"start": 0, "duration": 1, "pitch": 60}, {"start": 1, "duration": 1, "pitch": 62}]
        piece["voices"]["T2"] = [{"start": 0, "duration": 1, "pitch": 55}]
        written = score.write_musicxml(piece)
        self.assertIn("<clef-octave-change>-1</clef-octave-change>", written)
        path = Path(tempfile.mkdtemp()) / "tenor.musicxml"
        path.write_text(written, encoding="utf-8")
        voices, bpm, beats, extra, tempo_written = recognize.voices_from_file(path)
        self.assertEqual((bpm, beats, extra, tempo_written), (72, 4, "", True))
        self.assertEqual(voices["T"], piece["voices"]["T"])
        self.assertEqual(voices["T2"], [
            {"start": 0, "duration": 1, "pitch": 55},
            {"start": 1, "duration": 1, "pitch": 62},
        ])
        old = score.new_piece("f" * 32, "旧谱.pdf", 1)
        del old["tenor_sounding"]
        old["voices"]["S"] = [
            {"start": 0, "duration": 1, "pitch": 72},
            {"start": 1, "duration": 1, "pitch": 74},
        ]
        old["voices"]["S2"] = [{"start": 0, "duration": 1, "pitch": 64}]
        old["voices"]["T"] = [{"start": 0, "duration": 1, "pitch": 67}]
        score.prepare_piece(old)
        self.assertEqual([note["pitch"] for note in old["voices"]["S2"]], [64, 74])
        self.assertEqual(old["voices"]["T"][0]["pitch"], 55)
        self.assertTrue(old["tenor_sounding"])
        score.prepare_piece(old)
        self.assertEqual([note["pitch"] for note in old["voices"]["S2"]], [64, 74])
        self.assertEqual(old["voices"]["T"][0]["pitch"], 55)


class MusicXmlTests(unittest.TestCase):
    def test_exported_score_comes_back_with_the_written_notes(self):
        piece = score.new_piece("b" * 32, "试唱.pdf", 1)
        piece["bpm"] = 80
        piece["beats_per_bar"] = 6
        piece["voices"]["S"] = [
            {"start": 0, "duration": 4.5, "pitch": 67},
            {"start": 4.5, "duration": 0.6667, "pitch": 65},
        ]
        piece["voices"]["B"] = [
            {"start": 0, "duration": 3.5, "pitch": 55},
            {"start": 3.5, "duration": 0.25, "pitch": 55},
            {"start": 3.75, "duration": 0.25, "pitch": 53},
            {"start": 4.0, "duration": 0.5, "pitch": 52},
        ]
        piece["voices"]["P"] = [
            {"start": 0, "duration": 6, "pitch": 39},
            {"start": 0, "duration": 6, "pitch": 51},
            {"start": 2, "duration": 1, "pitch": 43},
        ]
        path = Path(tempfile.mkdtemp()) / "out.musicxml"
        path.write_text(score.write_musicxml(piece), encoding="utf-8")
        voices, bpm, beats, extra, tempo_written = recognize.voices_from_file(path)
        self.assertEqual((bpm, beats, extra, tempo_written), (80, 6, "", True))
        self.assertEqual(voices["S"], piece["voices"]["S"])
        self.assertEqual(voices["B"], piece["voices"]["B"])
        self.assertEqual(voices["P"], piece["voices"]["P"])
        self.assertEqual(voices["A"], [])
        self.assertEqual(voices["S2"], [])

    def test_the_project_keeps_the_electronic_score_it_just_wrote(self):
        folder = Path(tempfile.mkdtemp())
        piece = score.new_piece("c" * 32, "试唱.pdf", 1)
        piece["beats_per_bar"] = 4
        piece["voices"]["S"] = [{"start": 0, "duration": 1, "pitch": 72}, {"start": 1, "duration": 1.5, "pitch": 74}]
        piece["report"] = "homr：女高 2 个音。"
        app.adopt_electronic_score(folder, piece)
        self.assertIn("已在这里生成电子谱", piece["report"])
        self.assertEqual(piece["voices"]["S"], [
            {"start": 0.0, "duration": 1.0, "pitch": 72},
            {"start": 1.0, "duration": 1.5, "pitch": 74},
        ])
        self.assertIn("女高", (folder / "score.musicxml").read_text(encoding="utf-8"))


class ApiTests(unittest.TestCase):
    def test_pdf_import_voice_and_wav(self):
        client = TestClient(app.app)
        with app.pymupdf.open() as document:
            document.new_page().insert_text((72, 72), "fenbu")
            pdf = document.tobytes()
        created = client.post("/api/pieces", files=[("files", ("scan.pdf", pdf, "application/pdf"))])
        self.assertEqual(created.status_code, 200, created.text)
        piece = created.json()
        self.assertEqual(piece["pages"], 1)
        saved = client.put(f"/api/pieces/{piece['id']}/voice", json={
            "voice": "S", "bpm": 80, "beats_per_bar": 4, "bars": 2,
            "notes": [{"start": 0, "duration": 1, "pitch": 72}, {"start": 2, "duration": 2, "pitch": 67}]
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        page = client.get(f"/api/pieces/{piece['id']}/pages/1")
        self.assertEqual(page.content[:8], b"\x89PNG\r\n\x1a\n")
        wav = client.get(f"/api/pieces/{piece['id']}/voices/S.wav")
        self.assertEqual(wav.content[:4], b"RIFF")
        exported = client.get(f"/api/pieces/{piece['id']}/score.musicxml")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn(b"\xe5\xa5\xb3\xe9\xab\x98", exported.content)
        brought = client.post(
            f"/api/pieces/{piece['id']}/score",
            files=[("file", ("edited.musicxml", exported.content, "application/vnd.recordare.musicxml+xml"))],
        )
        self.assertEqual(brought.status_code, 200, brought.text)
        self.assertEqual(brought.json()["voices"]["S"], [
            {"start": 0.0, "duration": 1.0, "pitch": 72},
            {"start": 2.0, "duration": 2.0, "pitch": 67},
        ])
        self.assertIn("已导入电子谱", brought.json()["report"])
        stored = Path(os.environ["FENBU_DATA"]) / piece["id"] / "score.musicxml"
        self.assertTrue(stored.is_file())
        self.assertIn("女高", stored.read_text(encoding="utf-8"))
        self.assertTrue((Path(os.environ["FENBU_DATA"]) / piece["id"] / "page-01.png").is_file())

    def test_open_in_musescore_writes_the_score_and_starts_the_program(self):
        client = TestClient(app.app)
        with app.pymupdf.open() as document:
            document.new_page().insert_text((72, 72), "fenbu")
            pdf = document.tobytes()
        created = client.post("/api/pieces", files=[("files", ("scan.pdf", pdf, "application/pdf"))])
        piece = created.json()
        saved = client.put(f"/api/pieces/{piece['id']}/voice", json={
            "voice": "S", "bpm": 80, "beats_per_bar": 4, "bars": 1,
            "notes": [{"start": 0, "duration": 1, "pitch": 72}],
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        program = Path(tempfile.mkdtemp()) / "MuseScore4.exe"
        program.write_bytes(b"")
        with unittest.mock.patch.dict(os.environ, {"MUSESCORE": str(program)}), \
             unittest.mock.patch("app.subprocess.Popen") as popen:
            opened = client.post(f"/api/pieces/{piece['id']}/musescore")
        self.assertEqual(opened.status_code, 200, opened.text)
        stored = Path(os.environ["FENBU_DATA"]) / piece["id"] / "score.musicxml"
        self.assertIn("女高", stored.read_text(encoding="utf-8"))
        command = popen.call_args.args[0]
        self.assertEqual(command, [str(program), str(stored)])

    def test_a_missing_musescore_explains_where_to_point(self):
        client = TestClient(app.app)
        with app.pymupdf.open() as document:
            document.new_page().insert_text((72, 72), "fenbu")
            pdf = document.tobytes()
        created = client.post("/api/pieces", files=[("files", ("scan.pdf", pdf, "application/pdf"))])
        piece = created.json()
        client.put(f"/api/pieces/{piece['id']}/voice", json={
            "voice": "S", "bpm": 72, "beats_per_bar": 4, "bars": 1,
            "notes": [{"start": 0, "duration": 1, "pitch": 60}],
        })
        missing = Path(tempfile.mkdtemp()) / "missing.exe"
        with unittest.mock.patch.dict(os.environ, {"MUSESCORE": str(missing)}):
            opened = client.post(f"/api/pieces/{piece['id']}/musescore")
        self.assertEqual(opened.status_code, 409, opened.text)
        self.assertIn("MUSESCORE", opened.json()["detail"])

    def test_bpm_thirty_renders_and_delete_removes_the_piece(self):
        client = TestClient(app.app)
        with app.pymupdf.open() as document:
            document.new_page().insert_text((72, 72), "slow")
            pdf = document.tobytes()
        created = client.post("/api/pieces", files=[("files", ("slow.pdf", pdf, "application/pdf"))])
        self.assertEqual(created.status_code, 200, created.text)
        piece = created.json()
        saved = client.put(f"/api/pieces/{piece['id']}/voice", json={
            "voice": "S", "bpm": 72, "beats_per_bar": 4, "bars": 2,
            "notes": [{"start": 0, "duration": 1, "pitch": 72}, {"start": 1, "duration": 1, "pitch": 74}],
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        slow = client.get(f"/api/pieces/{piece['id']}/voices/S.wav?bpm=30")
        fast = client.get(f"/api/pieces/{piece['id']}/voices/S.wav?bpm=72")
        self.assertEqual(slow.status_code, 200, slow.text)
        self.assertEqual(fast.status_code, 200, fast.text)
        self.assertGreater(len(slow.content), len(fast.content))
        self.assertTrue(slow.content.startswith(b"RIFF"))
        app.JOBS[piece["id"]] = object()
        busy = client.delete(f"/api/pieces/{piece['id']}")
        self.assertEqual(busy.status_code, 409, busy.text)
        app.JOBS.pop(piece["id"], None)
        removed = client.delete(f"/api/pieces/{piece['id']}")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(client.get(f"/api/pieces/{piece['id']}").status_code, 404)
        listing = client.get("/api/pieces")
        self.assertNotIn(piece["id"], [row["id"] for row in listing.json()])


class TuneTests(unittest.TestCase):
    def test_decimals_and_fractions_land_on_the_same_slices(self):
        self.assertEqual(score.slices_from_input(0.333), 16)
        self.assertEqual(score.slices_from_input("0.3333333"), 16)
        self.assertEqual(score.slices_from_input("1/3"), 16)
        self.assertEqual(score.slices_from_input("0.33"), 16)
        self.assertEqual(score.slices_from_input(1.5), 72)
        self.assertEqual(score.slices_from_input("2/3"), 32)
        self.assertEqual(score.slices_from_input(0.667), 32)
        self.assertEqual(score.slices_from_input("0.4"), 19)
        self.assertEqual(16 + 16 + 16, 48)

    def test_three_triplets_fill_one_beat_and_the_other_voice_stays(self):
        piece = score.new_piece("1" * 32, "三连.pdf", 1)
        piece["voices"]["S"] = [
            {"start": 0, "duration": 1, "pitch": 72},
            {"start": 1, "duration": 1, "pitch": 74},
        ]
        piece["voices"]["S2"] = [{"start": 0, "duration": 1, "pitch": 67}]
        score.prepare_piece(piece)
        self.assertEqual([note["pitch"] for note in piece["voices"]["S2"]], [67, 74])
        score.replace_voice(piece, "S2", [
            {"start": 0, "duration": "1/3", "pitch": 64},
            {"start": 0.333, "duration": 0.3333333, "pitch": 65},
            {"start": "2/3", "duration": "1/3", "pitch": 66},
        ])
        self.assertEqual([note["pitch"] for note in piece["voices"]["S"]], [72, 74])
        slices = [score.slices_from_input(note["duration"]) for note in piece["voices"]["S2"]]
        self.assertEqual(slices, [16, 16, 16])
        self.assertEqual(sum(slices), 48)
        self.assertEqual([score.slices_from_input(note["start"]) for note in piece["voices"]["S2"]], [0, 16, 32])
        score.prepare_piece(piece)
        self.assertEqual([note["pitch"] for note in piece["voices"]["S2"]], [64, 65, 66])
        self.assertTrue(piece["tuned"])

    def test_one_voice_cannot_overlap_itself_but_divisi_can_share_a_beat(self):
        piece = score.new_piece("2" * 32, "分声.pdf", 1)
        piece["voices"]["S"] = [{"start": 0, "duration": 1, "pitch": 72}]
        with self.assertRaises(ValueError):
            score.replace_voice(piece, "S", [
                {"start": 0, "duration": 1, "pitch": 72},
                {"start": 0.5, "duration": 1, "pitch": 74},
            ])
        score.replace_voice(piece, "S2", [{"start": 0, "duration": 1, "pitch": 64}])
        self.assertEqual(piece["voices"]["S"][0]["pitch"], 72)
        self.assertEqual(piece["voices"]["S2"][0]["pitch"], 64)

    def test_notes_already_overlapping_can_still_be_edited(self):
        piece = score.new_piece("5" * 32, "重叠.pdf", 1)
        piece["voices"]["S"] = [
            {"start": 0, "duration": 2, "pitch": 72},
            {"start": 1, "duration": 1, "pitch": 74},
            {"start": 4, "duration": 1, "pitch": 76},
        ]
        score.replace_voice(piece, "S", [
            {"start": 0, "duration": 2, "pitch": 73},
            {"start": 1, "duration": 1, "pitch": 74},
            {"start": 4, "duration": 1, "pitch": 77},
            {"start": 6, "duration": 1, "pitch": 60},
        ])
        self.assertEqual([note["pitch"] for note in piece["voices"]["S"]], [73, 74, 77, 60])
        with self.assertRaises(ValueError):
            score.replace_voice(piece, "S", [
                {"start": 0, "duration": 2, "pitch": 73},
                {"start": 1, "duration": 1, "pitch": 74},
                {"start": 4, "duration": 1, "pitch": 77},
                {"start": 4.5, "duration": 1, "pitch": 79},
                {"start": 6, "duration": 1, "pitch": 60},
            ])

    def test_piano_keeps_a_low_chord(self):
        piece = score.new_piece("3" * 32, "钢琴.pdf", 1)
        score.replace_voice(piece, "P", [
            {"start": 0, "duration": 1.5, "pitch": 33},
            {"start": 0, "duration": 1.5, "pitch": 48},
        ])
        self.assertEqual(sorted(note["pitch"] for note in piece["voices"]["P"]), [33, 48])
        self.assertEqual(score.slices_from_input(piece["voices"]["P"][0]["duration"]), 72)

    def test_saved_divisi_is_not_filled_again_on_read(self):
        piece = score.new_piece("4" * 32, "再读.pdf", 1)
        piece["voices"]["S"] = [
            {"start": 0, "duration": 1, "pitch": 72},
            {"start": 1, "duration": 1, "pitch": 74},
        ]
        piece["voices"]["S2"] = [{"start": 0, "duration": 1, "pitch": 67}]
        folder = app.DATA / piece["id"]
        folder.mkdir()
        app.write_piece(folder, piece)
        client = TestClient(app.app)
        saved = client.put(
            f"/api/pieces/{piece['id']}/voice",
            json={"voice": "S2", "notes": [{"start": 0, "duration": 1, "pitch": 64}]},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        again = client.get(f"/api/pieces/{piece['id']}")
        self.assertEqual(again.status_code, 200, again.text)
        body = again.json()
        self.assertEqual([note["pitch"] for note in body["voices"]["S"]], [72, 74])
        self.assertEqual(body["voices"]["S2"], [{"start": 0.0, "duration": 1.0, "pitch": 64}])
        self.assertTrue(body["tuned"])


if __name__ == "__main__":
    unittest.main()

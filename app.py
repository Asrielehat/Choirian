"""分声：把扫描合唱谱记成四个声部，并导出各自的钢琴音频。"""
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

import pymupdf
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import recognize
import score

mimetypes.add_type("font/woff2", ".woff2")
ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("FENBU_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
LOCK = threading.Lock()
JOBS = {}
app = FastAPI(title="fenbu")


def piece_dir(piece_id):
    if not re.fullmatch(r"[a-f0-9]{32}", piece_id):
        raise HTTPException(400, "曲目编号无效")
    folder = DATA / piece_id
    if not (folder / "piece.json").is_file():
        raise HTTPException(404, "曲目不存在")
    return folder


def read_piece(folder):
    return score.prepare_piece(json.loads((folder / "piece.json").read_text(encoding="utf-8")))


def write_piece(folder, piece):
    piece["updated"] = time.time()
    target = folder / "piece.json"
    temporary = folder / "piece.tmp"
    temporary.write_text(json.dumps(piece, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def save_page(document, index, folder):
    page = document[index]
    scale = min(2.4, 1800 / max(page.rect.width, page.rect.height))
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    pixmap.save(folder / f"page-{index + 1:02d}.png")


def store_pdf(raw, name):
    if len(raw) > 80_000_000 or not raw.startswith(b"%PDF-"):
        raise HTTPException(400, "请上传 80 MB 以内的 PDF")
    try:
        document = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as error:
        raise HTTPException(400, f"PDF 打不开：{error}") from None
    if document.needs_pass or not 1 <= len(document) <= 40:
        document.close()
        raise HTTPException(400, "PDF 需未加密，且在 1–40 页")
    piece_id = uuid.uuid4().hex
    folder = DATA / piece_id
    folder.mkdir()
    try:
        for index in range(len(document)):
            save_page(document, index, folder)
        pages = len(document)
    finally:
        document.close()
    (folder / "source.pdf").write_bytes(raw)
    piece = score.new_piece(piece_id, name, pages)
    write_piece(folder, piece)
    return piece


def store_images(files):
    if not 1 <= len(files) <= 40:
        raise HTTPException(400, "一次导入 1–40 张图片")
    document = pymupdf.open()
    try:
        for upload in files:
            raw = upload.file.read(20_000_001)
            if len(raw) > 20_000_000:
                raise HTTPException(400, "单张图片不能超过 20 MB")
            try:
                image = pymupdf.open(stream=raw, filetype="png" if raw.startswith(b"\x89PNG") else "jpeg")
                rectangle = image[0].rect
                page = document.new_page(width=rectangle.width, height=rectangle.height)
                page.insert_image(rectangle, stream=raw)
                image.close()
            except Exception:
                raise HTTPException(400, "图片需要是 PNG 或 JPEG") from None
        piece_id = uuid.uuid4().hex
        folder = DATA / piece_id
        folder.mkdir()
        for index in range(len(document)):
            save_page(document, index, folder)
        piece = score.new_piece(piece_id, files[0].filename or "扫描谱", len(document))
        write_piece(folder, piece)
        return piece
    finally:
        document.close()


@app.get("/")
def home():
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/app.js")
def javascript():
    return FileResponse(ROOT / "app.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/style.css")
def stylesheet():
    return FileResponse(ROOT / "style.css", media_type="text/css", headers={"Cache-Control": "no-cache"})


@app.get("/tune")
def tune_page():
    return FileResponse(ROOT / "tune.html", headers={"Cache-Control": "no-cache"})


@app.get("/tune.js")
def tune_javascript():
    return FileResponse(ROOT / "tune.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


app.mount("/fonts", StaticFiles(directory=ROOT / "fonts"), name="fonts")


@app.get("/api/pieces")
def list_pieces():
    rows = []
    for path in DATA.glob("*/piece.json"):
        piece = read_piece(path.parent)
        rows.append({
            "id": piece["id"],
            "name": piece["name"],
            "pages": piece["pages"],
            "updated": piece.get("updated", 0),
            "counts": {voice: len((piece.get("voices") or {}).get(voice) or []) for voice in score.PARTS},
        })
    return sorted(rows, key=lambda row: row["updated"], reverse=True)


@app.post("/api/pieces")
def create_piece(files: list[UploadFile] = File(...)):
    if len(files) == 1 and (files[0].filename or "").lower().endswith(".pdf"):
        return store_pdf(files[0].file.read(80_000_001), files[0].filename or "score.pdf")
    return store_images(files)


@app.get("/api/pieces/{piece_id}")
def get_piece(piece_id: str):
    return read_piece(piece_dir(piece_id))


@app.delete("/api/pieces/{piece_id}")
def delete_piece(piece_id: str):
    folder = piece_dir(piece_id)
    with LOCK:
        if piece_id in JOBS:
            raise HTTPException(409, "正在识别，请等这一次结束")
        try:
            shutil.rmtree(folder)
        except OSError:
            raise HTTPException(409, "这份谱正在使用，稍后再删") from None
    return {"ok": True}


@app.get("/api/pieces/{piece_id}/pages/{number}")
def page_image(piece_id: str, number: int):
    folder = piece_dir(piece_id)
    path = folder / f"page-{number:02d}.png"
    if not path.is_file():
        raise HTTPException(404, "这一页不存在")
    return FileResponse(path, media_type="image/png")


class VoiceBody(BaseModel):
    voice: str
    notes: list[dict]
    bpm: int = 72
    beats_per_bar: int = 4
    bars: int = 8


@app.put("/api/pieces/{piece_id}/voice")
def save_voice(piece_id: str, body: VoiceBody):
    folder = piece_dir(piece_id)
    piece = read_piece(folder)
    try:
        update = score.replace_voice(piece, body.voice, body.notes)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    write_piece(folder, update)
    return update


def wav_response(piece, voice, bpm=None):
    notes = piece["voices"].get(voice) or []
    tempo = piece["bpm"] if bpm is None else bpm
    try:
        raw = score.render_wav(notes, tempo)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    headers = {
        "Content-Disposition": f'attachment; filename="{voice}.wav"',
        "Cache-Control": "no-store",
    }
    return Response(raw, media_type="audio/wav", headers=headers)


@app.get("/api/pieces/{piece_id}/voices/{voice}.wav")
def voice_wav(piece_id: str, voice: str, bpm: int | None = None):
    if voice not in score.PARTS:
        raise HTTPException(400, "声部无效")
    if bpm is not None and not 24 <= bpm <= 220:
        raise HTTPException(400, "速度超出范围")
    return wav_response(read_piece(piece_dir(piece_id)), voice, bpm)


def ensure_pdf(folder, piece):
    source = folder / "source.pdf"
    if source.is_file():
        return source
    document = pymupdf.open()
    try:
        for number in range(1, piece["pages"] + 1):
            image = folder / f"page-{number:02d}.png"
            pixmap = pymupdf.Pixmap(str(image))
            page = document.new_page(width=pixmap.width, height=pixmap.height)
            page.insert_image(page.rect, pixmap=pixmap)
        target = folder / "pages.pdf"
        document.save(target)
        return target
    finally:
        document.close()


def adopt_electronic_score(folder, piece):
    """Write the sounding parts into this project, then play exactly that file."""
    path = folder / "score.musicxml"
    path.write_text(score.write_musicxml(piece), encoding="utf-8")
    voices, bpm, beats, extra, tempo_written = recognize.voices_from_file(path)
    previous = piece.get("report") or ""
    note = "已在这里生成电子谱，并按它出声。"
    if note not in previous:
        previous = f"{previous} {note}".strip()
    if extra and extra not in previous:
        previous += extra
    score.apply_recognition(piece, voices, bpm if tempo_written else piece["bpm"], beats, previous)
    return piece


def report_progress(folder, done, total, message):
    with LOCK:
        current = read_piece(folder)
        if current.get("job", {}).get("status") != "running":
            return
        current["job"] = {"status": "running", "message": message, "done": done, "total": total}
        write_piece(folder, current)


def recognize_worker(piece_id):
    folder = piece_dir(piece_id)
    try:
        piece = read_piece(folder)
        result = recognize.recognize_piece(
            folder,
            piece,
            ensure_pdf(folder, piece),
            timeout=max(1200, piece["pages"] * 300),
            progress=lambda done, total, message: report_progress(folder, done, total, message),
        )
        with LOCK:
            piece = read_piece(folder)
            score.apply_recognition(piece, result["voices"], result["bpm"], result["beats_per_bar"], result["report"])
            adopt_electronic_score(folder, piece)
            write_piece(folder, piece)
    except Exception as error:
        with LOCK:
            piece = read_piece(folder)
            piece["job"] = {"status": "failed", "message": str(error)[:500]}
            write_piece(folder, piece)
    finally:
        JOBS.pop(piece_id, None)


@app.post("/api/pieces/{piece_id}/recognize", status_code=202)
def start_recognize(piece_id: str):
    folder = piece_dir(piece_id)
    with LOCK:
        if piece_id in JOBS:
            raise HTTPException(409, "正在识别，请等这一次结束")
        engine = recognize.engine_name()
        if not engine:
            raise HTTPException(409, "没有找到 homr，也没有找到 Audiveris。先运行 setup-homr.bat。")
        piece = read_piece(folder)
        piece["job"] = {"status": "running", "message": f"正在用 {engine} 读谱"}
        write_piece(folder, piece)
        thread = threading.Thread(target=recognize_worker, args=(piece_id,), daemon=True)
        JOBS[piece_id] = thread
        thread.start()
    return piece


@app.get("/api/pieces/{piece_id}/score.musicxml")
def export_score(piece_id: str):
    piece = read_piece(piece_dir(piece_id))
    try:
        raw = score.write_musicxml(piece).encode("utf-8")
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    filename = score.musicxml_name(piece["name"])
    disposition = f"attachment; filename=\"score.musicxml\"; filename*=UTF-8''{quote(filename)}"
    return Response(raw, media_type="application/vnd.recordare.musicxml+xml", headers={"Content-Disposition": disposition})


@app.post("/api/pieces/{piece_id}/score")
def import_score(piece_id: str, file: UploadFile = File(...)):
    folder = piece_dir(piece_id)
    raw = file.file.read(20_000_001)
    if len(raw) > 20_000_000:
        raise HTTPException(400, "乐谱文件不能超过 20 MB")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xml", ".musicxml", ".mxl"}:
        raise HTTPException(400, "请上传 MusicXML（.musicxml、.xml 或 .mxl）")
    target = folder / f"import{suffix}"
    target.write_bytes(raw)
    try:
        voices, bpm, beats, extra, tempo_written = recognize.voices_from_file(target)
    except (ValueError, ET.ParseError, zipfile.BadZipFile, KeyError) as error:
        raise HTTPException(400, f"这份电子谱读不了：{error}") from None
    finally:
        target.unlink(missing_ok=True)
    if not any(voices[part] for part in score.PARTS):
        raise HTTPException(400, "电子谱里没有读出音符")
    with LOCK:
        piece = read_piece(folder)
        if piece.get("job", {}).get("status") == "running":
            raise HTTPException(409, "正在识别，请等这一次结束")
        report = "已导入电子谱，并按它出声。"
        if extra:
            report += extra
        score.apply_recognition(piece, voices, bpm if tempo_written else piece["bpm"], beats, report)
        (folder / "score.musicxml").write_text(score.write_musicxml(piece), encoding="utf-8")
        write_piece(folder, piece)
    return piece


@app.get("/api/pieces/{piece_id}/parts.zip")
def parts_zip(piece_id: str, bpm: int | None = None):
    if bpm is not None and not 24 <= bpm <= 220:
        raise HTTPException(400, "速度超出范围")
    piece = read_piece(piece_dir(piece_id))
    try:
        raw = score.render_zip(piece, bpm)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return Response(raw, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="fenbu-parts.zip"'})

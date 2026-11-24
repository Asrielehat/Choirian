"""Download homr ONNX weights with a long read timeout and resume."""
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent / ".venv-homr" / "Lib" / "site-packages" / "homr"
OFFICIAL = "https://github.com/liebharc/homr/releases/download/onnx_checkpoints/"
MIRRORS = ("", "https://ghfast.top/", "https://gh-proxy.com/", "https://mirror.ghproxy.com/")
FILES = {
    "segnet_308-3296ccd40960f90ca6ab9c035cca945675d30a0f.zip": ROOT / "segmentation",
    "encoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.zip": ROOT / "transformer",
    "decoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.zip": ROOT / "transformer",
}


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        have = dest.stat().st_size if dest.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with requests.get(url, stream=True, timeout=(30, 120), headers=headers, allow_redirects=True) as response:
                if response.status_code == 416:
                    return
                response.raise_for_status()
                if response.status_code != 206:
                    have = 0
                print(f"GET {response.status_code} {url} at {have}", flush=True)
                with dest.open("ab" if response.status_code == 206 else "wb") as handle:
                    last = time.time()
                    for chunk in response.iter_content(1024 * 256):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        now = time.time()
                        if now - last > 8:
                            print(f"  {dest.name} {dest.stat().st_size / 1024 / 1024:.1f} MB", flush=True)
                            last = now
            print(f"  finished {dest.name} {dest.stat().st_size / 1024 / 1024:.1f} MB", flush=True)
            return
        except Exception as error:
            print(f"fail {attempt} {type(error).__name__}: {error}", flush=True)
            time.sleep(2)
    raise RuntimeError(url)


def fetch(name, folder):
    onnx = folder / name.replace(".zip", ".onnx")
    if onnx.is_file() and onnx.stat().st_size > 1_000_000:
        print("have", onnx.name, flush=True)
        return
    dest = folder / name
    last_error = None
    for prefix in MIRRORS:
        url = (prefix + OFFICIAL + name) if prefix else (OFFICIAL + name)
        if dest.exists():
            dest.unlink()
        try:
            download(url, dest)
            with zipfile.ZipFile(dest) as archive:
                print("zip entries", archive.namelist()[:6], flush=True)
                archive.extractall(folder)
            dest.unlink()
            print("extracted", name, flush=True)
            return
        except Exception as error:
            last_error = error
            print(f"mirror failed {url}: {error}", flush=True)
            if dest.exists():
                dest.unlink()
    raise SystemExit(f"could not fetch {name}: {last_error}")


if __name__ == "__main__":
    for filename, target in FILES.items():
        fetch(filename, target)
    print("done", flush=True)

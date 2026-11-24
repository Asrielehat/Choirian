@echo off
cd /d "%~dp0"
if not exist .venv py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv
.\.venv\Scripts\uv.exe python install 3.12
.\.venv\Scripts\uv.exe venv --python 3.12 .venv-homr
.\.venv\Scripts\uv.exe pip install --python .venv-homr homr
.\.venv-homr\Scripts\python.exe _fetch_homr_models.py

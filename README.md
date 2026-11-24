# Choirian

导入扫描的合唱 PDF 或照片，自动识别音符和节奏，生成女高、女低、男高、男低四条钢琴 WAV。

识别优先用开源项目 [homr](https://github.com/liebharc/homr)（AGPL-3.0）。它按页读图片，专门处理拍照和扫描谱。homr 读不出整份谱时，改用 Audiveris。Audiveris 若导出多个乐章，会留下音符最多的那一份，避免残缺乐章盖掉正谱。

## 启动

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

或双击 `start.bat`。打开 http://127.0.0.1:8770/ 。

homr 需要单独的 Python 3.12 环境。双击 `setup-homr.bat`，或：

```powershell
.\.venv\Scripts\python.exe -m pip install uv
.\.venv\Scripts\uv.exe python install 3.12
.\.venv\Scripts\uv.exe venv --python 3.12 .venv-homr
.\.venv\Scripts\uv.exe pip install --python .venv-homr homr
.\.venv-homr\Scripts\python.exe _fetch_homr_models.py
```

模型只下载一次，来自 homr 的 GitHub Release。官方下载器的读超时很短，国内网络容易中断，所以用 `_fetch_homr_models.py` 分段续传。想改用别的解释器时，把路径写到环境变量 `HOMR_PYTHON`。

Audiveris 作为后备。默认查找 `AUDIVERIS_CMD`，其次是项目里的 `tools/Audiveris/Audiveris.exe`，再其次是同级 ChoirLab 里的那一份。

支持未加密 PDF（最多 40 页、80 MB），以及 PNG / JPEG。曲目保存在 `data/`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

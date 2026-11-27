# Choirian

导入扫描的合唱 PDF 或照片，识别音符和节奏，按女高、女低、男高、男低生成钢琴 WAV。同一声部出现两个音时分成一声部和二声部；没分开的段落，两个声部一起唱。男高按实际音高低一个八度。谱里有钢琴时也会单独导出。

识别使用开源项目 [homr](https://github.com/liebharc/homr)（AGPL-3.0）。它按页读图片，专门处理拍照和扫描谱。合唱识谱还会认错，音高、节奏和一二声部都还有需要改进的地方。

## 改谱

改音符请用 [MuseScore](https://musescore.org)（推荐 MuseScore 4）。在 Choirian 里点「用 MuseScore 打开」，改完音高和节奏后，从 MuseScore 导出 MusicXML，再回到这里点「导入改过的谱」。程序不在默认位置时，把路径写到环境变量 `MUSESCORE`。

## 下载使用

Windows 直接下载 [Releases](https://github.com/Asrielehat/Choirian/releases) 里的 `Choirian-windows.zip`。解压到一个文件夹，双击 `打开Choirian.bat`（文件名乱了就双击 `OpenChoirian.bat`）。压缩包里已经带好 homr 和模型，不用另装 Python，也不用再运行 `setup-homr.bat`。

下面是从源码启动的办法。

## 听和导出

- 速度可以改到 24–168。播放和导出都按当前速度。
- 「休」拿掉一条声部。「独」可以同时点亮多条，全部点掉后回到原来的合奏。
- 可以导出单个声部、全部声部，以及一份 MusicXML。识别结果拿去改，优先用上面的 MuseScore，改过再导回来出声。

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

支持未加密 PDF（最多 40 页、80 MB），以及 PNG / JPEG。曲目保存在 `data/`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

# Build a folder a Windows user can unzip and double-click.
# Homr, its models, and Python are copied in. Nothing else is downloaded at runtime.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonBase = Join-Path $env:APPDATA "uv\python\cpython-3.12-windows-x86_64-none"
$homrSite = Join-Path $root ".venv-homr\Lib\site-packages"
if (-not (Test-Path (Join-Path $pythonBase "python.exe"))) {
  throw "Missing the Python 3.12 used by homr: $pythonBase"
}
if (-not (Test-Path (Join-Path $homrSite "homr"))) {
  throw "Missing .venv-homr. Run setup-homr.bat first."
}

$stage = Join-Path $env:TEMP "Choirian-windows"
$zipDir = Join-Path $root "dist"
$zip = Join-Path $zipDir "Choirian-windows.zip"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
New-Item -ItemType Directory -Path $zipDir -Force | Out-Null

$names = @(
  "app.py", "app.js", "index.html", "style.css", "tune.html", "tune.js",
  "recognize.py", "score.py", "homr_keep.py", "run.py",
  "打开Choirian.bat", "使用说明.txt", "README.md", "requirements.txt"
)
foreach ($name in $names) {
  Copy-Item (Join-Path $root $name) (Join-Path $stage $name)
}
New-Item -ItemType Directory -Path (Join-Path $stage "fonts") | Out-Null
& robocopy (Join-Path $root "fonts") (Join-Path $stage "fonts") /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Could not copy fonts ($LASTEXITCODE)" }

$runtime = Join-Path $stage "runtime"
& robocopy $pythonBase $runtime /E /XD include libs tcl /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Could not copy Python ($LASTEXITCODE)" }
& robocopy $homrSite (Join-Path $runtime "Lib\site-packages") /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Could not copy homr ($LASTEXITCODE)" }

$python = Join-Path $runtime "python.exe"
& $python -m pip install --disable-pip-version-check -r (Join-Path $root "requirements.txt") -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) {
  & $python -m pip install --disable-pip-version-check -r (Join-Path $root "requirements.txt")
  if ($LASTEXITCODE -ne 0) { throw "Could not install the app into the bundled Python" }
}

Push-Location $stage
try {
  & $python -c "import app, cv2, fastapi, homr, onnxruntime, pymupdf, recognize; assert recognize.homr_ready(), 'homr models missing'; print(recognize.homr_python())"
  if ($LASTEXITCODE -ne 0) { throw "The bundled app could not import homr" }
} finally {
  Pop-Location
}
if (Test-Path (Join-Path $stage "data")) { Remove-Item (Join-Path $stage "data") -Recurse -Force }
Get-ChildItem $runtime -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

if (Test-Path $zip) { Remove-Item $zip -Force }
tar -a -c -f $zip -C (Split-Path $stage -Parent) (Split-Path $stage -Leaf)
if ($LASTEXITCODE -ne 0) { throw "Could not zip the package" }
$bytes = (Get-Item $zip).Length
Write-Output "package $zip $bytes"

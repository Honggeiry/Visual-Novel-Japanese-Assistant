$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

python -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "正在安装所需图片库 Pillow..."
    python -m pip install -r vn_jp_tool\requirements.txt
}

python vn_jp_tool\app.py

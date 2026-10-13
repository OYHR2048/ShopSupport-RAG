# 在项目根目录运行。默认保持离线演示，不启动外部服务。
param([switch]$FreshDemo)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
if ($FreshDemo) {
    $demoStamp = Get-Date -Format 'yyyyMMddHHmmssfff'
    $env:DATABASE_URL = "sqlite+aiosqlite:///./data/local/demo-$demoStamp.db"
}
& ".\.venv\Scripts\python.exe" -m uvicorn app.asgi:app --host 127.0.0.1 --port 8000

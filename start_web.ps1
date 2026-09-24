$ErrorActionPreference = "Stop"

$port = 8000
$url = "http://127.0.0.1:$port"
Set-Location -LiteralPath $PSScriptRoot

$listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 3
        if ($health.ok) {
            Write-Host "投研观察池已在运行：$url"
            return
        }
    }
    catch {
    }

    throw "端口 $port 已被其他程序占用。请先释放该端口，或修改 start_web.ps1 中的 port。"
}

$python = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
if ($python) {
    Write-Host "正在启动投研观察池：$url"
    Write-Host "按 Ctrl+C 停止服务。"
    & $python.Source -m uvicorn web_app:app --host 127.0.0.1 --port $port
    if ($LASTEXITCODE -ne 0) {
        throw "Uvicorn 已退出，退出代码：$LASTEXITCODE"
    }
    return
}

$launcher = Get-Command py -ErrorAction SilentlyContinue | Select-Object -First 1
if ($launcher) {
    Write-Host "正在启动投研观察池：$url"
    Write-Host "按 Ctrl+C 停止服务。"
    & $launcher.Source -3 -m uvicorn web_app:app --host 127.0.0.1 --port $port
    if ($LASTEXITCODE -ne 0) {
        throw "Uvicorn 已退出，退出代码：$LASTEXITCODE"
    }
    return
}

throw "未找到 Python。请安装 Python 3.12 并确保 python 或 py 命令可用。"

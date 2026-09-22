# 启动 ngrok 隧道（手机远程访问 8000 端口服务）
# 用法：双击调用；隧道 URL（单行）自动写入 data/tunnel_url.txt（AGENTS.md 约定的唯一真值）
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
# ngrok 可执行文件解析阶梯：环境变量覆盖 -> 仓库内 ops\bin -> 用户 LOCALAPPDATA -> PATH。
# 原先硬编码了一台机器的 C:\Users\<name> 路径，换机部署时只能靠回退到 PATH 才能跑。
$ngrokCandidates = @(
    $env:NGROK_PATH,
    (Join-Path $root 'ops\bin\ngrok.exe'),
    (Join-Path $env:LOCALAPPDATA 'ngrok\ngrok.exe')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$ng = if ($ngrokCandidates) { $ngrokCandidates[0] } else { 'ngrok' }
$log = Join-Path $root 'data\ngrok_tunnel.log'
$urlFile = Join-Path $root 'data\tunnel_url.txt'

# 已存在的 ngrok 进程先停（避免多隧道抢占）
Get-CimInstance Win32_Process -Filter "Name='ngrok.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

$p = Start-Process -FilePath $ng -ArgumentList 'http', '8000', '--log=stdout' `
    -RedirectStandardOutput $log -WindowStyle Hidden -PassThru
Write-Output "ngrok started PID=$($p.Id)"

# 等待隧道就绪并抓取 URL：优先问 127.0.0.1:4040 API（最可靠），日志解析仅作兜底。
# ngrok 免费版每次重启会换域名，拿到后必须单行写入 tunnel_url.txt。
$url = ''
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Seconds 1
    try {
        $tunnels = (Invoke-RestMethod -Uri 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 3).tunnels
        $https = $tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1
        if ($https -and $https.public_url) { $url = $https.public_url; break }
    } catch {}
    if (Test-Path $log) {
        $line = Get-Content $log -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($line -match 'url=https://(\S+)') {
            $url = 'https://' + ($matches[1].TrimEnd('"'))
            break
        }
    }
}
if ($url) {
    # 唯一真值保持单行 URL（读取方直接 Get-Content 即用；多行说明文字会破坏解析）。
    # Windows PowerShell 5.1 的 -Encoding UTF8 会写 BOM，读取方 strip() 后仍带
    # ﻿ 前缀，会让 URL 精确比对与拼接失败 —— 这里强制无 BOM。
    [System.IO.File]::WriteAllText($urlFile, $url + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Write-Output "TUNNEL_URL=$url"
    Write-Output "手机用浏览器打开该地址，首次遇到 ngrok 警告页（ERR_NGROK_6024）要点 Visit Site 后再登录（口令见 config.json 的 access_token）。"
} else {
    Write-Output 'TUNNEL_URL=FAILED'
    Write-Output '隧道未就绪：检查 ngrok 是否已登录（ngrok config check）、8000 端口服务是否在跑（curl http://127.0.0.1:8000/api/health）。'
}

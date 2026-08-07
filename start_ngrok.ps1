# 启动 ngrok 隧道（手机远程访问 8000 端口服务）
# 用法：由 Win32_Process.Create 或双击调用；隧道 URL 自动写入 data/tunnel_url.txt
$ErrorActionPreference = 'Stop'
$ng = 'C:\Users\31557\AppData\Local\ngrok\ngrok.exe'
$log = 'd:\Users\31557\Desktop\AI拟人系统\data\ngrok_tunnel.log'
$err = 'd:\Users\31557\Desktop\AI拟人系统\data\ngrok_tunnel_err.log'
$urlFile = 'd:\Users\31557\Desktop\AI拟人系统\data\tunnel_url.txt'

# 已存在的 ngrok 进程先停（避免多隧道抢占）
Get-CimInstance Win32_Process -Filter "Name='ngrok.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

$p = Start-Process -FilePath $ng -ArgumentList 'http', '8000', '--log=stdout' `
    -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden -PassThru
Write-Output "ngrok started PID=$($p.Id)"

# 等待隧道就绪并抓取 URL（ngrok 3.x 日志: msg="started tunnel" ... url=https://xxx.ngrok-free.app）
$url = ''
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $log) {
        $line = Get-Content $log -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($line -match 'url=https://(\S+)') {
            $url = 'https://' + $matches[1]
            break
        }
    }
}
if ($url) {
    $content = @"
AI 拟人系统 - 手机远程访问地址（ngrok 隧道）
=========================================================
当前可用地址（$(Get-Date -Format 'yyyy-MM-dd HH:mm')）：
1. $url   <- 最新启动
手机使用方法：
1. 打开 APK，点 ⚙ 填下面的地址 + 访问口令
2. 保存并进入即可聊天
"@
    Set-Content -Path $urlFile -Value $content -Encoding UTF8
    Write-Output "TUNNEL_URL=$url"
} else {
    Write-Output 'TUNNEL_URL=FAILED'
    if (Test-Path $err) { Get-Content $err -Tail 10 -Encoding UTF8 }
}

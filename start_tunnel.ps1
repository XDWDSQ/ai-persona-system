# AI Persona System - Cloudflare Quick Tunnel launcher
# Usage: double-click start_tunnel.bat (or run this file directly)
# Behavior:
#   1. Starts cloudflared quick tunnel -> http://127.0.0.1:8000
#   2. Parses the printed tunnel URL from cloudflared stderr output
#   3. Saves the URL to data\tunnel_url.txt and prints it in this window
#   4. Stays in foreground; closing the window stops the tunnel (same as before)
$ErrorActionPreference = 'SilentlyContinue'

$root = $PSScriptRoot
$logFile = Join-Path $root 'data\tunnel_cloudflared.log'
$urlFile = Join-Path $root 'data\tunnel_url.txt'
$cfd = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'

if (-not (Test-Path -LiteralPath $cfd)) {
    Write-Host ''
    Write-Host '[X] cloudflared not found. Install it with:'
    Write-Host '    winget install --id Cloudflare.cloudflared -e'
    Write-Host ''
    exit 1
}

# Stale log from a previous run
Remove-Item -LiteralPath $logFile -ErrorAction SilentlyContinue

# Kill any previously running cloudflared QUICK tunnels (cmdline contains
# "--url") so only ONE tunnel stays alive after this script runs. Named
# tunnels (cloudflared tunnel run <name>, no --url) are left untouched.
Write-Host ' Stopping any old cloudflared quick tunnels ...'
$old = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match '--url' }
foreach ($p in $old) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 800

Write-Host ''
Write-Host ' AI Persona System - Cloudflare Tunnel starting ...'
Write-Host ' The new tunnel URL will be saved to data\tunnel_url.txt'
Write-Host ''

# Redirect stderr to the log file (cloudflared prints logs/URL there).
# Keep -NoNewWindow so closing this console also stops cloudflared.
$proc = Start-Process -FilePath $cfd `
    -ArgumentList 'tunnel','--url','http://127.0.0.1:8000','--no-autoupdate' `
    -RedirectStandardError $logFile `
    -NoNewWindow -PassThru

# Poll the log until the tunnel URL appears (max ~30s)
$url = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    if ($proc.HasExited) { break }
    if (Test-Path -LiteralPath $logFile) {
        $content = Get-Content -LiteralPath $logFile -Raw -ErrorAction SilentlyContinue
        if ($content -match 'https://[a-z0-9\-]+\.trycloudflare\.com') {
            $url = $Matches[0]
            break
        }
    }
}

if ($url) {
    Set-Content -LiteralPath $urlFile -Value $url -Encoding UTF8
    Write-Host ''
    Write-Host '  Phone access address (saved to data\tunnel_url.txt):'
    Write-Host '  ' + $url
    Write-Host ''
    Write-Host '  Open this URL on your phone and enter the access token.'
    Write-Host ''
} else {
    Write-Host '[!] Could not detect the tunnel URL from cloudflared output.'
    Write-Host '    Check data\tunnel_cloudflared.log for details.'
}

$proc.WaitForExit()

$ErrorActionPreference = 'Stop'
$root = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'runtime')) {
  $PSScriptRoot              # packaged ZIP: launcher lives at bundle root
} else {
  Split-Path -Parent $PSScriptRoot  # source tree: launcher lives in easy/
}
$python = Join-Path $root 'runtime\python\python.exe'
$node = Join-Path $root 'runtime\node\node.exe'
$server = Join-Path $root 'app\server'
$data = Join-Path $server 'data'
$config = Join-Path $server 'config.json'

if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $node)) {
  Write-Host '这个启动器需要 GitHub Release 中的 Windows 便携版，源码压缩包不包含内置运行时。' -ForegroundColor Yellow
  Write-Host '请到 Releases 下载 Memory-Vault-Windows-x64.zip，解压后双击 start.cmd。'
  exit 1
}

New-Item -ItemType Directory -Path $data -Force | Out-Null
if (-not (Test-Path -LiteralPath $config)) {
  Copy-Item -LiteralPath (Join-Path $server 'config.easy.json') -Destination $config
}

$env:MEMORY_VAULT_EASY = '1'
$vaultLog = Join-Path $data 'vault.log'
$hubLog = Join-Path $data 'hub.log'
$vaultErr = Join-Path $data 'vault-error.log'
$hubErr = Join-Path $data 'hub-error.log'

Write-Host 'Memory Vault 正在启动……' -ForegroundColor Cyan
$vault = Start-Process -FilePath $python `
  -ArgumentList @((Join-Path $root '_meta\mcp_server.py'), '--http', '--host', '127.0.0.1', '--port', '8900') `
  -WorkingDirectory $root -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput $vaultLog -RedirectStandardError $vaultErr

$hub = $null
try {
  Start-Sleep -Seconds 2
  $hub = Start-Process -FilePath $node -ArgumentList @('dist/index.js') `
    -WorkingDirectory $server -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $hubLog -RedirectStandardError $hubErr

  $ready = $false
  for ($i = 0; $i -lt 30; $i++) {
    try {
      $health = Invoke-RestMethod -Uri 'http://127.0.0.1:3900/api/health' -TimeoutSec 1
      if ($health.status -eq 'ok') { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  if (-not $ready) { throw "前端服务启动失败，请查看 $hubErr" }

  Write-Host '启动成功。浏览器将自动打开；首次使用请点联系人设置，填写 API 地址、模型和 Key。' -ForegroundColor Green
  Write-Host '这个窗口关闭后服务也会停止。日志在 app\server\data。'
  Start-Process 'http://127.0.0.1:3900'
  Wait-Process -Id $hub.Id
} finally {
  if ($hub -and -not $hub.HasExited) { Stop-Process -Id $hub.Id -Force -ErrorAction SilentlyContinue }
  if ($vault -and -not $vault.HasExited) { Stop-Process -Id $vault.Id -Force -ErrorAction SilentlyContinue }
}

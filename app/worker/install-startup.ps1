param(
  [string]$Config = (Join-Path $PSScriptRoot 'config.json')
)

$node = (Get-Command node -ErrorAction Stop).Source
$script = Join-Path $PSScriptRoot 'worker.mjs'
if (-not (Test-Path -LiteralPath $Config)) { throw "Missing worker config: $Config" }

$action = New-ScheduledTaskAction -Execute $node -Argument "`"$script`" `"$Config`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'ai-hub PC Worker' -Action $action -Trigger $trigger -Settings $settings -Description 'Outbound ai-hub Codex/Claude worker' -Force | Out-Null
Write-Host 'Installed: ai-hub PC Worker (starts at logon)'

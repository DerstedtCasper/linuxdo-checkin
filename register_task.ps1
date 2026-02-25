param(
    [string]$TaskName = "LinuxDo-Checkin",
    [string]$StartTime = "08:00"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runScript = Join-Path $scriptDir "run_checkin.ps1"

if (-not (Test-Path $runScript)) {
    Write-Error "Run script not found: $runScript"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$runScript`""
$startDateTime = [datetime]::ParseExact($StartTime, "HH:mm", $null)
$secondTime = $startDateTime.AddHours(12).ToString("HH:mm")
$trigger1 = New-ScheduledTaskTrigger -Daily -At $StartTime
$trigger2 = New-ScheduledTaskTrigger -Daily -At $secondTime
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($trigger1, $trigger2) `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
Write-Output "Scheduled task created: $($task.TaskName)"
Write-Output "State: $($task.State)"

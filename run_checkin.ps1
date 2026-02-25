param(
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Join-Path $scriptDir $EnvFile
$pythonExe = Join-Path $scriptDir ".venv\Scripts\python.exe"
$mainScript = Join-Path $scriptDir "main.py"
$logDir = Join-Path $scriptDir "logs"

if (-not (Test-Path $envPath)) {
    Write-Error "Env file not found: $envPath"
}

if (-not (Test-Path $pythonExe)) {
    Write-Error "Python executable not found: $pythonExe"
}

Get-Content -Path $envPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
        return
    }

    $pair = $line -split "=", 2
    if ($pair.Count -ne 2) {
        return
    }

    $key = $pair[0].Trim()
    $value = $pair[1].Trim().Trim("'").Trim('"')
    if (-not [string]::IsNullOrWhiteSpace($key)) {
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

$hasNewUser = -not [string]::IsNullOrWhiteSpace($env:LINUXDO_USERNAME) -and -not [string]::IsNullOrWhiteSpace($env:LINUXDO_PASSWORD)
$hasLegacyUser = -not [string]::IsNullOrWhiteSpace($env:USERNAME) -and -not [string]::IsNullOrWhiteSpace($env:PASSWORD)

if (-not $hasNewUser -and -not $hasLegacyUser) {
    Write-Error "Credentials are missing. Set LINUXDO_USERNAME and LINUXDO_PASSWORD in .env."
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir ("checkin_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

Push-Location $scriptDir
try {
    $stdoutLog = Join-Path $logDir ("checkin_stdout_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
    $stderrLog = Join-Path $logDir ("checkin_stderr_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

    $proc = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @("`"$mainScript`"") `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -NoNewWindow `
        -Wait `
        -PassThru

    if (Test-Path $stdoutLog) {
        Get-Content -Path $stdoutLog -Encoding UTF8 | Add-Content -Path $logFile -Encoding UTF8
    }
    if (Test-Path $stderrLog) {
        Get-Content -Path $stderrLog -Encoding UTF8 | Add-Content -Path $logFile -Encoding UTF8
    }

    Get-Content -Path $logFile -Encoding UTF8
    $exitCode = $proc.ExitCode
}
finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    Write-Error "Check-in script failed with exit code $exitCode. Log: $logFile"
}

Write-Output "Check-in script completed. Log: $logFile"

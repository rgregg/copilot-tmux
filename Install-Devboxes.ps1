param(
    [string]$Name,
    [string]$SharedDirectory = (Split-Path -Parent $PSScriptRoot),
    [string]$ProfilePath = $PROFILE.CurrentUserAllHosts
)

$ErrorActionPreference = 'Stop'
$linuxTools = & wsl.exe --exec wslpath -a -- $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Cannot translate the tools folder into a WSL path.' }
$linuxShared = & wsl.exe --exec wslpath -a -- $SharedDirectory
if ($LASTEXITCODE -ne 0) { throw 'Cannot translate the shared folder into a WSL path.' }
$installArgs = @("$linuxTools/setup_devboxes.py", '--shared-dir', $linuxShared)
if ($Name) { $installArgs += @('--name', $Name) }
& wsl.exe --exec python3 @installArgs
if ($LASTEXITCODE -ne 0) { throw 'WSL helper installation failed.' }

$source = Join-Path $PSScriptRoot 'Devboxes.ps1'
$line = ". '" + $source.Replace("'", "''") + "'"
$begin = '# >>> Shared devbox helpers >>>'
$end = '# <<< Shared devbox helpers <<<'
$block = "$begin`n$line`n$end"
$existing = if (Test-Path -LiteralPath $ProfilePath) { [IO.File]::ReadAllText($ProfilePath) } else { '' }
$pattern = '(?ms)^' + [regex]::Escape($begin) + '\r?\n.*?^' + [regex]::Escape($end) + '\r?\n?'
$updated = if ($existing -match $pattern) {
    [regex]::Replace($existing, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) "$block`n" })
} else {
    $existing.TrimEnd() + "`n`n$block`n"
}
if ($updated -ne $existing) {
    if ((Test-Path -LiteralPath $ProfilePath) -and -not (Test-Path -LiteralPath "$ProfilePath.before-devboxes")) {
        Copy-Item -LiteralPath $ProfilePath -Destination "$ProfilePath.before-devboxes"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ProfilePath) | Out-Null
    [IO.File]::WriteAllText($ProfilePath, $updated)
}
. $source
Write-Output 'Installed PowerShell-to-WSL helpers. Open a new PowerShell tab to load them.'

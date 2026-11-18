$ErrorActionPreference = 'Stop'
$calls = [Collections.Generic.List[object]]::new()
function global:wsl.exe {
    $script:calls.Add(@($args))
    $global:LASTEXITCODE = 7
}
. (Join-Path $PSScriptRoot '../Devboxes.ps1')
lobster --resume 'session with spaces' '$(not-executed)'
$call = $calls[0]
if ($call[1] -ne '~' -or $call[7] -ne 'lobster') { throw 'Lobster did not use the home-based WSL launcher.' }
if ($call[-3] -ne '--resume' -or $call[-2] -ne 'session with spaces' -or $call[-1] -ne '$(not-executed)') {
    throw 'Copilot arguments were not forwarded literally.'
}
if ($LASTEXITCODE -ne 7) { throw 'WSL exit code was lost.' }
copilot-tmux list
$call = $calls[1]
if ($call[1] -ne $PWD.Path -or $call[-2] -ne 'copilot-tmux' -or $call[-1] -ne 'list') {
    throw 'copilot-tmux lost the caller directory or arguments.'
}
tail-lobster3
if ($calls[2][-1] -ne 'tail-lobster3') { throw 'Tail-lobster3 shortcut failed.' }
tail-ubuntu hostname
if ($calls[3][-2] -ne 'tail-ubuntu' -or $calls[3][-1] -ne 'hostname') { throw 'Tail-ubuntu shortcut failed.' }
tail-lobster4 hostname
if ($calls[4][-2] -ne 'tail-lobster4' -or $calls[4][-1] -ne 'hostname') { throw 'Tail-lobster4 shortcut failed.' }
foreach ($name in @('lobster3', 'lobster4', 'ubuntu', 'lobster-3', 'lobster-4', 'rygregg-ubuntu')) {
    if (Test-Path "Function:\$name") { throw "Legacy remote function remains: $name" }
}

$temp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $temp | Out-Null
$profileFile = Join-Path $temp 'profile.ps1'
[IO.File]::WriteAllText($profileFile, '# original profile')
function global:wsl.exe {
    $global:LASTEXITCODE = 0
    if ($args[1] -eq 'wslpath') { '/test/tools with spaces' }
}
try {
    $installer = Join-Path $PSScriptRoot '../Install-Devboxes.ps1'
    & $installer -Name lobster4 -ProfilePath $profileFile
    $first = [IO.File]::ReadAllText($profileFile)
    & $installer -Name lobster4 -ProfilePath $profileFile
    if ([IO.File]::ReadAllText($profileFile) -ne $first) { throw 'Profile install is not repeatable.' }
    if (-not $first.StartsWith('# original profile')) { throw 'Existing profile was changed.' }
    if ([IO.File]::ReadAllText("$profileFile.before-devboxes") -ne '# original profile') {
        throw 'Original profile was not backed up.'
    }
} finally {
    Remove-Item -LiteralPath $profileFile,"$profileFile.before-devboxes"
    Remove-Item -LiteralPath $temp
}
Write-Output 'PowerShell helper tests passed.'

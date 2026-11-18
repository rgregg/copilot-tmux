function Invoke-DevboxWsl {
    param([string]$Command, [string[]]$Arguments, [string]$Directory = '~')
    & wsl.exe --cd $Directory --exec /bin/sh -c 'export PATH="$HOME/.local/bin:$PATH"; exec "$@"' sh $Command @Arguments
}

function lobster { Invoke-DevboxWsl -Command lobster -Arguments $args }
function lobsterclaw { Invoke-DevboxWsl -Command lobsterclaw -Arguments $args }
function lobster3 { Invoke-DevboxWsl -Command lobster3 -Arguments $args }
function lobster4 { Invoke-DevboxWsl -Command lobster4 -Arguments $args }
function lobster-3 { Invoke-DevboxWsl -Command lobster3 -Arguments $args }
function lobster-4 { Invoke-DevboxWsl -Command lobster4 -Arguments $args }
function ubuntu { Invoke-DevboxWsl -Command ubuntu -Arguments $args }
function rygregg-ubuntu { Invoke-DevboxWsl -Command ubuntu -Arguments $args }
function copilot-tmux { Invoke-DevboxWsl -Command copilot-tmux -Arguments $args -Directory $PWD.Path }
function devbox-update { Invoke-DevboxWsl -Command devbox-update -Arguments $args }
function devbox-doctor { Invoke-DevboxWsl -Command devbox-helpers -Arguments @('doctor') }

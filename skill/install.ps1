<#
.SYNOPSIS
    Install the llm-price-check agent skill on Windows.

.DESCRIPTION
    Copies SKILL.md and check.py into an agent skills folder.

    Claude Code and the GitHub Copilot CLI read different folders —
    %USERPROFILE%\.claude\skills and %USERPROFILE%\.copilot\skills. They are
    not interchangeable, so installing for one does not install for the other.
    Pick with -Target, or use -Target both.

    Copies rather than links, so it keeps working on a machine with no
    checkout of this repo — the usual case for the machine you want it on.

.PARAMETER Target
    Which agent to install for: claude (default), copilot, or both.

.PARAMETER Destination
    An explicit skills root, overriding -Target. Use for a location neither
    default covers, such as a project's .github\skills.

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -Target copilot

.EXAMPLE
    .\install.ps1 -Target both

.EXAMPLE
    .\install.ps1 -Destination ".github\skills"

.EXAMPLE
    # Standalone, no clone needed:
    irm https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/install.ps1 | iex

.EXAMPLE
    # Standalone, for Copilot. `irm | iex` cannot pass arguments, so use:
    & ([scriptblock]::Create((irm https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/install.ps1))) -Target copilot
#>
[CmdletBinding()]
param(
    [ValidateSet('claude', 'copilot', 'both')]
    [string]$Target = 'claude',

    [string]$Destination
)

$ErrorActionPreference = 'Stop'

$Skill = 'llm-price-check'
$Raw   = "https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/$Skill"

# --- where to install -------------------------------------------------------
# An explicit -Destination wins; otherwise -Target picks the root. Each agent
# reads only its own folder, so 'both' is two copies, not one shared one.
$Roots = @{
    claude  = Join-Path $env:USERPROFILE '.claude\skills'
    copilot = Join-Path $env:USERPROFILE '.copilot\skills'
}
if ($Destination) {
    $Destinations = @($Destination)
} elseif ($Target -eq 'both') {
    $Destinations = @($Roots.claude, $Roots.copilot)
} else {
    $Destinations = @($Roots[$Target])
}

# --- interpreter ------------------------------------------------------------
# python3 is frequently absent from PATH on Windows even when Python is
# installed, and the Microsoft Store alias can shadow it with a stub that
# opens the Store instead of running anything.
#
# Do not try to tell them apart by path. A working Store install of Python
# resolves through ...\AppData\Local\Microsoft\WindowsApps too, so rejecting
# that folder rejects a perfectly good interpreter and leaves the machine with
# none. Ask each candidate what it is and believe the answer: the stub prints
# nothing and exits non-zero, a real interpreter prints its major version.
$Py = $null
foreach ($candidate in @('py', 'python', 'python3')) {
    if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
    $major = & $candidate -c "import sys; print(sys.version_info[0])" 2>$null | Select-Object -Last 1
    if ($LASTEXITCODE -eq 0 -and "$major".Trim() -eq '3') {
        $Py = $candidate
        break
    }
}
if (-not $Py) {
    Write-Error "No usable Python 3 on PATH. Install Python 3, or the skill's check.py cannot run."
}

# --- source -----------------------------------------------------------------
# Resolved once, so installing for two agents does not download twice.
$Files     = @('SKILL.md', 'check.py')
$Here      = if ($PSScriptRoot) { $PSScriptRoot } else { $null }
$LocalCopy = if ($Here) { Join-Path $Here $Skill } else { $null }
$TempDir   = $null

if ($LocalCopy -and (Test-Path (Join-Path $LocalCopy 'SKILL.md'))) {
    Write-Host "Installing from this checkout."
    $Source = $LocalCopy
} else {
    Write-Host "Downloading from GitHub."
    $TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("$Skill-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
    foreach ($file in $Files) {
        Invoke-WebRequest -Uri "$Raw/$file" -OutFile (Join-Path $TempDir $file) -UseBasicParsing
    }
    $Source = $TempDir
}

# --- install ----------------------------------------------------------------
$Installed = @()
try {
    foreach ($root in $Destinations) {
        $Dest = Join-Path $root $Skill
        New-Item -ItemType Directory -Path $Dest -Force | Out-Null
        foreach ($file in $Files) {
            Copy-Item (Join-Path $Source $file) $Dest -Force
            $path = Join-Path $Dest $file
            if (-not (Test-Path $path) -or (Get-Item $path).Length -eq 0) {
                Write-Error "Install produced an empty $file in $Dest."
            }
        }
        $Installed += $Dest
    }
} finally {
    if ($TempDir -and (Test-Path $TempDir)) {
        Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
foreach ($path in $Installed) { Write-Host "Installed to $path" }
Write-Host "Checking it runs (this hits the network)..."
Write-Host ""

$Dest = $Installed[0]

& $Py (Join-Path $Dest 'check.py') 'sonnet 5'
if ($LASTEXITCODE -eq 0) {
    # Name the agent that was actually installed for. "Copilot or Claude Code"
    # is what led to files being moved by hand in the first place.
    $Agents = if ($Destination) { 'your agent' }
              elseif ($Target -eq 'both') { 'Claude Code and the Copilot CLI' }
              elseif ($Target -eq 'copilot') { 'the Copilot CLI' }
              else { 'Claude Code' }
    Write-Host ""
    Write-Host "Working. Restart $Agents, then type /$Skill."
} else {
    Write-Host ""
    Write-Host "The files are in place but the check could not fetch data."
    Write-Host "Most likely a corporate proxy or firewall blocking zrrbite.github.io."
    Write-Host "The skill still works - it falls back to fetching brief.txt - but test"
    Write-Host "it once in your agent before relying on it."
}

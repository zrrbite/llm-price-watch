<#
.SYNOPSIS
    Install the llm-price-check agent skill on Windows.

.DESCRIPTION
    Copies SKILL.md and check.py into an agent skills folder. Claude Code and
    GitHub Copilot both read %USERPROFILE%\.claude\skills, so the default
    serves either.

    Copies rather than links, so it keeps working on a machine with no
    checkout of this repo — the usual case for the machine you want it on.

.PARAMETER Destination
    Skills root to install into. Defaults to %USERPROFILE%\.claude\skills.
    Other valid roots: %USERPROFILE%\.copilot\skills, %USERPROFILE%\.agents\skills,
    or a project's .github\skills.

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -Destination "$env:USERPROFILE\.copilot\skills"

.EXAMPLE
    # Standalone, no clone needed:
    irm https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/install.ps1 | iex
#>
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:USERPROFILE ".claude\skills")
)

$ErrorActionPreference = 'Stop'

$Skill = 'llm-price-check'
$Raw   = "https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/$Skill"
$Dest  = Join-Path $Destination $Skill

# --- interpreter ------------------------------------------------------------
# python3 is frequently absent from PATH on Windows even when Python is
# installed, and the Store alias can shadow it with a stub that opens the
# Store. Prefer the launcher, which is the reliable one.
$Py = $null
foreach ($candidate in @('py', 'python', 'python3')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) {
        # Reject the Microsoft Store stub, which exits without running anything.
        if ($found.Source -notlike '*WindowsApps*' -or $candidate -eq 'py') {
            $Py = $candidate
            break
        }
    }
}
if (-not $Py) {
    Write-Error "No usable Python on PATH. Install Python 3, or the skill's check.py cannot run."
}

# --- install ----------------------------------------------------------------
New-Item -ItemType Directory -Path $Dest -Force | Out-Null

$Here      = if ($PSScriptRoot) { $PSScriptRoot } else { $null }
$LocalCopy = if ($Here) { Join-Path $Here $Skill } else { $null }

if ($LocalCopy -and (Test-Path (Join-Path $LocalCopy 'SKILL.md'))) {
    Write-Host "Installing from this checkout."
    Copy-Item (Join-Path $LocalCopy 'SKILL.md') $Dest -Force
    Copy-Item (Join-Path $LocalCopy 'check.py') $Dest -Force
} else {
    Write-Host "Downloading from GitHub."
    foreach ($file in @('SKILL.md', 'check.py')) {
        Invoke-WebRequest -Uri "$Raw/$file" -OutFile (Join-Path $Dest $file) -UseBasicParsing
    }
}

# --- verify -----------------------------------------------------------------
foreach ($file in @('SKILL.md', 'check.py')) {
    $path = Join-Path $Dest $file
    if (-not (Test-Path $path) -or (Get-Item $path).Length -eq 0) {
        Write-Error "Install produced an empty $file."
    }
}

Write-Host ""
Write-Host "Installed to $Dest"
Write-Host "Checking it runs (this hits the network)..."
Write-Host ""

& $Py (Join-Path $Dest 'check.py') 'sonnet 5'
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Working. Restart your editor, then type /$Skill in Copilot or Claude Code."
} else {
    Write-Host ""
    Write-Host "The files are in place but the check could not fetch data."
    Write-Host "Most likely a corporate proxy or firewall blocking zrrbite.github.io."
    Write-Host "The skill still works - it falls back to fetching brief.txt - but test"
    Write-Host "it once in your agent before relying on it."
}

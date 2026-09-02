[CmdletBinding()]
param(
    [switch]$InstallMissing,
    [switch]$UpgradeTools,
    [switch]$UpdateLocks,
    [switch]$SkipWslUpdate,
    [switch]$SkipDocker,
    [switch]$SkipTailscale
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolVersions = Import-PowerShellDataFile -LiteralPath (Join-Path $PSScriptRoot 'tool-versions.psd1')

function Refresh-ProcessPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machinePath;$userPath;$env:Path"
}

function Invoke-Checked {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    Write-Host "> $Command $($Arguments -join ' ')"
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command завершился с кодом $LASTEXITCODE"
    }
}

function Install-WingetTool {
    param(
        [string]$Name,
        [string]$Command,
        [string]$PackageId,
        [string]$Version = ''
    )

    $isInstalled = [bool](Get-Command $Command -ErrorAction SilentlyContinue)
    if (-not $isInstalled) {
        if (-not $InstallMissing) {
            throw "$Name не найден. Повторите bootstrap с -InstallMissing."
        }

        $arguments = @(
            'install', '--id', $PackageId, '--exact', '--silent',
            '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity'
        )
        if ($Version) {
            $arguments += @('--version', $Version)
        }
        Invoke-Checked 'winget' $arguments
        Refresh-ProcessPath
        return
    }

    if ($UpgradeTools) {
        $arguments = @(
            'upgrade', '--id', $PackageId, '--exact', '--silent',
            '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity'
        )
        if ($Version) {
            $arguments += @('--version', $Version)
        }
        Write-Host "> winget $($arguments -join ' ')"
        & winget @arguments
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "$Name не удалось автоматически обновить. Doctor проверит установленную версию."
        }
        Refresh-ProcessPath
    }
}

if (-not $IsWindows) {
    throw 'Этот bootstrap предназначен для основной Windows-машины. Linux bootstrap появится в фазе 3.'
}

Refresh-ProcessPath
if (($InstallMissing -or $UpgradeTools) -and -not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw 'winget не найден. Установите или обновите App Installer из Microsoft Store.'
}

Install-WingetTool 'PowerShell 7' 'pwsh' $toolVersions.WingetPackages.PowerShell
Install-WingetTool 'Git' 'git' $toolVersions.WingetPackages.Git
Install-WingetTool 'uv' 'uv' $toolVersions.WingetPackages.Uv
Install-WingetTool 'Node.js' 'node' $toolVersions.WingetPackages.Node $toolVersions.Runtime.Node

if (-not $SkipDocker) {
    Install-WingetTool 'Docker Desktop' 'docker' $toolVersions.WingetPackages.Docker
}
if (-not $SkipTailscale) {
    Install-WingetTool 'Tailscale' 'tailscale' $toolVersions.WingetPackages.Tailscale
}

if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
    if (-not $InstallMissing) {
        throw 'WSL не найден. Повторите bootstrap с -InstallMissing из административного PowerShell.'
    }
    Invoke-Checked 'wsl' @('--install', '--no-distribution')
    Write-Warning 'WSL установлен. Может потребоваться перезагрузка Windows и повторный bootstrap.'
}
elseif ($UpgradeTools -and -not $SkipWslUpdate) {
    Invoke-Checked 'wsl' @('--update', '--web-download')
}

Refresh-ProcessPath
Push-Location $projectRoot
try {
    Invoke-Checked 'uv' @('python', 'install', $toolVersions.Runtime.Python)

    if (-not (Get-Command corepack -ErrorAction SilentlyContinue)) {
        throw 'Corepack не найден в установленной версии Node.js.'
    }
    Invoke-Checked 'corepack' @('prepare', "pnpm@$($toolVersions.Runtime.Pnpm)", '--activate')

    $corepackBin = Join-Path $env:LOCALAPPDATA 'Programs\Corepack'
    New-Item -ItemType Directory -Path $corepackBin -Force | Out-Null
    Invoke-Checked 'corepack' @('enable', '--install-directory', $corepackBin)

    $userPath = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $pathParts = @($userPath -split ';' | Where-Object { $_ })
    if ($pathParts -notcontains $corepackBin) {
        [System.Environment]::SetEnvironmentVariable('Path', (($pathParts + $corepackBin) -join ';'), 'User')
    }
    $env:Path = "$corepackBin;$env:Path"

    if ($UpdateLocks -or -not (Test-Path -LiteralPath 'uv.lock')) {
        Invoke-Checked 'uv' @('lock')
    }
    else {
        Invoke-Checked 'uv' @('lock', '--check')
    }
    Invoke-Checked 'uv' @('sync', '--locked', '--all-groups')

    if ($UpdateLocks -or -not (Test-Path -LiteralPath 'pnpm-lock.yaml')) {
        Invoke-Checked 'pnpm' @('install', '--lockfile-only')
    }
    Invoke-Checked 'pnpm' @('install', '--frozen-lockfile', '--ignore-scripts')

    Invoke-Checked 'git' @('config', '--local', 'core.hooksPath', '.githooks')
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Bootstrap завершён. Tailscale login намеренно не автоматизирован.'
Write-Host 'Для персонального входа выполните: tailscale up'
Write-Host 'Затем запустите: pnpm doctor'

& (Join-Path $PSScriptRoot 'doctor.ps1')
exit $LASTEXITCODE

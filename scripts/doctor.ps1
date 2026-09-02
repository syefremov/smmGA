[CmdletBinding()]
param(
    [switch]$RequireTailscaleLogin
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolVersionsPath = Join-Path $PSScriptRoot 'tool-versions.psd1'
$toolVersions = Import-PowerShellDataFile -LiteralPath $toolVersionsPath
$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

function Add-Result {
    param(
        [ValidateSet('PASS', 'WARN', 'FAIL')]
        [string]$Status,
        [string]$Name,
        [string]$Details
    )

    "[{0}] {1}: {2}" -f $Status, $Name, $Details
    if ($Status -eq 'FAIL') {
        $failures.Add($Name)
    }
    elseif ($Status -eq 'WARN') {
        $warnings.Add($Name)
    }
}

function Invoke-NativeCapture {
    param(
        [string]$Command,
        [string[]]$Arguments = @()
    )

    $commandInfo = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $commandInfo) {
        return @{ ExitCode = 127; Output = '' }
    }

    $output = & $commandInfo.Source @Arguments 2>&1 | Out-String
    $normalizedOutput = $output.Replace([string][char]0, '').Trim()
    return @{ ExitCode = $LASTEXITCODE; Output = $normalizedOutput }
}

function Get-VersionFromText {
    param([string]$Text)

    $match = [regex]::Match($Text, '(\d+\.\d+(?:\.\d+)?)')
    if (-not $match.Success) {
        return $null
    }

    $parts = $match.Groups[1].Value.Split('.')
    while ($parts.Count -lt 3) {
        $parts += '0'
    }
    return [version]($parts -join '.')
}

function Test-MinimumVersion {
    param(
        [string]$Name,
        [string]$Command,
        [string[]]$Arguments,
        [string]$Minimum
    )

    $result = Invoke-NativeCapture -Command $Command -Arguments $Arguments
    if ($result.ExitCode -ne 0) {
        Add-Result FAIL $Name "команда недоступна или завершилась с кодом $($result.ExitCode)"
        return
    }

    $actual = Get-VersionFromText $result.Output
    if (-not $actual) {
        Add-Result FAIL $Name 'не удалось определить версию'
        return
    }

    if ($actual -lt [version]$Minimum) {
        Add-Result FAIL $Name "версия $actual ниже минимальной $Minimum"
        return
    }

    Add-Result PASS $Name "$actual (минимум $Minimum)"
}

$machinePath = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [System.Environment]::GetEnvironmentVariable('Path', 'User')
$env:Path = "$machinePath;$userPath;$env:Path"

Push-Location $projectRoot
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    if ($build -ge [int]$toolVersions.Minimum.WindowsBuild) {
        Add-Result PASS 'Windows' "$($os.Caption), build $build"
    }
    else {
        Add-Result FAIL 'Windows' "build $build ниже минимального $($toolVersions.Minimum.WindowsBuild)"
    }

    if ($os.Caption -match 'Windows 10') {
        Add-Result WARN 'Windows lifecycle' 'Windows 10 22H2 пригодна для локального пилота, но Windows 11 рекомендуется до production'
    }

    $ramGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    if ($ramGb -ge [double]$toolVersions.Minimum.RamGb) {
        Add-Result PASS 'RAM' "$ramGb GB"
    }
    else {
        Add-Result FAIL 'RAM' "$ramGb GB; требуется минимум $($toolVersions.Minimum.RamGb) GB"
    }

    $systemDisk = Get-CimInstance Win32_LogicalDisk | Where-Object DeviceID -eq $env:SystemDrive
    $freeDiskGb = [math]::Round($systemDisk.FreeSpace / 1GB, 1)
    if ($freeDiskGb -lt [double]$toolVersions.Minimum.FreeDiskGb) {
        Add-Result FAIL 'Свободное место' "$freeDiskGb GB; требуется минимум $($toolVersions.Minimum.FreeDiskGb) GB"
    }
    elseif ($freeDiskGb -lt [double]$toolVersions.Recommended.FreeDiskGb) {
        Add-Result WARN 'Свободное место' "$freeDiskGb GB; перед фазой 2 рекомендуется $($toolVersions.Recommended.FreeDiskGb) GB"
    }
    else {
        Add-Result PASS 'Свободное место' "$freeDiskGb GB"
    }

    Test-MinimumVersion 'PowerShell' 'pwsh' @('-NoLogo', '-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()') $toolVersions.Minimum.PowerShell
    Test-MinimumVersion 'Git' 'git' @('--version') $toolVersions.Minimum.Git

    $ssh = Invoke-NativeCapture 'ssh' @('-V')
    if ($ssh.ExitCode -eq 0 -and $ssh.Output -match 'OpenSSH') {
        Add-Result PASS 'OpenSSH' (($ssh.Output -split "`r?`n")[0])
    }
    else {
        Add-Result FAIL 'OpenSSH' 'клиент не найден'
    }

    $wslVersion = Invoke-NativeCapture 'wsl' @('--version')
    $parsedWslVersion = Get-VersionFromText $wslVersion.Output
    if ($wslVersion.ExitCode -eq 0 -and $parsedWslVersion) {
        if ($parsedWslVersion -ge [version]$toolVersions.Minimum.Wsl) {
            Add-Result PASS 'WSL' "$parsedWslVersion"
        }
        else {
            Add-Result FAIL 'WSL' "$parsedWslVersion ниже минимальной $($toolVersions.Minimum.Wsl)"
        }
    }
    else {
        Add-Result FAIL 'WSL' 'современная Store/MSI-версия не обнаружена; выполните wsl --update --web-download'
    }

    Test-MinimumVersion 'Docker CLI' 'docker' @('--version') $toolVersions.Minimum.DockerEngine
    $dockerServer = Invoke-NativeCapture 'docker' @('info', '--format', '{{.ServerVersion}}')
    if ($dockerServer.ExitCode -eq 0 -and $dockerServer.Output) {
        Add-Result PASS 'Docker daemon' $dockerServer.Output
    }
    else {
        Add-Result FAIL 'Docker daemon' 'не отвечает; запустите Docker Desktop'
    }
    Test-MinimumVersion 'Docker Compose' 'docker' @('compose', 'version', '--short') $toolVersions.Minimum.DockerCompose

    Test-MinimumVersion 'uv' 'uv' @('--version') $toolVersions.Minimum.Uv

    $pythonRequired = (Get-Content -Raw -LiteralPath (Join-Path $projectRoot '.python-version')).Trim()
    $pythonLocation = Invoke-NativeCapture 'uv' @('python', 'find', $pythonRequired)
    if ($pythonLocation.ExitCode -eq 0 -and (Test-Path -LiteralPath $pythonLocation.Output)) {
        $pythonVersion = & $pythonLocation.Output --version 2>&1 | Out-String
        if ($pythonVersion -match [regex]::Escape($pythonRequired)) {
            Add-Result PASS 'Python' $pythonVersion.Trim()
        }
        else {
            Add-Result FAIL 'Python' "ожидалась версия $pythonRequired, получено $($pythonVersion.Trim())"
        }
    }
    else {
        Add-Result FAIL 'Python' "uv-managed Python $pythonRequired не найден"
    }

    $nodeRequired = (Get-Content -Raw -LiteralPath (Join-Path $projectRoot '.node-version')).Trim()
    $nodeResult = Invoke-NativeCapture 'node' @('--version')
    if ($nodeResult.ExitCode -eq 0 -and $nodeResult.Output.TrimStart('v') -eq $nodeRequired) {
        Add-Result PASS 'Node.js' $nodeResult.Output
    }
    else {
        Add-Result FAIL 'Node.js' "ожидалась версия $nodeRequired, получено $($nodeResult.Output)"
    }

    $packageJson = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'package.json') | ConvertFrom-Json
    $pnpmRequired = ($packageJson.packageManager -split '@')[-1]
    $pnpmResult = Invoke-NativeCapture 'pnpm' @('--version')
    if ($pnpmResult.ExitCode -eq 0 -and $pnpmResult.Output -eq $pnpmRequired) {
        Add-Result PASS 'pnpm' $pnpmResult.Output
    }
    else {
        Add-Result FAIL 'pnpm' "ожидалась версия $pnpmRequired, получено $($pnpmResult.Output)"
    }

    $tailscaleVersion = Invoke-NativeCapture 'tailscale' @('version')
    if ($tailscaleVersion.ExitCode -eq 0) {
        Add-Result PASS 'Tailscale' (($tailscaleVersion.Output -split "`r?`n")[0])
        $tailscaleStatus = Invoke-NativeCapture 'tailscale' @('status', '--json')
        if ($tailscaleStatus.ExitCode -eq 0 -and $tailscaleStatus.Output) {
            $backendState = ($tailscaleStatus.Output | ConvertFrom-Json).BackendState
            if ($backendState -eq 'Running') {
                Add-Result PASS 'Tailscale login' 'подключение активно'
            }
            elseif ($RequireTailscaleLogin) {
                Add-Result FAIL 'Tailscale login' "состояние: $backendState"
            }
            else {
                Add-Result WARN 'Tailscale login' "состояние: $backendState; выполните персональный вход"
            }
        }
        elseif ($RequireTailscaleLogin) {
            Add-Result FAIL 'Tailscale login' 'статус недоступен'
        }
        else {
            Add-Result WARN 'Tailscale login' 'статус недоступен; выполните персональный вход'
        }
    }
    else {
        Add-Result FAIL 'Tailscale' 'команда не найдена'
    }

    foreach ($requiredFile in @('pyproject.toml', 'uv.lock', 'package.json', 'pnpm-lock.yaml', '.env.example')) {
        if (Test-Path -LiteralPath (Join-Path $projectRoot $requiredFile)) {
            Add-Result PASS "Файл $requiredFile" 'найден'
        }
        else {
            Add-Result FAIL "Файл $requiredFile" 'отсутствует'
        }
    }

    $hooksPath = (Invoke-NativeCapture 'git' @('config', '--local', '--get', 'core.hooksPath')).Output
    if ($hooksPath -eq '.githooks') {
        Add-Result PASS 'Git hooks' '.githooks'
    }
    else {
        Add-Result FAIL 'Git hooks' 'выполните bootstrap для настройки core.hooksPath'
    }
}
finally {
    Pop-Location
}

''
"Итог: ошибок — $($failures.Count), предупреждений — $($warnings.Count)."
if ($warnings.Count -gt 0) {
    "Предупреждения: $($warnings -join ', ')."
}
if ($failures.Count -gt 0) {
    "Ошибки: $($failures -join ', ')."
    exit 1
}

exit 0

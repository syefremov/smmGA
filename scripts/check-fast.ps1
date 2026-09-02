[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$machinePath = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [System.Environment]::GetEnvironmentVariable('Path', 'User')
$env:Path = "$machinePath;$userPath;$env:Path"

function Invoke-Checked {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command завершился с кодом $LASTEXITCODE"
    }
}

Push-Location $projectRoot
try {
    Invoke-Checked 'git' @('diff', '--cached', '--check')

    $stagedNames = @(git diff --cached --name-only --diff-filter=ACMR)
    $forbiddenNames = @(
        '(^|/)\.env($|\.)',
        '\.(key|pem|p12|pfx)$',
        '\.(dump|sql\.gz)$',
        '(^|/)(backups?|\.data)/'
    )
    foreach ($name in $stagedNames) {
        if ($name -eq '.env.example' -or $name -match '\.env\.[^/]+\.example$') {
            continue
        }
        foreach ($pattern in $forbiddenNames) {
            if ($name -match $pattern) {
                throw "Запрещённый локальный или чувствительный файл подготовлен к commit: $name"
            }
        }
    }

    $stagedDiff = git diff --cached --unified=0 --no-color
    $privateKeyPattern = 'BEGIN ' + '(RSA|OPENSSH|EC)' + ' PRIVATE KEY'
    $credentialPattern = '(?i)(password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*["'']?[A-Za-z0-9+/_.-]{12,}'
    if (($stagedDiff -join "`n") -match $privateKeyPattern -or ($stagedDiff -join "`n") -match $credentialPattern) {
        throw "В staged diff найден возможный секрет. Проверьте файлы: $($stagedNames -join ', ')"
    }

    Get-Content -Raw -LiteralPath 'package.json' | ConvertFrom-Json | Out-Null
    Invoke-Checked 'uv' @('lock', '--check')

    $expectedPython = (Get-Content -Raw -LiteralPath '.python-version').Trim()
    $configuredPython = (Select-String -Path 'pyproject.toml' -Pattern '^requires-python\s*=\s*"([^"]+)"$').Matches.Groups[1].Value
    if ($configuredPython -notmatch [regex]::Escape(($expectedPython -split '\.')[0..1] -join '.')) {
        throw '.python-version и requires-python расходятся'
    }

    'Быстрые проверки пройдены.'
}
finally {
    Pop-Location
}

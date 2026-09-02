[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('dev', 'test', 'build', 'db:migrate')]
    [string]$CommandName
)

Write-Error "Команда '$CommandName' зарезервирована, но появится только в фазе 2: исполняемый monorepo, Compose и CI."
exit 2

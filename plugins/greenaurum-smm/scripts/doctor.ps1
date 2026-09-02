# Read-only Windows employee check. Uses the endpoint from this plugin, never credentials.
$ErrorActionPreference = 'Stop'
try {
    $taskConfig = Get-Content -LiteralPath (Join-Path $PSScriptRoot '../.mcp.json') -Raw | ConvertFrom-Json
    $taskEndpoint = [uri]$taskConfig.mcpServers.smm.url
    if ($taskEndpoint.Scheme -ne 'https' -or $taskEndpoint.AbsolutePath -ne '/mcp/' -or
        $taskEndpoint.UserInfo -or $taskEndpoint.Query -or $taskEndpoint.Fragment) { throw 'Invalid configuration' }
    $taskMetadataUrl = $taskEndpoint.GetLeftPart([System.UriPartial]::Authority) + '/.well-known/oauth-protected-resource/mcp/'
    $taskMetadata = Invoke-RestMethod -Uri $taskMetadataUrl -TimeoutSec 5 -MaximumRedirection 0
    $taskMetadataOk = $taskMetadata.resource -eq $taskEndpoint.AbsoluteUri -and $taskMetadata.scopes_supported -contains 'smm:access'
    $taskProtected = $false
    try { $null = Invoke-WebRequest -UseBasicParsing -Uri $taskEndpoint -TimeoutSec 5 -MaximumRedirection 0 }
    catch { $taskProtected = [int]$_.Exception.Response.StatusCode -eq 401 }
    $taskCodex = $null -ne (Get-Command codex -ErrorAction SilentlyContinue)
    [pscustomobject]@{ HttpsMetadata = $taskMetadataOk; AnonymousDenied = $taskProtected; CodexCliFound = $taskCodex; PersonalOAuthVerified = $false } | ConvertTo-Json
    if (-not ($taskMetadataOk -and $taskProtected -and $taskCodex)) { exit 1 }
} catch {
    Write-Output 'Connection not ready. Check private network, HTTPS and plugin configuration. Details withheld.'
    exit 1
}

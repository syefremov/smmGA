@{
    Runtime = @{
        Python = '3.13.15'
        Node   = '24.19.0'
        Pnpm   = '11.19.0'
    }

    Minimum = @{
        WindowsBuild = 19045
        PowerShell    = '7.4.0'
        Git           = '2.40.0'
        Wsl           = '2.1.5'
        DockerEngine  = '24.0.0'
        DockerCompose = '2.20.0'
        Uv            = '0.12.8'
        RamGb         = 8
        FreeDiskGb    = 10
    }

    Recommended = @{
        FreeDiskGb = 30
    }

    WingetPackages = @{
        PowerShell = 'Microsoft.PowerShell'
        Git        = 'Git.Git'
        Docker     = 'Docker.DockerDesktop'
        Tailscale  = 'Tailscale.Tailscale'
        Uv         = 'astral-sh.uv'
        Node       = 'OpenJS.NodeJS.LTS'
    }
}

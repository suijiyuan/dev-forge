param(
    [switch]$ReplaceExtensions = @@REPLACE_EXTENSIONS_LITERAL@@,
    [switch]$ForceSettings,
    [switch]$ForceResources,
    [switch]$ForceVSCodeInstall,
    [switch]$AllowExternalExtensionsDirectory,
    [ValidateRange(10, 86400)][int]$ProcessTimeoutSeconds = 300,
    [ValidateRange(60, 86400)][int]$InstallerTimeoutSeconds = 1800
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version 2.0

$Root = $PSScriptRoot
$Installer = Join-Path $Root '@@INSTALLER_PATH@@'
$ArchiveMode = @@ARCHIVE_LITERAL@@
$ArchiveTarget = Join-Path $Root 'vscode\app'
$CodePath = $null
$TargetVSCodeVersion = @@TARGET_VERSION@@
$TargetVSCodeArch = @@TARGET_ARCH@@
$PackageKind = @@PACKAGE_KIND@@
$UserDataRoot = Join-Path $env:APPDATA 'Code\User'
$ExtensionsDir = Join-Path $Root 'extensions'
$CommonExtensions = @@COMMON_EXTENSIONS@@
$ProfileExtensions = @@PROFILE_EXTENSIONS@@
$ExtensionMetadata = @@EXTENSION_METADATA@@
$FileHashes = @@FILE_HASHES@@
$ExtensionBatchSize = 20
$DefaultSettingsSource = Join-Path $Root '@@DEFAULT_SETTINGS_PATH@@'
$SharedSettingsProfiles = @@SHARED_SETTINGS_PROFILES@@
$ProfileSettings = @@PROFILE_SETTINGS@@
$DefaultResources = @@DEFAULT_RESOURCES@@
$SharedProfileResources = @@SHARED_PROFILE_RESOURCES@@
$ProfileResources = @@PROFILE_RESOURCES@@
$XmlCatalogSettingToken = @@XML_CATALOG_TOKEN@@
$InstallLog = Join-Path ([IO.Path]::GetTempPath()) (
    'dev-forge-install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' +
    [Guid]::NewGuid().ToString('N') + '.log'
)

function Write-InstallLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    $Line = ('{0:yyyy-MM-dd HH:mm:ss.fff} {1}' -f (Get-Date), $Message)
    [IO.File]::AppendAllText(
        $InstallLog,
        $Line + [Environment]::NewLine,
        (New-Object Text.UTF8Encoding($false))
    )
}

function ConvertTo-NativeArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    return '"' + $Value.Replace('"', ([char]92 + '"')) + '"'
}

function Stop-ExternalProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        $Children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction Stop)
    } catch {
        $Children = @()
        Write-InstallLog "无法枚举 PID=$ProcessId 的子进程: $($_.Exception.Message)"
    }
    foreach ($Child in $Children) {
        Stop-ExternalProcessTree -ProcessId ([int]$Child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Invoke-ExternalProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label,
        [int]$TimeoutSeconds = $ProcessTimeoutSeconds,
        [switch]$Quiet
    )
    $Token = [Guid]::NewGuid().ToString('N')
    $StdOutPath = Join-Path ([IO.Path]::GetTempPath()) "dev-forge-$Token.out.log"
    $StdErrPath = Join-Path ([IO.Path]::GetTempPath()) "dev-forge-$Token.err.log"
    $ArgumentLine = (@(
        $Arguments | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }
    ) -join ' ')
    $StartedAt = Get-Date
    Write-InstallLog "START [$Label] $FilePath $ArgumentLine"
    try {
        $Process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $ArgumentLine `
            -RedirectStandardOutput $StdOutPath `
            -RedirectStandardError $StdErrPath `
            -PassThru
        if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-ExternalProcessTree -ProcessId $Process.Id
            $null = $Process.WaitForExit(5000)
            $TimedOutStdOut = if (Test-Path -LiteralPath $StdOutPath) {
                [IO.File]::ReadAllText($StdOutPath)
            } else { '' }
            $TimedOutStdErr = if (Test-Path -LiteralPath $StdErrPath) {
                [IO.File]::ReadAllText($StdErrPath)
            } else { '' }
            Write-InstallLog "TIMEOUT [$Label] PID=$($Process.Id) after ${TimeoutSeconds}s"
            if ($TimedOutStdOut) {
                Write-InstallLog "STDOUT [$Label] $($TimedOutStdOut.TrimEnd())"
            }
            if ($TimedOutStdErr) {
                Write-InstallLog "STDERR [$Label] $($TimedOutStdErr.TrimEnd())"
            }
            throw "$Label 超时（${TimeoutSeconds} 秒）。日志: $InstallLog"
        }
        $StdOut = if (Test-Path -LiteralPath $StdOutPath) {
            [IO.File]::ReadAllText($StdOutPath)
        } else { '' }
        $StdErr = if (Test-Path -LiteralPath $StdErrPath) {
            [IO.File]::ReadAllText($StdErrPath)
        } else { '' }
        $Duration = [Math]::Round(((Get-Date) - $StartedAt).TotalSeconds, 3)
        Write-InstallLog "END [$Label] PID=$($Process.Id) exit=$($Process.ExitCode) duration=${Duration}s"
        if ($StdOut) { Write-InstallLog "STDOUT [$Label] $($StdOut.TrimEnd())" }
        if ($StdErr) { Write-InstallLog "STDERR [$Label] $($StdErr.TrimEnd())" }
        if (-not $Quiet) {
            if ($StdOut) { Write-Host $StdOut.TrimEnd() }
            if ($StdErr) { Write-Warning $StdErr.TrimEnd() }
        }
        return [PSCustomObject]@{
            ExitCode = $Process.ExitCode
            StdOut = $StdOut
            StdErr = $StdErr
            DurationSeconds = $Duration
        }
    } finally {
        Remove-Item -LiteralPath $StdOutPath,$StdErrPath -Force -ErrorAction SilentlyContinue
    }
}

Write-InstallLog "Dev Forge 安装开始。Root=$Root Package=$PackageKind Version=$TargetVSCodeVersion Arch=$TargetVSCodeArch"
Write-Host "安装日志: $InstallLog"

if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "VS Code 安装文件不存在: $Installer"
}
if (-not (Test-Path -LiteralPath $ExtensionsDir -PathType Container)) {
    throw "扩展目录不存在: $ExtensionsDir"
}

# 在删除现有扩展前检查清单中的所有输入文件，避免离线包不完整时破坏当前环境。
$RequiredExtensions = @($CommonExtensions)
foreach ($ProfileName in $ProfileExtensions.Keys) {
    $RequiredExtensions += @($ProfileExtensions[$ProfileName])
}
foreach ($Extension in @($RequiredExtensions | Sort-Object -Unique)) {
    $ExtensionPath = Join-Path $Root $Extension
    if (-not (Test-Path -LiteralPath $ExtensionPath -PathType Leaf)) {
        throw "扩展文件不存在: $ExtensionPath"
    }
}
$RequiredSettingsFiles = @($DefaultSettingsSource)
foreach ($ProfileName in $ProfileSettings.Keys) {
    $RequiredSettingsFiles += Join-Path $Root $ProfileSettings[$ProfileName]
}
foreach ($SettingsPath in @($RequiredSettingsFiles | Sort-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $SettingsPath -PathType Leaf)) {
        throw "配置文件不存在: $SettingsPath"
    }
}
foreach ($ResourceName in $DefaultResources.Keys) {
    $ResourcePath = Join-Path $Root $DefaultResources[$ResourceName]
    $PathType = if ($ResourceName -in @('snippets', 'xml')) { 'Container' } else { 'Leaf' }
    if (-not (Test-Path -LiteralPath $ResourcePath -PathType $PathType)) {
        throw "Profile 资源不存在: $ResourcePath"
    }
}
foreach ($ProfileName in $ProfileResources.Keys) {
    foreach ($ResourceName in $ProfileResources[$ProfileName].Keys) {
        $ResourcePath = Join-Path $Root $ProfileResources[$ProfileName][$ResourceName]
        $PathType = if ($ResourceName -eq 'snippets') { 'Container' } else { 'Leaf' }
        if (-not (Test-Path -LiteralPath $ResourcePath -PathType $PathType)) {
            throw "Profile 资源不存在: $ResourcePath"
        }
    }
}

# 所有哈希必须在扩展清理前验证，避免损坏的离线包破坏已有环境。
foreach ($RelativePath in $FileHashes.Keys) {
    $FilePath = Join-Path $Root $RelativePath
    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        throw "待校验文件不存在: $FilePath"
    }
    $ActualHash = (Get-FileHash -LiteralPath $FilePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $ExpectedHash = ([string]$FileHashes[$RelativePath]).ToLowerInvariant()
    if ($ActualHash -ne $ExpectedHash) {
        throw "SHA-256 校验失败: $FilePath；期望 $ExpectedHash，实际 $ActualHash"
    }
}
Write-Host "离线包 SHA-256 校验完成，共 $($FileHashes.Count) 个文件。"

function Get-CodeInstallations {
    $Candidates = @()
    if (Test-Path -LiteralPath $ArchiveTarget -PathType Container) {
        $ArchiveCodePath = Get-ChildItem -LiteralPath $ArchiveTarget -Filter 'code.cmd' -File -Recurse |
            Where-Object { $_.FullName -like '*\bin\code.cmd' } |
            Select-Object -First 1 -ExpandProperty FullName
        if ($ArchiveCodePath) {
            $Candidates += [PSCustomObject]@{ Kind = 'archive'; Path = $ArchiveCodePath }
        }
    }
    if ($env:LOCALAPPDATA) {
        $Candidates += [PSCustomObject]@{
            Kind = 'user'
            Path = Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code\bin\code.cmd'
        }
    }
    if ($env:ProgramFiles) {
        $Candidates += [PSCustomObject]@{
            Kind = 'system'
            Path = Join-Path $env:ProgramFiles 'Microsoft VS Code\bin\code.cmd'
        }
    }
    $RegistrySources = @(
        [PSCustomObject]@{
            Kind = 'user'
            Path = 'Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
        },
        [PSCustomObject]@{
            Kind = 'system'
            Path = 'Registry::HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
        },
        [PSCustomObject]@{
            Kind = 'system'
            Path = 'Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
        }
    )
    foreach ($RegistrySource in $RegistrySources) {
        foreach ($RegistryEntry in @(Get-ItemProperty -Path $RegistrySource.Path -ErrorAction SilentlyContinue)) {
            $DisplayNameProperty = $RegistryEntry.PSObject.Properties['DisplayName']
            $InstallLocationProperty = $RegistryEntry.PSObject.Properties['InstallLocation']
            if (-not $DisplayNameProperty -or -not $InstallLocationProperty) { continue }
            $DisplayName = [string]$DisplayNameProperty.Value
            if ($DisplayName -notlike 'Microsoft Visual Studio Code*' -or
                $DisplayName -like '*Insiders*') { continue }
            $InstallLocation = [string]$InstallLocationProperty.Value
            if ([string]::IsNullOrWhiteSpace($InstallLocation)) { continue }
            $Candidates += [PSCustomObject]@{
                Kind = $RegistrySource.Kind
                Path = Join-Path $InstallLocation 'bin\code.cmd'
            }
        }
    }

    $SeenPaths = @{}
    foreach ($Candidate in $Candidates) {
        if (-not (Test-Path -LiteralPath $Candidate.Path -PathType Leaf)) { continue }
        if ($SeenPaths.ContainsKey($Candidate.Path)) { continue }
        $SeenPaths[$Candidate.Path] = $true
        $Candidate
    }
}

function Find-CodeCommand {
    return Get-CodeInstallations |
        Where-Object { $_.Kind -eq $PackageKind } |
        Select-Object -First 1 -ExpandProperty Path
}

function Get-CodeInstallationInfo {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Result = Invoke-ExternalProcess `
        -FilePath $Path `
        -Arguments @('--version') `
        -Label 'VS Code 版本检测' `
        -Quiet
    $VersionLines = @($Result.StdOut -split "`r?`n" | Where-Object { $_ -ne '' })
    if ($Result.ExitCode -ne 0 -or $VersionLines.Count -eq 0) { return $null }
    $DetectedArch = if ($VersionLines.Count -ge 3) { ([string]$VersionLines[2]).Trim() } else { $null }
    return @{
        Version = ([string]$VersionLines[0]).Trim()
        Arch = $DetectedArch
    }
}

$ExistingArchiveCodePath = $null
$ExistingArchiveInfo = $null
if ($ArchiveMode) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        $ArchiveProbe = [IO.Compression.ZipFile]::OpenRead($Installer)
        $ArchiveProbe.Dispose()
    } catch {
        throw "VS Code Archive ZIP 无法读取: $Installer。$($_.Exception.Message)"
    }
    if (Test-Path -LiteralPath $ArchiveTarget -PathType Container) {
        $ExistingArchiveCodePath = Find-CodeCommand
        $ExistingArchiveInfo = if ($ExistingArchiveCodePath) {
            Get-CodeInstallationInfo -Path $ExistingArchiveCodePath
        } else { $null }
        $ArchiveMatches = $ExistingArchiveInfo -and
            ($ExistingArchiveInfo.Version -eq $TargetVSCodeVersion) -and
            ($ExistingArchiveInfo.Arch -eq $TargetVSCodeArch)
        if ((-not $ArchiveMatches) -and (-not $ForceVSCodeInstall)) {
            $DetectedArchiveVersion = if ($ExistingArchiveInfo) {
                "$($ExistingArchiveInfo.Version)/$($ExistingArchiveInfo.Arch)"
            } else { '无法识别' }
            throw "Archive 目录已存在但版本/架构不匹配: $DetectedArchiveVersion；期望 $TargetVSCodeVersion/$TargetVSCodeArch。使用 -ForceVSCodeInstall 替换。"
        }
    }
}

$ExistingCodeInstallations = @(Get-CodeInstallations)
$DetectedPackageKinds = @(
    $ExistingCodeInstallations |
        Select-Object -ExpandProperty Kind -Unique
)
if ($DetectedPackageKinds.Count -gt 0 -and $DetectedPackageKinds -notcontains $PackageKind) {
    $DetectedPackageLabel = $DetectedPackageKinds -join ', '
    throw "检测到已安装的 VS Code package 为 $DetectedPackageLabel，与离线包 package 参数 $PackageKind 不一致。请使用匹配的离线包，或先卸载/移除现有 VS Code 后重试。"
}

$RunningCodeProcesses = @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue)
if ($RunningCodeProcesses.Count -gt 0) {
    throw '检测到 VS Code 正在运行。请关闭所有 VS Code 窗口，并从独立 PowerShell 重新运行安装脚本。'
}

function Assert-SafeExtensionsDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw '拒绝删除空的扩展目录路径。'
    }
    $FullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $PathRoot = [IO.Path]::GetPathRoot($FullPath).TrimEnd('\')
    $UserProfile = [IO.Path]::GetFullPath(
        [Environment]::GetFolderPath('UserProfile')
    ).TrimEnd('\')
    $BundleRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $SourceExtensions = [IO.Path]::GetFullPath($ExtensionsDir).TrimEnd('\')
    $UserDataPath = [IO.Path]::GetFullPath($UserDataRoot).TrimEnd('\')
    $LeafName = Split-Path $FullPath -Leaf
    if ($LeafName -ine 'extensions') {
        throw "拒绝删除非 extensions 目录: $FullPath"
    }
    if ($FullPath -ieq $PathRoot -or
        $FullPath -ieq $UserProfile -or
        $FullPath -ieq $BundleRoot -or
        $FullPath -ieq $SourceExtensions -or
        $FullPath -ieq $UserDataPath -or
        $BundleRoot.StartsWith($FullPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝删除危险的扩展目录: $FullPath"
    }
    if (Test-Path -LiteralPath $FullPath) {
        $PathItem = Get-Item -LiteralPath $FullPath -Force
        while ($PathItem) {
            if (($PathItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "拒绝递归删除包含符号链接或目录联接的扩展路径: $FullPath"
            }
            $PathItem = $PathItem.Parent
        }
    }
    return $FullPath
}

if ($ReplaceExtensions) {
    Write-Warning '-ReplaceExtensions 会删除本机现有扩展；如果已启用 Settings Sync，请先关闭 Extensions 和 Profiles 同步。'
    $UserExtensionsDirs = @(
        (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.vscode\extensions')
    )
    if ($env:VSCODE_EXTENSIONS) {
        $CustomExtensionsDir = [IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables($env:VSCODE_EXTENSIONS)
        ).TrimEnd('\')
        $CurrentUserRoot = [IO.Path]::GetFullPath(
            [Environment]::GetFolderPath('UserProfile')
        ).TrimEnd('\')
        if ((-not $CustomExtensionsDir.StartsWith(
                $CurrentUserRoot + '\',
                [StringComparison]::OrdinalIgnoreCase
            )) -and (-not $AllowExternalExtensionsDirectory)) {
            throw "VSCODE_EXTENSIONS 位于当前用户目录之外: $CustomExtensionsDir。确认路径后使用 -AllowExternalExtensionsDirectory 才允许递归删除。"
        }
        $UserExtensionsDirs += $CustomExtensionsDir
    }
    if ($ArchiveMode) {
        $UserExtensionsDirs += Join-Path $ArchiveTarget 'data\extensions'
    }
    foreach ($UserExtensionsDir in @($UserExtensionsDirs | Sort-Object -Unique)) {
        $UserExtensionsDir = Assert-SafeExtensionsDirectory -Path $UserExtensionsDir
        if (Test-Path -LiteralPath $UserExtensionsDir) {
            Write-Host "正在删除当前用户的 VS Code 扩展目录: $UserExtensionsDir"
            Remove-Item -LiteralPath $UserExtensionsDir -Recurse -Force
        }
    }

    # 删除物理扩展目录后也必须清理现有 Profile 的扩展清单。
    $ProfileExtensionStateFiles = @()
    $LegacyDefaultExtensionState = Join-Path $UserDataRoot 'extensions.json'
    if (Test-Path -LiteralPath $LegacyDefaultExtensionState -PathType Leaf) {
        $ProfileExtensionStateFiles += $LegacyDefaultExtensionState
    }
    $ProfilesRoot = Join-Path $UserDataRoot 'profiles'
    if (Test-Path -LiteralPath $ProfilesRoot -PathType Container) {
        $ProfileExtensionStateFiles += @(
            Get-ChildItem -LiteralPath $ProfilesRoot -Filter 'extensions.json' -File -Recurse |
                Select-Object -ExpandProperty FullName
        )
    }
    foreach ($ExtensionStateFile in @($ProfileExtensionStateFiles | Sort-Object -Unique)) {
        Write-Host "正在清理 VS Code Profile 扩展清单: $ExtensionStateFile"
        Remove-Item -LiteralPath $ExtensionStateFile -Force
    }
} else {
    Write-Host '默认保留本机现有扩展，只安装或更新离线包清单中的扩展。'
}

if ($ArchiveMode) {
    if ((Test-Path -LiteralPath $ArchiveTarget -PathType Container) -and
        (-not $ForceVSCodeInstall)) {
        $CodePath = $ExistingArchiveCodePath
        Write-Host "Archive 版本和架构匹配，继续使用: $ArchiveTarget"
    } else {
        if (Test-Path -LiteralPath $ArchiveTarget) {
            Write-Host "已指定 -ForceVSCodeInstall，替换 Archive 目录: $ArchiveTarget"
            Remove-Item -LiteralPath $ArchiveTarget -Recurse -Force
        }
        Write-Host '正在解压 VS Code...'
        Expand-Archive -LiteralPath $Installer -DestinationPath $ArchiveTarget
        $CodePath = Find-CodeCommand
        $ArchiveInfo = if ($CodePath) { Get-CodeInstallationInfo -Path $CodePath } else { $null }
        if ((-not $ArchiveInfo) -or
            ($ArchiveInfo.Version -ne $TargetVSCodeVersion) -or
            ($ArchiveInfo.Arch -ne $TargetVSCodeArch)) {
            throw "解压后的 VS Code 版本或架构与离线清单不一致。"
        }
    }
} else {
    $ExistingCodePath = Find-CodeCommand
    $ExistingCodeInfo = if ($ExistingCodePath) { Get-CodeInstallationInfo -Path $ExistingCodePath } else { $null }
    $SameVersion = $ExistingCodeInfo -and
        ($ExistingCodeInfo.Version -eq $TargetVSCodeVersion) -and
        ((-not $ExistingCodeInfo.Arch) -or ($ExistingCodeInfo.Arch -eq $TargetVSCodeArch))
    if ($SameVersion -and (-not $ForceVSCodeInstall)) {
        $CodePath = $ExistingCodePath
        Write-Host "已安装相同版本的 VS Code $TargetVSCodeVersion，跳过安装器。"
    } else {
        if ($ForceVSCodeInstall) {
            Write-Host '已指定 -ForceVSCodeInstall，强制重新安装 VS Code...'
        } else {
            Write-Host '正在安装 VS Code...'
        }
        $InstallResult = Invoke-ExternalProcess `
            -FilePath $Installer `
            -Arguments @('/VERYSILENT','/NORESTART','/MERGETASKS=!runcode') `
            -Label 'VS Code 安装器' `
            -TimeoutSeconds $InstallerTimeoutSeconds
        if ($InstallResult.ExitCode -ne 0) {
            throw "VS Code 安装失败，退出码: $($InstallResult.ExitCode)。日志: $InstallLog"
        }
        $CodePath = Find-CodeCommand
    }
}

if (-not $CodePath) {
    $CodePath = Find-CodeCommand
}
if (-not $CodePath) { throw '未找到 code.cmd；请确认 VS Code 已成功安装或解压。' }

function Test-ProfileAvailable {
    param([Parameter(Mandatory = $true)][string]$Name)

    $Result = Invoke-ExternalProcess `
        -FilePath $CodePath `
        -Arguments @('--profile', $Name, '--list-extensions') `
        -Label "探测 Profile $Name" `
        -Quiet
    return ($Result.ExitCode -eq 0)
}

function Ensure-Profile {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (Test-ProfileAvailable -Name $Name) {
        Write-Host "Profile 已存在: $Name"
        return
    }

    $BootstrapFolder = Join-Path ([IO.Path]::GetTempPath()) (
        'dev-forge-profile-' + [Guid]::NewGuid().ToString('N')
    )
    New-Item -ItemType Directory -Path $BootstrapFolder -Force | Out-Null
    $Created = $false
    try {
        Write-Host "正在创建 Profile: $Name..."
        $CreateResult = Invoke-ExternalProcess `
            -FilePath $CodePath `
            -Arguments @('--profile', $Name, '--new-window', $BootstrapFolder) `
            -Label "创建 Profile $Name"
        if ($CreateResult.ExitCode -ne 0) {
            throw "创建 Profile 失败: $Name，退出码: $($CreateResult.ExitCode)"
        }

        for ($Attempt = 0; $Attempt -lt 80; $Attempt++) {
            Start-Sleep -Milliseconds 250
            if (Test-ProfileAvailable -Name $Name) {
                $Created = $true
                break
            }
        }
    } finally {
        $BootstrapProcesses = @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue)
        foreach ($Process in $BootstrapProcesses) {
            if ($Process.MainWindowHandle -ne 0) { $null = $Process.CloseMainWindow() }
        }
        Start-Sleep -Milliseconds 500
        @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue) |
            Stop-Process -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $BootstrapFolder -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (-not $Created) {
        throw "VS Code 未能在 20 秒内创建 Profile: $Name"
    }

    if (-not (Test-ProfileAvailable -Name $Name)) {
        throw "关闭创建窗口后 VS Code 无法识别 Profile: $Name"
    }
    Write-Host "已创建 Profile: $Name"
}

$InstalledExtensionPaths = @{}

function Stop-ResidualCodeProcesses {
    $Processes = @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue)
    if ($Processes.Count -eq 0) { return }

    Write-Host '正在关闭扩展安装产生的残留 VS Code 进程...'
    $Processes | Stop-Process -Force -ErrorAction SilentlyContinue
    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
        if (@(Get-Process -Name 'Code' -ErrorAction SilentlyContinue).Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    }
    throw '无法关闭扩展安装产生的 VS Code 进程，请重新运行安装脚本。'
}

function Get-InstalledExtensionVersions {
    param(
        [string]$Profile
    )

    $Arguments = @('--list-extensions', '--show-versions')
    if ($Profile) { $Arguments += @('--profile', $Profile) }
    $Result = Invoke-ExternalProcess `
        -FilePath $CodePath `
        -Arguments $Arguments `
        -Label '读取已安装扩展' `
        -Quiet
    $ExtensionLines = @($Result.StdOut -split "`r?`n" | Where-Object { $_ -ne '' })
    if ($Result.ExitCode -ne 0) {
        $ProfileLabel = if ($Profile) { $Profile } else { 'Default' }
        throw "无法读取 $ProfileLabel Profile 的已安装扩展，退出码: $($Result.ExitCode)"
    }

    $Versions = @{}
    foreach ($Line in $ExtensionLines) {
        $Text = ([string]$Line).Trim()
        $Separator = $Text.LastIndexOf('@')
        if ($Separator -le 0 -or $Separator -ge ($Text.Length - 1)) { continue }
        $ExtensionId = $Text.Substring(0, $Separator)
        $ExtensionVersion = $Text.Substring($Separator + 1)
        $Versions[$ExtensionId] = $ExtensionVersion
    }
    return $Versions
}

function Install-ExtensionBatch {
    param(
        [Parameter(Mandatory = $true)][string[]]$RelativePaths,
        [string]$Profile
    )

    if ($RelativePaths.Count -eq 0) { return }
    $ProfileLabel = if ($Profile) { $Profile } else { 'Default' }
    $Arguments = @()
    $ExtensionPaths = @()
    $IsRepeatedInstall = $false
    foreach ($RelativePath in $RelativePaths) {
        $ExtensionPath = Join-Path $Root $RelativePath
        if (-not (Test-Path -LiteralPath $ExtensionPath -PathType Leaf)) {
            throw "扩展文件不存在: $ExtensionPath"
        }
        $ExtensionPaths += $ExtensionPath
        $Arguments += @('--install-extension', $ExtensionPath)
        $ExtensionKey = [IO.Path]::GetFullPath($ExtensionPath).ToLowerInvariant()
        if ($InstalledExtensionPaths.ContainsKey($ExtensionKey)) {
            $IsRepeatedInstall = $true
        }
    }
    # 清单已经显式包含离线所需扩展。禁止 CLI 再次展开 Extension Pack 和依赖，
    # 避免批量参数与自动展开任务并发安装同一 VSIX，造成 EPERM rename 冲突。
    $Arguments += '--do-not-include-pack-dependencies'
    $Arguments += '--force'
    if ($Profile) { $Arguments += @('--profile', $Profile) }

    $MaxAttempts = if ($RelativePaths.Count -eq 1) { 2 } else { 1 }
    for ($Attempt = 1; $Attempt -le $MaxAttempts; $Attempt++) {
        # 本地 VSIX 安装到另一个 Profile 时，VS Code 会重新处理同一个物理目录。
        # Oracle 等包含原生模块的扩展可能仍被上一次 CLI 进程占用，因此先清理残留进程。
        if ($IsRepeatedInstall -or ($Attempt -gt 1)) {
            Stop-ResidualCodeProcesses
        }

        Write-Host "正在向 $ProfileLabel Profile 批量安装 $($RelativePaths.Count) 个扩展..."
        $InstallResult = Invoke-ExternalProcess `
            -FilePath $CodePath `
            -Arguments $Arguments `
            -Label "安装 $ProfileLabel Profile 扩展"
        $ExtensionExitCode = $InstallResult.ExitCode
        if ($ExtensionExitCode -eq 0) {
            foreach ($ExtensionPath in $ExtensionPaths) {
                $ExtensionKey = [IO.Path]::GetFullPath($ExtensionPath).ToLowerInvariant()
                $InstalledExtensionPaths[$ExtensionKey] = $true
            }
            return
        }
        if ($RelativePaths.Count -gt 1) {
            Write-Warning "批量安装失败，将拆分为单个扩展重试。Profile: $ProfileLabel，退出码: $ExtensionExitCode"
            Stop-ResidualCodeProcesses
            foreach ($RelativePath in $RelativePaths) {
                Install-ExtensionBatch -RelativePaths @($RelativePath) -Profile $Profile
            }
            return
        }
        if ($Attempt -lt $MaxAttempts) {
            $ExtensionFile = Split-Path $ExtensionPaths[0] -Leaf
            Write-Warning "扩展安装失败，将在清理 VS Code 进程后重试: $ExtensionFile，退出码: $ExtensionExitCode"
            Start-Sleep -Milliseconds 500
        }
    }
    $ExtensionFile = Split-Path $ExtensionPaths[0] -Leaf
    throw "扩展安装失败: $ExtensionFile，Profile: $ProfileLabel，退出码: $ExtensionExitCode"
}

function Install-ProfileExtensions {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$RelativePaths,
        [string]$Profile
    )

    if ($RelativePaths.Count -eq 0) { return }
    $ProfileLabel = if ($Profile) { $Profile } else { 'Default' }
    $InstalledVersions = if (-not $ReplaceExtensions) {
        Get-InstalledExtensionVersions -Profile $Profile
    } else {
        @{}
    }
    $PendingExtensions = @()
    $SkippedCount = 0
    foreach ($RelativePath in @($RelativePaths | Select-Object -Unique)) {
        $Metadata = $ExtensionMetadata[$RelativePath]
        if (-not $Metadata) {
            throw "扩展元数据不存在: $RelativePath"
        }
        $InstalledVersion = $InstalledVersions[[string]$Metadata.Id]
        if ((-not $ReplaceExtensions) -and $InstalledVersion -eq [string]$Metadata.Version) {
            $SkippedCount++
            continue
        }
        $PendingExtensions += $RelativePath
    }
    if ($SkippedCount -gt 0) {
        Write-Host "$ProfileLabel Profile 已有 $SkippedCount 个相同版本扩展，跳过。"
    }
    if ($PendingExtensions.Count -eq 0) {
        Write-Host "$ProfileLabel Profile 的扩展已是目标版本。"
        return
    }
    for ($Offset = 0; $Offset -lt $PendingExtensions.Count; $Offset += $ExtensionBatchSize) {
        $Batch = @($PendingExtensions | Select-Object -Skip $Offset -First $ExtensionBatchSize)
        Install-ExtensionBatch -RelativePaths $Batch -Profile $Profile
    }
}

# Profile 的扩展集合相互独立。通用扩展需要同时登记到每个 Profile，
# 才能在切换后继续使用。
foreach ($ProfileName in $ProfileExtensions.Keys) {
    Ensure-Profile -Name $ProfileName
}
Install-ProfileExtensions -RelativePaths $CommonExtensions
foreach ($ProfileName in $ProfileExtensions.Keys) {
    $ExtensionsForProfile = @($CommonExtensions) + @($ProfileExtensions[$ProfileName])
    Install-ProfileExtensions -RelativePaths $ExtensionsForProfile -Profile $ProfileName
}

function Copy-SettingsFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "$Label 配置文件不存在: $Source"
    }
    if ((-not (Test-Path -LiteralPath $Target -PathType Leaf)) -or $ForceSettings) {
        New-Item -ItemType Directory -Path (Split-Path $Target) -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Target -Force
        Write-Host "$Label settings.json 已恢复。"
    } else {
        Write-Warning "$Label settings.json 已存在，未覆盖。使用 -ForceSettings 可覆盖。"
    }
}

function Get-ProfileResourceTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Base,
        [Parameter(Mandatory = $true)][string]$ResourceName
    )
    $Filename = switch ($ResourceName) {
        'keybindings' { 'keybindings.json' }
        'snippets' { 'snippets' }
        'tasks' { 'tasks.json' }
        'mcp' { 'mcp.json' }
        'xml' { 'xml' }
        default { throw "未知的 Profile 资源: $ResourceName" }
    }
    return Join-Path $Base $Filename
}

function Copy-ProfileResource {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][bool]$Directory
    )

    if ($Directory) {
        if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
            throw "$Label 资源目录不存在: $Source"
        }
        if ((Test-Path -LiteralPath $Target) -and
            (-not (Test-Path -LiteralPath $Target -PathType Container))) {
            if (-not $ForceResources) {
                Write-Warning "$Label 目标已存在且不是目录，未覆盖。使用 -ForceResources 可覆盖。"
                return
            }
            Remove-Item -LiteralPath $Target -Force
        }
        New-Item -ItemType Directory -Path $Target -Force | Out-Null
        foreach ($SourceFile in @(Get-ChildItem -LiteralPath $Source -File -Recurse)) {
            $RelativePath = $SourceFile.FullName.Substring($Source.Length).TrimStart('\')
            $TargetFile = Join-Path $Target $RelativePath
            if ((-not (Test-Path -LiteralPath $TargetFile -PathType Leaf)) -or $ForceResources) {
                New-Item -ItemType Directory -Path (Split-Path $TargetFile) -Force | Out-Null
                Copy-Item -LiteralPath $SourceFile.FullName -Destination $TargetFile -Force
            } else {
                Write-Warning "$Label 文件已存在，未覆盖: $RelativePath"
            }
        }
        Write-Host "$Label 资源目录已合并。"
        return
    }

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "$Label 资源文件不存在: $Source"
    }
    if ((-not (Test-Path -LiteralPath $Target -PathType Leaf)) -or $ForceResources) {
        New-Item -ItemType Directory -Path (Split-Path $Target) -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Target -Force
        Write-Host "$Label 资源已恢复。"
    } else {
        Write-Warning "$Label 已存在，未覆盖。使用 -ForceResources 可覆盖。"
    }
}

function Resolve-XmlCatalogSetting {
    param(
        [Parameter(Mandatory = $true)][string]$SettingsPath,
        [Parameter(Mandatory = $true)][string]$CatalogPath,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $SettingsContent = [IO.File]::ReadAllText($SettingsPath)
    $CatalogJsonPath = $CatalogPath.Replace('\', '\\')
    if ($SettingsContent.Contains($CatalogJsonPath)) {
        Write-Host "$Label 已注册 XML Catalog: $CatalogPath"
        return
    }
    if (-not $SettingsContent.Contains($XmlCatalogSettingToken)) {
        Write-Warning "$Label settings.json 已保留，尚未注册 XML Catalog。使用 -ForceSettings 重新运行可写入配置。"
        return
    }
    $SettingsContent = $SettingsContent.Replace($XmlCatalogSettingToken, $CatalogJsonPath)
    $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($SettingsPath, $SettingsContent, $Utf8WithoutBom)
    Write-Host "$Label 已注册 XML Catalog: $CatalogPath"
}

$DefaultSettingsTarget = Join-Path $UserDataRoot 'settings.json'
Copy-SettingsFile `
    -Source $DefaultSettingsSource `
    -Target $DefaultSettingsTarget `
    -Label 'Default'

$XmlCatalogTarget = $null
foreach ($ResourceName in $DefaultResources.Keys) {
    $Source = Join-Path $Root $DefaultResources[$ResourceName]
    $Target = Get-ProfileResourceTarget -Base $UserDataRoot -ResourceName $ResourceName
    Copy-ProfileResource `
        -Source $Source `
        -Target $Target `
        -Label "Default $ResourceName" `
        -Directory ($ResourceName -in @('snippets', 'xml'))
    if ($ResourceName -eq 'xml') {
        $XmlCatalogTarget = Join-Path $Target 'catalog.xml'
    }
}
if ($XmlCatalogTarget) {
    if (-not (Test-Path -LiteralPath $XmlCatalogTarget -PathType Leaf)) {
        throw "XML Catalog 文件不存在: $XmlCatalogTarget"
    }
    Resolve-XmlCatalogSetting `
        -SettingsPath $DefaultSettingsTarget `
        -CatalogPath $XmlCatalogTarget `
        -Label 'Default'
}

if (($SharedSettingsProfiles.Count -gt 0) -or
    ($ProfileSettings.Count -gt 0) -or
    ($SharedProfileResources.Count -gt 0) -or
    ($ProfileResources.Count -gt 0)) {
    $StoragePath = Join-Path $UserDataRoot 'globalStorage\storage.json'
    if (-not (Test-Path -LiteralPath $StoragePath -PathType Leaf)) {
        throw "未找到 VS Code Profile 元数据: $StoragePath"
    }
    $Storage = Get-Content -LiteralPath $StoragePath -Raw | ConvertFrom-Json

    function Get-ProfileMetadata {
        param([Parameter(Mandatory = $true)][string]$Name)
        $Result = @($Storage.userDataProfiles | Where-Object { $_.name -eq $Name }) | Select-Object -First 1
        if (-not $Result) { throw "安装扩展后仍未找到 Profile: $Name" }
        return $Result
    }

    function Set-ProfileResourceInheritance {
        param(
            [Parameter(Mandatory = $true)]$ProfileInfo,
            [Parameter(Mandatory = $true)][string]$ResourceName,
            [Parameter(Mandatory = $true)][bool]$UseDefault
        )
        if ((-not $ProfileInfo.PSObject.Properties['useDefaultFlags']) -or
            ($null -eq $ProfileInfo.useDefaultFlags)) {
            $ProfileInfo | Add-Member -NotePropertyName 'useDefaultFlags' -NotePropertyValue ([pscustomobject]@{}) -Force
        }
        if ($ProfileInfo.useDefaultFlags.PSObject.Properties[$ResourceName]) {
            $ProfileInfo.useDefaultFlags.$ResourceName = $UseDefault
        } else {
            $ProfileInfo.useDefaultFlags | Add-Member -NotePropertyName $ResourceName -NotePropertyValue $UseDefault
        }
    }

    foreach ($ProfileName in $SharedSettingsProfiles) {
        $ProfileInfo = Get-ProfileMetadata -Name $ProfileName
        Set-ProfileResourceInheritance -ProfileInfo $ProfileInfo -ResourceName 'settings' -UseDefault $true
        Write-Host "$ProfileName Profile 已设置为共享 Default settings.json。"
    }

    foreach ($ProfileName in $ProfileSettings.Keys) {
        $ProfileInfo = Get-ProfileMetadata -Name $ProfileName
        Set-ProfileResourceInheritance -ProfileInfo $ProfileInfo -ResourceName 'settings' -UseDefault $false
        $Source = Join-Path $Root $ProfileSettings[$ProfileName]
        $Target = Join-Path $UserDataRoot "profiles\$($ProfileInfo.location)\settings.json"
        Copy-SettingsFile -Source $Source -Target $Target -Label $ProfileName
        if ($XmlCatalogTarget) {
            Resolve-XmlCatalogSetting `
                -SettingsPath $Target `
                -CatalogPath $XmlCatalogTarget `
                -Label $ProfileName
        }
    }

    foreach ($ProfileName in $SharedProfileResources.Keys) {
        $ProfileInfo = Get-ProfileMetadata -Name $ProfileName
        foreach ($ResourceName in $SharedProfileResources[$ProfileName]) {
            Set-ProfileResourceInheritance `
                -ProfileInfo $ProfileInfo `
                -ResourceName $ResourceName `
                -UseDefault $true
            Write-Host "$ProfileName Profile 已设置为共享 Default $ResourceName。"
        }
    }

    foreach ($ProfileName in $ProfileResources.Keys) {
        $ProfileInfo = Get-ProfileMetadata -Name $ProfileName
        $ProfileRoot = Join-Path $UserDataRoot "profiles\$($ProfileInfo.location)"
        foreach ($ResourceName in $ProfileResources[$ProfileName].Keys) {
            Set-ProfileResourceInheritance `
                -ProfileInfo $ProfileInfo `
                -ResourceName $ResourceName `
                -UseDefault $false
            $Source = Join-Path $Root $ProfileResources[$ProfileName][$ResourceName]
            $Target = Get-ProfileResourceTarget -Base $ProfileRoot -ResourceName $ResourceName
            Copy-ProfileResource `
                -Source $Source `
                -Target $Target `
                -Label "$ProfileName $ResourceName" `
                -Directory ($ResourceName -in @('snippets', 'xml'))
        }
    }

    # Windows PowerShell 5 的 Set-Content -Encoding UTF8 会写入 BOM，
    # VS Code 可能因此无法解析 storage.json。显式写入无 BOM UTF-8。
    $StorageJson = $Storage | ConvertTo-Json -Depth 100
    $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($StoragePath, $StorageJson, $Utf8WithoutBom)
}
Write-InstallLog '安装完成。'
Write-Host "完成。日志: $InstallLog"

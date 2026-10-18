# Windows: configure, build and run the judge. Forward all arguments to judge.exe.
$ErrorActionPreference = 'Stop'
$BuildDir = Join-Path $PSScriptRoot 'build-windows'
$JudgeExe = Join-Path $BuildDir 'judge.exe'

function Invoke-JudgeBuildAndRun {
    Write-Host '[1/3] 配置 CMake ...'
    if (-not (Test-Path -LiteralPath (Join-Path $BuildDir 'CMakeCache.txt'))) {
        if (Get-Command ninja -ErrorAction SilentlyContinue) {
            cmake -S $PSScriptRoot -B $BuildDir -G Ninja -DCMAKE_BUILD_TYPE=Debug | Out-Host
        } elseif (Get-Command mingw32-make -ErrorAction SilentlyContinue) {
            cmake -S $PSScriptRoot -B $BuildDir -G 'MinGW Makefiles' -DCMAKE_BUILD_TYPE=Debug | Out-Host
        } else {
            cmake -S $PSScriptRoot -B $BuildDir | Out-Host
        }
    } else {
        cmake -S $PSScriptRoot -B $BuildDir | Out-Host
    }
    if ($LASTEXITCODE -ne 0) { return 2 }

    Write-Host '[2/3] 编译判题器 ...'
    cmake --build $BuildDir --config Debug | Out-Host
    if ($LASTEXITCODE -ne 0) { return 2 }
    if (-not (Test-Path -LiteralPath $JudgeExe)) {
        $JudgeExe = Join-Path $BuildDir 'Debug/judge.exe'
    }
    if (-not (Test-Path -LiteralPath $JudgeExe)) {
        Write-Error '找不到编译后的 judge.exe'
        return 2
    }

    Write-Host '[3/3] 开始判题 ...'
    & $JudgeExe --root $PSScriptRoot @args | Out-Host
    return $LASTEXITCODE
}

# Native executables launched from PowerShell should agree on UTF-8. cmd.exe's
# pause message otherwise uses the system's legacy code page (often GBK).
$PreviousOutputEncoding = [Console]::OutputEncoding
$PreviousInputEncoding = [Console]::InputEncoding
$PreviousPipeEncoding = $OutputEncoding
$PreviousCodePageText = chcp.com
$PreviousCodePage = if ($PreviousCodePageText -match '(\d+)') { $Matches[1] } else { $null }
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$Result = 2
try {
    chcp.com 65001 | Out-Null
    [Console]::OutputEncoding = $Utf8
    [Console]::InputEncoding = $Utf8
    $OutputEncoding = $Utf8
    $Result = Invoke-JudgeBuildAndRun @args
} finally {
    if ($PreviousCodePage) { chcp.com $PreviousCodePage | Out-Null }
    [Console]::OutputEncoding = $PreviousOutputEncoding
    [Console]::InputEncoding = $PreviousInputEncoding
    $OutputEncoding = $PreviousPipeEncoding
}
exit $Result

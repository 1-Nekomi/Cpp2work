$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$workspace = Join-Path $root 'imports\validation-final'

# Build the judge before opening the workbench. The build is incremental, so an
# up-to-date tree only costs a quick CMake check. Failures are reported here but
# do not block import/review, which do not need judge.exe.
function Build-Judge {
    $buildDir = Join-Path $root 'build-windows'
    if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
        Write-Warning '未找到 cmake，跳过判题器自动编译。请安装 CMake 后重新启动。'
        return
    }
    Write-Host '正在检查并编译判题器 ...'
    if (-not (Test-Path -LiteralPath (Join-Path $buildDir 'CMakeCache.txt'))) {
        if (Get-Command ninja -ErrorAction SilentlyContinue) {
            cmake -S $root -B $buildDir -G Ninja -DCMAKE_BUILD_TYPE=Debug | Out-Host
        } elseif (Get-Command mingw32-make -ErrorAction SilentlyContinue) {
            cmake -S $root -B $buildDir -G 'MinGW Makefiles' -DCMAKE_BUILD_TYPE=Debug | Out-Host
        } else {
            cmake -S $root -B $buildDir | Out-Host
        }
    } else {
        cmake -S $root -B $buildDir | Out-Host
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'CMake 配置失败，判题器未编译；导入和复核仍可使用。'
        return
    }
    cmake --build $buildDir --config Debug | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Warning '判题器编译失败，请查看上方输出；导入和复核仍可使用。'
        return
    }
    $judgeExe = Join-Path $buildDir 'judge.exe'
    if (-not (Test-Path -LiteralPath $judgeExe)) {
        $judgeExe = Join-Path $buildDir 'Debug\judge.exe'
    }
    if (Test-Path -LiteralPath $judgeExe) {
        Write-Host ('判题器就绪：' + $judgeExe)
    } else {
        Write-Warning '编译完成但找不到 judge.exe，请检查 CMake 输出目录。'
    }
}

Build-Judge

if (-not (Get-Command g++ -ErrorAction SilentlyContinue)) {
    Write-Warning '未找到 g++，批量判题时无法编译学生代码；请安装 MinGW-w64 并加入 PATH。'
}

# Prefer validation-final when it has data; otherwise use the newest imported
# batch anywhere under the project (for example a custom output folder chosen
# in the Import form). If nothing exists, keep the default path and let the
# workbench start with an empty workspace so the Import menu can create a batch.
if (-not (Test-Path -LiteralPath (Join-Path $workspace 'batch.json'))) {
    $found = Get-ChildItem -LiteralPath $root -Directory -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notin @('.git', '__pycache__') -and (Test-Path -LiteralPath (Join-Path $_.FullName 'batch.json')) } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($found) { $workspace = $found.FullName }
}

& (Join-Path $root 'import.ps1') review --workspace $workspace --open

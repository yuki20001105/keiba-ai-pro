param([switch]$SkipInstall, [switch]$Benchmark)
$ErrorActionPreference = 'Stop'
$gpuRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$gpuVenv = [IO.Path]::GetFullPath((Join-Path $gpuRepo '.local-runtime/gpu-validation/.venv'))
$gpuPython = Join-Path $gpuVenv 'Scripts/python.exe'
$gpuBasePython = Join-Path $gpuRepo 'python-api/.venv/Scripts/python.exe'
$gpuReport = Join-Path $gpuRepo 'reports/generated/gpu-validation-20260919-install.json'
if (-not $gpuVenv.StartsWith($gpuRepo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'GPU environment must stay inside the named repository runtime directory.'
}
if ((Test-Path -LiteralPath $gpuVenv) -and (Get-Item -LiteralPath $gpuVenv).LinkType) {
    throw 'Refusing a linked GPU environment.'
}
if (-not (Test-Path -LiteralPath $gpuPython)) {
    & $gpuBasePython -m venv $gpuVenv
    if ($LASTEXITCODE -ne 0) { throw 'Isolated virtual environment creation failed.' }
}
if (-not $SkipInstall) {
    # Keep all Python/build tools isolated. Actual wheel GPU support is verified,
    # not assumed: this machine's 4.7.0 wheel failed the GPU learner probe.
    & $gpuPython -m pip install --disable-pip-version-check --index-url https://pypi.org/simple --only-binary=:all: `
        --report $gpuReport 'lightgbm==4.7.0' 'numpy==2.4.6' 'scipy==1.17.1' 'pandas==3.0.5' `
        'pyarrow==25.0.1' 'joblib==1.6.0' 'psutil==7.2.2' 'zstandard==0.25.0' 'cmake==4.2.0'
    if ($LASTEXITCODE -ne 0) { throw 'Pinned official GPU environment installation failed.' }
}
$gpuSources = @{
    'khronos-opencl-icd-loader-2024.10.24-hfd05255_2.conda' = 'https://conda.anaconda.org/conda-forge/win-64/khronos-opencl-icd-loader-2024.10.24-hfd05255_2.conda'
    'libboost-headers-1.84.0-h57928b3_7.conda' = 'https://conda.anaconda.org/conda-forge/win-64/libboost-headers-1.84.0-h57928b3_7.conda'
    'lightgbm-4.7.0.tar.gz' = 'https://files.pythonhosted.org/packages/63/8e/4db5e29290d7e619c307fdb8dab0a0514090af2ce3ec483050e024ec6126/lightgbm-4.7.0.tar.gz'
}
foreach ($gpuName in $gpuSources.Keys) {
    $gpuDownload = Join-Path $gpuVenv $gpuName
    if (-not (Test-Path -LiteralPath $gpuDownload)) {
        Invoke-WebRequest -Uri $gpuSources[$gpuName] -OutFile $gpuDownload -TimeoutSec 90
    }
}
& $gpuPython (Join-Path $gpuRepo 'scripts/gpu/prepare_build.py')
if ($LASTEXITCODE -ne 0) { throw 'Build input SHA verification / extraction failed.' }
$gpuCmake = Join-Path $gpuVenv 'Scripts/cmake.exe'
$gpuSource = Join-Path $gpuVenv 'source/lightgbm-4.7.0'
$gpuBuild = Join-Path $gpuVenv 'build'
$gpuBoost = Join-Path $gpuVenv 'boost184/Library/include'
$gpuCuda = 'C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v11.8'
if (-not (Test-Path -LiteralPath "$gpuCuda/include/CL/cl.h")) { throw 'Existing OpenCL headers required; no driver installation is performed.' }
& $gpuCmake -S $gpuSource -B $gpuBuild -G 'Visual Studio 17 2022' -A x64 -DUSE_GPU=ON -DBUILD_CLI=OFF `
    "-DGPU_VALIDATION_BOOST_INCLUDE=$gpuBoost" "-DOpenCL_LIBRARY=$gpuCuda/lib/x64/OpenCL.lib" "-DOpenCL_INCLUDE_DIR=$gpuCuda/include"
if ($LASTEXITCODE -ne 0) { throw 'Isolated GPU configuration failed.' }
& $gpuCmake --build $gpuBuild --target _lightgbm --config Release --parallel 4
if ($LASTEXITCODE -ne 0) { throw 'Isolated GPU compilation failed.' }
$gpuBuiltDll = Join-Path $gpuSource 'Release/lib_lightgbm.dll'
if (-not (Test-Path -LiteralPath $gpuBuiltDll)) { throw 'Expected GPU build output missing.' }
Copy-Item -LiteralPath $gpuBuiltDll -Destination (Join-Path $gpuVenv 'Lib/site-packages/lightgbm/bin/lib_lightgbm.dll') -Force
$gpuProbe = Join-Path $gpuRepo 'scripts/gpu/validate.py'
if ($Benchmark) { & $gpuPython $gpuProbe --benchmark }
else { & $gpuPython $gpuProbe }
if ($LASTEXITCODE -ne 0) { throw 'GPU verification failed; production environment is unchanged.' }

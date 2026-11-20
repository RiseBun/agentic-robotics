param([string]$BuildRoot=(Join-Path $PSScriptRoot 'build\robot-mvp'), [switch]$SkipArchive, [switch]$SkipInstaller)
$ErrorActionPreference='Stop'
$projectRoot=[IO.Path]::GetFullPath($PSScriptRoot)
$nodeExecutable=(Get-Command node -ErrorAction Stop).Source
$electronRoot=Join-Path $projectRoot 'desktop\node_modules\electron\dist'
if(-not(Test-Path -LiteralPath (Join-Path $electronRoot 'electron.exe'))){throw 'Install locked Electron dependencies first.'}
Push-Location (Join-Path $projectRoot 'frontend')
try {
  & $nodeExecutable 'node_modules/typescript/bin/tsc' --noEmit
  if($LASTEXITCODE -ne 0){throw 'Typecheck failed.'}
  & $nodeExecutable 'node_modules/vite/bin/vite.js' build
  if($LASTEXITCODE -ne 0){throw 'Frontend build failed.'}
} finally { Pop-Location }
$candidate=Join-Path ([IO.Path]::GetFullPath($BuildRoot)) ([DateTime]::Now.ToString('yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
$output=Join-Path $candidate 'windows'
New-Item -ItemType Directory -Force -Path $output | Out-Null
# Enumerate only the known Electron distribution.
Get-ChildItem -LiteralPath $electronRoot | Copy-Item -Destination $output -Recurse
$appRoot=Join-Path $output 'resources\app'
New-Item -ItemType Directory -Force -Path (Join-Path $appRoot 'desktop') | Out-Null
foreach($file in @('main.cjs','preload.cjs','runtime-supervisor.cjs')){ Copy-Item -LiteralPath (Join-Path $projectRoot ('desktop\'+$file)) -Destination (Join-Path $appRoot 'desktop') }
& $nodeExecutable (Join-Path $projectRoot 'runtime\scripts\stage-robot-runtime.mjs') (Join-Path $appRoot 'runtime')
if($LASTEXITCODE -ne 0){throw 'Runtime boundary check failed.'}
New-Item -ItemType Directory -Force -Path (Join-Path $appRoot 'frontend') | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'frontend\dist') -Destination (Join-Path $appRoot 'frontend') -Recurse
$utf8=New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $appRoot 'package.json'),'{"name":"embuddies-robot","productName":"embuddies","version":"0.4.0","main":"desktop/main.cjs"}',$utf8)
Rename-Item -LiteralPath (Join-Path $output 'electron.exe') -NewName 'embuddies.exe'
$serviceRoot=Join-Path $candidate 'cap-runtime'
foreach($directory in @('agentic_cap','tests','experiments')){
  $target=Join-Path $serviceRoot $directory
  New-Item -ItemType Directory -Force -Path $target | Out-Null
  Get-ChildItem -LiteralPath (Join-Path $projectRoot ('cap-runtime\'+$directory)) -File -Filter '*.py' | Copy-Item -Destination $target
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'cap-runtime\README.md') -Destination $serviceRoot
if(-not $SkipArchive){ Compress-Archive -LiteralPath $output -DestinationPath (Join-Path $candidate 'embuddies-robot-windows.zip') }
Write-Output ('Robot MVP bundle: '+$output)

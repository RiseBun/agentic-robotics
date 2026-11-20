param([switch]$Development, [switch]$VerifyOnly)
$ErrorActionPreference='Stop'
$electron=Join-Path $PSScriptRoot 'desktop\node_modules\electron\dist\electron.exe'
if(-not(Test-Path -LiteralPath $electron)){throw 'Install locked Electron dependencies first.'}
if(-not(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'frontend\dist\index.html'))){throw 'Build the React frontend first.'}
$namespace=if($Development){'embuddies-development'}else{'embuddies'}
$data=Join-Path $env:LOCALAPPDATA ($namespace+'\data')
$profile=Join-Path $env:LOCALAPPDATA ($namespace+'\profile')
if($VerifyOnly){[pscustomobject]@{executable=$electron;data=$data;profile=$profile;mode='robot'}|ConvertTo-Json;return}
$env:EMBUDDIES_DATA_DIR=$data
foreach($name in @('ELECTRON_RUN_AS_NODE','EMBUDDIES_ENGINE','EMBUDDIES_NATIVE_EXECUTABLE','EMBUDDIES_BUNDLE_ROOT','EMBUDDIES_RUNTIME_ROOT','EMBUDDIES_UI_ROOT')){Remove-Item -LiteralPath ('Env:'+$name) -ErrorAction SilentlyContinue}
New-Item -ItemType Directory -Force -Path $profile | Out-Null
Start-Process -FilePath $electron -ArgumentList @(('"'+(Join-Path $PSScriptRoot 'desktop')+'"'),('"--user-data-dir='+$profile+'"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden

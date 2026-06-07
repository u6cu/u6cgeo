$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$BuildDir = Join-Path $Root "build"
$DistDir = Join-Path $Root "dist"
$ReleaseDir = Join-Path $DistDir "U6C"

Write-Host "Installing build/runtime dependencies..."
py -3 -m pip install -r requirements.txt
py -3 -m pip install pyinstaller

Write-Host "Cleaning old build output..."
Remove-Item -LiteralPath $BuildDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $DistDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Building U6C.exe..."
py -3 -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --console `
    --name U6C `
    --distpath $DistDir `
    --workpath (Join-Path $BuildDir "main") `
    --specpath (Join-Path $BuildDir "specs") `
    u6c_pc_webcam.py

Write-Host "Copying models, certs, and docs next to U6C.exe..."
Copy-Item -LiteralPath (Join-Path $Root "models") -Destination $ReleaseDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Root "certs") -Destination $ReleaseDir -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $ReleaseDir -Force
Copy-Item -LiteralPath (Join-Path $Root "requirements.txt") -Destination $ReleaseDir -Force

Write-Host "Building U6C_Cert_Helper.exe..."
py -3 -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name U6C_Cert_Helper `
    --distpath $ReleaseDir `
    --workpath (Join-Path $BuildDir "cert_helper") `
    --specpath (Join-Path $BuildDir "specs") `
    make_u6c_phone_https_cert.py

$pcLanBat = @'
@echo off
cd /d "%~dp0"
U6C.exe --fps 30 --continuous-yolo --preload-yolo --lan-stream --lan-fps 30
pause
'@

$phoneBat = @'
@echo off
cd /d "%~dp0"
U6C.exe --phone-camera --phone-https --continuous-yolo --preload-yolo --phone-fps 30 --phone-processed-fps 30
pause
'@

$certBat = @'
@echo off
cd /d "%~dp0"
U6C_Cert_Helper.exe %*
pause
'@

$buildReadme = @'
U6C EXE release

Run options:
- Start_PC_Camera_LAN.bat
  Uses the PC webcam and hosts the processed view on LAN.

- Start_Phone_Camera.bat
  Hosts the HTTPS phone-camera page. Use U6C_Cert_Helper.exe first if the iPhone certificate is not installed/trusted yet.

- U6C.exe
  Main app executable. You can pass the same command-line flags as the Python version.

- U6C_Cert_Helper.exe
  Creates the U6C phone HTTPS certificate files in this folder's certs directory.

Notes:
- Keep the models folder next to U6C.exe.
- Keep the certs folder next to U6C.exe for phone HTTPS mode.
- This EXE packaging makes casual source theft harder, but it is not impossible to reverse.
'@

[System.IO.File]::WriteAllText((Join-Path $ReleaseDir "Start_PC_Camera_LAN.bat"), $pcLanBat, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText((Join-Path $ReleaseDir "Start_Phone_Camera.bat"), $phoneBat, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText((Join-Path $ReleaseDir "Make_U6C_Cert.bat"), $certBat, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText((Join-Path $ReleaseDir "EXE_RELEASE_README.txt"), $buildReadme, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "Build complete:"
Write-Host $ReleaseDir
Write-Host ""
Write-Host "Main executable:"
Write-Host (Join-Path $ReleaseDir "U6C.exe")

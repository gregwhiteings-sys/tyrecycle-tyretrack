@echo off
setlocal
cd /d "%~dp0"
echo Close TyreTrack and other camera applications before this test.
echo Testing both RVC2 models and recording for 60 seconds.
if exist "TyreTrack.exe" (
  "TyreTrack.exe" --oak-hardware-test --seconds 60 --output "%~dp0oak_hardware_report.json"
) else (
  py -3.12 oak_hardware_test.py --seconds 60 --output "%~dp0oak_hardware_report.json"
)
echo.
if exist "oak_hardware_report.json" type "oak_hardware_report.json"
echo.
echo Send oak_hardware_report.json back for review.
pause

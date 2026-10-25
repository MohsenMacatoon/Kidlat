@echo off
rem Starts Kidlat Download Manager without a black console window.
if exist "%~dp0dist\Kidlat.exe" (
  start "" "%~dp0dist\Kidlat.exe"
  exit /b
)

set "PYW="
set "PYC="
where pyw >nul 2>nul && (set "PYW=pyw -3" & set "PYC=py -3")
if not defined PYW where pythonw >nul 2>nul && (set "PYW=pythonw" & set "PYC=python")
if not defined PYW goto nopython

%PYC% -c "import tkinter" >nul 2>nul
if errorlevel 1 goto notk

start "" %PYW% "%~dp0app\kidlat.pyw"
exit /b

:notk
echo.
echo  Python is installed, but its window toolkit (tkinter) is missing.
echo  Kidlat needs it to show its window. To add it:
echo.
echo    1. Open Windows Settings ^> Apps ^> Installed apps
echo    2. Find "Python 3.x", click the ... button ^> Modify
echo    3. Click Modify, tick "tcl/tk and IDLE", then Next ^> Install
echo.
echo  Then run this file again.
echo.
pause
exit /b

:nopython
echo.
echo  Python is not installed.
echo  Install it from https://www.python.org/downloads/windows/
echo  (tick "Add python.exe to PATH" during setup), then run this again.
echo.
pause

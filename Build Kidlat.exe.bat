@echo off
rem Optional: turns Kidlat into a single Kidlat.exe (no Python needed to RUN it afterwards).
rem Downloads PyInstaller once (about 3 MB). Run this when your data is cheap.
cd /d "%~dp0"
set "HERE=%~dp0"
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo Could not install PyInstaller. Check your internet and try again.
  pause
  exit /b 1
)
python -m PyInstaller --noconfirm --onefile --windowed --name Kidlat ^
  --icon "%HERE%app\kidlat.ico" --add-data "%HERE%app\kidlat.ico;." ^
  --paths "%HERE%app" ^
  --distpath "%HERE%dist" --workpath "%HERE%build" --specpath "%HERE%build" ^
  "%HERE%app\kidlat.pyw"
if exist "%HERE%dist\Kidlat.exe" (
  echo.
  echo  Done!  Your app is at:  %HERE%dist\Kidlat.exe
  echo  "Start Kidlat.bat" will now use it automatically.
) else (
  echo  Build failed - scroll up to see the error.
)
pause

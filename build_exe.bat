@echo off
setlocal

cd /d "C:\Users\Oluwatola Ayedun\Desktop\IconMaker"

REM Install/upgrade PyInstaller in the venv
".\.venv\Scripts\python.exe" -m pip install -U pyinstaller

REM Build EXE
".\.venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed ^
  --name IconMaker ^
  --icon "C:\Users\Oluwatola Ayedun\Desktop\Iconer\Icon Images\Icons\Ico-Ico.ico" ^
  --add-data "assets;assets" ^
  --add-data "Gen2.py;." ^
  --add-data "Gen4.py;." ^
  --add-data "Gen3.py;." ^
  "Gen1.py"

echo.
echo Done. EXE is here:
echo %CD%\dist\IconMaker.exe
echo.
pause
endlocal

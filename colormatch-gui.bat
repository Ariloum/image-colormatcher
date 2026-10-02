@echo off
rem colormatch GUI via embedded python (pythonw: no console window)
start "" "%~dp0python-embed\pythonw.exe" -m colormatcher.gui %*

@echo off
rem colormatch CLI via embedded python
"%~dp0python-embed\python.exe" -m colormatcher.cli %*

@echo off
rem Build ColorMatcher.exe (launcher wrapper, T-0042) with the C# compiler bundled in Windows (.NET Framework 4)
setlocal
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
"%CSC%" /nologo /target:winexe /optimize+ /codepage:65001 ^
  /win32icon:"%~dp0..\colormatcher\gui\icons\moon.ico" ^
  /r:System.Windows.Forms.dll ^
  /out:"%~dp0..\ColorMatcher.exe" "%~dp0launcher\ColorMatcher.cs"
if errorlevel 1 exit /b 1
echo Built: %~dp0..\ColorMatcher.exe

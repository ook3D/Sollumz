@echo off
rem Stand-in for nvtt_export.exe, so the NVTT backend can be tested without the real (login-gated,
rem non-redistributable) NVIDIA Texture Tools Exporter, e.g. on CI.
rem
rem Only supports batch mode: nvtt_export_stub.bat --batch <batch file>
rem Each batch line is `"<input>" <args...> --output "<output>"`. The stub checks that the input exists and
rem copies textures\test_image.dds to the output, nothing is actually compressed.
rem
rem Environment variables read by the stub:
rem   NVTT_STUB_LOG        if set, every batch line is appended to this file
rem   NVTT_STUB_EXIT_CODE  if set, the outputs are still written but the stub exits with this code (a crash)
rem
rem Paths in the batch file must be ASCII: cmd reads it in the OEM code page, not UTF-8.
rem This file must keep CRLF line endings (see .gitattributes), cmd mis-parses labels with LF only.

setlocal EnableExtensions DisableDelayedExpansion

if /i not "%~1"=="--batch" (
    echo nvtt_export_stub: only --batch mode is supported 1>&2
    exit /b 2
)
if not exist "%~2" (
    echo nvtt_export_stub: batch file "%~2" not found 1>&2
    exit /b 3
)

set "TEMPLATE=%~dp0textures\test_image.dds"
set "FAILED="

for /f "usebackq delims=" %%L in ("%~2") do (
    if defined NVTT_STUB_LOG >>"%NVTT_STUB_LOG%" echo(%%L
    call :job %%L
)

if defined FAILED exit /b 1
if defined NVTT_STUB_EXIT_CODE exit /b %NVTT_STUB_EXIT_CODE%
exit /b 0

rem Arguments are the tokens of one batch line: the first is the input, the last is the output.
:job
set "IN=%~1"
:job_last_arg
set "OUT=%~1"
shift
if not "%~1"=="" goto job_last_arg

if not exist "%IN%" (
    echo nvtt_export_stub: input "%IN%" not found 1>&2
    set "FAILED=1"
    exit /b
)
copy /y /b "%TEMPLATE%" "%OUT%" >nul || set "FAILED=1"
exit /b

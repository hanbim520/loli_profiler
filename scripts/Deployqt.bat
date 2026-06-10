@echo off
echo Deployqt.....
echo dir %cd%

call %WindeployqtPath% %RelasePath%\LoliProfiler.exe
call %WindeployqtPath% %RelasePath%\LoliProfilerCLI.exe

if exist %DeployPath% (
    rmdir /s/q %DeployPath%
)

mkdir %DeployPath%

mkdir %DeployPath%\LoliProfiler\

echo %DeployPath%

xcopy /S /Q %RelasePath%\* %DeployPath%\LoliProfiler\*

echo Copying Python analysis scripts...
copy /Y "%~dp0..\markdown_to_html.py" "%DeployPath%\LoliProfiler\"
copy /Y "%~dp0..\analyze_heap.py" "%DeployPath%\LoliProfiler\"
copy /Y "%~dp0..\pyproject.toml" "%DeployPath%\LoliProfiler\"

echo Copying loli CLI files...
mkdir "%DeployPath%\LoliProfiler\loli_cli"
copy /Y "%~dp0..\loli_cli\__init__.py" "%DeployPath%\LoliProfiler\loli_cli\"
copy /Y "%~dp0..\loli_cli\tree_model.py" "%DeployPath%\LoliProfiler\loli_cli\"
copy /Y "%~dp0..\loli_cli\loli_convert.py" "%DeployPath%\LoliProfiler\loli_cli\"
copy /Y "%~dp0..\loli_cli\core.py" "%DeployPath%\LoliProfiler\loli_cli\"
copy /Y "%~dp0..\loli_cli\cli.py" "%DeployPath%\LoliProfiler\loli_cli\"
copy /Y "%~dp0..\loli_cli\README.md" "%DeployPath%\LoliProfiler\loli_cli\"

powershell Compress-Archive -Path %DeployPath%\LoliProfiler -DestinationPath %DeployPath%\LoliProfiler.zip -Update

echo finish Deployqt.....

:Exit
exit /b %errorlevel%
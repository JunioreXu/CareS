@echo off
echo ========================================
echo CareS 打包脚本
echo ========================================
echo.

echo [1/3] 安装依赖...
pip install -r requirements.txt
echo.

echo [2/3] 打包成exe...
pyinstaller --onefile --windowed --name "CareS" --icon=NONE main.py
echo.

echo [3/3] 完成！
echo.
echo exe文件位置: dist\CareS.exe
echo.
pause

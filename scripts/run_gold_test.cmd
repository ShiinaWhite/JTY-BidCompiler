@echo off
REM Gold Test 一键复跑（Windows cmd）
REM 用法：scripts
un_gold_test.cmd <profile>  （默认 sample_a）
REM 用法：双击，或在 cmd 中执行 scripts\run_gold_test.cmd
setlocal
cd /d "%~dp0.."
set PYTHONPATH=src

echo ============================================
echo  JTY-BidCompiler  Gold Test  (sample_a)
echo ============================================
echo.
echo [1/3] 全流程：MVP-1 ~ MVP-5
python -m jty_bidcompiler.cli run --profile %1
if errorlevel 1 goto :fail
echo.
echo [2/3] 自测（单元测试 + 黄金判例）
python -m unittest discover -s tests -t .
if errorlevel 1 goto :fail
echo.
echo [3/3] 产物清单
dir /b projects\%1
echo.
echo 全部完成。
goto :eof

:fail
echo.
echo *** 失败：请检查上面的输出 ***
exit /b 1

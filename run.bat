@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: =============================================================================
:: 📊 Daily Market Analysis - Auto Launcher
:: 🚀 Integrated: Environment Activation + Data Analysis + Chart Generation
:: =============================================================================

echo.
echo ===========================================================================
echo 📊 DAILY MARKET ANALYSIS - AUTO LAUNCHER
echo ===========================================================================
echo 🕐 Runtime: %date% %time%
echo 📁 Directory: %cd%
echo.

:: Check if conda is available
where conda >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ❌ ERROR: Conda not found in PATH
    echo 💡 Please install Anaconda/Miniconda or add conda to PATH
    pause
    exit /b 1
)

echo 🔄 Step 1: Activating conda environment...
call conda activate backtrader
if %ERRORLEVEL% neq 0 (
    echo ❌ ERROR: Failed to activate backtrader environment
    echo 💡 Creating backtrader environment...
    call conda create -n backtrader python=3.9 -y
    call conda activate backtrader
)

echo ✅ Environment activated: backtrader
echo.

:: Check Python and dependencies
echo 🔍 Step 2: Checking Python environment...
python --version
if %ERRORLEVEL% neq 0 (
    echo ❌ ERROR: Python not available
    pause
    exit /b 1
)

:: Check if akshare is installed
python -c "import akshare" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ⚠️  akshare not found, installing...
    pip install akshare pandas plotly numpy
)

:: Check other dependencies
python -c "import pandas, plotly, numpy" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ⚠️  Installing missing dependencies...
    pip install pandas plotly numpy
)

echo ✅ Dependencies verified
echo.

:: Display usage guide
echo ===========================================================================
echo 📊 MARKET ANALYSIS FEATURES
echo ===========================================================================
echo ✅ Real-time Shanghai Composite Index (East Money API)
echo ✅ Latest Margin Trading Data (2025 data available)
echo ✅ CSI 300 Volume Ratio (FIXED - Real calculation, not 50%%)
echo ✅ Interactive Plotly Charts (Zoom, Hover, Export)
echo ✅ Daily Auto-update (Fresh data every run)
echo.
echo 🎯 WHAT YOU'LL GET:
echo   • 4 Interactive Subplots with 90-day display data
echo   • Shanghai Composite with 5-day MA + 250-day MA (年线)
echo   • MA250 calculated using 500-day historical data
echo   • Auto-generated HTML file: market_analysis_final.html
echo   • Browser auto-launch for immediate viewing
echo   • Data freshness verification
echo.

:: Trading day check
python -c "import datetime; weekday = datetime.datetime.now().weekday(); print('📈 Trading Status:', 'Weekday - Markets Active' if weekday < 5 else 'Weekend - Markets Closed')"
echo.

echo 🚀 Step 3: Launching Market Analysis...
echo ===========================================================================
echo.

:: Clean up old files to ensure fresh generation
echo 🧹 Cleaning up old chart files...
if exist "market_analysis_final.html" (
    del "market_analysis_final.html"
    echo   ✅ Removed old market_analysis_final.html
)
if exist "test_simple_plot.html" (
    del "test_simple_plot.html"
    echo   ✅ Removed old test_simple_plot.html
)
if exist "debug_*.py" (
    del "debug_*.py"
    echo   ✅ Removed debug files
)
echo   💡 Old files cleanup completed
echo.

:: Run the main analysis
python final_interactive_plot.py

:: Check if successful
if %ERRORLEVEL% equ 0 (
    echo.
    echo ===========================================================================
    echo 🎉 SUCCESS! Market Analysis Completed
    echo ===========================================================================
    
    :: Check if HTML file was generated
    if exist "market_analysis_final.html" (
        echo ✅ Fresh chart file generated: market_analysis_final.html
        for %%A in (market_analysis_final.html) do echo 📏 File size: %%~zA bytes
        echo.
        echo 🎯 The chart has been automatically opened in your browser
        echo 💾 You can also manually open: market_analysis_final.html
        echo 🔄 Note: Old files were cleaned, this is a fresh generation
    ) else (
        echo ⚠️  HTML file not found, but analysis may have completed
    )
    
    echo.
    echo 🔄 DAILY USAGE TIPS:
    echo   • Run this script every trading day after 6 PM
    echo   • Data updates automatically from East Money
    echo   • Charts support zoom, pan, and interactive exploration
    echo   • CSI 300 ratio now shows real market data (not fixed 50%%)
    echo   • Moving averages: 5-day line (short-term) + 250-day line (yearly trend)
    echo.
    echo 🎯 AUTOMATION OPTIONS:
    echo   • Windows Task Scheduler: Run daily at 18:00
    echo   • Manual: Double-click this file anytime
    echo   • Command line: run.bat
    
) else (
    echo.
    echo ❌ ERROR: Analysis failed with exit code %ERRORLEVEL%
    echo 💡 Please check the error messages above
    echo 🔧 Common solutions:
    echo   • Check internet connection
    echo   • Verify conda environment
    echo   • Ensure all dependencies are installed
)

echo.
echo ===========================================================================
echo 📊 Session completed at %time%
echo ===========================================================================
echo.
echo Press any key to exit...
pause >nul
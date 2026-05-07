#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日市场数据更新脚本
自动获取最新的股市和融资融券数据
"""

import os
import sys
from datetime import datetime
import subprocess

def print_banner():
    """打印启动横幅"""
    print("=" * 70)
    print("📊 每日市场数据更新器 - Daily Market Data Updater")
    print("=" * 70)
    print(f"🕐 运行时间: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}")
    
    # 显示当前工作目录
    current_dir = os.getcwd()
    print(f"📁 工作目录: {current_dir}")
    
    # 检查主脚本是否存在
    main_script = "final_interactive_plot.py"
    if os.path.exists(main_script):
        print(f"✅ 找到主脚本: {main_script}")
    else:
        print(f"❌ 主脚本不存在: {main_script}")
        return False
    
    print("=" * 70)
    return True

def check_environment():
    """检查Python环境和依赖"""
    print("\n🔍 检查运行环境...")
    
    # 检查Python版本
    python_version = sys.version_info
    print(f"🐍 Python版本: {python_version.major}.{python_version.minor}.{python_version.micro}")
    
    # 检查必要的库
    required_packages = ['akshare', 'pandas', 'plotly', 'numpy']
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"  ✅ {package}: 已安装")
        except ImportError:
            print(f"  ❌ {package}: 未安装")
            missing_packages.append(package)
    
    if missing_packages:
        print(f"\n⚠️ 缺少依赖包: {', '.join(missing_packages)}")
        print("请运行以下命令安装:")
        for package in missing_packages:
            print(f"  pip install {package}")
        return False
    
    print("✅ 所有依赖已满足")
    return True

def run_market_analysis():
    """运行市场分析脚本"""
    print("\n🚀 开始运行市场分析...")
    
    try:
        # 运行主脚本 (不捕获输出，让图表能正常显示)
        result = subprocess.run([sys.executable, "final_interactive_plot.py"], 
                              encoding='utf-8')
        
        if result.returncode == 0:
            print("✅ 市场分析完成!")
            
            # 检查是否生成了HTML文件
            html_file = "market_analysis_final.html"
            if os.path.exists(html_file):
                print(f"📊 图表已保存: {html_file}")
                
                # 询问是否手动打开
                try:
                    import webbrowser
                    print("🔄 正在尝试打开浏览器...")
                    webbrowser.open(f"file://{os.path.abspath(html_file)}")
                    print("✅ 图表已在浏览器中打开!")
                except Exception as e:
                    print(f"⚠️ 无法自动打开浏览器: {e}")
                    print(f"💡 请手动打开文件: {os.path.abspath(html_file)}")
            else:
                print("⚠️ 未找到生成的HTML文件")
                
        else:
            print("❌ 运行过程中出现错误")
            return False
            
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        return False
    
    return True

def show_usage_tips():
    """显示使用提示"""
    print("\n" + "=" * 70)
    print("💡 使用提示")
    print("=" * 70)
    print("🔄 自动化运行方式:")
    print("  1. 每日手动运行: python daily_market_update.py")
    print("  2. 定时运行 (Windows): 使用任务计划程序")
    print("  3. 定时运行 (Linux/Mac): 使用 crontab")
    print("")
    print("⏰ 建议运行时间:")
    print("  • 工作日 18:00 (交易结束后)")
    print("  • 周末不运行 (无新数据)")
    print("")
    print("📈 数据更新频率:")
    print("  • 上证指数: 每交易日更新")
    print("  • 融资融券: 通常T+1更新")
    print("")
    print("🔗 图表显示方式:")
    print("  • 自动在浏览器中打开交互式图表")
    print("  • 同时保存HTML文件到本地")
    print("  • 支持离线查看和分享")
    print("=" * 70)

def main():
    """主函数"""
    # 打印启动信息
    if not print_banner():
        return
    
    # 检查环境
    if not check_environment():
        input("\n按回车键退出...")
        return
    
    # 运行分析
    if run_market_analysis():
        print("\n🎉 今日市场数据更新完成!")
    else:
        print("\n😞 数据更新失败，请检查网络连接和错误信息")
    
    # 显示使用提示
    show_usage_tips()
    
    # 等待用户确认
    input("\n按回车键退出...")

if __name__ == "__main__":
    main()
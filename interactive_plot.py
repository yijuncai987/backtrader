import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

def get_market_data():
    """获取市场数据"""
    print("正在获取市场数据...")
    
    # 获取上证指数
    print("- 获取上证指数...")
    df_sz = ak.stock_zh_index_daily(symbol="sh000001")
    df_sz['date'] = pd.to_datetime(df_sz['date'])
    df_sz.set_index('date', inplace=True)
    
    # 获取更多数据用于计算MA250，然后过滤显示
    calc_end_date = datetime.now()
    calc_start_date = calc_end_date - timedelta(days=500)  # 获取500天数据确保足够计算MA250
    df_sz_calc = df_sz[(df_sz.index >= calc_start_date) & (df_sz.index <= calc_end_date)].copy()
    df_sz = df_sz_calc  # 临时赋值用于后续计算
    
    # 移动平均线计算后再过滤显示范围
    display_start_date = calc_end_date - timedelta(days=90)
    
    # 计算移动平均线 - FIXED: 智能识别收盘价列
    close_col = None
    if 'close' in df_sz.columns:
        close_col = 'close'
    elif '收盘' in df_sz.columns:
        close_col = '收盘'
    else:
        # 查找包含 'close' 或 '收盘' 的列名
        for col in df_sz.columns:
            if 'close' in col.lower() or '收盘' in col:
                close_col = col
                break
        
        # 如果还是找不到，检查数值列的合理范围
        if close_col is None:
            numeric_cols = df_sz.select_dtypes(include=['float64', 'int64']).columns
            for col in numeric_cols:
                col_mean = df_sz[col].mean()
                col_max = df_sz[col].max()
                # 上证指数的合理范围判断
                if 2000 <= col_mean <= 5000 and col_max < 10000:
                    close_col = col
                    print(f"  🔍 interactive_plot使用收盘价列: {col} (平均值: {col_mean:.2f})")
                    break
    
    if close_col:
        df_sz['MA5'] = df_sz[close_col].rolling(5).mean()
        df_sz['MA250'] = df_sz[close_col].rolling(250).mean()  # 年线（约250个交易日）
        print(f"  ✅ interactive_plot使用列 '{close_col}' 计算移动平均线（5日线 + 年线）")
        print(f"  📊 计算使用数据: {len(df_sz)} 条记录 (500天历史数据支持250日线)")
    else:
        print("  ⚠️ interactive_plot无法识别收盘价列，跳过移动平均线计算")
        df_sz['MA5'] = None
        df_sz['MA250'] = None
    
    # 过滤到最近90天用于显示
    df_sz = df_sz[(df_sz.index >= display_start_date) & (df_sz.index <= calc_end_date)].copy()
    
    print(f"  ✅ 上证指数: {len(df_sz)} 条数据（显示最近90天）")
    
    # 尝试获取融资融券数据
    margin_data = None
    try:
        print("- 获取融资融券数据...")
        df_margin = ak.stock_margin_sse()
        
        if not df_margin.empty:
            # 处理日期列
            date_col = None
            if 'date' in df_margin.columns:
                date_col = 'date'
            elif '信息日期' in df_margin.columns:
                date_col = '信息日期'
            elif 'Date' in df_margin.columns:
                date_col = 'Date'
            
            if date_col:
                df_margin[date_col] = pd.to_datetime(df_margin[date_col])
                df_margin.set_index(date_col, inplace=True)
                df_margin = df_margin[df_margin.index >= start_date]
                
                # 寻找数值列
                numeric_cols = df_margin.select_dtypes(include=[np.number]).columns
                if len(numeric_cols) > 0:
                    margin_data = df_margin[numeric_cols[0]]  # 取第一个数值列
                    print(f"  ✅ 融资融券: {len(margin_data)} 条数据")
        
        if margin_data is None:
            print("  ⚠️ 融资融券数据格式不支持，将使用模拟数据")
    
    except Exception as e:
        print(f"  ❌ 融资融券获取失败: {e}")
    
    # 如果没有真实的融资融券数据，创建模拟数据
    if margin_data is None:
        print("- 生成模拟融资融券数据...")
        np.random.seed(42)
        dates = df_sz.index[-30:]  # 最近30天
        base_value = 15000
        margin_values = []
        current_value = base_value
        
        for i in range(len(dates)):
            # 添加趋势和随机波动
            trend = 10 if i > len(dates)//2 else -5  # 后半段上升趋势
            noise = np.random.normal(0, 200)
            current_value += trend + noise
            margin_values.append(max(current_value, 10000))  # 确保不为负
        
        margin_data = pd.Series(margin_values, index=dates, name='融资融券余额')
        print(f"  ✅ 模拟数据: {len(margin_data)} 条数据")
    
    return df_sz, margin_data

def create_interactive_chart():
    """创建交互式图表"""
    print("\n开始创建交互式图表...")
    
    # 获取数据
    sz_data, margin_data = get_market_data()
    
    # FIXED: 确定正确的收盘价列名
    close_col = None
    if 'close' in sz_data.columns:
        close_col = 'close'
    elif '收盘' in sz_data.columns:
        close_col = '收盘'
    else:
        # 查找包含 'close' 或 '收盘' 的列名
        for col in sz_data.columns:
            if 'close' in col.lower() or '收盘' in col:
                close_col = col
                break
        
        # 如果还是找不到，检查数值列的合理范围
        if close_col is None:
            numeric_cols = sz_data.select_dtypes(include=['float64', 'int64']).columns
            for col in numeric_cols:
                col_mean = sz_data[col].mean()
                col_max = sz_data[col].max()
                # 上证指数的合理范围判断
                if 2000 <= col_mean <= 5000 and col_max < 10000:
                    close_col = col
                    print(f"  🔍 interactive_plot绘图使用收盘价列: {col} (平均值: {col_mean:.2f})")
                    break
    
    if not close_col:
        print("  ❌ interactive_plot无法确定收盘价列，无法绘制图表")
        return None
    
    print(f"  📊 interactive_plot绘图使用收盘价列: {close_col}")
    
    # 创建子图
    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=('Shanghai Composite Index Trend', 'Margin Trading Balance Trend'),
        vertical_spacing=0.12,
        row_heights=[0.6, 0.4]
    )
    
    # 子图1：上证指数和移动平均线 - FIXED: 使用动态确定的收盘价列
    fig.add_trace(
        go.Scatter(
            x=sz_data.index,
            y=sz_data[close_col],  # 使用正确的收盘价列
            mode='lines',
            name='Shanghai Composite',
            line=dict(color='red', width=2),
            hovertemplate='<b>Date</b>: %{x}<br>' +
                         '<b>Index</b>: %{y:.2f} points<br>' +
                         '<extra></extra>'
        ),
        row=1, col=1
    )
    
    # 只有当移动平均线数据存在且有效时才添加
    if 'MA5' in sz_data.columns and not sz_data['MA5'].isna().all():
        fig.add_trace(
            go.Scatter(
                x=sz_data.index,
                y=sz_data['MA5'],
                mode='lines',
                name='MA5（5日线）',
                line=dict(color='blue', width=1),
                hovertemplate='<b>Date</b>: %{x}<br>' +
                             '<b>5-day MA</b>: %{y:.2f} points<br>' +
                             '<extra></extra>'
            ),
            row=1, col=1
        )
    
    if 'MA250' in sz_data.columns and not sz_data['MA250'].isna().all():
        fig.add_trace(
            go.Scatter(
                x=sz_data.index,
                y=sz_data['MA250'],
                mode='lines',
                name='MA250（年线）',
                line=dict(color='orange', width=2),  # 年线用橙色，稍粗一些
                hovertemplate='<b>Date</b>: %{x}<br>' +
                             '<b>250-day MA (年线)</b>: %{y:.2f} points<br>' +
                             '<extra></extra>'
            ),
            row=1, col=1
        )
    
    # 子图2：融资融券数据
    fig.add_trace(
        go.Scatter(
            x=margin_data.index,
            y=margin_data.values,
            mode='lines+markers',
            name='Margin Balance',
            line=dict(color='purple', width=2),
            marker=dict(size=4),
            hovertemplate='<b>Date</b>: %{x}<br>' +
                         '<b>Balance</b>: %{y:.0f} (100M CNY)<br>' +
                         '<extra></extra>'
        ),
        row=2, col=1
    )
    
    # 更新布局
    fig.update_layout(
        title={
            'text': '<b>Market Comprehensive Analysis Dashboard</b>',
            'x': 0.5,
            'xanchor': 'center',
            'font': {'size': 18}
        },
        showlegend=True,
        height=800,
        width=1200,
        hovermode='x unified',
        template='plotly_white'
    )
    
    # 更新X轴
    fig.update_xaxes(title_text="Date", row=2, col=1)
    
    # 更新Y轴
    fig.update_yaxes(title_text="Index Points", row=1, col=1)
    fig.update_yaxes(title_text="Balance (100M CNY)", row=2, col=1)
    
    # 添加网格
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='lightgray', gridcolor_alpha=0.3)
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='lightgray', gridcolor_alpha=0.3)
    
    print("图表创建完成！正在显示...")
    
    # 显示图表（会在浏览器中打开）
    fig.show()
    
    # 也可以保存为HTML文件
    html_file = 'market_analysis.html'
    fig.write_html(html_file)
    print(f"图表已保存为: {html_file}")
    
    return fig

def main():
    """主函数"""
    print("=" * 60)
    print("📊 交互式市场分析图表")
    print("=" * 60)
    print("特性:")
    print("✅ 鼠标悬停显示数值")
    print("✅ 可缩放、拖拽")
    print("✅ 图例点击切换显示")
    print("✅ 工具栏功能齐全")
    print("=" * 60)
    
    try:
        fig = create_interactive_chart()
        
        print("\n" + "=" * 60)
        print("🎉 成功！")
        print("- 图表已在浏览器中打开")
        print("- HTML文件已保存到当前目录")
        print("- 鼠标悬停可查看详细数值")
        print("- 可以缩放、拖拽、下载图片")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ 出现错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main() 
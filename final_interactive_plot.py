import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

def check_data_freshness(data_date, data_name, max_delay_days=3):
    """Check data freshness"""
    today = datetime.now().date()
    data_date = data_date.date() if hasattr(data_date, 'date') else data_date
    days_old = (today - data_date).days
    
    if days_old == 0:
        print(f"  🌟 {data_name}: Today's latest data")
        return "Today"
    elif days_old == 1:
        print(f"  ✅ {data_name}: Yesterday's data (1 day ago)")
        return "Yesterday"
    elif days_old <= max_delay_days:
        print(f"  ⚠️ {data_name}: {days_old} days old data")
        return f"{days_old} days ago"
    else:
        print(f"  ❌ {data_name}: Outdated data ({days_old} days ago)")
        return f"{days_old} days ago"

def is_trading_day():
    """Simple check if it's a trading day (excluding weekends)"""
    today = datetime.now()
    weekday = today.weekday()  # 0=Monday, 6=Sunday
    if weekday >= 5:  # Saturday or Sunday
        return False, "Weekend - Non-trading day"
    return True, "Weekday"

def get_market_data():
    """Get market data and verify freshness"""
    print("🔄 Starting to fetch market data...")
    print(f"📅 Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Check if it's a trading day
    is_trading, day_type = is_trading_day()
    print(f"📈 Trading day status: {day_type}")
    
    # Get Shanghai Composite Index data
    print("\n- Fetching Shanghai Composite Index data...")
    try:
        df_sz = ak.stock_zh_index_daily_em(symbol="sh000001")
        
        # Process date format
        df_sz['date'] = pd.to_datetime(df_sz['date'])
        df_sz.set_index('date', inplace=True)
        df_sz = df_sz.sort_index()
        
        # Check data freshness
        sz_latest = df_sz.index.max()
        sz_freshness = check_data_freshness(sz_latest, "Shanghai Composite")
        
        # Get more data for MA calculation, then filter for display
        calc_end_date = datetime.now()
        calc_start_date = calc_end_date - timedelta(days=500)  # 获取500天数据确保足够计算MA250
        df_sz_calc = df_sz[(df_sz.index >= calc_start_date) & (df_sz.index <= calc_end_date)].copy()
        
        # Calculate moving averages on full dataset
        close_col = None
        if 'close' in df_sz_calc.columns:
            close_col = 'close'
        elif '收盘' in df_sz_calc.columns:
            close_col = '收盘'
        else:
            # 更安全的方式：查找包含 'close' 或 '收盘' 的列名
            for col in df_sz_calc.columns:
                if 'close' in col.lower() or '收盘' in col:
                    close_col = col
                    break
            
            # 如果还是找不到，检查数值列的合理范围
            if close_col is None:
                numeric_cols = df_sz_calc.select_dtypes(include=['float64', 'int64']).columns
                for col in numeric_cols:
                    col_mean = df_sz_calc[col].mean()
                    col_max = df_sz_calc[col].max()
                    if 2000 <= col_mean <= 5000 and col_max < 10000:
                        close_col = col
                        print(f"  🔍 自动识别收盘价列: {col} (平均值: {col_mean:.2f})")
                        break
        
        if close_col:
            df_sz_calc['MA5'] = df_sz_calc[close_col].rolling(5).mean()
            df_sz_calc['MA250'] = df_sz_calc[close_col].rolling(250).mean()  # 年线（约250个交易日）
            print(f"  ✅ 使用列 '{close_col}' 计算移动平均线（5日线 + 年线）")
            print(f"  📊 计算使用数据: {len(df_sz_calc)} 条记录 (500天历史数据支持250日线)")
            
            # 检查250日线计算结果
            ma250_valid_count = df_sz_calc['MA250'].notna().sum()
            if ma250_valid_count > 0:
                print(f"  ✅ MA250计算成功: {ma250_valid_count} 个有效值")
                latest_ma250 = df_sz_calc['MA250'].iloc[-1]
                if not pd.isna(latest_ma250):
                    print(f"  📈 最新MA250值: {latest_ma250:.2f} 点")
                else:
                    print(f"  ⚠️ 最新MA250值为空，可能需要更多历史数据")
            else:
                print(f"  ❌ MA250计算失败: 没有有效值")
        else:
            print("  ⚠️ 无法识别收盘价列，跳过移动平均线计算")
            df_sz_calc['MA5'] = None
            df_sz_calc['MA250'] = None
        
        # Filter to last 90 days for display
        display_start_date = calc_end_date - timedelta(days=90)
        df_sz_filtered = df_sz_calc[(df_sz_calc.index >= display_start_date) & (df_sz_calc.index <= calc_end_date)].copy()
        
        print(f"  ✅ Shanghai Composite: {len(df_sz_filtered)} records (latest: {sz_latest.strftime('%Y-%m-%d')})")
        if close_col:
            print(f"  📊 Index range: {df_sz_filtered[close_col].min():.2f} - {df_sz_filtered[close_col].max():.2f} points")
        else:
            print("  📊 Index range: Unable to determine (close column not identified)")
        
    except Exception as e:
        print(f"  ❌ Shanghai Composite fetch failed: {e}")
        return None, None, None
    
    # Get margin trading data - using new API (East Money)
    print("\n- Fetching margin trading data...")
    try:
        df_margin_full = ak.stock_margin_account_info()
        
        # Process date format
        df_margin_full['日期'] = pd.to_datetime(df_margin_full['日期'])
        df_margin_full = df_margin_full.sort_values('日期')
        
        # Check data freshness
        margin_latest = df_margin_full['日期'].max()
        margin_freshness = check_data_freshness(margin_latest, "Margin Trading")
        
        # Calculate total margin balance (data is already in 100M CNY)
        df_margin_full['融资融券余额'] = df_margin_full['融资余额'] + df_margin_full['融券余额']
        
        # Get last 90 days data
        start_date = margin_latest - timedelta(days=90)
        df_margin_recent = df_margin_full[df_margin_full['日期'] >= start_date].copy()
        
        # Set date as index
        df_margin_recent.set_index('日期', inplace=True)
        
        print(f"  📅 Margin data range: {df_margin_recent.index.min().strftime('%Y-%m-%d')} to {df_margin_recent.index.max().strftime('%Y-%m-%d')}")
        print(f"  📊 Data source: East Money")
        
        # Data is already in 100M CNY, use directly (only financing balance)
        df_margin_recent['融资余额_亿元'] = df_margin_recent['融资余额']
        
        # Calculate 5-day change percentage for financing balance (using 7 calendar days ago)
        df_margin_recent['融资余额_5日变化率'] = 0.0  # Initialize all to 0
        
        if len(df_margin_recent) >= 7:
            # Calculate 5-day change for each point where possible (using 7 calendar days ago)
            for i in range(6, len(df_margin_recent)):  # Start from 7th element (index 6)
                current_balance = df_margin_recent['融资余额_亿元'].iloc[i]
                past_balance = df_margin_recent['融资余额_亿元'].iloc[i-6]  # 7 calendar days ago (5 trading days)
                
                if past_balance > 0:
                    change_pct = ((current_balance - past_balance) / past_balance) * 100
                    # Round to 1 decimal place for consistent display
                    change_pct = round(change_pct, 1)
                    df_margin_recent.iloc[i, df_margin_recent.columns.get_loc('融资余额_5日变化率')] = change_pct
            
            # Display detailed calculation for latest change
            latest_balance = df_margin_recent['融资余额_亿元'].iloc[-1]
            seven_days_ago_balance = df_margin_recent['融资余额_亿元'].iloc[-7]
            latest_change = df_margin_recent['融资余额_5日变化率'].iloc[-1]
            latest_date = df_margin_recent.index[-1].strftime('%Y-%m-%d')
            seven_days_ago_date = df_margin_recent.index[-7].strftime('%Y-%m-%d')
            
            if latest_change != 0:
                change_direction = "📈" if latest_change > 0 else "📉" if latest_change < 0 else "➡️"
                print(f"  📊 5-day financing balance calculation (using 7 calendar days ago):")
                print(f"    • Current ({latest_date}): {latest_balance:.0f} 亿元")
                print(f"    • 7 days ago ({seven_days_ago_date}): {seven_days_ago_balance:.0f} 亿元")
                print(f"    • Change: {change_direction} {latest_change:+.1f}% ({latest_balance - seven_days_ago_balance:+.0f} 亿元)")
        else:
            print("  ⚠️ Not enough data for 5-day change calculation (need at least 7 days)")
        
        print(f"  ✅ Margin Trading: {len(df_margin_recent)} records")
        if len(df_margin_recent) > 0:
            print(f"  💰 Latest financing balance: {df_margin_recent['融资余额_亿元'].iloc[-1]:.0f} 亿元")
        else:
            print("  ❌ No margin trading data")
            
    except Exception as e:
        print(f"  ❌ Margin trading fetch failed: {e}")
        return df_sz_filtered, None, None
    
    # Get CSI 300 volume ratio data
    print("\n- Fetching CSI 300 volume ratio data...")
    try:
        # Get CSI 300 index data using East Money API
        df_hs300 = ak.stock_zh_index_daily_em(symbol="sh000300")
        df_hs300['date'] = pd.to_datetime(df_hs300['date'])
        df_hs300.set_index('date', inplace=True)
        df_hs300 = df_hs300.sort_index()
        
        # Get Shanghai, Shenzhen and Beijing market data using East Money API
        print("  📊 Fetching three markets data for total volume calculation")
        df_sh = ak.stock_zh_index_daily_em(symbol="sh000001")  # Shanghai Composite
        df_sz_main = ak.stock_zh_index_daily_em(symbol="sz399001")  # Shenzhen Component
        
        # Get Beijing Stock Exchange historical data using generic interface
        try:
            # Use index_zh_a_hist to get Beijing Stock Exchange historical data (北证50)
            df_bj = ak.index_zh_a_hist(symbol='899050', period='daily', start_date='20240101', end_date='20251231')
            
            if not df_bj.empty and '成交额' in df_bj.columns:
                # Process date format for Beijing data
                df_bj['date'] = pd.to_datetime(df_bj['日期'])
                df_bj['amount'] = df_bj['成交额']  # Rename for consistency
                df_bj.set_index('date', inplace=True)
                df_bj = df_bj.sort_index()
                
                latest_amount = df_bj['amount'].iloc[-1] / 100000000
                print(f"  ✅ Beijing market data included (北证50 historical: {latest_amount:.1f} 亿元)")
                print(f"  📅 Beijing data range: {len(df_bj)} records from {df_bj.index[0].strftime('%Y-%m-%d')}")
                include_bj = True
            else:
                print("  ⚠️ Beijing historical data empty or invalid")
                include_bj = False
                df_bj = None
        except Exception as e:
            print(f"  ⚠️ Beijing market data unavailable: {e}")
            include_bj = False
            df_bj = None
        
        # Process date format for all markets
        df_sh['date'] = pd.to_datetime(df_sh['date'])
        df_sz_main['date'] = pd.to_datetime(df_sz_main['date'])
        df_sh.set_index('date', inplace=True)
        df_sz_main.set_index('date', inplace=True)
        df_sh = df_sh.sort_index()
        df_sz_main = df_sz_main.sort_index()
        
        # Process Beijing market data if available
        if include_bj and df_bj is not None:
            # Beijing data is already processed above
            pass
        
        # Filter last 90 days data
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        df_hs300_filtered = df_hs300[(df_hs300.index >= start_date) & (df_hs300.index <= end_date)].copy()
        df_sh_filtered = df_sh[(df_sh.index >= start_date) & (df_sh.index <= end_date)].copy()
        df_sz_main_filtered = df_sz_main[(df_sz_main.index >= start_date) & (df_sz_main.index <= end_date)].copy()  # FIXED: 改名避免覆盖上证指数数据
        
        # Filter Beijing market data if available
        if include_bj and df_bj is not None:
            df_bj_filtered = df_bj[(df_bj.index >= start_date) & (df_bj.index <= end_date)].copy()
        else:
            df_bj_filtered = None
        
        # Calculate volume ratio using real market data
        ratio_data = []
        for date in df_hs300_filtered.index:
            try:
                if date not in df_sh_filtered.index or date not in df_sz_main_filtered.index:
                    continue
                    
                hs300_amount = df_hs300_filtered.loc[date, 'amount']  # CSI 300 trading amount
                sh_amount = df_sh_filtered.loc[date, 'amount']       # Shanghai market amount
                sz_amount = df_sz_main_filtered.loc[date, 'amount']       # Shenzhen market amount
                
                # Calculate total market amount (Shanghai + Shenzhen + Beijing if available)
                total_amount = sh_amount + sz_amount
                
                # Add Beijing market if available (using real historical data)
                bj_amount = 0
                if include_bj and df_bj_filtered is not None and date in df_bj_filtered.index:
                    bj_amount = df_bj_filtered.loc[date, 'amount']
                    total_amount += bj_amount
                
                if total_amount > 0 and hs300_amount > 0:
                    ratio = (hs300_amount / total_amount) * 100
                    
                    # Sanity check: CSI 300 ratio should be between 15%-75%
                    if 15 <= ratio <= 75:
                        ratio_data.append({
                            'date': date,
                            'hs300_amount': hs300_amount,
                            'total_amount': total_amount,
                            'sh_amount': sh_amount,
                            'sz_amount': sz_amount, 
                            'bj_amount': bj_amount,
                            'ratio': ratio,
                            'hs300_amount_display': f"{hs300_amount/100000000:.1f}",  # Convert to 亿元
                            'total_amount_display': f"{total_amount/100000000:.1f}",   # Convert to 亿元
                            'sh_amount_display': f"{sh_amount/100000000:.1f}",        # 沪市成交额
                            'sz_amount_display': f"{sz_amount/100000000:.1f}",        # 深市成交额
                            'bj_amount_display': f"{bj_amount/100000000:.1f}"         # 北交所成交额
                        })
                        
            except Exception as e:
                continue
        
        if ratio_data is not None and len(ratio_data) > 0:
            df_ratio = pd.DataFrame(ratio_data)
            df_ratio.set_index('date', inplace=True)
            
            print(f"  ✅ CSI 300 volume ratio: {len(df_ratio)} records")
            if len(df_ratio) > 0:
                latest_ratio = df_ratio['ratio'].iloc[-1]
                avg_ratio = df_ratio['ratio'].mean()
                print(f"  📊 Latest ratio: {latest_ratio:.1f}%")
                print(f"  📊 Average ratio (90 days): {avg_ratio:.1f}%")
        else:
            print("  ❌ Cannot calculate CSI 300 volume ratio")
            df_ratio = None
            
    except Exception as e:
        print(f"  ❌ CSI 300 data fetch failed: {e}")
        df_ratio = None
    
    # Data availability reminder
    if not is_trading:
        print(f"  💡 Note: Current is {day_type}, data may not update in real-time")
    
    # Data freshness summary
    print(f"\n📊 Data freshness summary:")
    print(f"  • Shanghai Composite: {sz_freshness}")
    print(f"  • Margin Trading: {margin_freshness}")
    if df_ratio is not None and len(df_ratio) > 0:
        ratio_latest = df_ratio.index.max()
        ratio_freshness = check_data_freshness(ratio_latest, "CSI 300 Ratio")
        print(f"  • CSI 300 Ratio: {ratio_freshness}")
    
    return df_sz_filtered, df_margin_recent, df_ratio

def create_interactive_chart():
    """Create interactive chart"""
    print("\nStarting to create interactive chart...")
    
    # Get data
    sz_data, margin_data, ratio_data = get_market_data()
    
    # 🔍 SIMPLIFIED: 直接使用 'close' 列，就像测试版本一样
    if 'close' not in sz_data.columns:
        print(f"  ❌ 数据中没有 'close' 列！实际列名: {sz_data.columns.tolist()}")
        return None
    
    close_col = 'close'
    print(f"  📊 绘图使用收盘价列: {close_col}")
    print(f"  📊 上证指数范围: {sz_data[close_col].min():.2f} - {sz_data[close_col].max():.2f} 点")
    print(f"  📊 最新收盘价: {sz_data[close_col].iloc[-1]:.2f} 点")
    
    # 检查移动平均线数据
    if 'MA5' in sz_data.columns and not sz_data['MA5'].isna().all():
        print(f"  ✅ MA5可用，最新值: {sz_data['MA5'].iloc[-1]:.2f} 点")
    else:
        print(f"  ❌ MA5不可用")
        
    if 'MA250' in sz_data.columns and not sz_data['MA250'].isna().all():
        ma250_valid = sz_data['MA250'].notna().sum()
        print(f"  ✅ MA250可用，有效数据点: {ma250_valid}/{len(sz_data)}")
        if not pd.isna(sz_data['MA250'].iloc[-1]):
            print(f"  📈 最新MA250值: {sz_data['MA250'].iloc[-1]:.2f} 点")
            
        # 显示MA250值的范围
        ma250_min = sz_data['MA250'].min()
        ma250_max = sz_data['MA250'].max()
        if not (pd.isna(ma250_min) or pd.isna(ma250_max)):
            print(f"  📊 MA250范围: {ma250_min:.2f} - {ma250_max:.2f} 点")
    else:
        print(f"  ❌ MA250不可用或全为空值")
        if 'MA250' in sz_data.columns:
            print(f"  🔍 MA250列存在但全为空: {sz_data['MA250'].isna().all()}")
        else:
            print(f"  🔍 MA250列不存在")
            print(f"  🔍 可用列: {sz_data.columns.tolist()}")
    
    # Create subplots (3 rows - removed securities lending)
    fig = make_subplots(
        rows=3, cols=1,
        subplot_titles=(
            'Shanghai Composite Index', 
            'Financing Balance',
            'CSI 300 Volume Ratio'
        ),
        vertical_spacing=0.08,
        row_heights=[0.5, 0.25, 0.25]
    )
    
    # Subplot 1: Shanghai Composite Index and moving averages - FIXED: 使用动态确定的收盘价列
    fig.add_trace(
        go.Scatter(
            x=sz_data.index,
            y=sz_data[close_col],  # 使用正确的收盘价列
            mode='lines',
            name='Shanghai Composite',
            line=dict(color='red', width=2),
            hovertemplate='<b>Date</b>: %{x}<br>' +
                         '<b>Shanghai Composite</b>: %{y:.2f} points<br>' +
                         '<extra></extra>'
        ),
        row=1, col=1
    )
    
    # Only add MA traces if they exist
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
    
    # Subplot 2: Financing balance (only financing, no securities lending)
    if margin_data is not None and len(margin_data) > 0:
        fig.add_trace(
            go.Scatter(
                x=margin_data.index,
                y=margin_data['融资余额_亿元'],
                mode='lines+markers',
                name='Financing Balance',
                line=dict(color='orange', width=2),
                marker=dict(size=4),
                hovertemplate='<b>Date</b>: %{x}<br>' +
                             '<b>融资余额</b>: %{y:.1f} 亿元<br>' +
                             '<b><span style="color:red;font-size:14px">🔥 5日变化: %{customdata:+.1f}%</span></b><br>' +
                             '<extra></extra>',
                customdata=margin_data['融资余额_5日变化率']
            ),
            row=2, col=1
        )
    else:
        # If no margin trading data, add empty chart
        fig.add_annotation(
            text="No financing balance data available",
            xref="x2", yref="y2",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=16, color="gray"),
            row=2, col=1
        )
    
    # Subplot 3: CSI 300 volume ratio
    if ratio_data is not None and len(ratio_data) > 0:
        fig.add_trace(
            go.Scatter(
                x=ratio_data.index,
                y=ratio_data['ratio'],
                mode='lines+markers',
                name='CSI 300 Volume Ratio',
                line=dict(color='green', width=2),
                marker=dict(size=4),
                hovertemplate='<b>Date</b>: %{x}<br>' +
                             '<b><span style="color:green;font-size:16px">🎯 CSI 300成交占比: %{y:.1f}%</span></b><br>' +
                             '<b>CSI 300成交额</b>: %{customdata[0]} 亿元<br>' +
                             '<b>全市场成交额</b>: %{customdata[1]} 亿元<br>' +
                             '<span style="color:gray;font-size:11px">沪市: %{customdata[2]} | 深市: %{customdata[3]} | 北交所: %{customdata[4]}</span><br>' +
                             '<extra></extra>',
                customdata=list(zip(
                    ratio_data['hs300_amount_display'], 
                    ratio_data['total_amount_display'],
                    ratio_data['sh_amount_display'],
                    ratio_data['sz_amount_display'],
                    ratio_data['bj_amount_display']
                ))
            ),
            row=3, col=1
        )
    else:
        # If no CSI 300 data, add empty chart
        fig.add_annotation(
            text="No CSI 300 volume ratio data available",
            xref="x4", yref="y4",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=16, color="gray"),
            row=4, col=1
        )
    
    # Update layout
    fig.update_layout(
        title={
            'text': '<b>📊 China A-Share Market Analysis (Real-time Update) - Data Source: East Money</b>',
            'x': 0.5,
            'xanchor': 'center',
            'font': {'size': 18, 'color': 'darkblue'}
        },
        showlegend=True,
        height=1100,
        width=1200,
        hovermode='x unified',
        template='plotly_white'
    )
    
    # Update X-axis title
    fig.update_xaxes(title_text="Date", row=3, col=1)
    
    # Update Y-axis titles and formats (disable k abbreviation)
    fig.update_yaxes(title_text="指数点位 (Points)", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(title_text="融资余额 (亿元)", tickformat=",.0f", row=2, col=1)
    fig.update_yaxes(title_text="成交占比 (%)", tickformat=",.1f", row=3, col=1)
    
    # Add grid
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='rgba(128,128,128,0.2)')
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='rgba(128,128,128,0.2)')
    
    print("Chart creation completed! Displaying...")
    
    # Save HTML file first
    html_file = 'market_analysis_final.html'
    fig.write_html(html_file)
    print(f"Chart saved as: {html_file}")
    
    # 不再使用 fig.show()，直接打开HTML文件
    import webbrowser
    import os
    html_path = os.path.abspath(html_file)
    webbrowser.open(f'file://{html_path}')
    print(f"✅ 图表已在浏览器中打开: {html_path}")
    print("💡 建议：请直接打开HTML文件，避免使用临时服务器")
    
    return fig

def main():
    """Main function"""
    print("=" * 60)
    print("📊 Comprehensive Market Analysis - Final Version")
    print("=" * 60)
    print("Data Sources:")
    print("✅ Shanghai Composite - akshare (East Money API)")
    print("✅ Real Margin Trading Data - East Money (Latest to 2025)")
    print("✅ CSI 300 Volume Ratio - akshare (CSI 300 Index Data)")
    print("✅ Fully Interactive Charts - Plotly")
    print("=" * 60)
    
    try:
        fig = create_interactive_chart()
        
        print("\n" + "=" * 60)
        print("🎉 Successfully created interactive chart!")
        print("Features:")
        print("✅ Hover to display detailed values")
        print("✅ Support zoom and drag operations")
        print("✅ Legend click to control show/hide")
        print("✅ Toolbar provides screenshot, reset functions")
        print("🌟 Using latest 2025 margin trading data (East Money)")
        print("📊 Data range: 90 days (Shanghai Composite + Margin Trading + CSI 300 Ratio)")
        print("💰 Clear unit notation (100M CNY + percentage)")
        print("📈 CSI 300 Volume Ratio: Reflects large-cap stock activity in the market")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main() 
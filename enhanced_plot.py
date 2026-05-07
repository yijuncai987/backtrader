import backtrader as bt
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt

def get_sz_index_data():
    """获取上证指数数据"""
    print("正在获取上证指数数据...")
    
    df_full = ak.stock_zh_index_daily(symbol="sh000001")
    
    if df_full.empty:
        print("错误：未能获取上证指数数据")
        return None

    df_full['date'] = pd.to_datetime(df_full['date'])
    df_full.set_index('date', inplace=True)

    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)
    
    df = df_full[(df_full.index >= start_date) & (df_full.index <= end_date)].copy()
    
    if df.empty:
        print("错误：在指定日期范围内没有上证指数数据")
        return None

    print(f"获取上证指数 {len(df)} 条数据")
    
    df.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    }, inplace=True)
    
    df['OpenInterest'] = 0
    
    return df

def get_margin_data():
    """获取融资融券数据"""
    print("正在获取融资融券数据...")
    
    try:
        # 获取融资融券余额数据
        df_margin = ak.stock_margin_sse()
        
        if df_margin.empty:
            print("警告：未能获取融资融券数据")
            return None
            
        df_margin['date'] = pd.to_datetime(df_margin['date'])
        df_margin.set_index('date', inplace=True)
        
        # 只保留最近90天的数据
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        df_margin = df_margin[(df_margin.index >= start_date) & (df_margin.index <= end_date)]
        
        print(f"获取融资融券 {len(df_margin)} 条数据")
        return df_margin
        
    except Exception as e:
        print(f"获取融资融券数据失败: {e}")
        return None

def get_hs300_ratio_data():
    """获取沪深300成交额占比数据"""
    print("正在获取沪深300和市场成交额数据...")
    
    try:
        # 获取沪深300指数数据
        df_hs300 = ak.stock_zh_index_daily(symbol="sh000300")
        df_hs300['date'] = pd.to_datetime(df_hs300['date'])
        df_hs300.set_index('date', inplace=True)
        
        # 获取上证成交额（代表）
        df_sh = ak.stock_zh_index_daily(symbol="sh000001")
        df_sh['date'] = pd.to_datetime(df_sh['date'])
        df_sh.set_index('date', inplace=True)
        
        # 计算最近90天
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        
        df_hs300 = df_hs300[(df_hs300.index >= start_date) & (df_hs300.index <= end_date)]
        df_sh = df_sh[(df_sh.index >= start_date) & (df_sh.index <= end_date)]
        
        # 简化计算：用沪深300成交额除以上证成交额作为参考比例
        # 注意：这是一个简化的计算，实际情况更复杂
        ratio_data = []
        for date in df_hs300.index:
            if date in df_sh.index:
                hs300_vol = df_hs300.loc[date, 'volume']
                sh_vol = df_sh.loc[date, 'volume']
                if sh_vol > 0:
                    ratio = (hs300_vol / sh_vol) * 100  # 转换为百分比
                    ratio_data.append({'date': date, 'hs300_ratio': ratio})
        
        if ratio_data:
            df_ratio = pd.DataFrame(ratio_data)
            df_ratio.set_index('date', inplace=True)
            print(f"计算沪深300占比 {len(df_ratio)} 条数据")
            return df_ratio
        else:
            print("警告：无法计算沪深300占比")
            return None
            
    except Exception as e:
        print(f"获取沪深300数据失败: {e}")
        return None

class Enhanced_Strategy(bt.Strategy):
    """增强策略，显示多个数据源"""
    
    def __init__(self):
        # 添加移动平均线
        self.sma10 = bt.indicators.SimpleMovingAverage(self.data0.close, period=10)
        self.sma20 = bt.indicators.SimpleMovingAverage(self.data0.close, period=20)

def main():
    """主函数"""
    # 获取所有数据
    sz_data = get_sz_index_data()
    margin_data = get_margin_data()
    ratio_data = get_hs300_ratio_data()
    
    if sz_data is None:
        print("无法获取上证指数数据，退出")
        return
    
    # 创建cerebro
    cerebro = bt.Cerebro()
    
    # 添加上证指数数据
    data_feed = bt.feeds.PandasData(dataname=sz_data)
    cerebro.adddata(data_feed, name='上证指数')
    
    # 添加策略
    cerebro.addstrategy(Enhanced_Strategy)
    
    print("\n开始运行回测...")
    cerebro.run()
    
    print("开始绘制综合图表...")
    
    # 创建子图布局
    fig, axes = plt.subplots(3, 1, figsize=(15, 12))
    fig.suptitle('市场综合分析图表', fontsize=16, fontweight='bold')
    
    # 子图1：上证指数K线图（使用backtrader原生绘图）
    print("绘制上证指数K线图...")
    cerebro.plot(style='candlestick', barup='red', bardown='green', volume=False)
    
    # 子图2：融资融券余额
    if margin_data is not None and len(margin_data) > 0:
        print("绘制融资融券数据...")
        axes[1].plot(margin_data.index, margin_data.iloc[:, 0], 
                    color='blue', linewidth=2, label='融资融券余额')
        axes[1].set_title('融资融券余额趋势', fontweight='bold')
        axes[1].set_ylabel('余额（万元）')
        axes[1].grid(True, alpha=0.3)
        axes[1].legend()
        
        # 格式化Y轴显示
        axes[1].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x/10000:.1f}万'))
    else:
        axes[1].text(0.5, 0.5, '融资融券数据获取失败', 
                    ha='center', va='center', transform=axes[1].transAxes,
                    fontsize=12, color='red')
        axes[1].set_title('融资融券余额趋势（数据缺失）')
    
    # 子图3：沪深300占比
    if ratio_data is not None and len(ratio_data) > 0:
        print("绘制沪深300占比...")
        axes[2].plot(ratio_data.index, ratio_data['hs300_ratio'], 
                    color='green', linewidth=2, label='沪深300成交额占比')
        axes[2].set_title('沪深300成交额占比趋势', fontweight='bold')
        axes[2].set_ylabel('占比（%）')
        axes[2].grid(True, alpha=0.3)
        axes[2].legend()
    else:
        axes[2].text(0.5, 0.5, '沪深300占比数据计算失败', 
                    ha='center', va='center', transform=axes[2].transAxes,
                    fontsize=12, color='red')
        axes[2].set_title('沪深300成交额占比趋势（数据缺失）')
    
    # 调整布局
    plt.tight_layout()
    plt.show()
    
    print("\n图表绘制完成！")

if __name__ == '__main__':
    main() 
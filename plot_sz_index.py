import backtrader as bt
from backtrader_plotting import Bokeh
from backtrader_plotting.schemes import Tradimo
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

def get_sz_index_data():
    """使用akshare获取上证指数数据"""
    print("正在从akshare获取上证指数数据...")
    
    # 先获取全部历史数据
    df_full = ak.stock_zh_index_daily(symbol="sh000001")
    
    if df_full.empty:
        print("错误：未能获取到数据，请检查网络或akshare接口。")
        return None

    # 修正：将 'date' 列转换为 datetime 对象并设为索引
    df_full['date'] = pd.to_datetime(df_full['date'])
    df_full.set_index('date', inplace=True)

    # 设置日期范围，在本地进行筛选
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90) # 延长到90天，图表更好看
    
    # 筛选最近90天的数据
    df = df_full[(df_full.index >= start_date) & (df_full.index <= end_date)]
        
    print(f"成功获取 {len(df)} 条数据，从 {df.index[0].date()} 到 {df.index[-1].date()}")
    
    # 数据格式转换
    df.index.name = 'datetime'
    df.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    }, inplace=True)
    
    # backtrader需要的列
    df['OpenInterest'] = 0
    
    return df

def main():
    """主函数"""
    # 创建Cerebro引擎
    cerebro = bt.Cerebro()

    # 获取数据
    data_df = get_sz_index_data()
    
    if data_df is None:
        return

    # 将pandas DataFrame转换为backtrader的数据源格式
    data_feed = bt.feeds.PandasData(dataname=data_df)
    
    # 添加数据到Cerebro
    cerebro.adddata(data_feed, name='SZ_Index')

    # 添加一个简单的移动平均线指标
    cerebro.addindicator(bt.indicators.SimpleMovingAverage, period=10)
    cerebro.addindicator(bt.indicators.SimpleMovingAverage, period=20)
    
    print("\n准备使用Plotly进行交互式绘图...")
    
    # 运行并使用 Plotly (via Bokeh) 绘图
    # 注意：这里我们使用的是Bokeh，它是Plotly生态的一部分，并且与backtrader-plotting结合得很好
    b = Bokeh(style='bar', plot_mode='single', scheme=Tradimo())
    cerebro.plot(b)

if __name__ == '__main__':
    main() 
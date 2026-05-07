import backtrader as bt
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

def get_sz_index_data():
    """使用akshare获取上证指数数据"""
    print("正在从akshare获取上证指数数据...")
    
    df_full = ak.stock_zh_index_daily(symbol="sh000001")
    
    if df_full.empty:
        print("错误：未能获取到数据，请检查网络或akshare接口。")
        return None

    df_full['date'] = pd.to_datetime(df_full['date'])
    df_full.set_index('date', inplace=True)

    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)
    
    df = df_full[(df_full.index >= start_date) & (df_full.index <= end_date)].copy()
        
    if df.empty:
        print("错误：在指定日期范围内没有数据。")
        return None

    print(f"成功获取 {len(df)} 条数据，从 {df.index[0].date()} 到 {df.index[-1].date()}")
    
    df.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    }, inplace=True)
    
    df['OpenInterest'] = 0
    
    return df

def main():
    """主函数"""
    cerebro = bt.Cerebro()

    data_df = get_sz_index_data()
    
    if data_df is None:
        return

    data_feed = bt.feeds.PandasData(dataname=data_df)
    cerebro.adddata(data_feed, name='上证指数')

    # 添加移动平均线
    cerebro.addindicator(bt.indicators.SimpleMovingAverage, period=10)
    cerebro.addindicator(bt.indicators.SimpleMovingAverage, period=20)
    
    print("\n开始运行并绘图...")
    
    # 运行回测
    cerebro.run()
    
    # 使用默认的matplotlib绘图
    cerebro.plot(style='candlestick', barup='red', bardown='green', volume=True)

if __name__ == '__main__':
    main() 
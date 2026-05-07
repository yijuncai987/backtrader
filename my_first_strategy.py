#!/usr/bin/env python
# -*- coding: utf-8; py-indent-offset:4 -*-

"""
我的第一个量化策略 - RSI超买超卖策略
适合新手学习和修改
"""

import backtrader as bt
import pandas as pd
import numpy as np
from datetime import datetime

class MyFirstStrategy(bt.Strategy):
    """
    RSI超买超卖策略
    - 当RSI < 30时买入（超卖）
    - 当RSI > 70时卖出（超买）
    """
    
    params = (
        ('rsi_period', 14),      # RSI周期
        ('rsi_upper', 70),       # 超买线
        ('rsi_lower', 30),       # 超卖线
        ('printlog', True),      # 是否打印日志
    )
    
    def __init__(self):
        # 添加RSI指标
        self.rsi = bt.indicators.RSI(self.data.close, period=self.params.rsi_period)
        
        # 记录交易状态
        self.order = None
        self.dataclose = self.datas[0].close
        
    def log(self, txt, dt=None):
        """日志记录函数"""
        if self.params.printlog:
            dt = dt or self.datas[0].datetime.date(0)
            print(f'{dt.isoformat()}: {txt}')
    
    def notify_order(self, order):
        """订单状态通知"""
        if order.status in [order.Submitted, order.Accepted]:
            return
        
        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(f'买入成功: 价格={order.executed.price:.2f}, 手续费={order.executed.comm:.2f}')
            else:
                self.log(f'卖出成功: 价格={order.executed.price:.2f}, 手续费={order.executed.comm:.2f}')
        
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log('订单被取消/拒绝')
        
        self.order = None
    
    def notify_trade(self, trade):
        """交易结果通知"""
        if not trade.isclosed:
            return
        
        self.log(f'交易结果: 盈亏={trade.pnl:.2f}, 净盈亏={trade.pnlcomm:.2f}')
    
    def next(self):
        """策略主逻辑 - 每个数据点都会执行"""
        
        # 记录当前数据
        self.log(f'收盘价={self.dataclose[0]:.2f}, RSI={self.rsi[0]:.2f}')
        
        # 如果有未完成的订单，跳过
        if self.order:
            return
        
        # 检查是否已有持仓
        if not self.position:
            # 没有持仓，检查买入信号
            if self.rsi[0] < self.params.rsi_lower:
                self.log(f'买入信号: RSI={self.rsi[0]:.2f} < {self.params.rsi_lower}')
                # 记录买入订单
                self.order = self.buy()
        
        else:
            # 有持仓，检查卖出信号
            if self.rsi[0] > self.params.rsi_upper:
                self.log(f'卖出信号: RSI={self.rsi[0]:.2f} > {self.params.rsi_upper}')
                # 记录卖出订单
                self.order = self.sell()
    
    def stop(self):
        """策略结束时调用"""
        self.log(f'策略结束 - RSI周期: {self.params.rsi_period}, 最终价值: {self.broker.getvalue():.2f}')


def create_sample_data():
    """创建示例数据"""
    print("生成模拟股票数据...")
    
    # 生成更有趣的股票数据（带趋势和震荡）
    np.random.seed(42)
    dates = pd.date_range(start='2020-01-01', end='2023-12-31', freq='D')
    
    # 创建有趋势的价格数据
    n_days = len(dates)
    base_price = 100.0
    trend = 0.0002  # 长期上涨趋势
    
    prices = [base_price]
    for i in range(1, n_days):
        # 添加趋势 + 随机波动
        daily_return = trend + np.random.normal(0, 0.015)
        price = prices[-1] * (1 + daily_return)
        
        # 偶尔添加大幅波动（模拟市场事件）
        if np.random.random() < 0.02:  # 2%概率
            shock = np.random.choice([-0.05, 0.08])  # 大跌或大涨
            price *= (1 + shock)
        
        prices.append(max(price, 1.0))  # 确保价格为正
    
    # 生成OHLC数据
    data = []
    for i, (date, close) in enumerate(zip(dates, prices)):
        # 只保留工作日
        if date.weekday() >= 5:  # 跳过周末
            continue
            
        # 生成当日价格
        volatility = 0.02
        high = close * (1 + abs(np.random.normal(0, volatility/2)))
        low = close * (1 - abs(np.random.normal(0, volatility/2)))
        open_price = low + (high - low) * np.random.random()
        
        # 确保价格逻辑正确
        high = max(high, open_price, close)
        low = min(low, open_price, close)
        
        volume = np.random.randint(50000, 200000)
        
        data.append({
            'Date': date,
            'Open': round(open_price, 2),
            'High': round(high, 2),
            'Low': round(low, 2),
            'Close': round(close, 2),
            'Volume': volume
        })
    
    df = pd.DataFrame(data)
    df.set_index('Date', inplace=True)
    
    # 保存数据
    csv_file = 'my_stock_data.csv'
    df.to_csv(csv_file)
    print(f"数据已保存: {csv_file}")
    print(f"数据点数: {len(df)}")
    
    return csv_file


def main():
    """主函数"""
    print("=" * 60)
    print("我的第一个量化策略 - RSI超买超卖")
    print("=" * 60)
    
    # 创建Cerebro引擎
    cerebro = bt.Cerebro()
    
    # 添加策略
    cerebro.addstrategy(MyFirstStrategy)
    
    # 创建数据
    data_file = create_sample_data()
    
    # 加载数据
    data = bt.feeds.GenericCSVData(
        dataname=data_file,
        datetime=0,
        open=1,
        high=2,  
        low=3,
        close=4,
        volume=5,
        openinterest=-1,
        dtformat='%Y-%m-%d'
    )
    
    cerebro.adddata(data)
    
    # 设置初始资金
    start_cash = 10000.0
    cerebro.broker.setcash(start_cash)
    
    # 设置手续费
    cerebro.broker.setcommission(commission=0.001)  # 0.1%
    
    # 设置交易数量
    cerebro.addsizer(bt.sizers.FixedSize, stake=10)
    
    # 添加分析器
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
    
    print(f'起始资金: ${start_cash:,.2f}')
    
    # 运行策略
    print("\n开始回测...")
    results = cerebro.run()
    
    # 打印结果
    final_value = cerebro.broker.getvalue()
    
    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    print(f'起始资金: ${start_cash:,.2f}')
    print(f'最终资金: ${final_value:,.2f}')
    print(f'总收益: ${final_value - start_cash:,.2f}')
    print(f'收益率: {((final_value / start_cash) - 1) * 100:.2f}%')
    
    # 分析器结果
    strat = results[0]
    sharpe = strat.analyzers.sharpe.get_analysis()
    drawdown = strat.analyzers.drawdown.get_analysis()
    
    if 'sharperatio' in sharpe:
        print(f'夏普比率: {sharpe["sharperatio"]:.3f}')
    print(f'最大回撤: {drawdown["max"]["drawdown"]:.2f}%')
    
    # 绘图
    try:
        print("\n正在生成图表...")
        cerebro.plot(style='candlestick')
        print("图表已显示")
    except Exception as e:
        print(f"绘图错误: {e}")


if __name__ == '__main__':
    main() 
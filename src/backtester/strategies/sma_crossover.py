import pandas as pd
from backtesting import Strategy
from backtesting.lib import crossover


class SMACrossover(Strategy):
    """Simple moving-average crossover.

    Buy when the fast SMA crosses above the slow SMA.
    Sell (close position) when the fast SMA crosses below the slow SMA.
    """

    fast_period: int = 10
    slow_period: int = 30

    def init(self):
        close = self.data.Close
        self.fast = self.I(lambda s: pd.Series(s).rolling(self.fast_period).mean().values, close)
        self.slow = self.I(lambda s: pd.Series(s).rolling(self.slow_period).mean().values, close)

    def next(self):
        if crossover(self.fast, self.slow):
            self.buy()
        elif crossover(self.slow, self.fast):
            self.position.close()

# Portfolio realistic ETF sell optimization

Universo: ETFs habilitados. Entrada: solo BUY_CANDIDATE. Salida: position_alerts.

Combinaciones evaluadas: 16

Ranking usado: 30% expectancy + 20% profit factor*10 + 35% portfolio return - 15% max drawdown.


## Mejor combinacion

- take_profit: 40.0
- trim_position: 10.0
- reduce_risk: 20.0
- exit_candidate: 100.0
- stop_loss_warning: 100.0
- rebalance_sell: 10.0
- total_trades: 789.0
- profit_factor: 1.76
- expectancy_pct: 0.53
- portfolio_return_pct: -3.6
- max_drawdown_pct: 12.166
- strategy_score: 0.594

## Top 5

 exit_candidate  stop_loss_warning  take_profit  trim_position  reduce_risk  rebalance_sell  total_trades  profit_factor  expectancy_pct  portfolio_return_pct  max_drawdown_pct  buy_count  sell_partial_count  sell_full_count  strategy_score
          100.0              100.0         40.0           10.0         20.0            10.0           789           1.76            0.53                 -3.60            12.166        351                 440               50           0.594
          100.0              100.0         40.0           10.0         20.0            20.0           789           1.76            0.53                 -3.60            12.166        351                 440               50           0.594
          100.0              100.0         25.0           10.0         20.0            10.0           789           1.76            0.53                 -4.70            12.166        351                 440               50           0.209
          100.0              100.0         25.0           10.0         20.0            20.0           789           1.76            0.53                 -4.70            12.166        351                 440               50           0.209
          100.0              100.0         40.0           10.0         35.0            10.0           791           1.71            0.46                 -4.43            12.284        353                 440               50           0.165
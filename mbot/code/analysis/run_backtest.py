# code/analysis/run_backtest.py

import json
import os
import sys
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_macd_backtest
from utilities.strategy_logic import calculate_macd_indicators

def main():
    print("\n--- [Modus: Einzel-Backtest (mbot - MACD)] ---")
    
    try:
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        config_path = os.path.join(project_root, 'code', 'strategies', 'macd', 'config.json')
        with open(config_path, 'r') as f:
            config = json.load(f)
        print(f"Lade Live-Konfiguration für {config['market']['symbol']} ({config['market']['timeframe']})...")
    except Exception as e:
        print(f"Fehler beim Laden der config.json: {e}")
        return

    start_date = input("Startdatum für den Backtest eingeben (JJJJ-MM-TT): ")
    end_date = input("Enddatum für den Backtest eingeben (JJJJ-MM-TT): ")
    start_capital = float(input("Startkapital für den Backtest eingeben (z.B. 1000): "))

    symbol = config['market']['symbol']
    timeframe = config['market']['timeframe']

    data = load_data(symbol, timeframe, start_date, end_date)
    if data.empty:
        print(f"Keine Daten für den Zeitraum {start_date} bis {end_date} gefunden.")
        return

    params = {
        **config['strategy'],
        **config['risk'],
        'start_capital': start_capital
    }

    print("Berechne Indikatoren und führe Backtest aus...")
    data_with_indicators = calculate_macd_indicators(data.copy(), params)
    result = run_macd_backtest(data_with_indicators.dropna(), params)

    print("\n" + "="*50)
    print("   +++ BACKTEST-ERGEBNIS +++")
    print("="*50)
    print(f"  Zeitraum:          {start_date} bis {end_date}")
    print(f"  Startkapital:      {start_capital:.2f} USDT")
    print(f"  Endkapital:        {result['end_capital']:.2f} USDT")
    print(f"  Gesamtgewinn (PnL):{result['total_pnl_pct']:.2f} %")
    print(f"  Max. Drawdown:     {result['max_drawdown_pct']*100:.2f} %")
    print(f"  Anzahl Trades:     {result['trades_count']}")
    print(f"  Win-Rate:          {result['win_rate']:.2f} %")
    print("="*50)

    trade_log_list = result.get('trade_log', [])
    if trade_log_list:
        print("\n" + "--- HANDELS-CHRONIK ---".center(110))
        print("  " + "-"*106)
        print("  {:^28} | {:<7} | {:<7} | {:>12} | {:>12} | {:>18}".format(
            "Datum & Uhrzeit (UTC)", "Seite", "Hebel", "Gewinn/Verlust", "Kontostand", "Grund"))
        print("  " + "-"*106)

        display_list = trade_log_list
        if len(trade_log_list) > 20:
            display_list = trade_log_list[:10] + [None] + trade_log_list[-10:]

        for trade in display_list:
            if trade is None:
                print("  ...".center(110))
                continue

            side_str = trade['side'].capitalize().ljust(7)
            leverage_str = f"{int(trade.get('leverage', 0))}x".ljust(7)
            pnl_str = f"{trade['pnl']:+9.2f} USDT".rjust(12)
            balance_str = f"{trade['balance']:.2f} USDT".rjust(12)
            reason_str = trade['reason'].ljust(15)
            
            print(f"  {trade['timestamp']:<28} | {side_str} | {leverage_str} | {pnl_str} | {balance_str} | {reason_str}")
        print("  " + "-"*106)

if __name__ == "__main__":
    main()

# code/analysis/run_backtest.py for mbot

import json
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_mbot_backtest # Geändert
from utilities.strategy_logic import calculate_mbot_indicators # Geändert

def main():
    print("\n--- [Modus: mbot Einzel-Backtest] ---")
    
    try:
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        config_path = os.path.join(project_root, 'code', 'strategies', 'envelope', 'config.json')
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

    # Kombiniere alle Parameter für den Backtest
    params = {
        **config,
        'start_capital': start_capital
    }

    print("Berechne Indikatoren und führe Backtest aus...")
    data_with_indicators = calculate_mbot_indicators(data.copy(), params)
    result = run_mbot_backtest(data_with_indicators.dropna(), params)

    print("\n" + "="*50)
    print("     +++ MBOT BACKTEST-ERGEBNIS +++")
    print("="*50)
    print(f"  Zeitraum:           {start_date} bis {end_date}")
    print(f"  Startkapital:       {start_capital:.2f} USDT")
    print(f"  Endkapital:         {result['end_capital']:.2f} USDT")
    print(f"  Gesamtgewinn (PnL): {result['total_pnl_pct']:.2f} %")
    print(f"  Max. Drawdown:      {result['max_drawdown_pct']*100:.2f} %")
    print(f"  Anzahl Trades:      {result['trades_count']}")
    print(f"  Win-Rate:           {result['win_rate']:.2f} %")
    print("="*50)
    
    # Optional: Trade-Log ausgeben (gekürzt bei vielen Trades)
    trade_log_list = result.get('trade_log', [])
    if trade_log_list:
        print("\n--- Handels-Chronik (Auszug) ---")
        display_list = trade_log_list
        if len(trade_log_list) > 20:
            display_list = trade_log_list[:10] + [None] + trade_log_list[-10:]

        for trade in display_list:
            if trade is None:
                print("...")
                continue
            
            pnl_color = "\033[92m" if trade['pnl'] > 0 else "\033[91m"
            print(f"{trade['timestamp']} | {trade['side'].upper():<5} | PnL: {pnl_color}{trade['pnl']:+8.2f} USDT\033[0m | Balance: {trade['balance']:.2f} USDT | Grund: {trade['reason']}")
        print("-" * 30)

if __name__ == "__main__":
    main()

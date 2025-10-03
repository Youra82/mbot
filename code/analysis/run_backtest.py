import json
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from analysis.backtest import load_data, run_backtest
from utilities.strategy_logic import calculate_macd_forecast_indicators

def main():
    print("\n--- [Modus: Einzel-Backtest für mbot] ---")
    
    try:
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        config_path = os.path.join(project_root, 'code', 'strategies', 'mbot', 'config.json')
        with open(config_path, 'r') as f:
            config = json.load(f)
        print(f"Lade mbot Live-Konfiguration für {config['market']['symbol']}...")
    except Exception as e:
        print(f"Fehler beim Laden der mbot/config.json: {e}")
        return

    start_date = input("Startdatum für den Backtest (JJJJ-MM-TT): ")
    end_date = input("Enddatum für den Backtest (JJJJ-MM-TT): ")
    start_capital = float(input("Startkapital (z.B. 1000): "))

    symbol = config['market']['symbol']
    timeframe = config['market']['timeframe']

    full_data = load_data(symbol, timeframe, start_date, end_date)
    if full_data.empty:
        print(f"Keine Daten für den Zeitraum gefunden.")
        return

    params = {
        **config['strategy'],
        **config['risk'],
        'start_capital': start_capital,
        'start_date_str': start_date # Wichtig für die Backtest-Funktion
    }

    print("Berechne Indikatoren und führe Backtest aus...")
    data_with_indicators = calculate_macd_forecast_indicators(full_data.copy(), params)
    result = run_backtest(data_with_indicators.dropna(), params)

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

if __name__ == "__main__":
    main()

def main(n_procs, n_gen_default):
    print("\n--- [Stufe 1/2] Globale Suche mit Pymoo für mbot ---")
    
    symbol_input = input("Handelspaar(e) eingeben (z.B. BTC ETH): ")
    timeframe_input = input("Zeitfenster eingeben (z.B. 1h 4h): ")
    start_date = input("Startdatum eingeben (JJJJ-MM-TT): ")
    end_date = input("Enddatum eingeben (JJJJ-MM-TT): ")
    n_gen_input = input(f"Anzahl der Generationen eingeben (Standard: {n_gen_default}): ")
    n_gen = int(n_gen_input) if n_gen_input else n_gen_default

    global START_CAPITAL, MINIMUM_TRADES
    START_CAPITAL = float(input("Startkapital in USDT eingeben (z.B. 1000): "))
    MINIMUM_TRADES = int(input("Mindestanzahl an Trades (z.B. 20): "))
    
    symbols_to_run = symbol_input.split()
    timeframes_to_run = timeframe_input.split()
    all_champions = []

    for symbol_short in symbols_to_run:
        for timeframe in timeframes_to_run:
            symbol = f"{symbol_short.upper()}/USDT:USDT"
            global HISTORICAL_DATA
            
            print(f"\nLade Daten für {symbol} ({timeframe})...")
            HISTORICAL_DATA = load_data(symbol, timeframe, start_date, end_date)
            
            if HISTORICAL_DATA is None or HISTORICAL_DATA.empty:
                print(f"FEHLER: Keine Daten für {symbol} im Zeitraum {start_date} bis {end_date} gefunden. Überspringe...")
                continue
            
            # NEU: Feedback zur Datenmenge
            print(f"Daten erfolgreich geladen: {len(HISTORICAL_DATA)} Kerzen von {HISTORICAL_DATA.index.min()} bis {HISTORICAL_DATA.index.max()}")


            print(f"\n===== Optimiere {symbol} auf {timeframe} für mbot =====")
            
            with Pool(n_procs) as pool:
                problem = OptimizationProblem(parallelization=StarmapParallelization(pool.starmap))
                problem.start_date_str = start_date

                algorithm = NSGA2(pop_size=100)
                termination = get_termination("n_gen", n_gen)

                with tqdm(total=n_gen, desc="Generationen") as pbar:
                    res = minimize(problem, algorithm, termination, seed=1, callback=TqdmCallback(pbar), verbose=False)

                valid_indices = [i for i, f in enumerate(res.F) if f[0] < -1]
                if not valid_indices: continue
                
                for i in sorted(valid_indices, key=lambda i: res.F[i][0])[:5]:
                    params_raw = res.X[i]
                    param_dict = {
                        'symbol': symbol, 'timeframe': timeframe, 
                        'start_date': start_date, 'end_date': end_date, 'start_capital': START_CAPITAL,
                        'pnl': -res.F[i][0], 'drawdown': res.F[i][1],
                        'params': {
                            'fast_len': int(params_raw[0]), 'slow_len': int(params_raw[1]), 'signal_len': int(params_raw[2]),
                            'swing_lookback': int(params_raw[3]), 'sl_buffer_pct': round(params_raw[4], 2),
                            'upper_percentile': int(params_raw[5]), 'lower_percentile': int(params_raw[6]),
                            'addons': { 'impulse_macd_filter': { 'enabled': True, 'lengthMA': int(params_raw[7]) }}
                        }
                    }
                    all_champions.append(param_dict)

    if not all_champions:
        print("\nKeine vielversprechenden Kandidaten gefunden."); return

    output_file = os.path.join(os.path.dirname(__file__), 'optimization_candidates.json')
    with open(output_file, 'w') as f:
        json.dump(all_champions, f, indent=4)
    print(f"\n--- Globale Suche beendet. Top-Kandidaten in '{output_file}' gespeichert. ---")

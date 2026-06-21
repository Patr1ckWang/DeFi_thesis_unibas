import os
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta
import pandas as pd

DIR = os.path.dirname(os.path.abspath(__file__))

# Token contract address (for DeFi Llama coins API)
TOKEN_ADDRESSES = {
    'sushi': 'ethereum:0x6b3595068778dd592e39a122f4f5a5cf09c90fe2',
    'uni':   'ethereum:0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984',
    'eth':   'coingecko:ethereum',
    'comp':  'ethereum:0xc00e94cb662c3520282e6f5717214004a7f26888',
    'link':  'ethereum:0x514910771af9ca656af840dff83e8264ecf986ca',
    'yfi':   'ethereum:0x0bc529c00c6401aef6d220be8c6ea1667f6ad93e',
}

def fetch_token_prices():

    # daily timestamps （ 2020-09-09 to 2021-05-05）
    start = datetime(2020, 9, 9, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2021, 5, 5, 0, 0, 0, tzinfo=timezone.utc)
    dates = []
    current = start
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)
    
    # API需要的格式
    addr_str = ','.join(TOKEN_ADDRESSES.values())
    
    all_rows = []
    
    for i, dt in enumerate(dates):
        ts = int(dt.timestamp())
        url = f'https://coins.llama.fi/prices/historical/{ts}/{addr_str}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        success = False
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
                    success = True
                    break
            except Exception as e:
                time.sleep(2)
                
        if not success:
            print(f"  WARNING: Failed for {dt.date()} after 3 attempts")
            continue
        
        row = {'date': dt.strftime('%Y-%m-%d')}
        coins = data.get('coins', {})
        for symbol, addr in TOKEN_ADDRESSES.items():
            coin_data = coins.get(addr, {})
            row[f'{symbol}_usd'] = coin_data.get('price', None)
        all_rows.append(row)
        
        # 限制速率
        if (i + 1) % 50 == 0:
            print(f"    Fetched {i+1}/{len(dates)} dates...")
        time.sleep(0.5)
    
    df = pd.DataFrame(all_rows)
    df['date'] = pd.to_datetime(df['date'])
    
    # Save
    outfile = os.path.join(DIR, 'raw_data', 'token_prices.csv')
    df.to_csv(outfile, index=False)

    for col in df.columns:
        if col.endswith('_usd'):
            valid = df[col].notna().sum()
            print(f"    {col}: {valid} valid prices", end='')
            if valid > 0:
                vals = df[col].dropna()
                print(f" (min=${vals.min():.2f}, max=${vals.max():.2f})")
            else:
                print()
    
    return df

def fetch_tvl():
    
    protocols = {'uniswap-v2': 'uniswap_v2', 'sushiswap': 'sushiswap'}
    all_data = {}
    
    for protocol_id, label in protocols.items():
        url = f'https://api.llama.fi/protocol/{protocol_id}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        success = False
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                    success = True
                    break
            except Exception as e:
                time.sleep(2)
                
        if not success:
            print(f"  ERROR fetching {protocol_id} after 3 attempts")
            continue
        
        tvl_data = data.get('tvl', [])
        print(f"  {protocol_id}: {len(tvl_data)} total data points")
        
        for entry in tvl_data:
            ts = entry.get('date', 0)
            total = entry.get('totalLiquidityUSD', 0)
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
            if '2020-09-01' <= date_str <= '2021-05-10':
                if date_str not in all_data:
                    all_data[date_str] = {}
                all_data[date_str][f'{label}_tvl_usd'] = total
        time.sleep(2)
    
    rows = []
    for date in sorted(all_data.keys()):
        row = {'date': date}
        row.update(all_data[date])
        rows.append(row)
    
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    
    outfile = os.path.join(DIR, 'raw_data','tvl_daily.csv')
    df.to_csv(outfile, index=False)

    for col in ['uniswap_v2_tvl_usd', 'sushiswap_tvl_usd']:
        if col in df.columns:
            vals = df[col].dropna()
            print(f"    {col}: min=${vals.min()/1e6:.1f}M, max=${vals.max()/1e6:.1f}M")
    
    return df

if __name__ == '__main__':
    fetch_token_prices()
    fetch_tvl()
    
    print("Done")

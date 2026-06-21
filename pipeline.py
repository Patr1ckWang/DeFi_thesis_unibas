"""
数据处理pipeline
输出: survival_panel.csv 
"""

import os
import sys
import json
import time
import csv
import urllib.request
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# timeline
T0 = pd.Timestamp('2020-09-09', tz='UTC')  # 迁移日
T_SHORT = pd.Timestamp('2020-09-15', tz='UTC')  # 短窗口结束
T_MID = pd.Timestamp('2020-11-17', tz='UTC')    # 中窗口结束 (UNI mining ends)
T_LONG = pd.Timestamp('2021-05-05', tz='UTC')   # 长窗口结束 (Uni V3 launch)

# 事件时间
UNI_AIRDROP = pd.Timestamp('2020-09-16', tz='UTC')
UNI_MINING_START = pd.Timestamp('2020-09-17', tz='UTC')
UNI_MINING_END = pd.Timestamp('2020-11-17', tz='UTC')


# Gas Price ( gasprice_daily.csv)
def load_gas_price():

    fp = os.path.join(DIR, 'raw_data','gasprice_daily.csv')

    df = pd.read_csv(fp)
    df.columns = ['date_str', 'unix_ts', 'value_wei']
    df['date'] = pd.to_datetime(df['date_str'], format='mixed')
    df['gas_gwei'] = df['value_wei'].astype(float) / 1e9
    df = df[(df['date'] >= '2020-09-09') & (df['date'] <= '2021-05-05')]
    df = df[['date', 'gas_gwei']].sort_values('date').reset_index(drop=True)
    return df

# 载入数据
def load_core_data():
    
    u_fp = os.path.join(DIR, 'users_v5.csv')
    if not os.path.exists(u_fp):
        u_fp = os.path.join(DIR, 'raw_data', 'users_v5.csv')
    users = pd.read_csv(u_fp)
    print(f"  users_v5.csv: {len(users)} rows, {users['user_address'].nunique()} unique users")
    
    s_fp = os.path.join(DIR, 'survival_v5.csv')
    if not os.path.exists(s_fp):
        s_fp = os.path.join(DIR, 'raw_data', 'survival_v5.csv')
    survival = pd.read_csv(s_fp)
    survival['event_time'] = pd.to_datetime(survival['event_time'])
    print(f"  survival.csv: {len(survival)} rows, {survival['user_address'].nunique()} unique users")
    
    return users, survival


# sushi初始余额
def compute_sushi_initial(users):
    
    # slp与lp迁移比率是1:1
    users['slp_initial'] = users['staked_sushi_0909']
    
    # 剔除 Runaway （修复 Immortal Time Bias）
    original_len = len(users)
    users = users[users['faction'] != 'Runaway'].copy()
    print(f"  Removed {original_len - len(users)} Runaway users from risk set.")

    migrators = users[users['faction'] == 'Migrator']
    print(f"  Migrators: {len(migrators)}")
    print(f"  SLP initial balance: mean={migrators['slp_initial'].mean():.4f}, "
          f"median={migrators['slp_initial'].median():.4f}")
    
    return users


# running balances 构建
def fix_running_balance(survival):
    
    neg_count = (survival['running_balance'] < 0).sum()
    neg_pct = neg_count / len(survival) * 100
    print(f"  Negative balance rows: {neg_count} ({neg_pct:.2f}%)")
    
    survival['running_balance'] = survival['running_balance'].clip(lower=0)
    
    # 加上时间序列合并的日期列
    survival['event_date'] = survival['event_time'].dt.normalize()
    
    return survival


# 死亡time和type
def compute_death(users, survival):
    
    DUST_THRESHOLD = 1e-6  #去dust
    
    # 前置过滤：排除已剔除用户（如 Runaway）的事件
    valid_users = set(users['user_address'].unique())
    survival = survival[survival['user_address'].isin(valid_users)]
    
    # 排序
    survival_sorted = survival.sort_values(['user_address', 'pool_name', 'platform', 'event_time'])
    
    # 第一次balance<=0就是死亡
    death_records = []
    
    grouped = survival_sorted.groupby(['user_address', 'pool_name', 'platform'])
    total_groups = len(grouped)
    
    for idx, ((user, pool, platform), group) in enumerate(grouped):
        
        balances = group['running_balance'].values
        events = group[['event_time', 'counterparty_type', 'amount']].values
        
        was_positive = False
        death_time = None
        death_type = None
        
        for i, bal in enumerate(balances):
            if bal > DUST_THRESHOLD:
                was_positive = True
            elif was_positive and bal <= DUST_THRESHOLD:
                # Death event
                death_time = events[i][0]  
                death_type = events[i][1]  # counterparty类型
                break
        
        #  死因
        if death_type:
            death_reason_map = {
                'sushiswap_mc': 'exit_to_sushi',
                'harvest_old': 'exit_to_harvest',
                'harvest_new': 'exit_to_harvest',
                'harvest_sushi': 'exit_to_harvest',
                'pickle': 'exit_to_pickle',
                'pool_contract': 'exit_burn',
                'user_transfer': 'exit_transfer',
                'mint': 'exit_other',
            }
            death_reason = death_reason_map.get(death_type, 'exit_other')
        else:
            death_reason = None
        
        # 最后时间
        last_time = group['event_time'].max()
        last_bal = balances[-1]
        
        death_records.append({
            'user_address': user,
            'pool_name': pool,
            'platform': platform,
            'death_time': death_time,
            'death_type': death_reason,
            'last_event_time': last_time,
            'last_balance': last_bal,
            'censored': death_time is None,  # True 如果没死
        })
    
    death_df = pd.DataFrame(death_records)
    
    # 计算生存时长
    death_df['death_time'] = pd.to_datetime(death_df['death_time'], utc=True)
    death_df['last_event_time'] = pd.to_datetime(death_df['last_event_time'], utc=True)
    
    # 修正：行政性删失 (Administrative Censoring)
    # 对于没死的用户，将其最后观测时间拉伸至T_LONG
    death_df.loc[death_df['censored'], 'last_event_time'] = T_LONG
    
    # Duration =（days，T0到死）
    death_df['duration_days'] = np.where(
        death_df['censored'],
        (death_df['last_event_time'] - T0).dt.total_seconds() / 86400,
        (death_df['death_time'] - T0).dt.total_seconds() / 86400
    )

    
    # 死没死
    death_df['event'] = (~death_df['censored']).astype(int)
    
    # 统计
    print(f"\n  Total user×pool×platform groups: {len(death_df)}")
    
    for plat in ['uniswap', 'sushiswap']:
        sub = death_df[death_df['platform'] == plat]
        died = sub[~sub['censored']]
        cens = sub[sub['censored']]
        print(f"\n  {plat.upper()}:")
        print(f"    Died: {len(died)} ({len(died)/len(sub)*100:.1f}%)")
        print(f"    Censored: {len(cens)} ({len(cens)/len(sub)*100:.1f}%)")
        if len(died) > 0:
            print(f"    Median survival: {died['duration_days'].median():.1f} days")
            print(f"    Death types:")
            for dt, count in died['death_type'].value_counts().items():
                print(f"      {dt}: {count} ({count/len(died)*100:.1f}%)")
    
    return death_df


# 按初始余额分层
def compute_wealth_quantiles(users):
    # 只有余额>0的用户
    has_balance = users[users['balance_0909'] > 1e-6].copy()
    
    if len(has_balance) > 0:
        has_balance['size_group'] = pd.qcut(
            has_balance['balance_0909'], 
            q=4, 
            labels=['Q1_small', 'Q2_medium', 'Q3_large', 'Q4_whale'],
            duplicates='drop'
        )
        users = users.merge(
            has_balance[['user_address', 'pool_name', 'size_group']], 
            on=['user_address', 'pool_name'], 
            how='left'
        )
        # 加入Q0_zero分组，nan填Q0
        users['size_group'] = users['size_group'].cat.add_categories('Q0_zero').fillna('Q0_zero')
    else:
        users['size_group'] = 'Q0_zero'
    
    print(f"  Size group distribution:")
    for g, count in users['size_group'].value_counts().sort_index().items():
        print(f"    {g}: {count}")
    
    # log_balance用来跑回归 (包含已质押的sushi，否则migrator的wealth会被低估为0)
    total_wealth = users['balance_0909'].fillna(0) + users['staked_sushi_0909'].fillna(0)
    users['log_balance'] = np.log1p(total_wealth.clip(lower=0))
    
    return users

# 输出面板数据
def build_survival_panel(users, death_df, gas_df, prices_df, tvl_df, pool_tvl_df=None):
    """Merge all data into a single analysis-ready LONG FORMAT panel for Cox time-varying covariates."""
    print("\n--- 8.2.6: Building LONG FORMAT survival panel ---")
    
    # 合并user属性和死亡数据
    uni_death = death_df[death_df['platform'] == 'uniswap'].copy()
    sushi_death = death_df[death_df['platform'] == 'sushiswap'].copy()
    
    # 合并user信息
    user_cols = ['user_address', 'pool_name', 'cohort', 'faction', 'user_type',
                 'balance_0909', 'staked_sushi_0909', 'first_entry_date',
                 'slp_initial', 'size_group', 'log_balance']
    
    # 获取所有的 Uniswap 分析对象 (Stayer/Fence-sitter 等有初始余额的人，以及 New 等后入场但出现在 death_df 中的人)
    uni_valid_keys = set(zip(uni_death['user_address'], uni_death['pool_name']))
    uni_users = users[
        (users['balance_0909'] > 1e-6) | 
        (users.apply(lambda x: (x.user_address, x.pool_name) in uni_valid_keys, axis=1))
    ].copy()
    panel_uni = uni_death.merge(
        uni_users[user_cols], 
        on=['user_address', 'pool_name'], 
        how='right'
    )
    panel_uni['track'] = 'uniswap'
    
    # 修复因为 right merge 导致缺失的 survival 时间 (静默者从未死掉， censored=True)
    panel_uni['censored'] = panel_uni['censored'].fillna(True)
    panel_uni['duration_days'] = panel_uni['duration_days'].fillna((T_LONG - T0).days)
    panel_uni['event'] = panel_uni['event'].fillna(0)
    panel_uni['last_event_time'] = panel_uni['last_event_time'].fillna(T_LONG)
    panel_uni['last_balance'] = panel_uni['last_balance'].fillna(panel_uni['balance_0909'])
    
    # 对于 SushiSwap 轨道同样处理（Migrator 和 Fence-sitter 有初始余额，或者在 death_df 中有活动）
    sushi_valid_keys = set(zip(sushi_death['user_address'], sushi_death['pool_name']))
    sushi_users = users[
        (users['staked_sushi_0909'] > 1e-6) | 
        (users.apply(lambda x: (x.user_address, x.pool_name) in sushi_valid_keys, axis=1))
    ].copy()
    panel_sushi = sushi_death.merge(
        sushi_users[user_cols],
        on=['user_address', 'pool_name'],
        how='right'
    )
    panel_sushi['track'] = 'sushiswap'
    
    # 静默的 Sushi 矿工（从未领取奖励或撤资）
    panel_sushi['censored'] = panel_sushi['censored'].fillna(True)
    panel_sushi['duration_days'] = panel_sushi['duration_days'].fillna((T_LONG - T0).days)
    panel_sushi['event'] = panel_sushi['event'].fillna(0)
    panel_sushi['last_event_time'] = panel_sushi['last_event_time'].fillna(T_LONG)
    panel_sushi['last_balance'] = panel_sushi['last_balance'].fillna(panel_sushi['slp_initial'])
    
    panel = pd.concat([panel_uni, panel_sushi], ignore_index=True)
    
    # 转为每日的long面板
    panel['entry_time'] = pd.to_datetime(panel['first_entry_date'], utc=True).clip(lower=T0)
    panel['entry_date'] = panel['entry_time'].dt.normalize()
    
    panel['exit_time'] = pd.to_datetime(np.where(
        panel['censored'],
        panel['last_event_time'],
        panel['death_time']
    ), utc=True)
    panel['exit_date'] = panel['exit_time'].dt.normalize()
    
    # 过滤掉退场时间早于入场时间的异常记录
    panel = panel[panel['exit_time'] >= panel['entry_time']].copy()
    
    # 为每个用户生成从 entry_date 到 exit_date 的每日日期列表
    def generate_dates(row):
        if row.entry_date <= row.exit_date:
            return pd.date_range(row.entry_date, row.exit_date)
        return [row.entry_date]
        
    panel['date'] = panel.apply(generate_dates, axis=1)
    
    long_panel = panel.explode('date').reset_index(drop=True)
    
    # 计算每日的起止时间 (相对于 T0 的天数)
    long_panel['day_offset'] = (long_panel['date'] - T0).dt.total_seconds() / 86400.0
    long_panel['start_time'] = long_panel['day_offset']
    long_panel['stop_time'] = long_panel['day_offset'] + 1.0
    
    # 首尾两天的精确时间调整
    exact_entry_offset = (long_panel['entry_time'] - T0).dt.total_seconds() / 86400.0
    exact_exit_offset = (long_panel['exit_time'] - T0).dt.total_seconds() / 86400.0
    
    long_panel['start_time'] = np.maximum(long_panel['start_time'], exact_entry_offset)
    
    is_last_day = long_panel['date'] == long_panel['exit_date']
    long_panel.loc[is_last_day, 'stop_time'] = exact_exit_offset[is_last_day]
    
    # 剔除无效的同一天同时间记录 (start >= stop)
    long_panel = long_panel[long_panel['start_time'] < long_panel['stop_time']].copy()
    
    # 分配事件状态：只有真实死亡那一天的记录 Event = 1，其他所有天为 0
    long_panel['daily_event'] = 0
    # 重新计算 is_last_day（因为经过了过滤）
    is_last_day = long_panel['date'] == long_panel['exit_date']
    died_mask = (~long_panel['censored']) & is_last_day
    long_panel.loc[died_mask, 'daily_event'] = 1
    
    # 合并每日的协变量
    long_panel['date_naive'] = long_panel['date'].dt.tz_localize(None)
    
    if gas_df is not None and len(gas_df) > 0:
        gas = gas_df.copy()
        gas['date'] = pd.to_datetime(gas['date'])
        gas = gas.set_index('date').resample('D').ffill().reset_index()
        gas['date'] = gas['date'].dt.tz_localize(None)
        long_panel = long_panel.merge(gas, left_on='date_naive', right_on='date', how='left')
        long_panel.drop(columns=['date_y'], errors='ignore', inplace=True)
        long_panel.rename(columns={'date_x': 'date'}, inplace=True)
        
    if prices_df is not None and len(prices_df) > 0:
        prices = prices_df.copy()
        prices['date'] = pd.to_datetime(prices['date'])
        prices = prices.set_index('date').resample('D').ffill().reset_index()
        prices['date'] = prices['date'].dt.tz_localize(None)
        long_panel = long_panel.merge(prices, left_on='date_naive', right_on='date', how='left')
        long_panel.drop(columns=['date_y'], errors='ignore', inplace=True)
        long_panel.rename(columns={'date_x': 'date'}, inplace=True)
        
    if tvl_df is not None and len(tvl_df) > 0:
        tvl = tvl_df.copy()
        tvl['date'] = pd.to_datetime(tvl['date'])
        tvl = tvl.set_index('date').resample('D').ffill().reset_index()
        tvl['date'] = tvl['date'].dt.tz_localize(None)
        long_panel = long_panel.merge(tvl, left_on='date_naive', right_on='date', how='left')
        long_panel.drop(columns=['date_y'], errors='ignore', inplace=True)
        long_panel.rename(columns={'date_x': 'date'}, inplace=True)
        
    if pool_tvl_df is not None and len(pool_tvl_df) > 0:
        pool_tvl = pool_tvl_df.copy()
        pool_tvl['date'] = pd.to_datetime(pool_tvl['date']).dt.tz_localize(None)
        pool_tvl.rename(columns={'platform': 'track'}, inplace=True)
        
        long_panel = long_panel.merge(
            pool_tvl[['track', 'pool_name', 'date', 'weth_reserve', 'swap_count']],
            left_on=['track', 'pool_name', 'date_naive'],
            right_on=['track', 'pool_name', 'date'],
            how='left'
        )
        long_panel.drop(columns=['date_y'], errors='ignore', inplace=True)
        long_panel.rename(columns={'date_x': 'date'}, inplace=True)
        
        long_panel['weth_reserve'] = long_panel.groupby(['track', 'pool_name'])['weth_reserve'].ffill()
        long_panel['swap_count'] = long_panel['swap_count'].fillna(0)
        
        if 'eth_usd' in long_panel.columns:
            long_panel['pool_tvl_usd'] = long_panel['weth_reserve'] * long_panel['eth_usd'] * 2
            
    # 计算SUSHI/UNI价格比率  又问题，暂时不用了
    if 'sushi_usd' in long_panel.columns and 'uni_usd' in long_panel.columns:
        long_panel['uni_usd'] = long_panel['uni_usd'].fillna(0)
        long_panel['sushi_uni_ratio'] = np.where(
            long_panel['uni_usd'] > 0,
            long_panel['sushi_usd'] / long_panel['uni_usd'],
            0
        )
        
    # 计算 Impermanent Loss (无常损失) 和 APY Decay (收益衰减)
    if 'eth_usd' in long_panel.columns:
        entry_prices = long_panel.sort_values('date').groupby(['user_address', 'pool_name', 'track'])['eth_usd'].first().reset_index()
        entry_prices.rename(columns={'eth_usd': 'entry_eth_usd'}, inplace=True)
        long_panel = long_panel.merge(entry_prices, on=['user_address', 'pool_name', 'track'], how='left')
        
        # IL = 2 * sqrt(p) / (1 + p) - 1
        p_ratio = long_panel['eth_usd'] / long_panel['entry_eth_usd']
        long_panel['impermanent_loss'] = (2 * np.sqrt(p_ratio) / (1 + p_ratio)) - 1
        
    # APY Decay Proxy (log days since migration)
    long_panel['days_since_migration'] = (long_panel['date'] - pd.to_datetime('2020-09-09', utc=True)).dt.total_seconds() / 86400.0
    long_panel['apy_decay_proxy'] = np.log1p(np.maximum(long_panel['days_since_migration'], 0))
        
    # D. 加入静态虚拟变量
    long_panel['is_old'] = (long_panel['cohort'] == 'Old').astype(int)
    long_panel['is_new'] = (long_panel['cohort'] == 'New').astype(int)
    long_panel['is_early_leaver'] = (long_panel['cohort'] == 'Early Leaver').astype(int)
    long_panel['is_returner'] = (long_panel['cohort'] == 'Returner').astype(int)
    
    long_panel['is_migrator'] = (long_panel['faction'] == 'Migrator').astype(int)
    long_panel['is_stayer'] = (long_panel['faction'] == 'Stayer').astype(int)
    
    long_panel['has_uni_mining'] = long_panel['pool_name'].isin(['USDC-WETH','USDT-WETH','DAI-WETH']).astype(int)
    # 移除了 has_harvest 避免完全多重共线性
    
    tier_map = {
        'USDC-WETH': 'full_coverage',
        'USDT-WETH': 'full_coverage', 
        'DAI-WETH': 'full_coverage',
        'COMP-WETH': 'half_coverage',
        'LINK-WETH': 'half_coverage',
        'YFI-WETH': 'half_coverage',
    }
    long_panel['analysis_tier'] = long_panel['pool_name'].map(tier_map)
    
    # 加入时间窗口指标
    long_panel['in_short_window'] = (long_panel['duration_days'] <= (T_SHORT - T0).days).astype(int)
    long_panel['in_mid_window'] = (long_panel['duration_days'] <= (T_MID - T0).days).astype(int)
    long_panel['in_long_window'] = (long_panel['duration_days'] <= (T_LONG - T0).days).astype(int)
    
    # 重命名event列
    long_panel.rename(columns={'event': 'overall_event', 'daily_event': 'event'}, inplace=True)
    
    # 清理中间列
    long_panel.drop(columns=[
        'entry_time', 'entry_date', 'exit_time', 'exit_date', 
        'date_naive', 'day_offset'
    ], errors='ignore', inplace=True)
    
    return long_panel

# main
if __name__ == '__main__':
    
    gas_df = load_gas_price()

    prices_df = pd.read_csv(os.path.join(DIR, 'raw_data', 'token_prices.csv'))
    tvl_df = pd.read_csv(os.path.join(DIR, 'raw_data','tvl_daily.csv'))
    
    pool_tvl_fp = os.path.join(DIR, 'raw_data', 'pool_tvl.csv')
    if os.path.exists(pool_tvl_fp):
        pool_tvl_df = pd.read_csv(pool_tvl_fp)
    else:
        pool_tvl_df = None
    
    users, survival = load_core_data()
    
    users = compute_sushi_initial(users)
    survival = fix_running_balance(survival)
    death_df = compute_death(users, survival)
    users = compute_wealth_quantiles(users)
    
    panel = build_survival_panel(users, death_df, gas_df, prices_df, tvl_df, pool_tvl_df)

    
    # Main analysis panel
    panel_file = os.path.join(DIR, 'cleaned_data', 'survival_panel.csv')
    panel.to_csv(panel_file, index=False)
   
    
    # Death details (for Fine-Gray analysis)
    death_file = os.path.join(DIR, 'cleaned_data', 'death_details.csv')
    death_df.to_csv(death_file, index=False)
  
    
    # Enhanced users table
    users_file = os.path.join(DIR, 'cleaned_data', 'users_enhanced.csv')
    users.to_csv(users_file, index=False)
    
    
    uni_panel = panel[panel['track'] == 'uniswap']
    print(f"\nUniswap track: {len(uni_panel)} user×pool observations")
    print(f"  By cohort: {uni_panel['cohort'].value_counts().to_dict()}")
    print(f"  By faction: {uni_panel['faction'].value_counts().to_dict()}")
    print(f"  Events (deaths): {uni_panel['event'].sum()}")
    print(f"  Censored: {(uni_panel['event'] == 0).sum()}")
    
    sushi_panel = panel[panel['track'] == 'sushiswap']
    print(f"\nSushiSwap track: {len(sushi_panel)} user×pool observations")
    print(f"  Events (deaths): {sushi_panel['event'].sum()}")
    print(f"  Censored: {(sushi_panel['event'] == 0).sum()}")
    
    print(f"\nTime-varying covariates coverage:")
    for col in ['gas_gwei', 'sushi_usd', 'uni_usd', 'eth_usd', 
                'uniswap_v2_tvl_usd', 'sushiswap_tvl_usd', 'pool_tvl_usd', 'swap_count',
                'impermanent_loss', 'apy_decay_proxy']:
        if col in panel.columns:
            valid = panel[col].notna().sum()
            pct = valid / len(panel) * 100
            print(f"  {col}: {valid}/{len(panel)} ({pct:.1f}%)")
    
    print("done")

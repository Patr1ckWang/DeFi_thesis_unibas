"""
 performs PSM diagnostics in Python and delegates matching
+ matched Cox PH estimation to R (run_psm_cox.R, via MatchIt + survival).

Matching: 1:1 Nearest Neighbor, caliper = 0.05 SD, with sensitivity at 0.2 SD.
Covariates: is_new, log_balance (initial tracked-platform balance at T0),
has_uni_mining — observable characteristics that may drive self-selection
into migration.

"""

import os
import sys
import subprocess
import pandas as pd
import numpy as np
from scipy import stats as scipy_stats

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(DIR)
RES_DIR_TBL = os.path.join(ROOT_DIR, 'results', 'tables')
RES_DIR_FIG = os.path.join(ROOT_DIR, 'results', 'figures')
os.makedirs(RES_DIR_TBL, exist_ok=True)
os.makedirs(RES_DIR_FIG, exist_ok=True)



# 1. Load Data
data_path = os.path.join(ROOT_DIR, 'cleaned_data', 'survival_panel.csv')
if not os.path.exists(data_path):
    print("ERROR: survival_panel.csv not found.")
    sys.exit(1)

panel = pd.read_csv(data_path)
panel['trajectory_id'] = panel['user_address'] + '_' + panel['pool_name']

print(f"\nFull panel: {len(panel):,} rows, {panel['trajectory_id'].nunique():,} trajectories")
print(f"  Users: {panel['user_address'].nunique():,}")

# 2. Construct Cross-Sectional Baseline
sushi_mig = panel[(panel['track'] == 'sushiswap') & (panel['faction'] == 'Migrator') & (panel['cohort'] != 'Returner')]
uni_stay  = panel[(panel['track'] == 'uniswap')   & (panel['faction'] == 'Stayer') & (panel['cohort'] != 'Returner')]
cross_panel = pd.concat([sushi_mig, uni_stay])

# Baseline: first observation per trajectory (start_time == 0)
cross_cs = cross_panel[cross_panel['start_time'] == 0].drop_duplicates('trajectory_id')

print(f"\nCross-sectional baseline: {len(cross_cs):,} trajectories")
print(f"  Migrator (treated): {(cross_cs['is_migrator']==1).sum():,}")
print(f"  Stayer (control):   {(cross_cs['is_migrator']==0).sum():,}")

# 3. Remove Dual-Role Users from Control
mig_users = set(cross_cs.loc[cross_cs['is_migrator'] == 1, 'user_address'])
dual_mask = (cross_cs['user_address'].isin(mig_users)) & (cross_cs['is_migrator'] == 0)
n_dual = dual_mask.sum()
print(f"\nDual-role users (Migrator in one pool, Stayer in another):")
print(f"  Stayer trajectories dropped from control: {n_dual}")

cross_cs_clean = cross_cs[~dual_mask].copy()
print(f"  After cleaning: Treated = {(cross_cs_clean['is_migrator']==1).sum()}, "
      f"Control = {(cross_cs_clean['is_migrator']==0).sum()}")

# 4. Pre-Matching Balance Diagnostics
def calc_smd(treat, control):
    diff = treat.mean() - control.mean()
    pooled_std = np.sqrt((treat.var() + control.var()) / 2)
    return diff / pooled_std if pooled_std > 1e-10 else 0.0

treat_pre = cross_cs_clean[cross_cs_clean['is_migrator'] == 1]
ctrl_pre  = cross_cs_clean[cross_cs_clean['is_migrator'] == 0]

print(f"\nPRE-MATCHING BALANCE")
print(f"{'Variable':<25} {'Treated':>10} {'Control':>10} {'SMD':>10}")

matching_vars = ['is_new', 'log_balance', 'has_uni_mining']
pre_smd_vals = {}
for var in matching_vars:
    smd = calc_smd(treat_pre[var], ctrl_pre[var])
    pre_smd_vals[var] = smd
    print(f"{var:<25} {treat_pre[var].mean():>10.4f} {ctrl_pre[var].mean():>10.4f} {smd:>10.4f}")

# 5. Save Data for R and Run Matching
panel.to_csv(os.path.join(ROOT_DIR, 'cleaned_data', 'survival_panel_for_psm.csv'), index=False)

r_script = os.path.join(DIR, 'run_psm_cox.R')
subprocess.run(['Rscript', r_script], check=True, cwd=ROOT_DIR)


# Read balance table
bal_csv_path = os.path.join(RES_DIR_TBL, 'Table_PSM_Balance.csv')
if os.path.exists(bal_csv_path):
    bal = pd.read_csv(bal_csv_path)
    print("\nBalance (SMD):")
    for _, row in bal.iterrows():
        print(f"  {row['Variable']:<25}: {row['SMD_Before']:>7.3f} -> {row['SMD_After']:>7.3f}")

# Read Cox results
cox_csv_path = os.path.join(RES_DIR_TBL, 'Table_PSM_Matched_Cox.csv')
if os.path.exists(cox_csv_path):
    cox = pd.read_csv(cox_csv_path)
    print("\nMatched Cox HR (is_migrator):")
    for _, row in cox[cox['Variable'] == 'is_migrator'].iterrows():
        print(f"  {row['Model']:<30}: HR={row['HR']:.3f}, SE={row['Robust_SE']:.3f}, p={row['P_Value']:.4f}")

# Compute selection share
hr_unmatched = 6.156  # from Table 2 M1
hr_matched_b = cox[(cox['Model'] == 'B_Matched_WithControls') & (cox['Variable'] == 'is_migrator')]
if len(hr_matched_b) > 0:
    hr_b = hr_matched_b.iloc[0]['HR']
    selection_share = (hr_unmatched - hr_b) / (hr_unmatched - 1.0) * 100
    print(f"\nObservable selection share: {selection_share:.0f}% of unmatched HR gap")
    print(f"  (HR: {hr_unmatched:.3f} unmatched -> {hr_b:.3f} matched, gap reduced by {hr_unmatched - hr_b:.3f})")


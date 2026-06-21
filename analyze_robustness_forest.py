"""
Generates forest plot and censoring sensitivity plot
Reads pre-computed results from run_forest_censor_sensitivity.R
(R -- regression; Python --visualization)
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import warnings

warnings.filterwarnings('ignore')

plt.rcParams['figure.dpi'] = 150
plt.rcParams['savefig.dpi'] = 300
plt.rcParams['font.size'] = 10

os.makedirs('results/figures', exist_ok=True)

# 1. POOL-LEVEL FOREST PLOT
pool_df = pd.read_csv('results/tables/Table_Pool_Heterogeneity.csv')
print(pool_df.to_string(index=False))

# Full-sample HR reference — read from canonical source so the
# reference line follows Table 2 automatically when the main Cox is re-run.
cox_main = pd.read_csv('results/tables/Table2_Cox_Main.csv')
ref_row = cox_main[(cox_main['Model'].str.contains('M1', na=False)) &
                   (cox_main['Term'] == 'is_migrator')]
full_sample_hr = float(ref_row['HR'].values[0])
hr_source = 'Table 2, M1'

pools_sorted = pool_df.sort_values('is_migrator_HR')

fig, ax = plt.subplots(figsize=(11, 7))
y_pos = range(len(pools_sorted))

ax.axvline(x=full_sample_hr, color='#009E73', linestyle='--', linewidth=1.2,
           alpha=0.7, zorder=1)
ax.text(full_sample_hr + 0.15, len(pools_sorted) - 1.5,
        f'Full-sample HR = {full_sample_hr:.2f}\n({hr_source})',
        va='top', fontsize=8, color='#009E73', fontstyle='italic')

for i, (_, row) in enumerate(pools_sorted.iterrows()):
    hr = row['is_migrator_HR']
    ci_lo = row['HR_CI_lower']
    ci_hi = row['HR_CI_upper']

    # Color: Wong palette, colorblind-safe — blue if significant, gray if not
    color = '#0072B2' if row['is_migrator_p'] < 0.05 else '#999999'

    # Confidence interval line
    ax.plot([ci_lo, ci_hi], [i, i], color=color, linewidth=2.5, alpha=0.85,
            zorder=4)
    # Point estimate
    ax.plot(hr, i, 's', color=color, markersize=8, zorder=5)
    # Label with HR, N, and events count
    ax.text(ci_hi + 0.3, i,
            f"HR = {hr:.2f}  [{row['N']:,} obs, {row['Events']} events]",
            va='center', fontsize=8)

# Reference line at HR = 1 (no effect)
ax.axvline(x=1.0, color='black', linestyle='--', linewidth=0.8, alpha=0.6,
           zorder=2)
ax.text(1.0, -0.65, 'HR = 1', ha='center', fontsize=8, alpha=0.6)

ax.set_yticks(list(y_pos))
ax.set_yticklabels(pools_sorted['Pool'], fontsize=11)
ax.set_xlabel('Hazard Ratio of is_migrator\n(> 1 = Migrators exit faster)', fontsize=11)

ref_note = '  Dashed green line: full-sample HR (Table 2, M1).'
ax.text(0.98, 0.12,
        'Estimates from per-pool Cox PH with cluster-robust SE at user_address.\n'
        f'Controls: is_new, log_balance.{ref_note}',
        transform=ax.transAxes, fontsize=7, ha='right', va='bottom',
        bbox=dict(boxstyle='round', facecolor='white', alpha=0.9))

plt.tight_layout()
plt.savefig('results/figures/Fig_Pool_Heterogeneity.png', dpi=300, bbox_inches='tight')
plt.savefig('results/figures/Fig_Pool_Heterogeneity.pdf', bbox_inches='tight')
plt.close()

# 2. CENSORING SENSITIVITY PLOT
censor_df = pd.read_csv('results/tables/Table_Censoring_Sensitivity.csv')
print(censor_df.to_string(index=False))

fig, ax = plt.subplots(figsize=(8, 5))
ax.plot(censor_df['Truncation_Day'], censor_df['is_migrator_HR'],
        marker='o', linestyle='-', color='#D55E00', linewidth=2,
        markersize=8, markerfacecolor='white', markeredgewidth=2)

# Add 95% CI shading
ax.fill_between(censor_df['Truncation_Day'],
                censor_df['HR_CI_lower'],
                censor_df['HR_CI_upper'],
                alpha=0.15, color='#D55E00')

# Reference line at HR = 1
ax.axhline(y=1.0, color='black', linestyle='--', linewidth=0.8, alpha=0.4)


for _, row in censor_df.iterrows():
    ax.annotate(f"{row['is_migrator_HR']:.2f}\n[{row['HR_CI_lower']:.2f}–{row['HR_CI_upper']:.2f}]",
                (row['Truncation_Day'], row['is_migrator_HR']),
                textcoords="offset points", xytext=(0, 14),
                fontsize=7.5, ha='center')

ax.set_xlabel('Artificial Right-Censoring Threshold (Days)', fontsize=11)
ax.set_ylabel('Hazard Ratio of is_migrator', fontsize=11)

# Build annotation dynamically from the data so it stays in sync
hr_30d = censor_df.loc[censor_df['Truncation_Day'] == 30, 'is_migrator_HR']
hr_30d_str = f"{hr_30d.values[0]:.2f}" if len(hr_30d) > 0 else "—"
# Stability band: max − min of HR from 90 days onward
hr_late = censor_df.loc[censor_df['Truncation_Day'] >= 90, 'is_migrator_HR']
band_str = f"{hr_late.max() - hr_late.min():.2f}" if len(hr_late) > 1 else "—"

ax.text(0.98, 0.97,
        f'HR is highest at 30-day truncation ({hr_30d_str}), reflecting the\n'
        f'concentration of mercenary exits in the first month after\n'
        f'migration. HR stabilises within ±{band_str} after 90 days.',
        transform=ax.transAxes, fontsize=8, ha='right', va='top',
        bbox=dict(boxstyle='round', facecolor='white', alpha=0.9))

plt.tight_layout()
plt.savefig('results/figures/Fig_Censoring_Sensitivity.png', dpi=300, bbox_inches='tight')
plt.savefig('results/figures/Fig_Censoring_Sensitivity.pdf', bbox_inches='tight')
plt.close()

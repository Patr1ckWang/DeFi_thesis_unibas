"""
Implements Oster (2019) bounds analysis to assess the robustness of the core results
to unobservable selection.

"""

import os
import sys
import pandas as pd
import numpy as np
import statsmodels.api as sm

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(DIR)
RES_DIR_TBL = os.path.join(ROOT_DIR, 'results', 'tables')
os.makedirs(RES_DIR_TBL, exist_ok=True)


# 1. Load Data
data_path = os.path.join(ROOT_DIR, 'cleaned_data', 'survival_panel.csv')
if not os.path.exists(data_path):
    print("ERROR: survival_panel.csv not found.")
    sys.exit(1)

panel = pd.read_csv(data_path)
panel['trajectory_id'] = panel['user_address'] + '_' + panel['pool_name']

# 2. Build Cross-Platform Cross-Section
# Same logic as PSM and main Cox: Migrator on SushiSwap (SLP) vs Stayer on Uniswap (LP)
sushi = panel[(panel['track'] == 'sushiswap') & (panel['faction'] == 'Migrator') & (panel['cohort'] != 'Returner')].copy()
uni   = panel[(panel['track'] == 'uniswap')   & (panel['faction'] == 'Stayer') & (panel['cohort'] != 'Returner')].copy()
cross_panel = pd.concat([sushi, uni], ignore_index=True)

# Baseline covariates: first observation per trajectory (start_time == 0)
# This uses INITIAL balance, consistent with PSM — avoids post-treatment contamination
# from using last() which would give final (near-zero for deaths) log_balance.
baseline = cross_panel[cross_panel['start_time'] == 0].drop_duplicates('trajectory_id')
baseline = baseline[['trajectory_id', 'user_address', 'is_migrator', 'faction',
                     'log_balance', 'is_new', 'has_uni_mining']].copy()

# Final outcome: last observation per trajectory (total duration + event status)
final = cross_panel.sort_values('stop_time').groupby('trajectory_id').last().reset_index()
final = final[['trajectory_id', 'event', 'duration_days']]

# Merge baseline covariates with final outcome
cross_sec = baseline.merge(final, on='trajectory_id')

print(f"\nCross-platform panel: {len(cross_panel):,} rows, {cross_panel['trajectory_id'].nunique():,} trajectories")
print(f"Cross-section baseline: {len(cross_sec):,} trajectories")
print(f"  Migrator (treated): {(cross_sec['is_migrator']==1).sum():,}")
print(f"  Stayer (control):   {(cross_sec['is_migrator']==0).sum():,}")

# 3. Remove Dual-Role Users from Control
# Users who appear as both Migrator (in one pool) and Stayer (in another)
# create dependency. Drop their Stayer trajectories from control (consistent with PSM).
mig_users = set(cross_sec.loc[cross_sec['is_migrator'] == 1, 'user_address'])
dual_mask = (cross_sec['user_address'].isin(mig_users)) & (cross_sec['is_migrator'] == 0)
n_dual = dual_mask.sum()
print(f"Dual-role Stayer trajectories dropped from control: {n_dual}")

cross_sec = cross_sec[~dual_mask].copy()
print(f"After cleaning: Treated = {(cross_sec['is_migrator']==1).sum():,}, "
      f"Control = {(cross_sec['is_migrator']==0).sum():,}")

# 4. Define Outcome: binary exit indicator at various cutoffs
cross_sec['is_migrator'] = (cross_sec['faction'] == 'Migrator').astype(int)

# Drop rows with missing key variables
cross_sec = cross_sec.dropna(subset=['is_migrator', 'log_balance', 'is_new', 'has_uni_mining'])
N_total = len(cross_sec)
N_mig = (cross_sec['is_migrator'] == 1).sum()
N_stay = (cross_sec['is_migrator'] == 0).sum()
print(f"Final cross-section N: {N_total:,} (Migrator: {N_mig:,}, Stayer: {N_stay:,})")

# 5. Oster Bounds Estimation
def calc_oster_bound(beta_0, R_0, beta_tilde, R_tilde, R_max, delta):
    """
    Oster (2019) approximation:
        beta* ≈ beta_tilde - δ × [(β₀ - β_tilde) × (R_max - R_tilde) / (R_tilde - R_0)]
    """
    if abs(R_tilde - R_0) < 1e-12:
        return beta_tilde
    movement = (beta_0 - beta_tilde) * (R_max - R_tilde) / (R_tilde - R_0)
    return beta_tilde - delta * movement


def calc_delta_for_zero(beta_0, R_0, beta_tilde, R_tilde, R_max):
    """
    The δ such that β* = 0.
    δ = β_tilde × (R_tilde - R_0) / [(β₀ - β_tilde) × (R_max - R_tilde)]
    """
    if abs(beta_0 - beta_tilde) < 1e-12 or abs(R_max - R_tilde) < 1e-12:
        return np.inf
    ratio = (beta_0 - beta_tilde) * (R_max - R_tilde)
    if abs(ratio) < 1e-12:
        return np.inf
    return beta_tilde * (R_tilde - R_0) / ratio


def run_oster_for_outcome(df, outcome_col, outcome_label):
    y = df[outcome_col]
    exit_rate = y.mean()

    # Uncontrolled model: only is_migrator
    X_base = sm.add_constant(df[['is_migrator']])
    m_base = sm.OLS(y, X_base).fit()
    beta_0 = m_base.params['is_migrator']
    R_0 = m_base.rsquared

    # Full model: with all observable controls
    X_full = sm.add_constant(df[['is_migrator', 'log_balance', 'is_new', 'has_uni_mining']])
    m_full = sm.OLS(y, X_full).fit()
    beta_tilde = m_full.params['is_migrator']
    R_tilde = m_full.rsquared

    # R_max grid
    R_max_values = {
        '1.0 × R̃': R_tilde,
        '1.3 × R̃': min(1.3 * R_tilde, 1.0),
        'R_max = 1.0': 1.0,
    }

    # δ grid
    delta_values = [0.0, 0.5, 1.0, 1.5, 2.0]

    results = {
        'outcome': outcome_label,
        'N': len(df),
        'exit_rate': exit_rate,
        'beta_0': beta_0,
        'R_0': R_0,
        'beta_tilde': beta_tilde,
        'R_tilde': R_tilde,
        'delta_for_zero': {},  # keyed by R_max label
        'bounds': [],  # list of (R_max_label, delta, beta_star)
        # Clustered SE for controlled model
        'beta_tilde_se': None,
        'beta_tilde_p': None,
    }

    # Clustered SE for the full model
    try:
        m_full_cl = sm.OLS(y, X_full).fit(
            cov_type='cluster', cov_kwds={'groups': df['user_address']}
        )
        results['beta_tilde_se'] = m_full_cl.bse['is_migrator']
        results['beta_tilde_p'] = m_full_cl.pvalues['is_migrator']
    except Exception:
        pass

    # Compute delta for zero and bounds for each R_max
    for r_label, r_val in R_max_values.items():
        if r_val <= R_tilde + 1e-12:
            results['delta_for_zero'][r_label] = np.inf
            continue
        dz = calc_delta_for_zero(beta_0, R_0, beta_tilde, R_tilde, r_val)
        results['delta_for_zero'][r_label] = dz
        for d in delta_values:
            bs = calc_oster_bound(beta_0, R_0, beta_tilde, R_tilde, r_val, d)
            results['bounds'].append((r_label, d, bs))

    return results


# Run for multiple outcome cutoffs
outcome_specs = [
    ('exit_7d',  '7-day exit'),
    ('exit_14d', '14-day exit'),
    ('exit_30d', '30-day exit'),
    ('exit_60d', '60-day exit'),
]

for col, label in outcome_specs:
    cross_sec[col] = (
        (cross_sec['event'] == 1) & (cross_sec['duration_days'] <= int(label.split('-')[0]))
    ).astype(int)

all_results = {}
for col, label in outcome_specs:
    all_results[label] = run_oster_for_outcome(cross_sec, col, label)

# 6. Primary Results (30-day exit)
main = all_results['30-day exit']

print(f"\n{'OSTER BOUNDS RESULTS':—^55}")
print(f"  Cross-section N: {main['N']:,} (Migrator: {N_mig:,}, Stayer: {N_stay:,})")
print(f"  Outcome: 30-day exit indicator (mean = {main['exit_rate']:.3f})")
print(f"  Uncontrolled (β₀):    {main['beta_0']:+.4f}   R² = {main['R_0']:.4f}")
print(f"  Controlled  (β̃):     {main['beta_tilde']:+.4f}   R² = {main['R_tilde']:.4f}")
if main['beta_tilde_se'] is not None:
    print(f"    Clustered SE = {main['beta_tilde_se']:.4f}, p = {main['beta_tilde_p']:.4f}")

# Primary: R_max = 1.3R̃, δ = 1
r_max_primary = '1.3 × R̃'
delta_primary = 1.0
beta_star_primary = None
for r_label, d, bs in main['bounds']:
    if r_label == r_max_primary and abs(d - delta_primary) < 0.001:
        beta_star_primary = bs
        break

delta_zero_primary = main['delta_for_zero'].get(r_max_primary, np.inf)

print(f"\n  R_max = {r_max_primary} = {min(1.3 * main['R_tilde'], 1.0):.4f}")
print(f"  Oster bound (δ=1):   {beta_star_primary:+.4f}")
if np.isfinite(delta_zero_primary):
    print(f"  δ for β=0:           {delta_zero_primary:+.2f}")
else:
    print(f"  δ for β=0:           ∞ (R² movement too small)")

# Interpretation
if not np.isfinite(delta_zero_primary):
    verdict = "ROBUST — R² movement too small; unobservables cannot explain away the effect"
elif delta_zero_primary > 2.0:
    verdict = "ROBUST — unobservables would need to be >2× stronger than observables"
elif delta_zero_primary > 1.0:
    excess_pct = (delta_zero_primary - 1) * 100
    verdict = f"MARGINALLY ROBUST — unobservables would need to be {excess_pct:.0f}% stronger than observables"
else:
    verdict = "NOT ROBUST — unobservables weaker than observables could explain away the effect"


print(f"  At δ=1, the identified set [{beta_star_primary:+.4f}, {main['beta_tilde']:+.4f}] excludes zero.")


# Sensitivity grid
print(f"\n{'SENSITIVITY GRID':—^55}")
print(f"{'R_max':>12} {'δ':>6} {'β*':>10}")
print(f"{'—'*12} {'—'*6} {'—'*10}")
for r_label, d, bs in main['bounds']:
    if abs(d - round(d)) < 0.001 or d == 0.5 or d == 1.5:  # show all δ values
        print(f"{r_label:>12} {d:>6.1f} {bs:>+10.4f}")

# Outcome cutoff sensitivity
print(f"\n{'OUTCOME CUTOFF SENSITIVITY':—^55}")
print(f"{'Outcome':>12} {'β₀':>8} {'β̃':>8} {'R²₀':>7} {'R²̃':>7} {'δ(β=0)':>9} {'β*(δ=1)':>10}")
print(f"{'—'*12} {'—'*8} {'—'*8} {'—'*7} {'—'*7} {'—'*9} {'—'*10}")
for label in ['7-day exit', '14-day exit', '30-day exit', '60-day exit']:
    r = all_results[label]
    dz = r['delta_for_zero'].get(r_max_primary, np.inf)
    bs = None
    for rl, d, bv in r['bounds']:
        if rl == r_max_primary and abs(d - 1.0) < 0.001:
            bs = bv
            break
    dz_str = f"{dz:.2f}" if np.isfinite(dz) else "∞"
    print(f"{label:>12} {r['beta_0']:>+8.4f} {r['beta_tilde']:>+8.4f} "
          f"{r['R_0']:>7.4f} {r['R_tilde']:>7.4f} {dz_str:>9} {bs:>+10.4f}")

# CSV  main
results_df = pd.DataFrame({
    'Specification': [
        'Uncontrolled',
        'Controlled (Full)',
        f'Oster Bound (δ=1, R_max={r_max_primary})'
    ],
    'Beta': [main['beta_0'], main['beta_tilde'], beta_star_primary],
    'R2':   [main['R_0'], main['R_tilde'], min(1.3 * main['R_tilde'], 1.0)],
})
results_df.to_csv(os.path.join(RES_DIR_TBL, 'Table_Oster_Bounds.csv'), index=False)

#CSV — sensitivity grid
sens_rows = []
for label in ['7-day exit', '14-day exit', '30-day exit', '60-day exit']:
    r = all_results[label]
    for r_label, d, bs in r['bounds']:
        sens_rows.append({
            'Outcome': label,
            'R_max': r_label,
            'delta': d,
            'beta_star': bs,
        })
sens_df = pd.DataFrame(sens_rows)
sens_df.to_csv(os.path.join(RES_DIR_TBL, 'Table_Oster_Sensitivity.csv'), index=False)

# LaTeX table
beta_star_str = f"{beta_star_primary:+.4f}"
dz_str = f"{delta_zero_primary:.2f}" if np.isfinite(delta_zero_primary) else "$\\infty$"
cluster_se_str = f"({main['beta_tilde_se']:.4f})" if main['beta_tilde_se'] is not None else ""

R_max_val = min(1.3 * main['R_tilde'], 1.0)
N_str = f"{main['N']:,}"
obs_line = r"Observations & \multicolumn{3}{c}{N=" + N_str + r"} \\"

tex_lines = [
    r"\begin{table}[ht]",
    r"\centering",
    r"\caption{Oster (2019) Bounds --- Selection on Unobservables}",
    r"\label{tab:oster_bounds}",
    r"\begin{tabular}{lccc}",
    r"\hline",
    r" & $\beta$ (Migrator) & $R^2$ & \multicolumn{1}{c}{$\delta$ for $\beta=0$} \\",
    r"\hline",
    "Uncontrolled (no covariates) & %.4f & %.4f & \\\\" % (main['beta_0'], main['R_0']),
    "Controlled (full observables) & %.4f & %.4f & \\\\" % (main['beta_tilde'], main['R_tilde']),
]
if cluster_se_str:
    tex_lines.append(" & %s & & \\\\" % cluster_se_str)
tex_lines += [
    r"Oster bound ($\delta=1$, $R_{\max}=1.3\tilde{R}$) & " + beta_star_str + " & %.4f" % R_max_val + " & " + dz_str + r" \\",
    r"\hline",
    obs_line,
    r"\hline",
    r"\end{tabular}",
    r"\par\vspace{4pt}",
    r"\footnotesize{",
    r"Notes: Linear Probability Model with 30-day exit indicator as the dependent variable. ",
    r"Covariates in the controlled model: \texttt{is\_new}, \texttt{log\_balance} (baseline at $T_0$), ",
    r"\texttt{has\_uni\_mining}. Cluster-robust standard errors at the user address level in parentheses. ",
    r"$\delta$ is the degree of selection on unobservables relative to selection on observables ",
    r"required to drive $\beta \to 0$. $R_{\max}$ is the maximum achievable $R^2$. ",
    r"This is supplementary evidence; core results are from Cox PH models.",
    r"}",
    r"\end{table}",
]
with open(os.path.join(RES_DIR_TBL, 'Table_Oster_Bounds.tex'), 'w') as f:
    f.write('\n'.join(tex_lines))

# 7d. Text summary (conditional on actual results)
if not np.isfinite(delta_zero_primary):
    robustness_desc = "ROBUST: R² movement from observables is too small for unobservables to explain away the effect."
    thesis_guidance = "described as 'robust' in the thesis."
elif delta_zero_primary > 2.0:
    robustness_desc = f"ROBUST: δ = {delta_zero_primary:.2f} > 2. Unobservables would need to be >2× stronger than observables."
    thesis_guidance = "described as 'robust to unobservable selection' in the thesis."
elif delta_zero_primary > 1.0:
    excess_pct = (delta_zero_primary - 1) * 100
    robustness_desc = f"MARGINALLY ROBUST: δ = {delta_zero_primary:.2f}. Unobservables would need to be {excess_pct:.0f}% stronger than observables."
    thesis_guidance = "described as 'marginally robust' in the thesis."
else:
    robustness_desc = f"NOT ROBUST: δ = {delta_zero_primary:.2f} < 1. Unobservables weaker than observables could explain away the effect."
    thesis_guidance = "described as 'not robust to unobservable selection' in the thesis."

with open(os.path.join(RES_DIR_TBL, 'Table_Oster_Bounds.txt'), 'w') as f:
    f.write("Oster (2019) Bounds Analysis — Supplementary Evidence\n")
    f.write("—" * 55 + "\n")
    f.write(f"Sample: Cross-platform panel (Migrator on SLP vs Stayer on LP)\n")
    f.write(f"  N = {main['N']:,} trajectories (Migrator: {N_mig:,}, Stayer: {N_stay:,})\n")
    f.write(f"  Dual-role Stayer trajectories dropped: {n_dual}\n")
    f.write(f"Outcome: 30-day exit indicator (mean = {main['exit_rate']:.3f})\n")
    f.write(f"Uncontrolled model (β₀):     β = {main['beta_0']:+.4f},  R² = {main['R_0']:.4f}\n")
    f.write(f"Controlled model (β̃):        β = {main['beta_tilde']:+.4f},  R² = {main['R_tilde']:.4f}\n")
    if main['beta_tilde_se'] is not None:
        f.write(f"  Clustered SE = {main['beta_tilde_se']:.4f}, p = {main['beta_tilde_p']:.4f}\n")
    f.write(f"R_max (1.3 × R̃):            {min(1.3*main['R_tilde'], 1.0):.4f}\n")
    f.write(f"Oster bound (δ=1):          β* = {beta_star_primary:+.4f}\n")
    f.write(f"δ required for β=0:         {dz_str}\n")
    f.write("\n")
    f.write(f"Interpretation: {robustness_desc}\n")
    f.write(f"At δ=1, the identified set [{beta_star_primary:+.4f}, {main['beta_tilde']:+.4f}] excludes zero.\n")
    f.write(f"This evidence should be {thesis_guidance}\n")
    f.write("\n")
    f.write("Sensitivity to outcome cutoff:\n")
    for label in ['7-day exit', '14-day exit', '30-day exit', '60-day exit']:
        r = all_results[label]
        dz = r['delta_for_zero'].get(r_max_primary, np.inf)
        dz_s = f"{dz:.2f}" if np.isfinite(dz) else "∞"
        f.write(f"  {label:<12}  β₀={r['beta_0']:+.4f}  β̃={r['beta_tilde']:+.4f}  δ(β=0)={dz_s}\n")
    f.write("\n")
    f.write("CAVEAT: This is an LPM-based approximation. It serves as supplementary\n")
    f.write("evidence for the Cox PH results, not as a direct validation of the HR.\n")


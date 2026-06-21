"""
analyze_multinomial_logit.py
Multinomial Logit — Migrator Endpoint Destinations

Outcome classification (0–4):
  0: Stay in Sushi (censored, survives to end of observation window)
  1: Revert to Uni — Genuine (LP re-deposit on Uniswap BEFORE 16 Sep 2020)
  2: Revert to Uni — Incentivized (LP re-deposit on Uniswap ON/AFTER 16 Sep 2020)
  3: Exit to Burn (withdrew capital, never returned to Uniswap)
  4: Exit to Yield Aggregator (Harvest / Pickle)
"""

import pandas as pd
import numpy as np
import statsmodels.api as sm
from scipy import stats as scipy_stats
import os, sys, warnings
from datetime import datetime

warnings.filterwarnings('ignore')


DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(DIR)
RES_DIR = os.path.join(ROOT_DIR, 'results', 'tables')
os.makedirs(RES_DIR, exist_ok=True)

UNI_LAUNCH_DATE = pd.to_datetime('2020-09-16', utc=True)
DUST = 1e-6

# 1. Load Data

# Core user attributes
users = pd.read_csv(os.path.join(ROOT_DIR, 'cleaned_data', 'users_enhanced.csv'))

# Death details (cleaned by pipeline)
death_df = pd.read_csv(os.path.join(ROOT_DIR, 'cleaned_data', 'death_details.csv'))
death_df['death_time'] = pd.to_datetime(death_df['death_time'])

# Raw events for return detection (only file with counterparty_type)
events = pd.read_csv(os.path.join(ROOT_DIR, 'raw_data', 'survival_v5.csv'))
events['event_time'] = pd.to_datetime(events['event_time'])

# Survival panel for has_uni_mining and validation
panel = pd.read_csv(os.path.join(ROOT_DIR, 'cleaned_data', 'survival_panel.csv'))

# 2. Build Core Migrator Sample (exclude Returners)

# Filter Migrators, exclude Returners
migrators = users[(users['faction'] == 'Migrator') & (users['cohort'] != 'Returner')].copy()

# Merge has_uni_mining from panel (one value per pool)
pool_uni = panel[['pool_name', 'has_uni_mining']].drop_duplicates()
migrators = migrators.merge(pool_uni, on='pool_name', how='left')

# Validate essential columns
required_cols = ['user_address', 'pool_name', 'log_balance', 'cohort', 'has_uni_mining']
for col in required_cols:
    if col not in migrators.columns:
        print(f"ERROR: Required column '{col}' missing from Migrator data.")
        sys.exit(1)

# Drop rows with missing log_balance 
n_before = len(migrators)
migrators = migrators[migrators['log_balance'].notna()].copy()
if len(migrators) < n_before:
    print(f"  Dropped {n_before - len(migrators)} rows with missing log_balance")

# Validate N matches Table 1
n_mig = len(migrators)
print(f"  Migrators (excl. Returners): {n_mig}")
if n_mig != 2604:
    print(f"  WARNING: Expected 2,604 Migrators per Table 1, got {n_mig}")
    print(f"  Proceeding with N={n_mig} — check users_enhanced.csv for discrepancies")
else:
    print(f"  Matches Table 1 (N=2,604)")

# 3. Classify Outcomes

outcomes = []
n_dual_track = 0  # Migrators who appear on BOTH SLP and LP

for idx, row in migrators.iterrows():
    user = row['user_address']
    pool = row['pool_name']

    sushi_death = death_df[(death_df['user_address'] == user) &
                           (death_df['pool_name'] == pool) &
                           (death_df['platform'] == 'sushiswap')]

    if sushi_death.empty:
        # No Sushi death record → treat as Stay (censored)
        outcomes.append({'user_address': user, 'pool_name': pool,
                         'outcome': 0, 'outcome_label': 'Stay in Sushi',
                         'death_time': None, 'death_type': None})
        continue

    s_row = sushi_death.iloc[0]

    if s_row['censored']:
        # Censored on Sushi → Stay
        outcomes.append({'user_address': user, 'pool_name': pool,
                         'outcome': 0, 'outcome_label': 'Stay in Sushi',
                         'death_time': s_row['death_time'], 'death_type': s_row['death_type']})
        continue

    death_time = s_row['death_time']
    death_type = s_row['death_type']

    # Check for LP re-deposit on Uniswap (same pool) AFTER Sushi death
    uni_returns = events[(events['user_address'] == user) &
                         (events['pool_name'] == pool) &
                         (events['platform'] == 'uniswap') &
                         (events['event_time'] >= death_time) &
                         (events['amount'] > DUST) &
                         (events['counterparty_type'].isin(['mint', 'uni_mining']))]

    if len(uni_returns) > 0:
        first_return_time = uni_returns['event_time'].min()
        if first_return_time < UNI_LAUNCH_DATE:
            outcome = 1
            label = 'Revert Genuine'
        else:
            outcome = 2
            label = 'Revert Incentivized'
    else:
        # No LP return to Uniswap detected
        if death_type in ['exit_to_harvest', 'exit_to_pickle']:
            outcome = 4
            label = 'Yield Aggregator'
        elif death_type in ['exit_burn', 'exit_transfer']:
            outcome = 3
            label = 'Exit to Burn'
        else:
            # Unknown death type → default to Burn
            outcome = 3
            label = 'Exit to Burn'

    # Count dual-track users (appear on BOTH platforms)
    user_tracks = panel[(panel['user_address'] == user) &
                        (panel['pool_name'] == pool)]['track'].unique()
    if len(user_tracks) > 1:
        n_dual_track += 1

    outcomes.append({'user_address': user, 'pool_name': pool,
                     'outcome': outcome, 'outcome_label': label,
                     'death_time': death_time, 'death_type': death_type})

outcomes_df = pd.DataFrame(outcomes)
migrators = migrators.merge(outcomes_df, on=['user_address', 'pool_name'], how='left')

# Safeguard: fill any merge misses
if migrators['outcome'].isna().any():
    n_miss = migrators['outcome'].isna().sum()
    print(f"  WARNING: {n_miss} rows with missing outcome after merge — defaulting to Stay")
    migrators['outcome'] = migrators['outcome'].fillna(0)
    migrators['outcome_label'] = migrators['outcome_label'].fillna('Stay in Sushi')

# 4. Outcome Distribution Report

print(f"  Dual-track Migrators (appear on BOTH SLP and LP): {n_dual_track} "
      f"({n_dual_track / n_mig * 100:.1f}%)")

outcome_labels = {
    0: 'Stay in Sushi',
    1: 'Revert Genuine (<16 Sep)',
    2: 'Revert Incentivized (≥16 Sep)',
    3: 'Exit to Burn',
    4: 'Yield Aggregator',
}

dist = migrators['outcome'].value_counts().sort_index()
print(f"\n  {'Outcome':<35} {'N':>6} {'%':>8}")
for k in sorted(outcome_labels.keys()):
    n = dist.get(k, 0)
    pct = n / n_mig * 100
    print(f"  {outcome_labels[k]:<35} {n:>6} {pct:>7.1f}%")
print(f"  {'Total':<35} {n_mig:>6} {100.0:>7.1f}%")

# Breakdown by cohort
print(f"\n  Outcome × Cohort breakdown:")
for k in sorted(outcome_labels.keys()):
    subset = migrators[migrators['outcome'] == k]
    n_new = (subset['cohort'] == 'New').sum()
    n_old = (subset['cohort'] == 'Old').sum()
    print(f"  {outcome_labels[k]:<35} New={n_new:>5}  Old={n_old:>4}")

# Breakdown by UNI mining pool
print(f"\n  Outcome × UNI Mining breakdown:")
for k in sorted(outcome_labels.keys()):
    subset = migrators[migrators['outcome'] == k]
    n_uni = (subset['has_uni_mining'] == 1).sum()
    n_no = (subset['has_uni_mining'] == 0).sum()
    print(f"  {outcome_labels[k]:<35} UNI={n_uni:>5}  NoUNI={n_no:>4}")

# 5. Variables for MNLogit

migrators['is_new'] = (migrators['cohort'] == 'New').astype(int)

# Predictors
X_vars = ['log_balance', 'is_new', 'has_uni_mining']
X = migrators[X_vars].copy()
X = sm.add_constant(X)
y = migrators['outcome'].values

# Verify no missing values
if X.isna().any().any():
    print("  ERROR: Missing values in predictors")
    print(X.isna().sum())
    sys.exit(1)

print(f"  Predictors: const + {X_vars}")
print(f"  Observations: {len(y)}")
print(f"  Outcome categories: {sorted(np.unique(y).astype(int))}")

# 6. Estimate Multinomial Logit
groups = migrators['user_address'].astype('category').cat.codes

try:
    mnl = sm.MNLogit(y, X).fit(maxiter=200, cov_type='cluster',
                                cov_kwds={'groups': groups}, disp=False)
    cov_type_used = 'cluster (user_address)'
except Exception as e:
    print(f"  Cluster SE failed ({str(e)[:80]}...), falling back to HC1")
    mnl = sm.MNLogit(y, X).fit(maxiter=200, cov_type='HC1', disp=False)
    cov_type_used = 'HC1 (heteroskedasticity-robust)'

print(f"  Converged: {mnl.mle_retvals['converged']}")
print(f"  Covariance: {cov_type_used}")
print(f"  Pseudo R²: {mnl.prsquared:.4f}")
print(f"  Log-Likelihood: {mnl.llf:.2f}")
print(f"  LL-Null: {mnl.llnull:.2f}")
print(f"  LLR p-value: {mnl.llr_pvalue:.4e}")
print(f"  N obs: {mnl.nobs}")

# 7. Coefficient Summary Table
coef_labels = {
    'const': 'Constant',
    'log_balance': 'Log LP/SLP Balance',
    'is_new': 'New User',
    'has_uni_mining': 'Pool UNI Mining',
}

# Build mapping: params column index → actual outcome number
# MNLogit drops the reference (smallest) outcome; remaining sorted unique outcomes
model_outcomes = sorted([o for o in np.unique(y) if o != np.min(y)])
print(f"  Model equations map to outcomes: {list(enumerate(model_outcomes))}")

# Print formatted coefficient table
for eq_col, actual_outcome in enumerate(model_outcomes):
    print(f"\n  --- {outcome_labels[actual_outcome]} (outcome={actual_outcome}) ---")
    print(f"  {'Variable':<25} {'Coef':>8} {'SE':>8} {'z':>8} {'p':>8}")
    for var_name in X.columns:
        coef = mnl.params.loc[var_name, eq_col]
        se = mnl.bse.loc[var_name, eq_col]
        z = coef / se if se and se > 0 else np.nan
        p = 2 * (1 - scipy_stats.norm.cdf(abs(z))) if not np.isnan(z) else np.nan
        label = coef_labels.get(var_name, var_name)
        print(f"  {label:<25} {coef:>8.4f} {se:>8.4f} {z:>8.3f} {p:>8.4f}")

# 8. Marginal Effects (AME)

# Predict probabilities at observed X
probs = mnl.predict(X)
# For each variable (except const), compute discrete change or derivative
ame_rows = []
for j, var_name in enumerate(X.columns):
    if var_name == 'const':
        continue

    # Compute AME via numerical derivative for each observation
    h = 1e-4 if var_name != 'is_new' else 1  # discrete change for binary, small h for continuous
    is_binary = (var_name in ['is_new', 'has_uni_mining'])

    X_plus = X.copy()
    if is_binary:
        X_plus[var_name] = 1 - X_plus[var_name]  # Flip binary
    else:
        X_plus[var_name] = X_plus[var_name] + h

    probs_plus = mnl.predict(X_plus)

    if is_binary:
        # Discrete change: flip 0→1
        # Compute for rows where original = 0
        mask0 = (X[var_name] == 0)
        if mask0.sum() > 0:
            me = (probs_plus.loc[mask0] - probs.loc[mask0]).mean()
        else:
            me = pd.Series(0, index=probs.columns)
    else:
        # Numerical derivative
        me = (probs_plus - probs) / h
        me = me.mean()

    # Format output — probs columns are integers (0, 1, 2, 3)
    for k in sorted(outcome_labels.keys()):
        if k in me.index:
            effect = me[k]
            ame_rows.append({
                'variable': coef_labels.get(var_name, var_name),
                'outcome': k,
                'outcome_label': outcome_labels[k],
                'AME': effect,
            })

ame_df = pd.DataFrame(ame_rows)
print(f"\n  {'Variable':<25} {'Outcome':<35} {'AME':>10}")
for _, r in ame_df.iterrows():
    stars = '***' if abs(r['AME']) / max(abs(ame_df['AME'])) > 0.5 else ''
    print(f"  {r['variable']:<25} {r['outcome_label']:<35} {r['AME']:>10.4f}")

# 9. Predicted vs Actual (Classification Accuracy)
pred_probs = mnl.predict(X)
pred_outcome = np.argmax(pred_probs.values, axis=1)
accuracy = (pred_outcome == y).mean()
print(f"  Overall accuracy: {accuracy:.3f} ({accuracy*100:.1f}%)")

# Per-outcome precision/recall
print(f"\n  {'Outcome':<35} {'Precision':>10} {'Recall':>10} {'N':>6}")
for k in sorted(outcome_labels.keys()):
    actual_k = (y == k)
    pred_k = (pred_outcome == k)
    precision = (actual_k & pred_k).sum() / max(pred_k.sum(), 1)
    recall = (actual_k & pred_k).sum() / max(actual_k.sum(), 1)
    print(f"  {outcome_labels[k]:<35} {precision:>10.3f} {recall:>10.3f} {actual_k.sum():>6.0f}")

# 10. CSV 
csv_rows = []
for eq_col, actual_outcome in enumerate(model_outcomes):
    for var_name in X.columns:
        coef = mnl.params.loc[var_name, eq_col]
        se = mnl.bse.loc[var_name, eq_col]
        ci_lower = coef - 1.96 * se if not np.isnan(coef) and not np.isnan(se) else np.nan
        ci_upper = coef + 1.96 * se if not np.isnan(coef) and not np.isnan(se) else np.nan
        z = coef / se if se and not np.isnan(se) and se > 0 else np.nan
        p = 2 * (1 - scipy_stats.norm.cdf(abs(z))) if not np.isnan(z) else np.nan

        csv_rows.append({
            'outcome': actual_outcome,
            'outcome_label': outcome_labels.get(actual_outcome, f'Outcome {actual_outcome}'),
            'variable': coef_labels.get(var_name, var_name),
            'variable_code': var_name,
            'coefficient': round(coef, 6) if not np.isnan(coef) else '',
            'std_error': round(se, 6) if not np.isnan(se) else '',
            'z_stat': round(z, 4) if not np.isnan(z) else '',
            'p_value': round(p, 6) if not np.isnan(p) else '',
            'ci_95_lower': round(ci_lower, 6) if not np.isnan(ci_lower) else '',
            'ci_95_upper': round(ci_upper, 6) if not np.isnan(ci_upper) else '',
        })

    # Add AME for this outcome
    for _, ame_row in ame_df[ame_df['outcome'] == actual_outcome].iterrows():
        csv_rows.append({
            'outcome': actual_outcome,
            'outcome_label': outcome_labels.get(actual_outcome, f'Outcome {actual_outcome}'),
            'variable': f"{ame_row['variable']} (AME)",
            'variable_code': f"AME_{ame_row['variable'].lower().replace(' ','_')}",
            'coefficient': round(ame_row['AME'], 6),
            'std_error': '',
            'z_stat': '',
            'p_value': '',
            'ci_95_lower': '',
            'ci_95_upper': '',
        })

# Model fit row
csv_rows.append({
    'outcome': '', 'outcome_label': 'MODEL FIT',
    'variable': 'Pseudo R-squared', 'variable_code': 'pr2',
    'coefficient': round(mnl.prsquared, 6), 'std_error': '', 'z_stat': '', 'p_value': '', 'ci_95_lower': '', 'ci_95_upper': '',
})
csv_rows.append({
    'outcome': '', 'outcome_label': 'MODEL FIT',
    'variable': 'Log-Likelihood', 'variable_code': 'llf',
    'coefficient': round(mnl.llf, 4), 'std_error': '', 'z_stat': '', 'p_value': '', 'ci_95_lower': '', 'ci_95_upper': '',
})
csv_rows.append({
    'outcome': '', 'outcome_label': 'MODEL FIT',
    'variable': 'LR chi2 p-value', 'variable_code': 'llr_p',
    'coefficient': mnl.llr_pvalue, 'std_error': '', 'z_stat': '', 'p_value': '', 'ci_95_lower': '', 'ci_95_upper': '',
})
csv_rows.append({
    'outcome': '', 'outcome_label': 'MODEL FIT',
    'variable': 'N observations', 'variable_code': 'nobs',
    'coefficient': mnl.nobs, 'std_error': '', 'z_stat': '', 'p_value': '', 'ci_95_lower': '', 'ci_95_upper': '',
})
csv_rows.append({
    'outcome': '', 'outcome_label': 'MODEL FIT',
    'variable': 'Classification accuracy', 'variable_code': 'accuracy',
    'coefficient': round(accuracy, 6), 'std_error': '', 'z_stat': '', 'p_value': '', 'ci_95_lower': '', 'ci_95_upper': '',
})

csv_out = pd.DataFrame(csv_rows)
csv_path = os.path.join(RES_DIR, 'Table_MNLogit_4States.csv')
csv_out.to_csv(csv_path, index=False)

# 11. LaTeX Table
def fmt_coef(c, s, p):
    """Format coefficient with SE and significance stars."""
    if pd.isna(c):
        return '—'
    stars = ''
    if not pd.isna(p):
        if p < 0.01:
            stars = '***'
        elif p < 0.05:
            stars = '**'
        elif p < 0.10:
            stars = '*'
    return f'{c:.3f}{stars}'

def fmt_se(s):
    """Format standard error."""
    if pd.isna(s):
        return ''
    return f'({s:.3f})'

latex_lines = []
latex_lines.append('% Table_MNLogit_4States.tex — Multinomial Logit Results')
latex_lines.append('% Auto-generated by analyze_multinomial_logit.py on ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
latex_lines.append('')
latex_lines.append('\\begin{table}[htbp]')
latex_lines.append('  \\centering')
latex_lines.append('  \\caption{Multinomial Logit — Determinants of Migrator Endpoint Destinations}')
latex_lines.append('  \\label{tab:mnlogit}')
latex_lines.append('  \\small')
n_eq = len(model_outcomes)
latex_lines.append('  \\begin{tabular}{l' + 'c' * n_eq + '}')
latex_lines.append('    \\toprule')
latex_lines.append('    & ' + ' & '.join([outcome_labels[o] for o in model_outcomes]) + ' \\\\')
latex_lines.append('    \\midrule')

for var_name in X.columns:
    label = coef_labels.get(var_name, var_name)
    coef_line = f'    {label}'
    se_line = '    '
    for eq_col, actual_outcome in enumerate(model_outcomes):
        coef_val = mnl.params.loc[var_name, eq_col]
        se_val = mnl.bse.loc[var_name, eq_col]
        z_val = coef_val / se_val if se_val and not np.isnan(se_val) and se_val > 0 else np.nan
        p_val = 2 * (1 - scipy_stats.norm.cdf(abs(z_val))) if not np.isnan(z_val) else np.nan
        coef_line += f' & {fmt_coef(coef_val, se_val, p_val)}'
        se_line += f' & {fmt_se(se_val)}'
    coef_line += ' \\\\'
    se_line += ' \\\\'
    latex_lines.append(coef_line)
    latex_lines.append(se_line)

latex_lines.append('    \\midrule')

# Model fit stats
latex_lines.append(f'    Pseudo $R^2$ & \\multicolumn{{{n_eq}}}{{c}}{{{mnl.prsquared:.3f}}} \\\\')
latex_lines.append(f'    Log-Likelihood & \\multicolumn{{{n_eq}}}{{c}}{{{mnl.llf:.1f}}} \\\\')
latex_lines.append(f'    Observations & \\multicolumn{{{n_eq}}}{{c}}{{{mnl.nobs}}} \\\\')
latex_lines.append('    \\bottomrule')
latex_lines.append('  \\end{tabular}')

# Notes
n_rg = dist.get(1, 0)
n_ri = dist.get(2, 0)
n_burn = dist.get(3, 0)
n_stay = dist.get(0, 0)
n_ya = dist.get(4, 0)
latex_lines.append('')
latex_lines.append('  \\begin{minipage}{\\textwidth}')
latex_lines.append('    \\footnotesize')
latex_lines.append(f'    \\emph{{Notes:}} Multinomial logit estimates, reference outcome = Stay in Sushi '
                   f'(N={n_stay}). Coefficients are log-odds relative to base outcome. '
                   f'Standard errors clustered at user-address level in parentheses. '
                   f'Outcomes: Revert Genuine (N={n_rg}) = LP re-deposit on Uniswap before 16 Sep 2020; '
                   f'Revert Incentivized (N={n_ri}) = LP re-deposit on/after 16 Sep; '
                   f'Exit to Burn (N={n_burn}) = withdrawal, no LP return; '
                   f'Yield Aggregator (N={n_ya}) = exit to Harvest/Pickle. '
                   f'Sample: {n_mig} Migrator trajectories (excl. Returners). '
                   f'* $p<0.10$, ** $p<0.05$, *** $p<0.01$.')
latex_lines.append('  \\end{minipage}')
latex_lines.append('\\end{table}')

latex_path = os.path.join(RES_DIR, 'Table_MNLogit_4States.tex')
with open(latex_path, 'w') as f:
    f.write('\n'.join(latex_lines))



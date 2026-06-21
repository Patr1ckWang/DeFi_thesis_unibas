import os
import sys
import pandas as pd
import numpy as np
import scipy.stats as sps
from lifelines.statistics import logrank_test

# Helpers
def add_stars(p):
    if pd.isna(p):
        return ""
    if p < 0.01:
        return "***"
    if p < 0.05:
        return "**"
    if p < 0.10:
        return "*"
    return ""

def fmt(x, d=3):
    if pd.isna(x):
        return ""
    return f"{x:.{d}f}"

def proportions_ztest(count1, nobs1, count2, nobs2):
    p1 = count1 / nobs1 if nobs1 > 0 else np.nan
    p2 = count2 / nobs2 if nobs2 > 0 else np.nan
    p_pool = (count1 + count2) / (nobs1 + nobs2)
    se = np.sqrt(p_pool * (1 - p_pool) * (1 / nobs1 + 1 / nobs2))
    z = (p1 - p2) / se if se > 0 else 0.0
    p = 2 * sps.norm.sf(abs(z))
    return z, p

def cohens_d(mean_t, mean_c, n_t, n_c, var_t, var_c):
    if n_t < 2 or n_c < 2:
        return np.nan
    pooled_var = ((n_t - 1) * var_t + (n_c - 1) * var_c) / (n_t + n_c - 2)
    if pooled_var <= 0:
        return np.nan
    return (mean_t - mean_c) / np.sqrt(pooled_var)

def fmt_pval(p, threshold=0.001):
    if pd.isna(p):
        return ""
    if p < threshold:
        return f"p<{threshold:.3f}"
    return f"p={p:.3f}"

# Paths
DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_PATH = os.path.join(DIR, 'raw_data', 'users_v5.csv')
PANEL_PATH = os.path.join(DIR, 'cleaned_data', 'survival_panel.csv')
OUT_CSV = os.path.join(DIR, 'results', 'tables', 'Table1_Summary_Statistics.csv')
OUT_TEX = os.path.join(DIR, 'results', 'tables', 'Table1_Summary_Statistics.tex')

# PANEL A — faction counts from raw extraction
users_raw = pd.read_csv(USERS_PATH)

fac_order = ['Stayer', 'Migrator', 'Fence-sitter', 'Runaway']
fac_labels = {
    'Stayer':       'Stayer',
    'Migrator':     'Migrator',
    'Fence-sitter': 'Fence-sitter',
    'Runaway':      'Runaway',
    'Early Leaver': 'Early Leaver',   # NaN faction → Early Leaver cohort
}

raw_fac = users_raw['faction'].fillna('Early Leaver')
n_up = raw_fac.value_counts().reindex(fac_order + ['Early Leaver']).fillna(0).astype(int)
n_addr = (users_raw.groupby(raw_fac)['user_address']
          .nunique()
          .reindex(fac_order + ['Early Leaver'])
          .fillna(0).astype(int))

total_up = int(n_up.sum())
total_addr = int(users_raw['user_address'].nunique())

# Old/New cohort breakdown within Stayer and Migrator (for Panel A sub-rows)
stay_old_up = int(((users_raw['faction'] == 'Stayer') & (users_raw['cohort'] == 'Old')).sum())
stay_new_up = int(((users_raw['faction'] == 'Stayer') & (users_raw['cohort'] == 'New')).sum())
mig_old_up  = int(((users_raw['faction'] == 'Migrator') & (users_raw['cohort'] == 'Old')).sum())
mig_new_up  = int(((users_raw['faction'] == 'Migrator') & (users_raw['cohort'] == 'New')).sum())
run_old_up  = int(((users_raw['faction'] == 'Runaway') & (users_raw['cohort'] == 'Old')).sum())
run_new_up  = int(((users_raw['faction'] == 'Runaway') & (users_raw['cohort'] == 'New')).sum())

# Build Panel A rows with Old/New sub-rows
pa_rows = []
for fac in fac_order + ['Early Leaver']:
    pa_rows.append({
        'Faction': fac_labels[fac],
        'User×Pool (N)': int(n_up[fac]),
        'Unique Addresses': int(n_addr[fac]),
    })
    if fac == 'Stayer':
        pa_rows.append({'Faction': '  Old (pre-26 Aug entry)', 'User×Pool (N)': stay_old_up, 'Unique Addresses': ''})
        pa_rows.append({'Faction': '  New (post-26 Aug entry)', 'User×Pool (N)': stay_new_up, 'Unique Addresses': ''})
    elif fac == 'Migrator':
        pa_rows.append({'Faction': '  Old (pre-26 Aug entry)', 'User×Pool (N)': mig_old_up, 'Unique Addresses': ''})
        pa_rows.append({'Faction': '  New (post-26 Aug entry)', 'User×Pool (N)': mig_new_up, 'Unique Addresses': ''})
    elif fac == 'Runaway':
        pa_rows.append({'Faction': '  Old (pre-26 Aug entry)', 'User×Pool (N)': run_old_up, 'Unique Addresses': ''})
        pa_rows.append({'Faction': '  New (post-26 Aug entry)', 'User×Pool (N)': run_new_up, 'Unique Addresses': ''})
count_panel = pd.DataFrame(pa_rows)

# PANEL B — core sample construction
panel = pd.read_csv(PANEL_PATH)

# head(1) not first(): first() returns the first non-null value per column,
# which can silently borrow future (t>0) data if any covariate has NaN at
# baseline.  head(1) returns the actual first row of each sorted group,
# preserving NaN.  (All covariates are time-invariant in the current data
base = (
    panel
    .sort_values(['user_address', 'pool_name', 'start_time'])
    .groupby(['user_address', 'pool_name'])
    .head(1)
)

for col in ['faction', 'cohort', 'log_balance', 'is_new', 'has_uni_mining',
            'size_group', 'overall_event', 'duration_days']:
    if col not in base.columns:
        print(f"ERROR: column '{col}' missing from survival_panel.csv")
        sys.exit(1)

core_mask = (base['faction'].isin(['Stayer', 'Migrator'])) & (base['cohort'] != 'Returner')
core = base[core_mask].copy()

treated = core[core['faction'] == 'Migrator'].copy()
control = core[core['faction'] == 'Stayer'].copy()

n_t = len(treated)
n_c = len(control)

# Panel A: unique-address exclusion flow  raw user

raw_fac_a = users_raw['faction'].fillna('Early Leaver')
all_pa_addr = set(users_raw['user_address'].unique())

# Step 1: exclude Runaway + Early Leaver
step1_pa = users_raw[raw_fac_a.isin(['Stayer', 'Migrator', 'Fence-sitter'])]
step1_pa_addr = set(step1_pa['user_address'].unique())
excl1_addr_count = len(all_pa_addr - step1_pa_addr)   # only in Runaway/EA

# Step 2a: exclude Fence-sitter (ambiguous treatment status)
fs_addr_set = set(users_raw[users_raw['faction'] == 'Fence-sitter']
                  ['user_address'].unique())
stay_mig_addr_set = set(users_raw[users_raw['faction'].isin(['Stayer', 'Migrator'])]
                        ['user_address'].unique())
fs_only_addr = fs_addr_set - stay_mig_addr_set   # addresses ONLY Fence-sitter
after_fs_addr = step1_pa_addr - fs_only_addr

# Step 2b: exclude Returner cohort 
step2_pa_addr = set(users_raw[(users_raw['faction'].isin(['Stayer', 'Migrator'])) &
                               (users_raw['cohort'] != 'Returner')]
                    ['user_address'].unique())
excl2a_addr_count = len(fs_only_addr)                      # Fence-sitter only
excl2b_addr_count = len(after_fs_addr - step2_pa_addr)     # Returner only

# Verify split == total excl2
assert excl2a_addr_count + excl2b_addr_count == len(step1_pa_addr - step2_pa_addr), \
    f"Split mismatch: {excl2a_addr_count}+{excl2b_addr_count} != {len(step1_pa_addr - step2_pa_addr)}"

# Returner counts
returner_up_mask = ((users_raw['faction'].isin(['Stayer', 'Migrator'])) &
                     (users_raw['cohort'] == 'Returner'))
n_returner_up = int(returner_up_mask.sum())

# Core user×pool count
n_core_up = int(((users_raw['faction'].isin(['Stayer', 'Migrator'])) &
                  (users_raw['cohort'] != 'Returner')).sum())

# Fence-sitter user×pool count 
n_fs_up = int(n_up['Fence-sitter'])

# Panel B rows — descriptive statistics
var_defs = [
    ('overall_event', 'Event (death = 1)', 'binary'),
    ('duration_days', 'Survival duration (days)', 'outcome'),
    ('log_balance',   'Log LP/SLP balance',    'continuous'),
    ('is_new',        'New user (=1)',          'binary'),
    ('has_uni_mining','Pool has UNI mining (=1)','binary'),
]

# is_whale derived from size_group 
core['is_whale'] = np.where(core['size_group'].notna(),
                             (core['size_group'] == 'Q4_whale').astype(int),
                             np.nan)
treated['is_whale'] = np.where(treated['size_group'].notna(),
                                (treated['size_group'] == 'Q4_whale').astype(int),
                                np.nan)
control['is_whale'] = np.where(control['size_group'].notna(),
                                (control['size_group'] == 'Q4_whale').astype(int),
                                np.nan)
var_defs.append(('is_whale', 'Whale (top 20% = 1)', 'binary'))

body_rows = []

for col, label, vtype in var_defs:
    t = treated[col].dropna()
    c = control[col].dropna()

    # Guard: if either group is empty or too small, skip inference
    if len(t) < 2 or len(c) < 2:
        print(f"  WARNING: {col} has <2 obs in one group — skipping")
        continue

    t_mean = t.mean()
    c_mean = c.mean()
    t_sd = t.std()
    c_sd = c.std()
    diff = t_mean - c_mean

    # Standard Cohen's d (df-weighted pooled SD)
    smd = cohens_d(t_mean, c_mean, len(t), len(c), t.var(), c.var())

    if vtype == 'binary':
        # Proportions z-test for binary variables
        count1 = int(t.sum())
        count2 = int(c.sum())
        _, p = proportions_ztest(count1, len(t), count2, len(c))
    elif col == 'duration_days':
        # Log-rank test: duration_days is right-censored (66.1% of Stayers
        # survive to 237 days with event=0).  A t-test treats all 237d
        # values as exact death times, biasing means and variances.
        mask_t = treated['duration_days'].notna() & treated['overall_event'].notna()
        mask_c = control['duration_days'].notna() & control['overall_event'].notna()
        lr = logrank_test(
            treated.loc[mask_t, 'duration_days'],
            control.loc[mask_c, 'duration_days'],
            event_observed_A=treated.loc[mask_t, 'overall_event'],
            event_observed_B=control.loc[mask_c, 'overall_event'],
        )
        p = lr.p_value
    else:
        # Welch t-test for continuous variables
        _, p = sps.ttest_ind(t, c, equal_var=False)

    if vtype in ('continuous', 'outcome'):
        # Mean row
        body_rows.append({
            'Variable': label,
            '(1) Migrator': fmt(t_mean),
            '(2) Stayer':   fmt(c_mean),
            '(3) Diff (1)-(2)': f"{fmt(diff)}{add_stars(p)}",
            '(4) SMD': fmt(smd),
        })
        # SD / p-value row
        body_rows.append({
            'Variable': '',
            '(1) Migrator': f"({t_sd:.3f})",
            '(2) Stayer':   f"({c_sd:.3f})",
            '(3) Diff (1)-(2)': f"[{fmt_pval(p)}]" if pd.notna(p) else "",
            '(4) SMD': '',
        })
    else:  # binary
        body_rows.append({
            'Variable': label,
            '(1) Migrator': fmt(t_mean),
            '(2) Stayer':   fmt(c_mean),
            '(3) Diff (1)-(2)': f"{fmt(diff)}{add_stars(p)}",
            '(4) SMD': fmt(smd),
        })

# Observations row
body_rows.append({
    'Variable': 'Observations (user×pool)',
    '(1) Migrator': str(n_t),
    '(2) Stayer':   str(n_c),
    '(3) Diff (1)-(2)': '',
    '(4) SMD': '',
})

body_df = pd.DataFrame(body_rows)

#  CSV
os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)

with open(OUT_CSV, 'w') as f:
    f.write("# Table 1 — Summary Statistics\n")
    f.write("# Panel A: Address counts by Faction (raw users_v5.csv, pre-pipeline)\n")
    f.write(f"# Total user×pool: {total_up}, Total unique addresses: {total_addr}\n")
    f.write(f"# Core sample (Stayer + Migrator, excl. Returner): {n_core_up} "
            f"(Migrator={n_t}, Stayer={n_c})  [Panel B N={n_t + n_c}]\n")
    count_panel.to_csv(f, index=False)
    f.write("\n")
    f.write("# Panel B: Descriptive statistics (Stayer vs Migrator, core sample)\n")
    f.write("# Observation window: 9 Sep 2020 – 4 May 2021 (237 days)\n")
    body_df.to_csv(f, index=False, lineterminator='\n')

# LaTeX — Table 1
def esc(s):
    """Escape LaTeX-special characters."""
    if s is None:
        return ""
    return str(s).replace("_", r"\_").replace("×", r"$\times$")


def make_latex():
    lines = []
    lines.append(r"\begin{table}[htbp]")
    lines.append(r"\centering")
    lines.append(r"\small")
    lines.append(r"\caption{Summary Statistics and Covariate Balance}")
    lines.append(r"\label{tab:summary}")

    # Panel A
    lines.append(r"\begin{tabular}{lcc}")
    lines.append(r"\toprule")
    lines.append(r"\multicolumn{3}{l}{\textbf{Panel A: Full ecosystem and sample construction}} \\")
    lines.append(r"Faction & User$\times$Pool & Unique addresses \\")
    lines.append(r"\midrule")

    for fac in fac_order + ['Early Leaver']:
        if fac == 'Stayer':
            lines.append(
                f"{esc(fac_labels[fac])} & {int(n_up[fac])} & {int(n_addr[fac])} \\\\"
            )
            lines.append(
                f"\\quad Old (pre-26 Aug entry) & {stay_old_up} & \\\\"
            )
            lines.append(
                f"\\quad New (post-26 Aug entry) & {stay_new_up} & \\\\"
            )
        elif fac == 'Migrator':
            lines.append(
                f"{esc(fac_labels[fac])} & {int(n_up[fac])} & {int(n_addr[fac])} \\\\"
            )
            lines.append(
                f"\\quad Old (pre-26 Aug entry) & {mig_old_up} & \\\\"
            )
            lines.append(
                f"\\quad New (post-26 Aug entry) & {mig_new_up} & \\\\"
            )
        elif fac == 'Runaway':
            lines.append(
                f"{esc(fac_labels[fac])} & {int(n_up[fac])} & {int(n_addr[fac])} \\\\"
            )
            lines.append(
                f"\\quad Old (pre-26 Aug entry) & {run_old_up} & \\\\"
            )
            lines.append(
                f"\\quad New (post-26 Aug entry) & {run_new_up} & \\\\"
            )
        else:
            lines.append(
                f"{esc(fac_labels[fac])} & {int(n_up[fac])} & {int(n_addr[fac])} \\\\"
            )
    lines.append(r"\addlinespace")
    lines.append(f"Raw extraction total & {total_up} & {total_addr} \\\\")
    lines.append(r"\addlinespace")
    lines.append(r"Exclude: Runaway + Early Leaver & "
                 f"{int(n_up['Runaway']) + int(n_up['Early Leaver'])} & "
                 f"{excl1_addr_count} \\\\")
    lines.append(r"\addlinespace")
    lines.append(r"Exclude: Fence-sitter (ambiguous treatment) & "
                 f"{n_fs_up} & "
                 f"{excl2a_addr_count} \\\\")
    lines.append(r"Exclude: Returner cohort (endogenous re-entry) & "
                 f"{n_returner_up} & "
                 f"{excl2b_addr_count} \\\\")
    lines.append(r"\midrule")
    lines.append(f"Core analysis sample & {n_core_up} & "
                 f"{len(step2_pa_addr)} \\\\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append("")
    lines.append(r"\vspace{1em}")
    lines.append("")

    # Panel B
    n_cols = 5
    lines.append(r"\begin{tabular}{lcccc}")
    lines.append(r"\toprule")
    lines.append(r"\multicolumn{5}{l}{\textbf{Panel B: Descriptive statistics (core sample)}} \\")
    lines.append(f" & (1) Migrator & (2) Stayer & (3) Diff & (4) SMD \\\\")
    lines.append(f" & ($N={n_t}$) & ($N={n_c}$) & & \\\\")
    lines.append(r"\midrule")

    for r in body_rows:
        var_col = esc(r['Variable']) if r['Variable'] else ''
        diff_val = str(r['(3) Diff (1)-(2)'])
        smd_val = str(r['(4) SMD'])
        if diff_val.startswith('-'):
            diff_val = '$-$' + diff_val[1:]
        if smd_val.startswith('-'):
            smd_val = '$-$' + smd_val[1:]
        lines.append(
            f"{var_col} & {r['(1) Migrator']} & {r['(2) Stayer']} & "
            f"{diff_val} & {smd_val} \\\\"
        )
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")

    # Notes
    lines.append(r"\begin{minipage}{0.95\linewidth}")
    lines.append(r"\footnotesize")
    lines.append(
        r"\textit{Notes:} Panel A reports sample attrition from the raw extraction. "
        r"Panel B reports summary statistics for the core analysis sample "
        r"(Migrator and Stayer trajectories, excluding the Returner cohort). "
        r"SD for continuous/survival variables are in parentheses. "
        r"Diff = Migrator $-$ Stayer mean gap. "
        r"SMD = standardised mean difference (Cohen's $d$). "
        r"$^{*}\,p<0.10$, $^{**}\,p<0.05$, $^{***}\,p<0.01$ "
        r"(Welch's $t$-test for continuous variables, "
        r"proportions $z$-test for binary, Log-rank test for survival). "
        r"See Section~4.1 for sample-construction details and the Appendix for variable definitions."
    )
    lines.append(r"\end{minipage}")
    lines.append(r"\end{table}")
    return "\n".join(lines)


with open(OUT_TEX, 'w') as f:
    f.write(make_latex())



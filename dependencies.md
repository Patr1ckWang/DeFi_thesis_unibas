# Environment and Dependencies for Replication


The project uses a dual-stack pipeline: Python handles data extraction, descriptive statistics, difference-in-differences estimation, multinomial logit, Oster bounds, propensity score matching, and most visualization; R handles Cox proportional hazards regression, post-matching survival estimation, heterogeneity interactions, accelerated failure time models, and other robustness checks.

---

## 1. Directory Structure

```
defipaper/
├── scripts_data/                    # Upstream data preparation (BigQuery SQL + Python)
│   ├── survival_v5.sql              # BigQuery: dual-track event-level extraction 
│   ├── users.sql                    # BigQuery: user classification (cohort x faction) at migration
│   ├── migration_time.sql           # BigQuery: pool-level migration block and liquidity
│   ├── pool_tvl.sql                 # BigQuery: pool-level total value locked over time
│   ├── pipeline.py                  # Python: data cleaning -> survival_panel.csv, users_enhanced.csv, death_details.csv
│   └── fetch_external_data.py       # Python: external data -> token_prices.csv, tvl_daily.csv
├── scripts_empirical/               # Core empirical analysis
│   ├── generate_table1.py           # Table 1: descriptive statistics and balance
│   ├── analyze_km_survival.py       # Kaplan-Meier survival curves and descriptives
│   ├── run_cox_models.R             # Main Cox proportional hazards models (Table 2)
│   ├── run_heterogeneity.R          # Interaction heterogeneity analysis
│   ├── run_forest_censor_sensitivity.R  # Pool-level forest and censoring sensitivity (R computation)
│   ├── analyze_robustness_forest.py     # Pool-level forest and censoring sensitivity (Python visualization)
│   ├── analyze_did_event_study.py   # Difference-in-differences event study
│   ├── analyze_psm_endogeneity.py   # Propensity score matching (Python side)
│   ├── run_psm_cox.R                # Matched Cox estimation (R side)
│   ├── analyze_oster_bounds.py      # Oster bounds for unobservable selection
│   ├── analyze_multinomial_logit.py # Multinomial logit endpoint destinations
│   ├── run_robust_tests.R           # AFT models, time-stratified Cox, Schoenfeld test, subsamples
│   ├── analyze_mixed_factions.py    # Cross-pool faction composition analysis
├── cleaned_data/                    # Processed data files
├── raw_data/                        # Raw extractions and external data
```

---

## 2. Python Environment

**Engine version**: Python 3.9 or higher

**Installation**:

```bash
pip install pandas numpy scipy matplotlib lifelines statsmodels pyfixest
```

**Package inventory**:

| Package | Purpose | Script(s) |
|---|---|---|
| `pandas`, `numpy` | Panel data manipulation, feature engineering, time-series extraction | All Python scripts |
| `lifelines` | Kaplan-Meier survival estimation (`KaplanMeierFitter`), log-rank test (`logrank_test`) | `analyze_km_survival.py` |
| `statsmodels` | Multinomial logit (`MNLogit`), OLS for the linear probability model underlying Oster bounds, `add_constant()` | `analyze_multinomial_logit.py`, `analyze_oster_bounds.py` |
| `pyfixest` | Two-way fixed effects (TWFE) difference-in-differences and event-study estimation | `analyze_did_event_study.py` |
| `scipy` | Welch's t-test, standard normal p-values, auxiliary statistical tests | `generate_table1.py`, `analyze_multinomial_logit.py` |
| `matplotlib` | Publication-quality PDF and PNG figures (KM curves, forest plots, love plots, event-study coefficient plots) | `analyze_km_survival.py`, `analyze_robustness_forest.py`, `analyze_psm_endogeneity.py`, `analyze_did_event_study.py` |

Standard library modules used (no installation required): `os`, `sys`, `csv`, `json`, `subprocess`, `urllib.request`, `itertools`, `warnings`, `datetime`, `time`.

The following packages, listed in earlier versions of this document, have been removed because no script imports them directly: `openpyxl`, `patsy`, `python-dateutil`, `pytz`.

---

## 3. R Environment

**Engine version**: R 4.2 or higher

**Installation**:

```R
install.packages(c("survival", "modelsummary", "MatchIt", "dplyr", "cobalt", "ggplot2", "sandwich", "lmtest"))
```

**Package inventory**:

| Package | Purpose | Script(s) |
|---|---|---|
| `survival` | Core engine: survival object construction (`Surv()`), Cox proportional hazards (`coxph()`), Schoenfeld residuals test (`cox.zph()`), accelerated failure time models (`survreg()`) | All R scripts |
| `modelsummary` | Formatted export: regression estimates to LaTeX (`.tex`) and CSV tables | `run_cox_models.R`, `run_heterogeneity.R` |
| `MatchIt` | Propensity score matching: 1:1 nearest-neighbor with caliper = 0.05 SD | `run_psm_cox.R` |
| `dplyr` | Data wrangling and subset extraction within R | `run_psm_cox.R`, `run_forest_censor_sensitivity.R`, `run_robust_tests.R` |
| `cobalt` | Covariate balance diagnostics (`bal.tab()`) and love plot generation after matching | `run_psm_cox.R` |
| `ggplot2` | Love plot visualization | `run_psm_cox.R` |
| `sandwich` | Cluster-robust variance-covariance estimation (`vcovCL`) for AFT models | `run_robust_tests.R` |
| `lmtest` | Coefficient tests (`coeftest`) for AFT models with cluster-robust standard errors | `run_robust_tests.R` |



## 4. Input Data Files

| File | Source | Description |
|:---|:---|:---|
| `raw_data/survival_v5.csv` | Google BigQuery extraction | User-by-pool-by-day-by-platform event-level data |
| `raw_data/users_v5.csv` | Google BigQuery extraction | User classification (faction and cohort assignments) |
| `raw_data/token_prices.csv` | `fetch_external_data.py` (DeFi Llama API) | Daily token prices (SUSHI, ETH, UNI, etc.) |
| `raw_data/tvl_daily.csv` | `fetch_external_data.py` (DeFi Llama API) | Protocol-level total value locked |
| `cleaned_data/survival_panel.csv` | `pipeline.py` | Survival panel in counting-process format: Surv(start, stop, event) |
| `cleaned_data/users_enhanced.csv` | `pipeline.py` | Enhanced user profiles with derived covariates |
| `cleaned_data/death_details.csv` | `pipeline.py` | Death event details: death_time, death_type, censored indicator, event indicator |

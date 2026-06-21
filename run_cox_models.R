# Time-Varying Cox Proportional Hazards — Cross-Platform Main Table
#
# Panel A: Uni-only descriptive benchmark (mechanical — migration IS exit from Uni)
# Panel B: Cross-platform (Migrator-on-SLP vs Stayer-on-LP), M1→M3 progressive
#
# Output:
#   results/tables/Table2_Cox_Main.tex
#   results/tables/Table2_Cox_Main.csv

library(survival)


# Load data & resolve path
initial_args <- commandArgs(trailingOnly = FALSE)
file_arg <- grep("^--file=", initial_args, value = TRUE)
if (length(file_arg) > 0) {
  script_path <- normalizePath(sub("^--file=", "", file_arg))
  script_dir <- dirname(script_path)
  data_path <- file.path(script_dir, "..", "cleaned_data", "survival_panel.csv")
} else {
  data_path <- file.path("cleaned_data", "survival_panel.csv")
}
df <- read.csv(data_path)

# Data preparation
num_vars <- c(
  "is_migrator", "is_new", "log_balance", "gas_gwei",
  "sushi_usd", "eth_usd", "has_uni_mining",
  "impermanent_loss", "apy_decay_proxy"
)
for (v in num_vars) {
  df[[v]] <- as.numeric(df[[v]])
}

# Exclude Returner cohort
df <- subset(df, cohort != "Returner")

# Expected trajectory counts (from Table 1)
EXPECTED_MIG_SLP <- 2604
EXPECTED_STY_LP <- 4059

# 2. Panel A: Uni-Only Descriptive Baseline (Mechanical Benchmark)

df_uni <- subset(df, track == "uniswap" &
  faction %in% c("Migrator", "Stayer"))

n_uni_traj <- length(unique(paste(df_uni$user_address, df_uni$pool_name)))


uni_vars <- c(
  "user_address", "start_time", "stop_time", "event",
  "is_migrator", "is_new", "log_balance", "has_uni_mining"
)
df_uni_pre <- nrow(df_uni)
df_uni_clean <- na.omit(df_uni[, uni_vars])
n_uni_drop <- df_uni_pre - nrow(df_uni_clean)

f0 <- Surv(start_time, stop_time, event) ~ is_migrator + is_new +
  log_balance + cluster(user_address)

fit0 <- tryCatch(
  {
    coxph(f0, data = df_uni_clean, robust = TRUE)
  },
  error = function(e) {
    return(NULL)
  }
)



# 3. Panel B: Cross-Platform Cox (Main Specification)

df_sushi <- subset(df, track == "sushiswap" & faction == "Migrator")
df_uni_s <- subset(df, track == "uniswap" & faction == "Stayer")
df_cross <- rbind(df_sushi, df_uni_s)

n_mig_traj <- length(unique(paste(df_sushi$user_address, df_sushi$pool_name)))
n_sty_traj <- length(unique(paste(df_uni_s$user_address, df_uni_s$pool_name)))


# Integrity checks
stopifnot(n_mig_traj == EXPECTED_MIG_SLP)
stopifnot(n_sty_traj == EXPECTED_STY_LP)

cross_vars <- c(
  "user_address", "start_time", "stop_time", "event",
  "is_migrator", "is_new", "log_balance",
  "sushi_usd", "eth_usd", "has_uni_mining"
)
df_cross_pre <- nrow(df_cross)
df_cross_clean <- na.omit(df_cross[, cross_vars])
n_cross_drop <- df_cross_pre - nrow(df_cross_clean)

# 4. Model fitting
run_model <- function(formula_str, data, label) {
  f <- as.formula(formula_str)
  fit <- tryCatch(
    {
      coxph(f, data = data, robust = TRUE)
    },
    error = function(e) {
      return(NULL)
    }
  )
  return(fit)
}

# M1: Cross-Platform Base
f1 <- Surv(start_time, stop_time, event) ~ is_migrator + is_new +
  log_balance + cluster(user_address)
fit1 <- run_model(f1, df_cross_clean, "M1 (Cross Base)")

# M2: + Pool Controls
f2 <- Surv(start_time, stop_time, event) ~ is_migrator + is_new +
  log_balance + has_uni_mining + cluster(user_address)
fit2 <- run_model(f2, df_cross_clean, "M2 (+Pool)")

# M3: + Macro Interactions
f3 <- Surv(start_time, stop_time, event) ~ is_migrator +
  is_migrator:sushi_usd + is_migrator:eth_usd +
  is_new + log_balance + cluster(user_address)
fit3 <- run_model(f3, df_cross_clean, "M3 (+Macro)")


all_fits <- list(
  "M0 (Uni-Only, mechanical)" = fit0,
  "M1 (Cross Base)" = fit1,
  "M2 (+Pool)" = fit2,
  "M3 (+Macro)" = fit3
)



#  CSV
csv_rows_list <- list()
for (nm in names(all_fits)) {
  fit <- all_fits[[nm]]
  if (is.null(fit)) next
  s <- summary(fit)
  cm <- s$coefficients
  civ <- confint(fit)
  lt <- fit$logtest
  lr_pval <- 1 - pchisq(as.numeric(lt["test"]), as.numeric(lt["df"]))
  for (i in seq_len(nrow(cm))) {
    csv_rows_list[[length(csv_rows_list) + 1]] <- c(
      Model     = nm,
      Term      = rownames(cm)[i],
      HR        = round(exp(cm[i, 1]), 3),
      SE_robust = round(cm[i, 4], 4),
      CI_lower  = round(exp(civ[i, 1]), 3),
      CI_upper  = round(exp(civ[i, 2]), 3),
      P_value   = format.pval(cm[i, 6], digits = 3),
      N         = fit$n,
      Events    = fit$nevent,
      C_index   = round(fit$concordance["concordance"], 3),
      LR_test_p = format.pval(lr_pval, digits = 3)
    )
  }
}
csv_df <- do.call(rbind, csv_rows_list)
write.csv(csv_df, "results/tables/Table2_Cox_Main.csv", row.names = FALSE)

# 7. LaTeX table
model_list <- list()
for (nm in names(all_fits)) {
  if (!is.null(all_fits[[nm]])) {
    model_list[[nm]] <- all_fits[[nm]]
  }
}

coef_labels <- c(
  "is_migrator"            = "Migrator (1=Yes)",
  "is_new"                 = "New User (1=Yes)",
  "log_balance"            = "Log LP/SLP Balance",
  "has_uni_mining"         = "Pool has UNI Mining",
  "is_migrator:sushi_usd"  = "Migrator $\\times$ SUSHI Price",
  "is_migrator:eth_usd"    = "Migrator $\\times$ ETH Price"
)

# Validate coef_map coverage
all_coef_names <- unique(unlist(lapply(model_list, function(m) names(coef(m)))))
missing_from_map <- setdiff(all_coef_names, names(coef_labels))


# Build per-model fit statistics row
build_gof <- function(fit) {
  if (is.null(fit)) {
    return(NULL)
  }
  data.frame(
    raw = c("nobs", "r.squared", "nevent"),
    clean = c("Observations", "C-index", "Events"),
    fmt = c(0, 3, 0),
    stringsAsFactors = FALSE
  )
}

# Custom notes
table_notes <- c(
  "Hazard ratios > 1 indicate higher risk of exit. Cluster-robust standard errors at the user-address level.",
  "M0 (Uni-Only) is a mechanical benchmark: migration IS exit from Uniswap. Its coefficients are not causal.",
  "M1–M3 estimate cross-platform models: Migrators tracked on SushiSwap (SLP), Stayers tracked on Uniswap (LP).",
  "M3 interacts macro variables with Migrator status to avoid collinearity with the nonparametric baseline hazard.",
  "* p<0.10, ** p<0.05, *** p<0.01."
)

library(modelsummary)
modelsummary(
  model_list,
  output = "results/tables/Table2_Cox_Main.tex",
  exponentiate = TRUE,
  stars = c("*" = .1, "**" = .05, "***" = .01),
  coef_map = coef_labels,
  gof_omit = "DF|Deviance|AIC|BIC|Log\\.Lik\\.|R2",
  title = "Main Cox Regression Results — Hazard Ratios",
  notes = table_notes
)

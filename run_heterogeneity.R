# Builds on the Cross-Platform Cox specification (M2 baseline from run_cox_models.R).
# Two models test whether the Migrator death premium varies across user types:
#   H3 (New vs Old):  is_migrator × is_new
#   H4 (Whale vs Retail): is_migrator × is_whale  (+ log_balance main effect)
#
# Output:
#   results/tables/Table_Heterogeneity_Interactions.tex
#   results/tables/Table_Heterogeneity_Interactions.csv

library(survival)


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


# 1. Data preparation

num_vars <- c("is_migrator", "is_new", "log_balance", "has_uni_mining")
for (v in num_vars) {
  df[[v]] <- as.numeric(df[[v]])
}

# Create is_whale from size_group
df$is_whale <- ifelse(df$size_group == "Q4_whale", 1, 0)


df <- subset(df, cohort != "Returner")
df <- subset(df, faction != "Fence-sitter")

# 3. Build Cross-Platform Panel (identical to run_cox_models.R)
# Migrator tracked on SushiSwap (SLP)  vs.  Stayer tracked on Uniswap (LP)
df_sushi <- subset(df, track == "sushiswap" & faction == "Migrator")
df_uni <- subset(df, track == "uniswap" & faction == "Stayer")
df_cross <- rbind(df_sushi, df_uni)

# Expected trajectory counts (from Table 1)
EXPECTED_MIG_SLP <- 2604
EXPECTED_STY_LP <- 4059

n_mig_traj <- length(unique(paste(
  df_cross$user_address[df_cross$faction == "Migrator"],
  df_cross$pool_name[df_cross$faction == "Migrator"]
)))
n_sty_traj <- length(unique(paste(
  df_cross$user_address[df_cross$faction == "Stayer"],
  df_cross$pool_name[df_cross$faction == "Stayer"]
)))


# 4. Variable selection & NA diagnostics
cross_vars <- c(
  "user_address", "start_time", "stop_time", "event",
  "is_migrator", "is_new", "is_whale", "log_balance",
  "has_uni_mining", "pool_name"
)
df_cross_clean <- na.omit(df_cross[, cross_vars])

# 5. Baseline formula — aligned with M2 from run_cox_models.R
#   is_migrator + is_new + log_balance + has_uni_mining + cluster(user_address)
f_base <- "Surv(start_time, stop_time, event) ~ is_migrator + is_new + log_balance + has_uni_mining + cluster(user_address)"

# Helper to fit, diagnose, and report
run_het_model <- function(formula_str, data, label) {
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

# 6. Fit heterogeneity models

# H3: New vs Old Users — is_migrator × is_new
# H3: New vs Old Users — is_migrator × is_new
f_het_new <- paste0(f_base, " + is_migrator:is_new")
fit_het_new <- run_het_model(f_het_new, df_cross_clean, "H3: New × Migrator")

# H4: Whale vs Retail — is_migrator × is_whale
# NOTE: log_balance is retained as a main effect (wealth gradient is universal).
# is_whale captures pre-migration LP wealth; log_balance captures current tracked-platform balance.
f_het_whale <- paste0(f_base, " + is_whale + is_migrator:is_whale")
fit_het_whale <- run_het_model(f_het_whale, df_cross_clean, "H4: Whale × Migrator")



all_fits <- list(
  "H3 (New × Migrator)"   = fit_het_new,
  "H4 (Whale × Migrator)" = fit_het_whale
)

# 8.LaTeX table

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
  "is_whale"               = "Whale (Top 20\\% LP at $T_0$)",
  "is_migrator:is_new"     = "Migrator $\\times$ New User",
  "is_migrator:is_whale"   = "Migrator $\\times$ Whale"
)



table_notes <- c(
  "Hazard ratios > 1 indicate higher risk of exit (death). Cluster-robust standard errors at the user-address level.",
  "Cross-platform specification: Migrators tracked on SushiSwap (SLP), Stayers tracked on Uniswap (LP).",
  "Sample excludes Returner cohort and Fence-sitter, consistent with Table 1 Panel A Core sample (N = 6,663 trajectories).",
  "H3 tests whether newly onboarded Migrators exit faster than veteran Migrators.",
  "H4 tests whether whale Migrators (top 20\\% LP balance at $T_0$) exit faster than retail Migrators, controlling for log balance.",
  "* p<0.10, ** p<0.05, *** p<0.01."
)

library(modelsummary)
modelsummary(
  model_list,
  output       = "results/tables/Table_Heterogeneity_Interactions.tex",
  exponentiate = TRUE,
  stars        = c("*" = .1, "**" = .05, "***" = .01),
  coef_map     = coef_labels,
  gof_omit     = "DF|Deviance|AIC|BIC|Log\\.Lik\\.|R2",
  title        = "Heterogeneity Analysis — Interaction Terms (Cox PH)",
  notes        = table_notes
)

# 9.CSV

csv_rows <- list()
for (nm in names(all_fits)) {
  fit <- all_fits[[nm]]
  if (is.null(fit)) next
  s <- summary(fit)
  cm <- s$coefficients
  civ <- confint(fit)
  lt <- fit$logtest
  lr_pval <- 1 - pchisq(as.numeric(lt["test"]), as.numeric(lt["df"]))
  for (i in seq_len(nrow(cm))) {
    csv_rows[[length(csv_rows) + 1]] <- c(
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
csv_df <- do.call(rbind, csv_rows)
write.csv(csv_df, "results/tables/Table_Heterogeneity_Interactions.csv", row.names = FALSE)

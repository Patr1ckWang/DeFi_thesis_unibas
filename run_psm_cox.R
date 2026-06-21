# Propensity Score Matching + Matched Cox PH
#
# PSM estimation (1:1 NN, caliper = 0.05 SD) to control for observable
# self-selection between Migrators and Stayers.
#
# Matching covariates: is_new, log_balance, has_uni_mining
# Note: log_balance is measured as the initial tracked-platform balance at T0
#   (SLP balance for Migrators on SushiSwap; LP balance for Stayers on Uniswap).
#   The paper acknowledges this in limitations.

library(survival)
library(MatchIt)
library(dplyr)

set.seed(42)

# 1. Load Data
df <- read.csv("cleaned_data/survival_panel_for_psm.csv")
df$is_migrator <- as.numeric(df$is_migrator)
df$is_new <- as.numeric(df$is_new)
df$has_uni_mining <- as.numeric(df$has_uni_mining)
df$log_balance <- as.numeric(df$log_balance)


# 2. Construct Cross-Sectional Baseline
sushi_panel <- df[df$track == "sushiswap" & df$faction == "Migrator" & df$cohort != "Returner", ]
uni_panel <- df[df$track == "uniswap" & df$faction == "Stayer" & df$cohort != "Returner", ]
cross_panel <- rbind(sushi_panel, uni_panel)

# Baseline: first observation per trajectory (start_time == 0)
cross_cs <- cross_panel %>%
  filter(start_time == 0) %>%
  distinct(trajectory_id, .keep_all = TRUE)

# cat(sprintf("Cross-sectional baseline: %d trajectories\n", nrow(cross_cs)))
# cat(sprintf("  Migrator (treated): %d\n", sum(cross_cs$is_migrator == 1)))
# cat(sprintf("  Stayer (control):   %d\n", sum(cross_cs$is_migrator == 0)))

# 3. Drop Dual-Role Users from Control
# Users who appear as both Migrator (in one pool) and Stayer (in another)
# create dependency in matching. Drop their Stayer trajectories from control.
mig_users <- unique(cross_cs$user_address[cross_cs$is_migrator == 1])
cross_cs$is_dual_role <- cross_cs$user_address %in% mig_users & cross_cs$is_migrator == 0
n_dual <- sum(cross_cs$is_dual_role)
# cat(sprintf("Dual-role Stayer trajectories dropped: %d\n", n_dual))

cross_cs_clean <- cross_cs[!cross_cs$is_dual_role, ]


# 4. Pre-Matching Balance
pre_smd <- function(var, treat, control) {
  m1 <- mean(treat[[var]], na.rm = TRUE)
  m0 <- mean(control[[var]], na.rm = TRUE)
  v1 <- var(treat[[var]], na.rm = TRUE)
  v0 <- var(control[[var]], na.rm = TRUE)
  d <- (m1 - m0) / sqrt((v1 + v0) / 2)
  return(c(mean_t = m1, mean_c = m0, smd = d))
}

treat_pre <- cross_cs_clean[cross_cs_clean$is_migrator == 1, ]
ctrl_pre <- cross_cs_clean[cross_cs_clean$is_migrator == 0, ]

vars <- c("is_new", "log_balance", "has_uni_mining")
pre_bal <- do.call(rbind, lapply(vars, function(v) pre_smd(v, treat_pre, ctrl_pre)))
rownames(pre_bal) <- vars


# 5. Propensity Score Matching (Caliper = 0.05 SD)
m.out <- matchit(is_migrator ~ is_new + log_balance + has_uni_mining,
  data = cross_cs_clean, method = "nearest",
  distance = "glm", caliper = 0.05
)

s.out <- summary(m.out)


# 6. Sample Attrition Report
matched_cs <- match.data(m.out)
# MatchIt summary$nn matrix may include ESS rows. Access by name.
nn_mat <- s.out$nn
n_treat_before <- nn_mat["All", "Treated"]
n_ctrl_before <- nn_mat["All", "Control"]
n_treat_after <- nn_mat["Matched", "Treated"]
n_ctrl_after <- nn_mat["Matched", "Control"]
if ("Unmatched" %in% rownames(nn_mat)) {
  n_treat_unmatched <- nn_mat["Unmatched", "Treated"]
  n_ctrl_unmatched <- nn_mat["Unmatched", "Control"]
} else {
  n_treat_unmatched <- n_treat_before - n_treat_after
  n_ctrl_unmatched <- n_ctrl_before - n_ctrl_after
}



# 7. Common Support Check
ps_treat <- m.out$distance[m.out$treat == 1]
ps_ctrl <- m.out$distance[m.out$treat == 0]
ps_min <- max(min(ps_treat), min(ps_ctrl))
ps_max <- min(max(ps_treat), max(ps_ctrl))


# 8. Love Plot
library(cobalt)
library(ggplot2)
p <- love.plot(m.out,
  binary = "std", abs = TRUE,
  var.order = "unadjusted",
  drop.distance = TRUE,
  threshold = 0.1,
  wrap = 50,
  colors = c("#E69F00", "#56B4E9"),
  title = NULL,
  xlab = "Absolute Standardized Mean Difference"
) +
  theme(axis.title.x = element_text(size = 10))

pdf("results/figures/Fig_PSM_LovePlot.pdf", width = 7, height = 5)
print(p)
dev.off()

png("results/figures/Fig_PSM_LovePlot.png", width = 7 * 300, height = 5 * 300, res = 300)
print(p)
dev.off()

# 9. Combined Pre/Post Balance Table
post_sum <- s.out$sum.matched
pre_sum <- s.out$sum.all

balance_rows <- c("is_new", "log_balance", "has_uni_mining")
var_labels <- c("New User", "Log(Initial Balance)", "Has UNI Mining")

bal_csv <- data.frame(
  Variable = var_labels,
  Mean_Treated = sapply(balance_rows, function(r) pre_sum[r, "Means Treated"]),
  Mean_Control = sapply(balance_rows, function(r) pre_sum[r, "Means Control"]),
  SMD_Before = sapply(balance_rows, function(r) pre_sum[r, "Std. Mean Diff."]),
  Mean_Treated_Post = sapply(balance_rows, function(r) post_sum[r, "Means Treated"]),
  Mean_Control_Post = sapply(balance_rows, function(r) post_sum[r, "Means Control"]),
  SMD_After = sapply(balance_rows, function(r) post_sum[r, "Std. Mean Diff."])
)
write.csv(bal_csv, "results/tables/Table_PSM_Balance.csv", row.names = FALSE)

tex_lines <- c(
  "\\begin{table}[ht]",
  "\\centering",
  "\\caption{Covariate Balance: Pre- and Post-Matching (1:1 NN, Caliper = 0.05 SD)}",
  "\\label{tab:psm_balance}",
  "\\begin{tabular}{lcccccc}",
  "\\hline",
  " & \\multicolumn{3}{c}{Before Matching} & \\multicolumn{3}{c}{After Matching} \\\\",
  "\\cmidrule(lr){2-4} \\cmidrule(lr){5-7}",
  "Variable & Treated & Control & SMD & Treated & Control & SMD \\\\",
  "\\hline"
)

for (i in seq_along(balance_rows)) {
  tex_lines <- c(tex_lines, sprintf(
    "%s & %.3f & %.3f & %.3f & %.3f & %.3f & %.3f \\\\",
    var_labels[i],
    bal_csv$Mean_Treated[i], bal_csv$Mean_Control[i], bal_csv$SMD_Before[i],
    bal_csv$Mean_Treated_Post[i], bal_csv$Mean_Control_Post[i], bal_csv$SMD_After[i]
  ))
}

tex_lines <- c(
  tex_lines,
  "\\hline",
  paste0(
    "N (Treated/Control) & \\multicolumn{3}{c}{",
    n_treat_before, " / ", n_ctrl_before,
    "} & \\multicolumn{3}{c}{",
    n_treat_after, " / ", n_ctrl_after, "} \\\\"
  ),
  "\\hline",
  "\\end{tabular}",
  "\\par\\vspace{4pt}",
  "\\footnotesize{Note: SMD = Standardized Mean Difference. Caliper = 0.05 SD of the logit of the propensity score.}",
  "\\end{table}"
)
writeLines(tex_lines, "results/tables/Table_PSM_Balance.tex")

# 10. Extract Matched Panel
matched_panel <- cross_panel[cross_panel$trajectory_id %in% matched_cs$trajectory_id, ]
write.csv(matched_panel, "cleaned_data/survival_matched_panel.csv", row.names = FALSE)


# 11. Cox Models on Matched Sample
fit_a <- coxph(Surv(start_time, stop_time, event) ~ is_migrator + cluster(user_address),
  data = matched_panel, robust = TRUE
)

fit_b <- coxph(Surv(start_time, stop_time, event) ~ is_migrator + is_new + log_balance + has_uni_mining + cluster(user_address),
  data = matched_panel, robust = TRUE
)

# 12. Sensitivity: Caliper = 0.2 SD (Austin 2011 recommendation)
m.out2 <- matchit(is_migrator ~ is_new + log_balance + has_uni_mining,
  data = cross_cs_clean, method = "nearest",
  distance = "glm", caliper = 0.2
)
s2 <- summary(m.out2)

matched_cs2 <- match.data(m.out2)
matched_panel2 <- cross_panel[cross_panel$trajectory_id %in% matched_cs2$trajectory_id, ]

fit_a2 <- coxph(Surv(start_time, stop_time, event) ~ is_migrator + cluster(user_address),
  data = matched_panel2, robust = TRUE
)
fit_b2 <- coxph(Surv(start_time, stop_time, event) ~ is_migrator + is_new + log_balance + has_uni_mining + cluster(user_address),
  data = matched_panel2, robust = TRUE
)

# 13. Extract Key Results
extract_cox <- function(fit, model_name) {
  s <- summary(fit)
  coefs <- s$coefficients
  ci <- s$conf.int
  data.frame(
    Model      = model_name,
    Variable   = rownames(coefs),
    HR         = round(coefs[, "exp(coef)"], 3),
    SE         = round(coefs[, "se(coef)"], 3),
    Robust_SE  = round(coefs[, "robust se"], 3),
    P_Value    = round(coefs[, "Pr(>|z|)"], 4),
    CI_Lower   = round(ci[, "lower .95"], 3),
    CI_Upper   = round(ci[, "upper .95"], 3),
    N          = s$n,
    N_Events   = s$nevent
  )
}

cox_results <- rbind(
  extract_cox(fit_a, "A_Matched_NoControls"),
  extract_cox(fit_b, "B_Matched_WithControls"),
  extract_cox(fit_a2, "A_Sens_Caliper0.2"),
  extract_cox(fit_b2, "B_Sens_Caliper0.2")
)
write.csv(cox_results, "results/tables/Table_PSM_Matched_Cox.csv", row.names = FALSE)

# 14. LaTeX Table for Matched Cox
hr_a <- round(summary(fit_a)$coefficients["is_migrator", "exp(coef)"], 3)
se_a <- round(summary(fit_a)$coefficients["is_migrator", "robust se"], 3)
p_a <- summary(fit_a)$coefficients["is_migrator", "Pr(>|z|)"]

hr_b_mig <- round(summary(fit_b)$coefficients["is_migrator", "exp(coef)"], 3)
se_b_mig <- round(summary(fit_b)$coefficients["is_migrator", "robust se"], 3)
p_b_mig <- summary(fit_b)$coefficients["is_migrator", "Pr(>|z|)"]

hr_b_new <- round(summary(fit_b)$coefficients["is_new", "exp(coef)"], 3)
se_b_new <- round(summary(fit_b)$coefficients["is_new", "robust se"], 3)
hr_b_bal <- round(summary(fit_b)$coefficients["log_balance", "exp(coef)"], 3)
se_b_bal <- round(summary(fit_b)$coefficients["log_balance", "robust se"], 3)
hr_b_uni <- round(summary(fit_b)$coefficients["has_uni_mining", "exp(coef)"], 3)
se_b_uni <- round(summary(fit_b)$coefficients["has_uni_mining", "robust se"], 3)

n_obs <- summary(fit_a)$n
n_events <- summary(fit_a)$nevent

star <- function(p) {
  ifelse(p < 0.01, "***", ifelse(p < 0.05, "**", ifelse(p < 0.10, "*", "")))
}

tex_cox <- c(
  "\\begin{table}[ht]",
  "\\centering",
  "\\caption{Matched Sample Cox Regression (Hazard Ratios)}",
  "\\label{tab:psm_cox}",
  "\\begin{tabular}{lcc}",
  "\\hline",
  " & (A) Matched & (B) Matched \\\\",
  " & (no controls) & (with controls) \\\\",
  "\\hline",
  sprintf(
    "Migrator (1=Yes) & %.3f%s & %.3f%s \\\\",
    hr_a, star(p_a), hr_b_mig, star(p_b_mig)
  ),
  sprintf(" & (%.3f) & (%.3f) \\\\", se_a, se_b_mig),
  sprintf("New User (1=Yes) & & %.3f \\\\", hr_b_new),
  sprintf(" & & (%.3f) \\\\", se_b_new),
  sprintf("Log(Initial Balance) & & %.3f%s \\\\", hr_b_bal, star(summary(fit_b)$coefficients["log_balance", "Pr(>|z|)"])),
  sprintf(" & & (%.3f) \\\\", se_b_bal),
  sprintf("Pool has UNI Mining & & %.3f%s \\\\", hr_b_uni, star(summary(fit_b)$coefficients["has_uni_mining", "Pr(>|z|)"])),
  sprintf(" & & (%.3f) \\\\", se_b_uni),
  "\\hline",
  sprintf("Observations & \\multicolumn{2}{c}{%s} \\\\", format(n_obs, big.mark = ",")),
  sprintf("Events & \\multicolumn{2}{c}{%s} \\\\", format(n_events, big.mark = ",")),
  "\\hline",
  "\\end{tabular}",
  "\\par\\vspace{4pt}",
  "\\footnotesize{",
  "Notes: Exponentiated coefficients (Hazard Ratios). Cluster-robust standard errors at the user address level in parentheses.",
  "1:1 Nearest Neighbor matching with caliper = 0.05 SD of the logit of the propensity score.",
  "Model (A) is the raw matched estimate. Model (B) additionally controls for matching covariates.",
  paste0("Significance: *** $p<$0.01, ** $p<$0.05, * $p<$0.10."),
  "}",
  "\\end{table}"
)
writeLines(tex_cox, "results/tables/Table_PSM_Matched_Cox.tex")

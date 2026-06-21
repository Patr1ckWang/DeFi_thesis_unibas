# Covers four dimensions of robustness:
#   1. Schoenfeld residuals test (PH assumption) — on BOTH M1 and M2
#   2. Time-stratified Cox (0–7d, 8–60d, 61+d) — on M2 specification
#   3. Subsample exclusions (Pure USDC-WETH)
#   4. AFT models (Weibull, LogNormal, LogLogistic) with cluster-robust SE


library(survival)
library(dplyr)
library(sandwich)
library(lmtest)

set.seed(42)


# 0. DATA LOADING AND PREPARATION

df <- read.csv("cleaned_data/survival_panel.csv")

# Convert key variables to numeric
df$is_migrator <- as.numeric(df$is_migrator)
df$is_new <- as.numeric(df$is_new)
df$log_balance <- as.numeric(df$log_balance)
df$has_uni_mining <- as.numeric(df$has_uni_mining)

# Cross-platform panel: Migrator on SLP vs Stayer on LP
# Fence-sitter faction automatically excluded by faction == "Migrator"/"Stayer"
df_sushi <- subset(df, track == "sushiswap" & faction == "Migrator" & cohort != "Returner")
df_uni <- subset(df, track == "uniswap" & faction == "Stayer" & cohort != "Returner")


df_cross <- rbind(df_sushi, df_uni)

# Select only variables used in models — no gas_gwei or platform_tvl_usd
# (those are not used in any formula; including them in na.omit would
#  silently drop ~802 obs with NA sushiswap_tvl_usd)
vars_cross <- c(
    "user_address", "start_time", "stop_time", "event",
    "is_migrator", "is_new", "log_balance",
    "has_uni_mining", "pool_name"
)

df_cross_clean <- na.omit(df_cross[, vars_cross])



# MODEL FORMULAS
f_m2 <- "Surv(start_time, stop_time, event) ~ is_migrator + is_new + log_balance + has_uni_mining + cluster(user_address)"
f_m1 <- "Surv(start_time, stop_time, event) ~ is_migrator + is_new + log_balance + cluster(user_address)"



# 1. SCHOENFELD RESIDUALS TEST

# 1A: M2 specification (primary — matched to run_heterogeneity.R baseline)
fit_schoenfeld_m2 <- coxph(as.formula(f_m2), data = df_cross_clean, robust = TRUE)
zph_m2 <- cox.zph(fit_schoenfeld_m2)

# 1B: M1 specification (sensitivity — confirms PH violation is not driven by has_uni_mining)
fit_schoenfeld_m1 <- coxph(as.formula(f_m1), data = df_cross_clean, robust = TRUE)
zph_m1 <- cox.zph(fit_schoenfeld_m1)

# Save M2 Schoenfeld test (primary)
sink("results/tables/Model_Robust_Schoenfeld_Test.txt")
cat("Schoenfeld Residuals Test — M2 Specification\n")
cat("Model: Surv(start, stop, event) ~ is_migrator + is_new + log_balance + has_uni_mining\n")
cat("Cluster-robust SE at user_address level\n\n")
print(zph_m2)
cat("\n\nReference (M1, no has_uni_mining):\n")
print(zph_m1)
sink()

# Generate Schoenfeld residual plot for is_migrator (M2)
png("results/figures/Fig_Robust_Schoenfeld_Migrator.png", width = 2400, height = 1800, res = 300)
plot(zph_m2,
    var = "is_migrator",
    main = "",
    xlab = "Time (days)", ylab = "Beta(t) for is_migrator"
)
abline(h = coef(fit_schoenfeld_m2)["is_migrator"], lty = 2, col = "red")
dev.off()

pdf("results/figures/Fig_Robust_Schoenfeld_Migrator.pdf", width = 8, height = 6)
plot(zph_m2,
    var = "is_migrator",
    main = "",
    xlab = "Time (days)", ylab = "Beta(t) for is_migrator"
)
abline(h = coef(fit_schoenfeld_m2)["is_migrator"], lty = 2, col = "red")
dev.off()


# 2. TIME-STRATIFIED COX

# Helper: safely fit Cox and extract all coefficient summaries
fit_stratum <- function(data, label) {
    fit <- tryCatch(
        {
            coxph(as.formula(f_m2), data = data, robust = TRUE)
        },
        error = function(e) {
            return(NULL)
        }
    )
    return(fit)
}

# Stratum 1: 0–7 days (pre-UNI defense, pure mercenary window)
df_0_7 <- subset(df_cross_clean, stop_time <= 7)
fit_0_7 <- fit_stratum(df_0_7, "0–7 Days (Pre-UNI)")

# Stratum 2: 8–60 days (UNI mining period)
df_8_60 <- subset(df_cross_clean, start_time >= 7 & stop_time <= 60)
fit_8_60 <- fit_stratum(df_8_60, "8–60 Days (UNI Mining)")

# Stratum 3: >60 days (post-UNI mining)
df_60_plus <- subset(df_cross_clean, start_time >= 60)
fit_60_plus <- fit_stratum(df_60_plus, ">60 Days (Post-Mining)")

# Extract coefficient vector with SE and p for all covariates
extract_coef_row <- function(fit, stratum_label, covariate) {
    if (is.null(fit) || !(covariate %in% names(coef(fit)))) {
        return(c(NA, NA, NA, NA))
    }
    s <- summary(fit)
    coef_val <- coef(fit)[covariate]
    if ("robust se" %in% colnames(s$coefficients)) {
        se_val <- s$coefficients[covariate, "robust se"]
    } else {
        se_val <- s$coefficients[covariate, "se(coef)"]
    }
    hr_val <- exp(coef_val)
    p_val <- s$coefficients[covariate, "Pr(>|z|)"]
    return(c(coef_val, se_val, hr_val, p_val))
}

# Build comprehensive time-stratified table
covariates <- c("is_migrator", "is_new", "log_balance", "has_uni_mining")
fits <- list("0-7 Days" = fit_0_7, "8-60 Days" = fit_8_60, ">60 Days" = fit_60_plus)
n_obs_list <- list(
    "0-7 Days"  = if (!is.null(fit_0_7)) summary(fit_0_7)$n else NA,
    "8-60 Days" = if (!is.null(fit_8_60)) summary(fit_8_60)$n else NA,
    ">60 Days"  = if (!is.null(fit_60_plus)) summary(fit_60_plus)$n else NA
)
n_ev_list <- list(
    "0-7 Days"  = if (!is.null(fit_0_7)) summary(fit_0_7)$nevent else NA,
    "8-60 Days" = if (!is.null(fit_8_60)) summary(fit_8_60)$nevent else NA,
    ">60 Days"  = if (!is.null(fit_60_plus)) summary(fit_60_plus)$nevent else NA
)

ts_rows <- list()
for (stratum in names(fits)) {
    for (cov in covariates) {
        row <- extract_coef_row(fits[[stratum]], stratum, cov)
        ts_rows[[length(ts_rows) + 1]] <- data.frame(
            Stratum = stratum,
            Covariate = cov,
            Coef = as.numeric(row[1]),
            SE = as.numeric(row[2]),
            HR = as.numeric(row[3]),
            P_val = as.numeric(row[4]),
            N = as.integer(n_obs_list[[stratum]]),
            Events = as.integer(n_ev_list[[stratum]]),
            stringsAsFactors = FALSE
        )
    }
}
ts_res <- do.call(rbind, ts_rows)
write.csv(ts_res, "results/tables/Model_Robust_TimeStratified.csv", row.names = FALSE)


# 3. SUBSAMPLE EXCLUSIONS
# 3A: Pure USDC-WETH (single-pool robustness)
#     has_uni_mining is pool-invariant within a single pool → use f_m1
df_usdc <- subset(df_cross_clean, pool_name == "USDC-WETH")
fit_usdc <- coxph(as.formula(f_m1), data = df_usdc, robust = TRUE)
s_usdc <- summary(fit_usdc)


# 3B: No Harvest subsample — NOT APPLICABLE


# Save subsample results
ss_res <- data.frame(
    Sample   = c("Pure USDC-WETH"),
    N        = c(s_usdc$n),
    Events   = c(s_usdc$nevent),
    Coef     = c(coef(fit_usdc)["is_migrator"]),
    HR       = c(exp(coef(fit_usdc)["is_migrator"])),
    P_val    = c(s_usdc$coefficients["is_migrator", "Pr(>|z|)"])
)
write.csv(ss_res, "results/tables/Model_Robust_Subsamples.csv", row.names = FALSE)



# 4. AFT MODELS (Accelerated Failure Time — PH Assumption Remedy)

# Collapse counting-process panel to cross-section (one row per trajectory)
# NOTE: survreg() does not support start-stop Surv objects.
df_aft_cs <- df_cross_clean %>%
    group_by(user_address, pool_name) %>%
    summarise(
        duration     = max(stop_time),
        event        = max(event),
        is_migrator  = first(is_migrator),
        is_new       = first(is_new),
        log_balance  = first(log_balance),
        .groups      = "drop"
    )



# Fit AFT model with given distribution
run_aft <- function(distribution, data) {
    f <- Surv(duration, event) ~ is_migrator + is_new + log_balance
    fit <- tryCatch(
        {
            survreg(f, data = data, dist = distribution)
        },
        error = function(e) {
            
            return(NULL)
        }
    )
    return(fit)
}

# Extract coefficients with cluster-robust SE at user_address level
extract_aft <- function(fit, label, data) {
    if (is.null(fit)) {
        return(NULL)
    }

    # Cluster-robust variance-covariance matrix at user_address level
    vcov_cl <- tryCatch(
        {
            vcovCL(fit, cluster = data$user_address, type = "HC0")
        },
        error = function(e) {
            
            return(NULL)
        }
    )

    if (!is.null(vcov_cl)) {
        ct <- coeftest(fit, vcov. = vcov_cl)
        mig_coef <- ct["is_migrator", "Estimate"]
        mig_se <- ct["is_migrator", "Std. Error"]
        mig_p <- ct["is_migrator", "Pr(>|z|)"]
        se_type <- "cluster (user_address)"
    } else {
        # Fallback: survreg with robust = TRUE (Huber-White, non-clustered)
        fit_r <- survreg(formula(fit), data = data, dist = fit$dist, robust = TRUE)
        s <- summary(fit_r)
        mig_coef <- s$table["is_migrator", "Value"]
        mig_se <- s$table["is_migrator", "Std. Error"]
        mig_p <- s$table["is_migrator", "p"]
        se_type <- "robust (Huber-White)"
    }

    

    return(data.frame(
        Model              = label,
        is_migrator_coef   = mig_coef,
        is_migrator_SE     = mig_se,
        is_migrator_AF     = exp(mig_coef),
        is_migrator_p      = mig_p,
        LogLik             = as.numeric(logLik(fit)),
        SE_type            = se_type,
        stringsAsFactors   = FALSE
    ))
}

# Fit all three AFT distributions
aft_weibull <- run_aft("weibull", df_aft_cs)
aft_lognormal <- run_aft("lognormal", df_aft_cs)
aft_loglogistic <- run_aft("loglogistic", df_aft_cs)


# Collect results (no <<- side effects)
aft_list <- list(
    extract_aft(aft_weibull, "Weibull", df_aft_cs),
    extract_aft(aft_lognormal, "LogNormal", df_aft_cs),
    extract_aft(aft_loglogistic, "LogLogistic", df_aft_cs)
)
aft_results <- do.call(rbind, aft_list[!sapply(aft_list, is.null)])


write.csv(aft_results, "results/tables/Model_Robust_AFT.csv", row.names = FALSE)

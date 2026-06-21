"""
Cross-pool "schizophrenia" analysis — users with different faction
"""

import pandas as pd


df = pd.read_csv("cleaned_data/users_enhanced.csv")

df_f = df[df["faction"].notna()].copy()
n_total = df_f["user_address"].nunique()

#Multi-pool overview 
pool_counts = df_f.groupby("user_address")["pool_name"].nunique()
multi_pool_ix = pool_counts[pool_counts > 1].index
n_multi = len(multi_pool_ix)

#Mixed faction users (any combination) 
faction_sets = df_f.groupby("user_address")["faction"].apply(set)
mixed_all = faction_sets[faction_sets.apply(len) > 1]
n_mixed_any = len(mixed_all)


combos = mixed_all.groupby(mixed_all.apply(lambda x: "+".join(sorted(x))))

# Migrator + Stayer subset  
migstay_users = mixed_all[mixed_all == {"Migrator", "Stayer"}].index.tolist()

# Exclude Returner cohort 
core_migstay = [
    u
    for u in migstay_users
    if df_f[df_f["user_address"] == u]["cohort"].iloc[0] != "Returner"
]
n_core_dual = len(core_migstay)
core_dual_stayer_traj = df_f[
    (df_f["user_address"].isin(core_migstay))
    & (df_f["faction"] == "Stayer")
]
n_dual_stayer_traj = len(core_dual_stayer_traj)
n_stayer_total = (df_f["faction"] == "Stayer").sum()

summary_rows = []
for combo_label, users in combos:
    ulist = users.index.tolist()
    n_ret = sum(
        1
        for u in ulist
        if any(df_f[df_f["user_address"] == u]["cohort"] == "Returner")
    )
    summary_rows.append(
        {
            "combination": combo_label,
            "n_users": len(ulist),
            "n_returner_included": n_ret,
            "n_users_excl_returner": len(ulist) - n_ret,
        }
    )

summary = pd.DataFrame(summary_rows)
summary.to_csv("results/tables/Table_MixedFactions_Summary.csv", index=False)
print(f"\nSaved: results/tables/Table_MixedFactions_Summary.csv")

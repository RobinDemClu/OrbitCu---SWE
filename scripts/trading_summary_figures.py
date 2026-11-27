"""Summary figures for the trading chapter: correlations, training vs test Sharpe, placebo searches,
event studies. Reads the result files written by the trading_* scripts."""
import os, numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
D = os.environ.get("ORBITCU_DATA", "/mnt/project-files/data/").rstrip("/") + "/trading/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "latex", "figures")
BLUE, RED, GREY = "#1f5fa8", "#c0392b", "#888888"
def clean(ax):
    ax.spines[["top", "right"]].set_visible(False)

# 1. correlations: what the satellite index is linked to (rank correlation, 95 % interval from Fisher z)
C = [("Official statistics", "China running share vs NBS output deviation", 0.43, 68),
     ("Official statistics", "China supply surprise vs NBS surprise", 0.33, 68),
     ("Market", "NBS surprise vs copper on release day", 0.02, 68),
     ("Market", "World share change vs COMEX, 4 weeks", 0.05, 95),
     ("Market", "World share change vs COMEX, 13 weeks", -0.12, 29),
     ("Market", "World share change vs LME stocks, 4 weeks", 0.06, 95),
     ("Market", "World share change vs LME spread, 4 weeks", 0.02, 95),
     ("Market", "Supply surprise vs next-week COMEX return", -0.10, 200),
     ("Market", "Company output surprise vs share reaction", -0.06, 66),
     ("Market", "China surprise vs SHFE/LME ratio, next 4 weeks", -0.20, 87),
     ("Market", "China share vs SHFE/LME ratio, same 13 weeks", 0.42, 30),
     ("Market", "China surprise vs SHFE calendar spread, next 8 weeks", -0.30, 43),
     ("Market", "China surprise vs Chinese sulphuric acid, next 30 days", -0.24, 53)]
fig, ax = plt.subplots(figsize=(8.2, 4.8))
for i, (grp, lab, r, n) in enumerate(C[::-1]):
    lo, hi = np.tanh(np.arctanh(r) + np.array([-1, 1]) * 1.96 / np.sqrt(n - 3))
    col = BLUE if grp == "Official statistics" else RED
    ax.plot([lo, hi], [i, i], color=col, lw=2, alpha=.5); ax.plot(r, i, "o", color=col)
labels = [f"{lab} (n={n})" for _, lab, _, n in C[::-1]]
ax.axvline(0, color=GREY, lw=.8); ax.set_xlim(-0.6, 0.8); ax.set_yticks(range(len(C))); ax.set_yticklabels(labels, fontsize=7.5)
ax.set_xlabel("Rank correlation with 95 % interval (blue: official statistics, red: market)")
clean(ax); plt.tight_layout(); plt.savefig(os.path.join(OUT, "trading_correlations.png"), dpi=160); plt.close()

# 2. training vs test Sharpe for the four large searches
S = [("Copper futures (COMEX, LME)", "strategy_search_results.csv"), ("Copper fabricator shares", "fabricator_search_results.csv"),
     ("Copper vs aluminium and zinc", "relative_value_search.csv"), ("Shanghai vs London", "shfe_lme_search.csv")]
fig, axs = plt.subplots(2, 2, figsize=(7.2, 6.2), sharex=True, sharey=True)
for ax, (title, f) in zip(axs.flat, S):
    R = pd.read_csv(D + f)
    if "family" in R: R = R[R.family == "satellite"]
    else: R = R[R.signal.str.startswith("sat_")]
    ax.scatter(R.sharpe_train, R.sharpe_test, s=4, alpha=.35, color=BLUE)
    top = R.nlargest(10, "sharpe_train"); ax.scatter(top.sharpe_train, top.sharpe_test, s=14, color=RED)
    ax.axhline(0, color=GREY, lw=.7); ax.axvline(0, color=GREY, lw=.7)
    ax.set_title(f"{title}\n{len(R)} satellite strategies", fontsize=8.5); clean(ax)
for ax in axs[1]: ax.set_xlabel("Sharpe 2019-2022 (training)")
for ax in axs[:, 0]: ax.set_ylabel("Sharpe 2023-2026 (test)")
plt.tight_layout(); plt.savefig(os.path.join(OUT, "trading_train_test.png"), dpi=160); plt.close()

# 3. placebo searches: best full-period Sharpe found with time-shifted signals vs the real one
def real_best(f):
    R = pd.read_csv(D + f); R = R[R.signal.str.startswith("sat_")]; return R.sharpe_all.max()
P = [("Copper futures", "strategy_search_placebo.csv", "best_all", real_best("strategy_search_results.csv")),
     ("Fabricator shares", "fabricator_search_placebo.csv", "best_all", real_best("fabricator_search_results.csv")),
     ("Shanghai vs London", "shfe_lme_placebo.csv", "best_all", real_best("shfe_lme_search.csv"))]
fig, axs = plt.subplots(1, 3, figsize=(7.2, 2.6), sharey=True)
for ax, (title, f, col, real) in zip(axs, P):
    x = pd.read_csv(D + f)[col]
    ax.hist(x, bins=12, color=GREY, alpha=.7); ax.axvline(real, color=RED, lw=2)
    ax.set_title(f"{title}\n{np.mean(x >= real):.0%} of placebos beat the real best ({real:.2f})", fontsize=8); ax.set_xlabel("best Sharpe 2019-2026"); clean(ax)
axs[0].set_ylabel("placebo searches"); plt.tight_layout(); plt.savefig(os.path.join(OUT, "trading_placebo.png"), dpi=160); plt.close()

# 4. event studies: copper return after satellite-dated stops and restarts (excess over the average week)
E = pd.read_csv(D + "event_study.csv"); E = E[E.group == "all"]
F = pd.read_csv(D + "fast_alert_tests.csv"); F = F[(F.group == "all alerts") & (F.asset == "COMEX copper")]
fig, axs = plt.subplots(1, 2, figsize=(7.2, 2.9))
for k, (inst, col) in enumerate([("HG", BLUE), ("LME", "#6a9fd8")]):
    for j, ev in enumerate(["stop", "restart"]):
        q = E[(E.inst == inst) & (E.event == ev)]
        xs = np.arange(len(q)) + j * 5 + k * 0.38
        b = axs[0].bar(xs, q.excess_pct, width=.36, color=col if ev == "stop" else (RED if inst == "HG" else "#e08a7f"))
axs[0].set_xticks([1.7, 6.7]); axs[0].set_xticklabels([f"after stops (n={E[E.event=='stop'].n.max()})", f"after restarts (n={E[E.event=='restart'].n.max()})"], fontsize=8)
axs[0].axhline(0, color=GREY, lw=.7); axs[0].set_ylabel("excess copper return, %"); axs[0].set_title("Weekly events: 1, 2, 4, 8 weeks later\n(COMEX dark, LME light)", fontsize=8); clean(axs[0])
for j, ev in enumerate(["stop", "restart"]):
    q = F[F.event == ev]; axs[1].plot(q.days, q.mean_pct, "o-", color=BLUE if ev == "stop" else RED, label=f"{ev}s (n={q.n.max()})")
axs[1].axhline(0, color=GREY, lw=.7); axs[1].set_xlabel("trading days after the alert"); axs[1].set_ylabel("mean COMEX return, %")
axs[1].set_title("Fast VIIRS alerts", fontsize=8); axs[1].legend(frameon=False, fontsize=7); clean(axs[1])
plt.tight_layout(); plt.savefig(os.path.join(OUT, "trading_events.png"), dpi=160); plt.close()
print("figures written")

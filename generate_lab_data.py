"""
Simulated clinical laboratory dataset generator
------------------------------------------------
Creates ~50,000 synthetic test records (NO real patient data) with realistic
patterns for a portfolio project on turnaround time (TAT) and QC analysis.

Built-in patterns you can "discover" in your analysis:
  1. Night shift has slower TAT (fewer staff)
  2. STAT samples are faster than routine ones
  3. Microbiology/culture tests are far slower than chemistry or hematology
  4. Sample rejection is higher at night and varies by sample type
  5. QC failures cluster on one analyzer, and QC failures add repeat-test delay
  6. Volume peaks in the morning and dips overnight
  7. Weekends are slightly slower

Outputs:
  lab_data.csv  - flat file for Excel / Power BI / Tableau
  lab_data.db   - SQLite database (table: lab_tests) for SQL practice

Usage:
  pip install pandas numpy
  python generate_lab_data.py
"""

import sqlite3
import numpy as np
import pandas as pd

SEED = 42
N_RECORDS = 50_000
START_DATE = "2025-01-01"
END_DATE = "2025-12-31"

rng = np.random.default_rng(SEED)

# ---------------------------------------------------------------------------
# 1. Test catalogue: name, department, sample type, analyzer, base TAT (min),
#    relative volume weight
# ---------------------------------------------------------------------------
TESTS = [
    # name,               dept,                sample,   analyzer,  base_tat, weight
    ("Full Blood Count",  "Hematology",        "EDTA",   "HEM-01",   45,       18),
    ("ESR",               "Hematology",        "EDTA",   "HEM-01",   60,       5),
    ("Malaria Parasite",  "Parasitology",      "EDTA",   "MICRO-01", 50,       10),
    ("Urea & Electrolytes", "Clinical Chemistry", "Serum", "CHEM-01", 90,       14),
    ("Liver Function",    "Clinical Chemistry", "Serum", "CHEM-02",  100,      9),
    ("Fasting Glucose",   "Clinical Chemistry", "Plasma", "CHEM-01", 60,       10),
    ("Lipid Profile",     "Clinical Chemistry", "Serum", "CHEM-02",  110,      6),
    ("Widal Test",        "Serology",          "Serum",  "SERO-01",  75,       7),
    ("HIV Screening",     "Serology",          "Serum",  "SERO-01",  80,       5),
    ("Urinalysis",        "Urinalysis",        "Urine",  "URI-01",   40,       8),
    ("Urine Culture",     "Microbiology",      "Urine",  "MICRO-01", 2200,     4),
    ("Blood Culture",     "Microbiology",      "Blood",  "MICRO-01", 3000,     2),
    ("Prothrombin Time",  "Coagulation",       "Citrate", "COAG-01", 70,       2),
]
test_df = pd.DataFrame(
    TESTS, columns=["test_name", "department", "sample_type", "analyzer", "base_tat", "weight"]
)
test_probs = test_df["weight"] / test_df["weight"].sum()

# ---------------------------------------------------------------------------
# 2. Timestamps: pick a day, then an hour using a realistic volume curve
# ---------------------------------------------------------------------------
hourly_volume = np.array([
    1, 1, 1, 1, 1, 2,        # 00-05  (very quiet)
    4, 8, 12, 12, 11, 9,     # 06-11  (morning peak)
    7, 6, 6, 6, 5, 5,        # 12-17
    4, 4, 3, 3, 2, 2,        # 18-23
], dtype=float)
hour_probs = hourly_volume / hourly_volume.sum()

dates = pd.date_range(START_DATE, END_DATE, freq="D")
day_idx = rng.integers(0, len(dates), N_RECORDS)
hours = rng.choice(24, size=N_RECORDS, p=hour_probs)
minutes = rng.integers(0, 60, N_RECORDS)

received_time = (
    dates[day_idx]
    + pd.to_timedelta(hours, unit="h")
    + pd.to_timedelta(minutes, unit="m")
)

# ---------------------------------------------------------------------------
# 3. Build the base table
# ---------------------------------------------------------------------------
test_choice = rng.choice(len(test_df), size=N_RECORDS, p=test_probs)
df = test_df.iloc[test_choice].reset_index(drop=True)
df.insert(0, "sample_id", [f"LAB{100000 + i}" for i in range(N_RECORDS)])
df["received_time"] = received_time
df = df.sort_values("received_time").reset_index(drop=True)
df["sample_id"] = [f"LAB{100000 + i}" for i in range(len(df))]

df["hour"] = df["received_time"].dt.hour
df["day_of_week"] = df["received_time"].dt.day_name()
df["is_weekend"] = df["received_time"].dt.dayofweek >= 5


def assign_shift(h):
    if 7 <= h < 15:
        return "Morning"
    if 15 <= h < 23:
        return "Evening"
    return "Night"


df["shift"] = df["hour"].apply(assign_shift)

# Priority: mostly routine, some urgent, some STAT
df["priority"] = rng.choice(["Routine", "Urgent", "STAT"], size=len(df), p=[0.70, 0.20, 0.10])

# Technicians per shift (night has fewer people)
tech_pool = {
    "Morning": [f"TECH{n:02d}" for n in range(1, 9)],
    "Evening": [f"TECH{n:02d}" for n in range(9, 14)],
    "Night":   [f"TECH{n:02d}" for n in range(14, 17)],
}
df["technician_id"] = [rng.choice(tech_pool[s]) for s in df["shift"]]

# Requesting ward / source
df["source"] = rng.choice(
    ["Outpatient", "Inpatient", "Emergency", "Antenatal"],
    size=len(df),
    p=[0.45, 0.25, 0.20, 0.10],
)

# ---------------------------------------------------------------------------
# 4. Sample rejection (higher at night, varies by sample type)
# ---------------------------------------------------------------------------
base_reject = {"EDTA": 0.025, "Serum": 0.035, "Plasma": 0.03,
               "Urine": 0.04, "Blood": 0.02, "Citrate": 0.05}
shift_reject_mult = {"Morning": 1.0, "Evening": 1.2, "Night": 1.8}

reject_prob = (
    df["sample_type"].map(base_reject) * df["shift"].map(shift_reject_mult)
)
df["rejected"] = rng.random(len(df)) < reject_prob


def reject_reason(sample_type):
    options = {
        "EDTA":    (["Clotted sample", "Insufficient volume", "Mislabeled", "Wrong tube"], [0.45, 0.25, 0.15, 0.15]),
        "Serum":   (["Hemolyzed", "Insufficient volume", "Mislabeled", "Lipemic"],         [0.50, 0.20, 0.15, 0.15]),
        "Plasma":  (["Hemolyzed", "Insufficient volume", "Mislabeled", "Wrong tube"],      [0.40, 0.25, 0.15, 0.20]),
        "Urine":   (["Contaminated", "Insufficient volume", "Mislabeled", "Delayed delivery"], [0.40, 0.20, 0.15, 0.25]),
        "Blood":   (["Contaminated", "Insufficient volume", "Mislabeled", "Wrong bottle"], [0.45, 0.20, 0.15, 0.20]),
        "Citrate": (["Underfilled tube", "Clotted sample", "Mislabeled", "Insufficient volume"], [0.50, 0.20, 0.15, 0.15]),
    }
    reasons, probs = options[sample_type]
    return rng.choice(reasons, p=probs)


df["rejection_reason"] = None
rej_idx = df.index[df["rejected"]]
df.loc[rej_idx, "rejection_reason"] = [reject_reason(s) for s in df.loc[rej_idx, "sample_type"]]

# ---------------------------------------------------------------------------
# 5. QC status (CHEM-02 has a quality problem; night is slightly worse)
# ---------------------------------------------------------------------------
analyzer_qc_fail = {
    "HEM-01": 0.015, "CHEM-01": 0.020, "CHEM-02": 0.060,   # CHEM-02 = trouble
    "MICRO-01": 0.015, "SERO-01": 0.020, "URI-01": 0.010, "COAG-01": 0.025,
}
qc_prob = df["analyzer"].map(analyzer_qc_fail) * np.where(df["shift"] == "Night", 1.3, 1.0)
df["qc_failed"] = (rng.random(len(df)) < qc_prob) & (~df["rejected"])
df["qc_status"] = np.where(df["qc_failed"], "Fail - Repeated", "Pass")
df.loc[df["rejected"], "qc_status"] = "N/A"

# ---------------------------------------------------------------------------
# 6. Turnaround time (minutes)
# ---------------------------------------------------------------------------
shift_mult = {"Morning": 1.0, "Evening": 1.1, "Night": 1.35}
priority_mult = {"Routine": 1.0, "Urgent": 0.75, "STAT": 0.5}

tat_mean = (
    df["base_tat"]
    * df["shift"].map(shift_mult)
    * df["priority"].map(priority_mult)
    * np.where(df["is_weekend"], 1.12, 1.0)
)

# Log-normal noise gives realistic right-skew (a few very late results)
noise = rng.lognormal(mean=0.0, sigma=0.30, size=len(df))
tat = tat_mean * noise

# QC failure forces a repeat run: adds 30-90 minutes
tat = tat + np.where(df["qc_failed"], rng.integers(30, 91, len(df)), 0)

df["tat_minutes"] = tat.round(0).astype("float")
df.loc[df["rejected"], "tat_minutes"] = np.nan   # rejected samples never get a result

df["result_time"] = df["received_time"] + pd.to_timedelta(df["tat_minutes"], unit="m")

# Target TAT (minutes) per priority, then flag whether the target was met
tat_target = {"Routine": df["base_tat"] * 1.5, "Urgent": df["base_tat"] * 1.2, "STAT": df["base_tat"] * 0.9}
df["target_tat"] = np.select(
    [df["priority"] == "Routine", df["priority"] == "Urgent", df["priority"] == "STAT"],
    [tat_target["Routine"], tat_target["Urgent"], tat_target["STAT"]],
).round(0)
df["met_target"] = np.where(df["rejected"], np.nan, (df["tat_minutes"] <= df["target_tat"]).astype(float))

# ---------------------------------------------------------------------------
# 7. Final tidy-up and export
# ---------------------------------------------------------------------------
final_cols = [
    "sample_id", "test_name", "department", "sample_type", "analyzer",
    "source", "priority", "shift", "technician_id",
    "received_time", "result_time", "tat_minutes", "target_tat", "met_target",
    "qc_status", "rejected", "rejection_reason", "day_of_week", "is_weekend",
]
out = df[final_cols].copy()
out["rejected"] = out["rejected"].astype(int)
out["is_weekend"] = out["is_weekend"].astype(int)

out.to_csv("lab_data.csv", index=False)

with sqlite3.connect("lab_data.db") as conn:
    out.to_sql("lab_tests", conn, if_exists="replace", index=False)

# ---------------------------------------------------------------------------
# 8. Quick sanity checks (also a preview of your insights)
# ---------------------------------------------------------------------------
print(f"Rows generated: {len(out):,}")
print(f"Overall rejection rate: {out['rejected'].mean():.1%}")
print("\nAvg TAT (min) by shift, excluding microbiology:")
print(out[(out['department'] != 'Microbiology')].groupby("shift")["tat_minutes"].mean().round(0))
print("\nQC fail rate by analyzer:")
print((out[out['qc_status'] != 'N/A'].assign(f=lambda d: d['qc_status'].str.startswith('Fail'))
       .groupby("analyzer")["f"].mean() * 100).round(1))
print("\nFiles saved: lab_data.csv, lab_data.db")

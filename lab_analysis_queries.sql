-- ----------------------------------------------------------------------------
-- SECTION 1: TARGET TAT COMPLIANCE
-- ----------------------------------------------------------------------------

-- 1.1 Overall % of samples meeting target TAT
SELECT
    COUNT(*) AS resulted_samples,
    SUM(met_target) AS met_target,
    ROUND(100.0 * SUM(met_target) / COUNT(*), 1) AS pct_met_target
FROM lab_tests
WHERE rejected = 0;


-- 1.2 Compliance by priority level
SELECT
    priority,
    COUNT(*) AS samples,
    ROUND(AVG(tat_minutes), 1) AS avg_tat_min,
    ROUND(100.0 * SUM(met_target) / COUNT(*), 1) AS pct_met_target
FROM lab_tests
WHERE rejected = 0
GROUP BY priority
ORDER BY CASE priority WHEN 'STAT' THEN 1 WHEN 'Urgent' THEN 2 ELSE 3 END;


-- 1.3 Compliance by department and shift (find where targets are missed)
SELECT
    department,
    shift,
    COUNT(*) AS samples,
    ROUND(100.0 * SUM(met_target) / COUNT(*), 1) AS pct_met_target
FROM lab_tests
WHERE rejected = 0
GROUP BY department, shift
ORDER BY department, pct_met_target;


-- 1.4 Underperforming tests: those meeting target less often than the lab average
SELECT
    test_name,
    department,
    COUNT(*) AS samples,
    ROUND(100.0 * SUM(met_target) / COUNT(*), 1) AS pct_met_target
FROM lab_tests
WHERE rejected = 0
GROUP BY test_name, department
HAVING 100.0 * SUM(met_target) / COUNT(*) <
       (SELECT 100.0 * SUM(met_target) / COUNT(*) FROM lab_tests WHERE rejected = 0)
ORDER BY pct_met_target ASC;


-- ----------------------------------------------------------------------------
-- SECTION 2: MONTHLY TRENDS (WINDOW FUNCTIONS)
-- ----------------------------------------------------------------------------

-- 2.1 Monthly volume and average TAT, with month-over-month change 
WITH monthly AS (
    SELECT
        strftime('%Y-%m', received_time) AS month,
        COUNT(*) AS total_samples,
        ROUND(AVG(tat_minutes), 1) AS avg_tat_min
    FROM lab_tests
    WHERE rejected = 0 AND department <> 'Microbiology'
    GROUP BY month
)
SELECT
    month,
    total_samples,
    avg_tat_min,
    LAG(avg_tat_min) OVER (ORDER BY month) AS prev_month_tat,
    ROUND(avg_tat_min - LAG(avg_tat_min) OVER (ORDER BY month), 1) AS tat_change_min,
    ROUND(100.0 * (avg_tat_min - LAG(avg_tat_min) OVER (ORDER BY month))
          / LAG(avg_tat_min) OVER (ORDER BY month), 1) AS tat_change_pct
FROM monthly
ORDER BY month;


-- 2.2 Three-month moving average of TAT 
WITH monthly AS (
    SELECT
        strftime('%Y-%m', received_time) AS month,
        AVG(tat_minutes) AS avg_tat
    FROM lab_tests
    WHERE rejected = 0 AND department <> 'Microbiology'
    GROUP BY month
)
SELECT
    month,
    ROUND(avg_tat, 1) AS avg_tat_min,
    ROUND(AVG(avg_tat) OVER (
        ORDER BY month
        ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
    ), 1) AS moving_avg_3m
FROM monthly
ORDER BY month;


-- 2.3 Monthly rejections with running (cumulative) total
WITH monthly_rej AS (
    SELECT
        strftime('%Y-%m', received_time) AS month,
        COUNT(*) AS total_samples,
        SUM(rejected) AS rejected_samples
    FROM lab_tests
    GROUP BY month
)
SELECT
    month,
    total_samples,
    rejected_samples,
    ROUND(100.0 * rejected_samples / total_samples, 2) AS rejection_rate_pct,
    SUM(rejected_samples) OVER (ORDER BY month) AS cumulative_rejections
FROM monthly_rej
ORDER BY month;


-- 2.4 Monthly QC failure rate per analyzer, ranked worst to best each month
WITH qc_monthly AS (
    SELECT
        strftime('%Y-%m', received_time) AS month,
        analyzer,
        COUNT(*) AS runs,
        ROUND(100.0 * SUM(CASE WHEN qc_status LIKE 'Fail%' THEN 1 ELSE 0 END)
              / COUNT(*), 2) AS qc_fail_pct
    FROM lab_tests
    WHERE qc_status <> 'N/A'
    GROUP BY month, analyzer
)
SELECT
    month,
    analyzer,
    qc_fail_pct,
    RANK() OVER (PARTITION BY month ORDER BY qc_fail_pct DESC) AS worst_rank
FROM qc_monthly
ORDER BY month, worst_rank;


-- ----------------------------------------------------------------------------
-- SECTION 3: RANKING AND COMPARISON
-- ----------------------------------------------------------------------------

-- 3.1 Slowest 3 tests in each department 
WITH test_avg AS (
    SELECT
        department,
        test_name,
        ROUND(AVG(tat_minutes), 1) AS avg_tat_min
    FROM lab_tests
    WHERE rejected = 0
    GROUP BY department, test_name
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY department ORDER BY avg_tat_min DESC) AS rn
    FROM test_avg
)
SELECT department, test_name, avg_tat_min
FROM ranked
WHERE rn <= 3
ORDER BY department, rn;


-- 3.2 How much slower is each shift than the overall average? 
WITH shift_avg AS (
    SELECT shift, AVG(tat_minutes) AS avg_tat
    FROM lab_tests
    WHERE rejected = 0 AND department <> 'Microbiology'
    GROUP BY shift
)
SELECT
    shift,
    ROUND(avg_tat, 1) AS avg_tat_min,
    ROUND(AVG(avg_tat) OVER (), 1) AS overall_avg,
    ROUND(100.0 * (avg_tat - AVG(avg_tat) OVER ()) / AVG(avg_tat) OVER (), 1) AS pct_vs_overall
FROM shift_avg
ORDER BY avg_tat DESC;


-- 3.3 Technician performance: rank within each shift by average TAT
WITH tech_stats AS (
    SELECT
        shift,
        technician_id,
        COUNT(*) AS samples,
        ROUND(AVG(tat_minutes), 1) AS avg_tat_min
    FROM lab_tests
    WHERE rejected = 0 AND department <> 'Microbiology'
    GROUP BY shift, technician_id
)
SELECT
    shift,
    technician_id,
    samples,
    avg_tat_min,
    DENSE_RANK() OVER (PARTITION BY shift ORDER BY avg_tat_min) AS speed_rank
FROM tech_stats
ORDER BY shift, speed_rank;


-- ----------------------------------------------------------------------------
-- SECTION 4: DISTRIBUTION AND PERCENTILES
-- ----------------------------------------------------------------------------

-- 4.1 90th percentile TAT by shift )
WITH ordered AS (
    SELECT
        shift,
        tat_minutes,
        ROW_NUMBER() OVER (PARTITION BY shift ORDER BY tat_minutes) AS rn,
        COUNT(*)     OVER (PARTITION BY shift) AS n
    FROM lab_tests
    WHERE rejected = 0 AND department <> 'Microbiology'
)
SELECT
    shift,
    MIN(CASE WHEN rn >= 0.5 * n THEN tat_minutes END) AS median_tat_min,
    MIN(CASE WHEN rn >= 0.9 * n THEN tat_minutes END) AS p90_tat_min
FROM ordered
GROUP BY shift;


-- 4.2 TAT bands: how many results fall into each time bucket?
SELECT
    CASE
        WHEN tat_minutes <= 60  THEN '1. Up to 1 hour'
        WHEN tat_minutes <= 120 THEN '2. 1-2 hours'
        WHEN tat_minutes <= 240 THEN '3. 2-4 hours'
        ELSE                         '4. Over 4 hours'
    END AS tat_band,
    COUNT(*) AS samples,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_total
FROM lab_tests
WHERE rejected = 0 AND department <> 'Microbiology'
GROUP BY tat_band
ORDER BY tat_band;


-- ----------------------------------------------------------------------------
-- SECTION 5: ROOT-CAUSE QUERIES 
-- ----------------------------------------------------------------------------

-- 5.1 Cost of QC failure: how much delay does a repeat add?
SELECT
    analyzer,
    qc_status,
    COUNT(*) AS samples,
    ROUND(AVG(tat_minutes), 1) AS avg_tat_min
FROM lab_tests
WHERE rejected = 0 AND department <> 'Microbiology'
GROUP BY analyzer, qc_status
ORDER BY analyzer, qc_status;


-- 5.2 Rejection rate by shift and sample type
SELECT
    shift,
    sample_type,
    COUNT(*) AS samples,
    SUM(rejected) AS rejected,
    ROUND(100.0 * SUM(rejected) / COUNT(*), 2) AS rejection_rate_pct
FROM lab_tests
GROUP BY shift, sample_type
ORDER BY rejection_rate_pct DESC;


-- 5.3 Top rejection reasons with share of all rejections 
SELECT
    rejection_reason,
    COUNT(*) AS n,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_rejections,
    ROUND(100.0 * SUM(COUNT(*)) OVER (ORDER BY COUNT(*) DESC)
          / SUM(COUNT(*)) OVER (), 1) AS cumulative_pct
FROM lab_tests
WHERE rejected = 1
GROUP BY rejection_reason
ORDER BY n DESC;


-- 5.4 Busiest hours vs. slowest hours (staffing insight)
SELECT
    CAST(strftime('%H', received_time) AS INTEGER) AS hour_of_day,
    COUNT(*) AS samples,
    ROUND(AVG(tat_minutes), 1) AS avg_tat_min
FROM lab_tests
WHERE rejected = 0 AND department <> 'Microbiology'
GROUP BY hour_of_day
ORDER BY hour_of_day;


-- 5.5 Weekend vs weekday performance
SELECT
    CASE WHEN is_weekend = 1 THEN 'Weekend' ELSE 'Weekday' END AS day_type,
    COUNT(*) AS samples,
    ROUND(AVG(tat_minutes), 1) AS avg_tat_min,
    ROUND(100.0 * SUM(met_target) / COUNT(*), 1) AS pct_met_target
FROM lab_tests
WHERE rejected = 0 AND department <> 'Microbiology'
GROUP BY is_weekend;
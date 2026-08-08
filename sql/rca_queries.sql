-- ============================================================================
-- rca_queries.sql  —  the root-cause investigation, as a documented query library
-- ----------------------------------------------------------------------------
-- This file reads top-to-bottom as the *story* of finding the marginal etch
-- chamber. Each query is one step of the standard yield-investigation arc:
--   symptom -> suspect -> confirm (independent signals) -> size -> recommend
-- The Python layer (src/investigation.py) runs these same queries and renders
-- the charts; this file is the human-readable / SQL-client version.
-- Run order matters; later queries assume the views in views.sql exist.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- STEP 1 — SYMPTOM: every product is missing its yield target. By how much?
-- ----------------------------------------------------------------------------
SELECT product_name, target_yield_pct, actual_yield, gap_to_target, wafers
FROM v_yield_by_product
ORDER BY gap_to_target;          -- most negative gap first


-- ----------------------------------------------------------------------------
-- STEP 2 — SUSPECT: yield is uniform across products, so the cause is shared
-- infrastructure, not one product. Break gate-etch yield out by etch tool.
-- ----------------------------------------------------------------------------
SELECT tool_name, wafers, avg_yield
FROM v_etch_tool_yield
ORDER BY avg_yield;              -- the laggard surfaces here


-- ----------------------------------------------------------------------------
-- STEP 3 — CONFIRMATION #1 (defect signature): does the suspect tool also
-- produce a distinctive defect signature? Edge-ring fraction by etch tool.
-- ----------------------------------------------------------------------------
SELECT tool_name, edge_ring_defects, total_defects, edge_ring_pct
FROM v_edge_ring_by_tool
ORDER BY edge_ring_pct DESC;


-- ----------------------------------------------------------------------------
-- STEP 4 — CONFIRMATION #1b (spatial): edge-ring should be a *radial* signature.
-- Compare mean defect radius and edge-zone share for the suspect vs the others.
-- ----------------------------------------------------------------------------
WITH tagged AS (
    SELECT DISTINCT y.wafer_id,
           MAX(CASE WHEN g.tool_name = 'ETCH-02' THEN 1 ELSE 0 END)
               OVER (PARTITION BY y.wafer_id) AS on_suspect
    FROM yield_data y
    JOIN v_gate_etch_runs g ON g.wafer_id = y.wafer_id
)
SELECT CASE tg.on_suspect WHEN 1 THEN 'ETCH-02' ELSE 'other etchers' END AS tool_group,
       COUNT(*)                                       AS defects,
       ROUND(AVG(dz.radius_mm), 1)                    AS avg_radius_mm,
       ROUND(100.0 * SUM(CASE WHEN dz.zone = 'edge' THEN 1 ELSE 0 END) / COUNT(*), 1) AS edge_zone_pct
FROM tagged tg
JOIN v_defect_zone dz ON dz.wafer_id = tg.wafer_id
GROUP BY tg.on_suspect
ORDER BY avg_radius_mm DESC;


-- ----------------------------------------------------------------------------
-- STEP 5 — CONFIRMATION #2 (independent root signal): unscheduled downtime.
-- A chamber drifting out of spec also breaks down. Downtime per etch tool.
-- ----------------------------------------------------------------------------
SELECT tool_name, unscheduled_hrs, unscheduled_events, pm_hrs
FROM v_tool_downtime
WHERE tool_type = 'ETCH'
ORDER BY unscheduled_hrs DESC;


-- ----------------------------------------------------------------------------
-- STEP 6 — CONVERGENCE: all three signals in one scorecard.
-- The suspect is the tool that is simultaneously worst on yield, highest on
-- edge-ring %, and highest on unscheduled downtime.
-- ----------------------------------------------------------------------------
SELECT tool_name, avg_yield, edge_ring_pct, unscheduled_hrs, unscheduled_events, wafers_processed
FROM v_tool_rca
ORDER BY edge_ring_pct DESC;


-- ----------------------------------------------------------------------------
-- STEP 7 — SIZE THE IMPACT: average yield on the suspect vs the good etchers,
-- and the estimated good-die lost if suspect wafers had matched the others.
-- ----------------------------------------------------------------------------
WITH tagged AS (
    SELECT y.wafer_id, y.yield_pct, y.total_die,
           MAX(CASE WHEN g.tool_name = 'ETCH-02' THEN 1 ELSE 0 END) AS on_suspect
    FROM yield_data y
    JOIN v_gate_etch_runs g ON g.wafer_id = y.wafer_id
    GROUP BY y.wafer_id, y.yield_pct, y.total_die
),
benchmark AS (
    SELECT AVG(yield_pct) AS good_yield FROM tagged WHERE on_suspect = 0
)
SELECT
    SUM(CASE WHEN on_suspect = 1 THEN 1 ELSE 0 END)                                   AS suspect_wafers,
    ROUND(AVG(CASE WHEN on_suspect = 1 THEN yield_pct END), 2)                        AS suspect_avg_yield,
    ROUND((SELECT good_yield FROM benchmark), 2)                                      AS good_etcher_yield,
    ROUND(SUM(CASE WHEN on_suspect = 1
              THEN total_die * ((SELECT good_yield FROM benchmark) - yield_pct) / 100.0
              ELSE 0 END))                                                            AS est_good_die_lost
FROM tagged;


-- ----------------------------------------------------------------------------
-- STEP 8 — EXPOSURE: which lots are most dependent on the suspect chamber,
-- so containment can be prioritised.
-- ----------------------------------------------------------------------------
WITH per_lot AS (
    SELECT l.lot_number,
           COUNT(DISTINCT g.wafer_id)                                               AS gate_etch_wafers,
           COUNT(DISTINCT CASE WHEN g.tool_name = 'ETCH-02' THEN g.wafer_id END)    AS on_suspect
    FROM v_gate_etch_runs g
    JOIN wafers w  ON w.wafer_id = g.wafer_id
    JOIN lots l    ON l.lot_id = w.lot_id
    WHERE g.tool_type = 'ETCH'
    GROUP BY l.lot_number
)
SELECT lot_number, on_suspect, gate_etch_wafers,
       ROUND(100.0 * on_suspect / gate_etch_wafers, 1) AS pct_on_suspect
FROM per_lot
ORDER BY pct_on_suspect DESC;

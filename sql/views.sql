-- ============================================================================
-- views.sql  —  the analytical view layer
-- ----------------------------------------------------------------------------
-- Every view here is a named, documented query the dashboard and the
-- investigation notebook read via pandas.read_sql. Building the analysis as
-- views (not ad-hoc strings) is what a real BI/analytics engineer does:
-- the logic lives in one place, is testable, and the front-end stays thin.
--
-- Zone cut-offs are data-calibrated: on this 300 mm wafer (150 mm radius),
-- EDGE_RING defects sit at a mean radius of ~143 mm, CENTER at ~18 mm, so
-- center < 50 mm, mid 50–110 mm, edge >= 110 mm cleanly separates the classes.
-- ============================================================================

----------------------------------------------------------------------
-- 1. Yield vs target, by product  (the "symptom" view)
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_yield_by_product;
CREATE VIEW v_yield_by_product AS
SELECT p.product_name,
       p.target_yield_pct,
       ROUND(AVG(y.yield_pct), 2)                       AS actual_yield,
       ROUND(AVG(y.yield_pct) - p.target_yield_pct, 2)  AS gap_to_target,
       COUNT(*)                                         AS wafers
FROM yield_data y
JOIN lots l     ON l.lot_id = y.lot_id
JOIN products p ON p.product_id = l.product_id
GROUP BY p.product_name, p.target_yield_pct;

----------------------------------------------------------------------
-- 2. Yield by technology node
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_yield_by_node;
CREATE VIEW v_yield_by_node AS
SELECT p.technology_node_nm,
       ROUND(AVG(y.yield_pct), 2) AS avg_yield,
       COUNT(*)                   AS wafers
FROM yield_data y
JOIN lots l     ON l.lot_id = y.lot_id
JOIN products p ON p.product_id = l.product_id
GROUP BY p.technology_node_nm;

----------------------------------------------------------------------
-- 3. Gate-etch yield by ETCH tool  (the "suspect" view)
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_etch_tool_yield;
CREATE VIEW v_etch_tool_yield AS
SELECT t.tool_name,
       COUNT(DISTINCT y.wafer_id)  AS wafers,
       ROUND(AVG(y.yield_pct), 2)  AS avg_yield
FROM run_history rh
JOIN tools t      ON t.tool_id = rh.tool_id
JOIN yield_data y ON y.wafer_id = rh.wafer_id
WHERE rh.step_id = 4 AND t.tool_type = 'ETCH'
GROUP BY t.tool_name;

----------------------------------------------------------------------
-- 4. Defects with radial zone  (the spatial-signature view)
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_defect_zone;
CREATE VIEW v_defect_zone AS
SELECT d.defect_id,
       d.wafer_id,
       d.x_coord_mm,
       d.y_coord_mm,
       ROUND(SQRT(d.x_coord_mm*d.x_coord_mm + d.y_coord_mm*d.y_coord_mm), 2) AS radius_mm,
       CASE
         WHEN SQRT(d.x_coord_mm*d.x_coord_mm + d.y_coord_mm*d.y_coord_mm) < 50  THEN 'center'
         WHEN SQRT(d.x_coord_mm*d.x_coord_mm + d.y_coord_mm*d.y_coord_mm) < 110 THEN 'mid'
         ELSE 'edge'
       END                                                                   AS zone,
       d.defect_type,
       d.layer,
       d.defect_size_um,
       d.killer_flag
FROM defects d;

----------------------------------------------------------------------
-- 5. Edge-ring fraction per ETCH tool at gate etch  (confirmation #1)
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_edge_ring_by_tool;
CREATE VIEW v_edge_ring_by_tool AS
SELECT t.tool_name,
       COUNT(*)                                                                AS total_defects,
       SUM(CASE WHEN d.defect_type = 'EDGE_RING' THEN 1 ELSE 0 END)            AS edge_ring_defects,
       ROUND(100.0 * SUM(CASE WHEN d.defect_type = 'EDGE_RING' THEN 1 ELSE 0 END) / COUNT(*), 1) AS edge_ring_pct
FROM defects d
JOIN run_history rh ON rh.wafer_id = d.wafer_id AND rh.step_id = 4
JOIN tools t        ON t.tool_id = rh.tool_id
WHERE t.tool_type = 'ETCH'
GROUP BY t.tool_name;

----------------------------------------------------------------------
-- 6. Maintenance downtime per tool  (confirmation #2)
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_tool_downtime;
CREATE VIEW v_tool_downtime AS
SELECT t.tool_id,
       t.tool_name,
       t.tool_type,
       ROUND(SUM(CASE WHEN m.maint_type = 'UNSCHEDULED' THEN m.duration_hours ELSE 0 END), 2) AS unscheduled_hrs,
       SUM(CASE WHEN m.maint_type = 'UNSCHEDULED' THEN 1 ELSE 0 END)                          AS unscheduled_events,
       ROUND(SUM(CASE WHEN m.maint_type = 'PM' THEN m.duration_hours ELSE 0 END), 2)          AS pm_hrs
FROM tools t
LEFT JOIN maintenance m ON m.tool_id = t.tool_id
GROUP BY t.tool_id, t.tool_name, t.tool_type;

----------------------------------------------------------------------
-- 7. Tool RCA scorecard  (the join that names the suspect)
--    edge-ring % + unscheduled downtime + wafer exposure, per ETCH tool.
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_tool_rca;
CREATE VIEW v_tool_rca AS
WITH exposure AS (
    SELECT rh.tool_id, COUNT(DISTINCT rh.wafer_id) AS wafers_processed
    FROM run_history rh
    WHERE rh.step_id = 4
    GROUP BY rh.tool_id
),
yield_by_tool AS (
    SELECT rh.tool_id, ROUND(AVG(y.yield_pct), 2) AS avg_yield
    FROM run_history rh
    JOIN yield_data y ON y.wafer_id = rh.wafer_id
    WHERE rh.step_id = 4
    GROUP BY rh.tool_id
)
SELECT t.tool_name,
       yt.avg_yield,
       er.edge_ring_pct,
       dt.unscheduled_hrs,
       dt.unscheduled_events,
       ex.wafers_processed
FROM tools t
JOIN v_edge_ring_by_tool er ON er.tool_name = t.tool_name
LEFT JOIN exposure       ex ON ex.tool_id  = t.tool_id
LEFT JOIN yield_by_tool  yt ON yt.tool_id  = t.tool_id
LEFT JOIN v_tool_downtime dt ON dt.tool_id = t.tool_id
WHERE t.tool_type = 'ETCH';

----------------------------------------------------------------------
-- 8. Weekly yield trend  (the dashboard line chart source)
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_weekly_yield;
CREATE VIEW v_weekly_yield AS
SELECT strftime('%Y-W%W', test_date)   AS week,
       COUNT(*)                        AS wafers,
       ROUND(AVG(yield_pct), 2)        AS avg_yield,
       ROUND(MIN(yield_pct), 2)        AS worst,
       ROUND(MAX(yield_pct), 2)        AS best
FROM yield_data
GROUP BY week;

----------------------------------------------------------------------
-- 9. Scrap rate per lot
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_scrap_by_lot;
CREATE VIEW v_scrap_by_lot AS
SELECT l.lot_number,
       COUNT(*)                                                              AS wafers,
       SUM(CASE WHEN w.status = 'SCRAP' THEN 1 ELSE 0 END)                   AS scrapped,
       ROUND(100.0 * SUM(CASE WHEN w.status = 'SCRAP' THEN 1 ELSE 0 END) / COUNT(*), 1) AS scrap_pct
FROM wafers w
JOIN lots l ON l.lot_id = w.lot_id
GROUP BY l.lot_number;

----------------------------------------------------------------------
-- 10. Operator / shift performance at gate etch
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_shift_yield;
CREATE VIEW v_shift_yield AS
SELECT o.shift,
       COUNT(DISTINCT y.wafer_id)  AS wafers,
       ROUND(AVG(y.yield_pct), 2)  AS avg_yield
FROM run_history rh
JOIN operators o  ON o.operator_id = rh.operator_id
JOIN yield_data y ON y.wafer_id = rh.wafer_id
WHERE rh.step_id = 4
GROUP BY o.shift;

----------------------------------------------------------------------
-- 11. Defect-type Pareto (with cumulative %)
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_defect_pareto;
CREATE VIEW v_defect_pareto AS
WITH c AS (SELECT defect_type, COUNT(*) AS n FROM defects GROUP BY defect_type)
SELECT defect_type,
       n,
       ROUND(100.0 * n / SUM(n) OVER (), 1)                            AS pct,
       ROUND(100.0 * SUM(n) OVER (ORDER BY n DESC) / SUM(n) OVER (), 1) AS cumulative_pct
FROM c;

----------------------------------------------------------------------
-- 12. Loss decomposition by product (param / defect / edge fail)
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_loss_decomposition;
CREATE VIEW v_loss_decomposition AS
SELECT p.product_name,
       ROUND(AVG(y.param_fail), 2)  AS avg_param_fail,
       ROUND(AVG(y.defect_fail), 2) AS avg_defect_fail,
       ROUND(AVG(y.edge_fail), 2)   AS avg_edge_fail
FROM yield_data y
JOIN lots l     ON l.lot_id = y.lot_id
JOIN products p ON p.product_id = l.product_id
GROUP BY p.product_name;

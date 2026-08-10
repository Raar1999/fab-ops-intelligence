-- ---------------------------------------------------------------------------
-- fabops.semantic — the analytical vocabulary over schema v2.
--
-- Every statement here is a TEMP view. The dataset is opened read-only and the
-- layer lives in the connection's temp schema, so installing it cannot change
-- a byte of `fab.db` — which matters because the dataset's manifest records a
-- SHA-256 of that file and a benchmark scores it afterwards. An analytical
-- layer that mutated the artifact it reads would invalidate the provenance of
-- every result computed from it.
--
-- Order matters: a view may only reference views declared above it.
--
-- Three conventions are declared once here and used everywhere below.
--
--   * `day_index` is whole days since `dataset_meta.time_origin`, floored.
--     Every temporal view buckets on it, so "day 30" means the same instant on
--     every surface.
--   * A radius is measured from the wafer centre in millimetres and expressed
--     as a fraction of the *product's own* wafer radius, never in absolute mm:
--     the v1 layer's 50/110 mm cut-offs were calibrated against one wafer size
--     and would silently mean something else on another.
--   * A step is addressed by `step_name`, never by an id or a sequence number.
--     This is the Phase 0 `v_gate_etch_runs` anchor generalized: instead of one
--     view per interesting step, the spine carries the name and a caller
--     filters on it.
-- ---------------------------------------------------------------------------

-- ------------------------------------------------------------------ the spine
-- One row per run: which wafer, of which lot and product, met which chamber of
-- which tool at which step, when. Exposure is the substrate of every
-- attribution question, and this is exposure.
CREATE TEMP VIEW fact_wafer_step AS
SELECT r.run_id,
       r.wafer_id,
       w.lot_id,
       w.slot_number,
       l.product_id,
       p.product_name,
       f.flow_step_id,
       f.step_sequence,
       s.step_id,
       s.step_name,
       s.operation_type,
       r.tool_id,
       t.tool_name,
       t.tool_type,
       r.chamber_id,
       c.chamber_name,
       t.tool_name || '/' || c.chamber_name AS chamber_label,
       r.recipe_id,
       r.operator_id,
       r.start_time,
       r.end_time,
       CAST(julianday(r.start_time) - julianday(m.time_origin) AS INTEGER)
           AS day_index
FROM runs r
JOIN wafers w        ON w.wafer_id = r.wafer_id
JOIN lots l          ON l.lot_id = w.lot_id
JOIN products p      ON p.product_id = l.product_id
JOIN flow_steps f    ON f.flow_step_id = r.flow_step_id
JOIN process_steps s ON s.step_id = f.step_id
JOIN tools t         ON t.tool_id = r.tool_id
JOIN chambers c      ON c.chamber_id = r.chamber_id
CROSS JOIN dataset_meta m;

-- ------------------------------------------------------------------- yield
-- Attainment, not yield. Products in this world differ by up to ten points of
-- declared target, so a mean over products measures the product mix at least
-- as much as the fab — the artifact the audit verified in v1's weekly view.
-- `attainment_pts` subtracts each product's own target, which is the smallest
-- correction that makes two weeks comparable.
CREATE TEMP VIEW fact_yield AS
SELECT y.yield_id,
       y.wafer_id,
       y.lot_id,
       l.product_id,
       p.product_name,
       p.target_yield_pct,
       y.total_die,
       y.good_die,
       y.total_die - y.good_die AS bad_die,
       y.yield_pct,
       y.yield_pct - p.target_yield_pct AS attainment_pts,
       y.test_time,
       CAST(julianday(y.test_time) - julianday(m.time_origin) AS INTEGER)
           AS day_index
FROM wafer_yield y
JOIN lots l     ON l.lot_id = y.lot_id
JOIN products p ON p.product_id = l.product_id
CROSS JOIN dataset_meta m;

-- One row per die, with the wafer's product and the bin. The die grid is a
-- pure function of (world, product), so `die_x`/`die_y` name the same physical
-- place in every dataset and a spatial yield map is comparable across wafers.
CREATE TEMP VIEW fact_die AS
SELECT b.wafer_id,
       b.die_x,
       b.die_y,
       b.bin_code,
       b.bin_code = 'PASS' AS is_pass,
       w.lot_id,
       l.product_id,
       p.product_name
FROM die_bins b
JOIN wafers w   ON w.wafer_id = b.wafer_id
JOIN lots l     ON l.lot_id = w.lot_id
JOIN products p ON p.product_id = l.product_id;

-- ------------------------------------------------------------------ defects
-- Geometry, never a label. `classified_type` is carried because an analyst
-- reads it, but every spatial quantity below is computed from `(x_mm, y_mm)`
-- and the product's own wafer radius — the classifier is a noisy instrument
-- over a hidden origin (ADR-019 §4) and a zone derived from it would be
-- measuring the instrument.
CREATE TEMP VIEW fact_defect AS
SELECT d.defect_id,
       d.inspection_id,
       d.wafer_id,
       w.lot_id,
       l.product_id,
       p.product_name,
       d.x_mm,
       d.y_mm,
       d.size_um,
       d.classified_type,
       d.layer,
       i.flow_step_id,
       s.step_name AS inspection_step,
       i.inspection_time,
       CAST(julianday(i.inspection_time) - julianday(m.time_origin) AS INTEGER)
           AS day_index,
       SQRT(d.x_mm * d.x_mm + d.y_mm * d.y_mm) AS radius_mm,
       SQRT(d.x_mm * d.x_mm + d.y_mm * d.y_mm) / (p.wafer_size_mm / 2.0)
           AS radius_fraction
FROM defects d
JOIN inspections i   ON i.inspection_id = d.inspection_id
JOIN flow_steps f    ON f.flow_step_id = i.flow_step_id
JOIN process_steps s ON s.step_id = f.step_id
JOIN wafers w        ON w.wafer_id = d.wafer_id
JOIN lots l          ON l.lot_id = w.lot_id
JOIN products p      ON p.product_id = l.product_id
CROSS JOIN dataset_meta m;

-- The zone a defect landed in, as a declared cut of the radial fraction.
-- EDGE starts at 0.80 of the radius because that is where the world's own
-- ring geometry starts; choosing it to make a result come out would be
-- choosing the answer.
CREATE TEMP VIEW v_defect_zone AS
SELECT defect_id,
       wafer_id,
       lot_id,
       product_name,
       layer,
       classified_type,
       inspection_step,
       day_index,
       radius_mm,
       radius_fraction,
       CASE WHEN radius_fraction >= 0.80 THEN 'EDGE'
            WHEN radius_fraction <= 0.33 THEN 'CENTER'
            ELSE 'MID' END AS zone
FROM fact_defect;

-- Per wafer and layer: how a wafer's defects are distributed radially. This is
-- the per-wafer spatial substrate the defect monitor scores signatures from.
CREATE TEMP VIEW v_wafer_defect_profile AS
SELECT z.wafer_id,
       z.layer,
       z.product_name,
       COUNT(*) AS defects,
       AVG(z.radius_fraction) AS mean_radius_fraction,
       SUM(z.zone = 'EDGE')   * 1.0 / COUNT(*) AS edge_share,
       SUM(z.zone = 'MID')    * 1.0 / COUNT(*) AS mid_share,
       SUM(z.zone = 'CENTER') * 1.0 / COUNT(*) AS center_share,
       MIN(z.day_index) AS day_index
FROM v_defect_zone z
GROUP BY z.wafer_id, z.layer, z.product_name;

-- ------------------------------------------------------- process parameters
-- An FDC reading against the setpoint the recipe actually asked for. The
-- deviation is what a control chart is drawn on; the raw value is not, because
-- setpoints differ by product and a chart over mixed setpoints charts the mix.
CREATE TEMP VIEW fact_run_param AS
SELECT rm.run_meas_id,
       rm.run_id,
       rm.param_name,
       rm.value,
       rm.set_value,
       rm.unit,
       rm.value - rm.set_value AS deviation,
       CASE WHEN rm.set_value <> 0
            THEN (rm.value - rm.set_value) / rm.set_value END AS deviation_frac,
       f.wafer_id,
       f.lot_id,
       f.product_name,
       f.step_name,
       f.operation_type,
       f.tool_name,
       f.chamber_label,
       f.recipe_id,
       f.start_time,
       f.day_index
FROM run_measurements rm
JOIN fact_wafer_step f ON f.run_id = rm.run_id;

-- A metrology reading against the recipe's own metric target for that product
-- and step. The target never moves — a fault changes the measurement, not the
-- specification — so the deviation is "how far off spec did this run come
-- out", which is comparable across products.
CREATE TEMP VIEW fact_metrology AS
SELECT m.metrology_id,
       m.wafer_id,
       m.param_name,
       m.value,
       m.unit,
       m.meas_time,
       rc.metric_target,
       rc.metric_usl,
       rc.metric_lsl,
       m.value - rc.metric_target AS deviation,
       CASE WHEN rc.metric_target <> 0
            THEN (m.value - rc.metric_target) / rc.metric_target END
           AS deviation_frac,
       f.run_id,
       f.lot_id,
       f.product_name,
       f.step_name AS measured_step,
       f.operation_type,
       f.tool_name,
       f.chamber_label,
       m.metrology_tool_id,
       f.day_index
FROM metrology m
JOIN fact_wafer_step f ON f.wafer_id = m.wafer_id
                      AND f.flow_step_id = m.flow_step_id
JOIN recipes rc ON rc.step_id = f.step_id AND rc.product_id = f.product_id
WHERE rc.metric_target IS NOT NULL;

-- ------------------------------------------------------------- equipment
-- Daily equipment rollup, at the grain behaviour actually attaches to. A state
-- interval is booked to the day it *starts*: intervals here are sub-day, the
-- fab's own totals are preserved exactly, and the alternative — splitting an
-- interval across midnight — needs a recursive query to answer a question
-- nobody asks.
CREATE TEMP VIEW fact_tool_day AS
SELECT t.tool_name,
       t.tool_name || '/' || COALESCE(c.chamber_name, '*') AS chamber_label,
       CAST(julianday(ts.start_time) - julianday(m.time_origin) AS INTEGER)
           AS day_index,
       ts.state,
       COUNT(*) AS intervals,
       SUM((julianday(ts.end_time) - julianday(ts.start_time)) * 1440.0)
           AS minutes
FROM tool_states ts
JOIN tools t          ON t.tool_id = ts.tool_id
LEFT JOIN chambers c  ON c.chamber_id = ts.chamber_id
CROSS JOIN dataset_meta m
GROUP BY 1, 2, 3, 4;

-- Utilization and availability per chamber over the whole horizon. MTBF/MTTR
-- are Python's job (they need an ordering over events, not an aggregate), and
-- `fabops.monitors.equipment` computes them from `v_chamber_state_intervals`.
CREATE TEMP VIEW v_chamber_utilization AS
SELECT chamber_label,
       SUM(CASE WHEN state = 'PRODUCTIVE' THEN minutes ELSE 0 END) AS productive_min,
       SUM(CASE WHEN state = 'IDLE'       THEN minutes ELSE 0 END) AS idle_min,
       SUM(CASE WHEN state = 'DOWN'       THEN minutes ELSE 0 END) AS down_min,
       SUM(CASE WHEN state = 'PM'         THEN minutes ELSE 0 END) AS pm_min,
       SUM(CASE WHEN state = 'QUAL'       THEN minutes ELSE 0 END) AS qual_min,
       SUM(minutes) AS total_min,
       SUM(CASE WHEN state = 'PRODUCTIVE' THEN minutes ELSE 0 END)
           / NULLIF(SUM(minutes), 0) AS utilization
FROM fact_tool_day
GROUP BY chamber_label;

-- Every state interval, ordered, with its duration. The input to MTBF/MTTR.
CREATE TEMP VIEW v_chamber_state_intervals AS
SELECT t.tool_name || '/' || COALESCE(c.chamber_name, '*') AS chamber_label,
       ts.state_id,
       ts.state,
       ts.start_time,
       ts.end_time,
       (julianday(ts.end_time) - julianday(ts.start_time)) * 1440.0 AS minutes
FROM tool_states ts
JOIN tools t         ON t.tool_id = ts.tool_id
LEFT JOIN chambers c ON c.chamber_id = ts.chamber_id;

-- Maintenance and alarms at the chamber grain, with the day they fell on.
CREATE TEMP VIEW fact_maintenance AS
SELECT mt.maint_id,
       t.tool_name,
       t.tool_name || '/' || COALESCE(c.chamber_name, '*') AS chamber_label,
       mt.maint_type,
       mt.action_code,
       mt.technician,
       mt.start_time,
       mt.end_time,
       (julianday(mt.end_time) - julianday(mt.start_time)) * 24.0 AS hours,
       CAST(julianday(mt.start_time) - julianday(m.time_origin) AS INTEGER)
           AS day_index
FROM maintenance mt
JOIN tools t         ON t.tool_id = mt.tool_id
LEFT JOIN chambers c ON c.chamber_id = mt.chamber_id
CROSS JOIN dataset_meta m;

CREATE TEMP VIEW fact_alarm AS
SELECT a.alarm_id,
       t.tool_name,
       t.tool_name || '/' || c.chamber_name AS chamber_label,
       a.alarm_code,
       a.severity,
       a.alarm_time,
       CAST(julianday(a.alarm_time) - julianday(m.time_origin) AS INTEGER)
           AS day_index
FROM alarms a
JOIN tools t    ON t.tool_id = a.tool_id
JOIN chambers c ON c.chamber_id = a.chamber_id
CROSS JOIN dataset_meta m;

-- ------------------------------------------------------- monitoring views
-- Target-normalized yield over time. `mean_yield_pct` is kept beside
-- `mean_attainment_pts` deliberately: the two disagree exactly when the
-- product mix moved, and seeing them disagree is how an analyst learns that
-- the raw series was never the fab.
CREATE TEMP VIEW v_yield_trend AS
SELECT day_index / 7 AS week_index,
       COUNT(*) AS wafers,
       COUNT(DISTINCT product_name) AS products,
       AVG(yield_pct) AS mean_yield_pct,
       AVG(attainment_pts) AS mean_attainment_pts
FROM fact_yield
GROUP BY day_index / 7;

CREATE TEMP VIEW v_yield_trend_daily AS
SELECT day_index,
       COUNT(*) AS wafers,
       AVG(yield_pct) AS mean_yield_pct,
       AVG(attainment_pts) AS mean_attainment_pts
FROM fact_yield
GROUP BY day_index;

CREATE TEMP VIEW v_product_attainment AS
SELECT product_name,
       target_yield_pct,
       COUNT(*) AS wafers,
       AVG(yield_pct) AS mean_yield_pct,
       AVG(attainment_pts) AS mean_attainment_pts,
       SUM(total_die) AS total_die,
       SUM(total_die - good_die) AS lost_die
FROM fact_yield
GROUP BY product_name, target_yield_pct;

-- ------------------------------------------------- exposure and commonality
-- How many wafers each chamber saw at each step, and over what window.
CREATE TEMP VIEW v_chamber_exposure AS
SELECT step_name,
       operation_type,
       tool_name,
       chamber_label,
       COUNT(DISTINCT wafer_id) AS wafers,
       COUNT(*) AS runs,
       MIN(day_index) AS first_day,
       MAX(day_index) AS last_day
FROM fact_wafer_step
GROUP BY step_name, operation_type, tool_name, chamber_label;

-- Each lot's dependence on each chamber at each step — the containment
-- ranking's substrate.
CREATE TEMP VIEW v_lot_exposure AS
SELECT f.step_name,
       f.chamber_label,
       f.lot_id,
       f.product_name,
       COUNT(DISTINCT f.wafer_id) AS exposed_wafers,
       (SELECT COUNT(*) FROM wafers w WHERE w.lot_id = f.lot_id) AS lot_wafers
FROM fact_wafer_step f
GROUP BY f.step_name, f.chamber_label, f.lot_id, f.product_name;

-- The commonality split, computed the only way it means anything: within
-- product. A chamber's wafers are compared against the wafers of the *other*
-- chambers at the same step that ran the same product, and the per-product
-- differences are pooled by wafer count. Support floors keep a chamber that
-- ran four wafers of one product out of the table entirely.
CREATE TEMP VIEW v_chamber_step_product_yield AS
SELECT f.step_name,
       f.chamber_label,
       f.product_name,
       COUNT(DISTINCT f.wafer_id) AS wafers,
       SUM(y.yield_pct) AS sum_yield_pct,
       AVG(y.yield_pct) AS mean_yield_pct
FROM (SELECT DISTINCT step_name, chamber_label, product_name, wafer_id
      FROM fact_wafer_step) f
JOIN fact_yield y ON y.wafer_id = f.wafer_id
GROUP BY f.step_name, f.chamber_label, f.product_name;

CREATE TEMP VIEW v_step_product_yield AS
SELECT step_name,
       product_name,
       SUM(wafers) AS wafers,
       SUM(sum_yield_pct) AS sum_yield_pct
FROM v_chamber_step_product_yield
GROUP BY step_name, product_name;

CREATE TEMP VIEW v_chamber_yield_deficit AS
SELECT c.step_name,
       c.chamber_label,
       SUM(c.wafers) AS wafers,
       SUM(c.wafers * (c.mean_yield_pct
                       - (s.sum_yield_pct - c.sum_yield_pct)
                         / (s.wafers - c.wafers)))
           / SUM(c.wafers) AS deficit_pts
FROM v_chamber_step_product_yield c
JOIN v_step_product_yield s ON s.step_name = c.step_name
                           AND s.product_name = c.product_name
WHERE c.wafers >= 5 AND (s.wafers - c.wafers) >= 20
GROUP BY c.step_name, c.chamber_label;

-- Edge-zone defect share of the wafers each chamber processed, at one layer.
-- Grouped by layer as well as step so a caller reads the layer the step it
-- cares about is inspected at, rather than pooling two different scans.
CREATE TEMP VIEW v_chamber_defect_signature AS
SELECT f.step_name,
       f.chamber_label,
       z.layer,
       COUNT(*) AS defects,
       COUNT(DISTINCT f.wafer_id) AS wafers,
       AVG(z.radius_fraction) AS mean_radius_fraction,
       SUM(z.zone = 'EDGE') * 1.0 / COUNT(*) AS edge_share,
       SUM(z.zone = 'CENTER') * 1.0 / COUNT(*) AS center_share
FROM (SELECT DISTINCT step_name, chamber_label, wafer_id
      FROM fact_wafer_step) f
JOIN v_defect_zone z ON z.wafer_id = f.wafer_id
GROUP BY f.step_name, f.chamber_label, z.layer;

-- Mean |deviation| from the recipe target per chamber and metrology parameter.
CREATE TEMP VIEW v_chamber_metrology_deviation AS
SELECT measured_step,
       chamber_label,
       param_name,
       COUNT(*) AS readings,
       AVG(deviation) AS mean_deviation,
       AVG(ABS(deviation_frac)) AS mean_abs_deviation_frac
FROM fact_metrology
GROUP BY measured_step, chamber_label, param_name;

-- The routing mix, per product and week. Scenario G's confounder is ordinary
-- observable data (ADR-015) and this is the view that shows it.
CREATE TEMP VIEW v_routing_mix AS
SELECT step_name,
       product_name,
       tool_name,
       day_index / 7 AS week_index,
       COUNT(*) AS runs
FROM fact_wafer_step
GROUP BY step_name, product_name, tool_name, day_index / 7;

-- ============================================================================
-- star_model.sql  —  a denormalised fact table for fast BI-style aggregation
-- ----------------------------------------------------------------------------
-- fact_yield: one row per wafer, pre-joined to its lot/product/gate-etch tool
-- dimensions. In a real warehouse this is materialised by an ETL job; here we
-- rebuild it from the normalised tables. It makes the dashboard joins trivial.
-- ============================================================================

DROP TABLE IF EXISTS fact_yield;

CREATE TABLE fact_yield AS
SELECT
    y.wafer_id,
    y.lot_id,
    l.lot_number,
    l.priority,
    l.status                AS lot_status,
    w.status                AS wafer_status,
    p.product_id,
    p.product_name,
    p.technology_node_nm,
    p.target_yield_pct,
    et.tool_name            AS gate_etch_tool,     -- the dimension the RCA hinges on
    y.total_die,
    y.good_die,
    y.yield_pct,
    y.param_fail,
    y.defect_fail,
    y.edge_fail,
    y.test_date
FROM yield_data y
JOIN wafers   w  ON w.wafer_id = y.wafer_id
JOIN lots     l  ON l.lot_id   = y.lot_id
JOIN products p  ON p.product_id = l.product_id
LEFT JOIN (
    -- each wafer's gate-etch tool; LEFT so wafers without a run still appear.
    -- The step is resolved by NAME (same anchor as v_gate_etch_runs in
    -- views.sql — resolved inline here because the star model is applied
    -- before the view layer exists).
    SELECT rh.wafer_id, t.tool_name
    FROM run_history rh
    JOIN tools t ON t.tool_id = rh.tool_id
    WHERE rh.step_id = (SELECT step_id FROM process_steps
                        WHERE step_name = 'ETCH-GATE')
    GROUP BY rh.wafer_id, t.tool_name
) et ON et.wafer_id = y.wafer_id;

CREATE INDEX IF NOT EXISTS ix_fact_yield_tool    ON fact_yield(gate_etch_tool);
CREATE INDEX IF NOT EXISTS ix_fact_yield_product ON fact_yield(product_name);
CREATE INDEX IF NOT EXISTS ix_fact_yield_date    ON fact_yield(test_date);

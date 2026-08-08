"""
generate_fab_db.py
------------------------------------------------------------------
Builds a realistic (SYNTHETIC) semiconductor fab database for SQL practice.

Outputs:
  - fab.db            : ready-to-query SQLite database
  - fab_database.sql  : portable CREATE TABLE + INSERT script (tested on SQLite,
                        portable to PostgreSQL/MySQL/SQL Server with minor tweaks)

Design goal: the data contains a coherent, *discoverable* story so that SQL
practice questions have real answers. Planted signals:
  * Etch tool ETCH-02 is a "marginal" chamber:
       -> produces more EDGE_RING defects
       -> wafers it processes have measurably lower yield
       -> it logs more UNSCHEDULED maintenance / downtime
  * Edge slots (1, 2, 24, 25) yield slightly worse (edge effect).
  * Yield correlates (negatively) with total defect count.

All numbers are generated, not real fab data.
"""

import sqlite3, random, math, os
from datetime import date, datetime, timedelta

random.seed(42)

# Write outputs next to this script (repo's data/ directory) so the project
# is self-contained and portable — no hard-coded absolute paths.
OUT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(OUT, "fab.db")
SQL_PATH = os.path.join(OUT, "fab_database.sql")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

# ------------------------------------------------------------------
# 1. SCHEMA (single source of truth for both .db and .sql)
# ------------------------------------------------------------------
DDL = """
DROP TABLE IF EXISTS maintenance;
DROP TABLE IF EXISTS yield_data;
DROP TABLE IF EXISTS defects;
DROP TABLE IF EXISTS inspections;
DROP TABLE IF EXISTS run_history;
DROP TABLE IF EXISTS wafers;
DROP TABLE IF EXISTS lots;
DROP TABLE IF EXISTS operators;
DROP TABLE IF EXISTS process_steps;
DROP TABLE IF EXISTS tools;
DROP TABLE IF EXISTS products;

CREATE TABLE products (
    product_id          INTEGER PRIMARY KEY,
    product_name        TEXT NOT NULL,
    technology_node_nm  INTEGER NOT NULL,
    wafer_size_mm       INTEGER NOT NULL,
    die_size_mm2        REAL NOT NULL,
    target_yield_pct    REAL NOT NULL
);

CREATE TABLE tools (
    tool_id        INTEGER PRIMARY KEY,
    tool_name      TEXT NOT NULL,
    tool_type      TEXT NOT NULL,      -- LITHO, ETCH, DEPOSITION, IMPLANT, CMP, CLEAN, METROLOGY
    vendor         TEXT,
    chamber_count  INTEGER NOT NULL,
    install_date   TEXT NOT NULL,
    location_bay   TEXT
);

CREATE TABLE process_steps (
    step_id           INTEGER PRIMARY KEY,
    step_name         TEXT NOT NULL,
    step_sequence     INTEGER NOT NULL,
    operation_type    TEXT NOT NULL,
    target_param_name TEXT,
    target_value      REAL,
    tolerance         REAL,
    is_inspection     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE operators (
    operator_id         INTEGER PRIMARY KEY,
    operator_name       TEXT NOT NULL,
    shift               TEXT NOT NULL,      -- A, B, C
    hire_date           TEXT,
    certification_level INTEGER NOT NULL    -- 1..3
);

CREATE TABLE lots (
    lot_id       INTEGER PRIMARY KEY,
    lot_number   TEXT NOT NULL,
    product_id   INTEGER NOT NULL,
    start_date   TEXT NOT NULL,
    finish_date  TEXT,
    priority     TEXT NOT NULL,            -- HOT, STANDARD, LOW
    status       TEXT NOT NULL,            -- COMPLETED, IN_PROGRESS, SCRAPPED
    wafer_count  INTEGER NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE wafers (
    wafer_id     INTEGER PRIMARY KEY,
    lot_id       INTEGER NOT NULL,
    slot_number  INTEGER NOT NULL,         -- 1..25
    status       TEXT NOT NULL,            -- GOOD, SCRAP, REWORK
    FOREIGN KEY (lot_id) REFERENCES lots(lot_id)
);

CREATE TABLE run_history (
    run_id       INTEGER PRIMARY KEY,
    wafer_id     INTEGER NOT NULL,
    step_id      INTEGER NOT NULL,
    tool_id      INTEGER NOT NULL,
    operator_id  INTEGER NOT NULL,
    chamber_id   INTEGER,
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    measured_value REAL,
    pass_fail    TEXT,                      -- PASS, FAIL
    FOREIGN KEY (wafer_id)    REFERENCES wafers(wafer_id),
    FOREIGN KEY (step_id)     REFERENCES process_steps(step_id),
    FOREIGN KEY (tool_id)     REFERENCES tools(tool_id),
    FOREIGN KEY (operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE inspections (
    inspection_id    INTEGER PRIMARY KEY,
    wafer_id         INTEGER NOT NULL,
    step_id          INTEGER NOT NULL,
    tool_id          INTEGER NOT NULL,
    inspection_date  TEXT NOT NULL,
    total_defect_count INTEGER NOT NULL,
    scan_area_mm2    REAL NOT NULL,
    FOREIGN KEY (wafer_id) REFERENCES wafers(wafer_id),
    FOREIGN KEY (step_id)  REFERENCES process_steps(step_id),
    FOREIGN KEY (tool_id)  REFERENCES tools(tool_id)
);

CREATE TABLE defects (
    defect_id      INTEGER PRIMARY KEY,
    inspection_id  INTEGER NOT NULL,
    wafer_id       INTEGER NOT NULL,
    x_coord_mm     REAL NOT NULL,
    y_coord_mm     REAL NOT NULL,
    defect_size_um REAL NOT NULL,
    defect_type    TEXT NOT NULL,          -- CENTER, EDGE_RING, SCRATCH, PARTICLE, RANDOM
    layer          TEXT,
    killer_flag    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (inspection_id) REFERENCES inspections(inspection_id),
    FOREIGN KEY (wafer_id)      REFERENCES wafers(wafer_id)
);

CREATE TABLE yield_data (
    yield_id     INTEGER PRIMARY KEY,
    wafer_id     INTEGER NOT NULL,
    lot_id       INTEGER NOT NULL,
    total_die    INTEGER NOT NULL,
    good_die     INTEGER NOT NULL,
    yield_pct    REAL NOT NULL,
    param_fail   INTEGER NOT NULL,
    defect_fail  INTEGER NOT NULL,
    edge_fail    INTEGER NOT NULL,
    test_date    TEXT NOT NULL,
    FOREIGN KEY (wafer_id) REFERENCES wafers(wafer_id),
    FOREIGN KEY (lot_id)   REFERENCES lots(lot_id)
);

CREATE TABLE maintenance (
    maint_id       INTEGER PRIMARY KEY,
    tool_id        INTEGER NOT NULL,
    maint_type     TEXT NOT NULL,          -- PM, UNSCHEDULED, REPAIR
    start_time     TEXT NOT NULL,
    end_time       TEXT NOT NULL,
    duration_hours REAL NOT NULL,
    technician     TEXT,
    description    TEXT,
    downtime_flag  INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (tool_id) REFERENCES tools(tool_id)
);
"""

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.executescript(DDL)

# Containers (table_name -> (columns, rows)) for portable .sql emission
TABLES = {}

def add(table, columns, rows):
    TABLES[table] = (columns, rows)
    ph = ",".join("?" * len(columns))
    cur.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({ph})", rows)

# ------------------------------------------------------------------
# 2. DIMENSION DATA
# ------------------------------------------------------------------
products = [
    (1, "Logic-180",  180, 300, 64.0, 92.0),
    (2, "Logic-90",    90, 300, 40.0, 88.0),
    (3, "SRAM-65",     65, 300, 25.0, 85.0),
    (4, "RF-45",       45, 300, 30.0, 80.0),
    (5, "Mobile-28",   28, 300, 18.0, 75.0),
    (6, "AI-14",       14, 300, 22.0, 68.0),
]
add("products",
    ["product_id","product_name","technology_node_nm","wafer_size_mm","die_size_mm2","target_yield_pct"],
    products)
PROD = {p[0]: p for p in products}

tools = [
    (1,  "LITHO-01",  "LITHO",      "ASML",            2, "2019-03-12", "BAY-A"),
    (2,  "LITHO-02",  "LITHO",      "ASML",            2, "2020-06-01", "BAY-A"),
    (3,  "ETCH-01",   "ETCH",       "Lam Research",    3, "2018-11-20", "BAY-B"),
    (4,  "ETCH-02",   "ETCH",       "Lam Research",    3, "2017-02-15", "BAY-B"),  # marginal tool
    (5,  "ETCH-03",   "ETCH",       "Lam Research",    3, "2021-09-10", "BAY-B"),
    (6,  "CVD-01",    "DEPOSITION", "Applied Materials",2,"2019-01-05", "BAY-C"),
    (7,  "CVD-02",    "DEPOSITION", "Applied Materials",2,"2020-08-22", "BAY-C"),
    (8,  "PVD-01",    "DEPOSITION", "Applied Materials",1,"2019-07-30", "BAY-C"),
    (9,  "IMP-01",    "IMPLANT",    "Axcelis",         1, "2018-05-18", "BAY-D"),
    (10, "CMP-01",    "CMP",        "Applied Materials",2,"2019-04-14", "BAY-E"),
    (11, "CMP-02",    "CMP",        "Applied Materials",2,"2021-03-03", "BAY-E"),
    (12, "CLN-01",    "CLEAN",      "SCREEN",          2, "2019-02-09", "BAY-F"),
    (13, "INSP-01",   "METROLOGY",  "KLA",             1, "2020-01-15", "BAY-G"),
    (14, "INSP-02",   "METROLOGY",  "KLA",             1, "2021-11-02", "BAY-G"),
    (15, "CD-SEM-01", "METROLOGY",  "Hitachi",         1, "2020-05-25", "BAY-G"),
]
add("tools",
    ["tool_id","tool_name","tool_type","vendor","chamber_count","install_date","location_bay"],
    tools)
TOOLS_BY_TYPE = {}
for t in tools:
    TOOLS_BY_TYPE.setdefault(t[2], []).append(t)
BAD_ETCH_ID = 4  # ETCH-02

steps = [
    (1,  "CLEAN-INIT",       1,  "CLEAN",      "particles",     0.0,   5.0,  0),
    (2,  "DEPO-GATE-OX",     2,  "DEPOSITION", "thickness_nm",  120.0, 8.0,  0),
    (3,  "LITHO-GATE",       3,  "LITHO",      "overlay_nm",    3.0,   4.0,  0),
    (4,  "ETCH-GATE",        4,  "ETCH",       "cd_nm",         45.0,  5.0,  0),
    (5,  "INSPECT-POSTETCH", 5,  "METROLOGY",  "defect_count",  0.0,   0.0,  1),
    (6,  "IMPLANT-SD",       6,  "IMPLANT",    "dose_e15",      2.0,   0.2,  0),
    (7,  "DEPO-ILD",         7,  "DEPOSITION", "thickness_nm",  300.0, 15.0, 0),
    (8,  "CMP-ILD",          8,  "CMP",        "remove_nm",     200.0, 20.0, 0),
    (9,  "INSPECT-POSTCMP",  9,  "METROLOGY",  "defect_count",  0.0,   0.0,  1),
    (10, "LITHO-METAL",     10,  "LITHO",      "overlay_nm",    3.0,   4.0,  0),
    (11, "ETCH-METAL",      11,  "ETCH",       "cd_nm",         50.0,  5.0,  0),
    (12, "FINAL-INSPECT",   12,  "METROLOGY",  "defect_count",  0.0,   0.0,  1),
]
add("process_steps",
    ["step_id","step_name","step_sequence","operation_type","target_param_name","target_value","tolerance","is_inspection"],
    steps)
STEP = {s[0]: s for s in steps}
INSPECT_STEPS = [s for s in steps if s[7] == 1]
STEP_LAYER = {1:"SUB",2:"GATE_OX",3:"GATE",4:"GATE",5:"GATE",6:"SD",
              7:"ILD",8:"ILD",9:"ILD",10:"METAL1",11:"METAL1",12:"METAL1"}

op_first = ["Arjun","Priya","Rahul","Sneha","Vikram","Anita","Karthik","Divya",
            "Suresh","Meera","Rohan","Lakshmi"]
operators = []
for i, name in enumerate(op_first, start=1):
    shift = "ABC"[(i-1) % 3]
    hire = date(2018,1,1) + timedelta(days=random.randint(0, 2200))
    cert = random.choice([1,2,2,3])
    operators.append((i, name, shift, hire.isoformat(), cert))
add("operators",
    ["operator_id","operator_name","shift","hire_date","certification_level"],
    operators)
OPS_BY_SHIFT = {}
for o in operators:
    OPS_BY_SHIFT.setdefault(o[2], []).append(o)

# ------------------------------------------------------------------
# 3. LOTS + WAFERS
# ------------------------------------------------------------------
lots = []
lot_meta = {}   # lot_id -> dict
base_day = date(2025, 8, 1)
statuses = (["COMPLETED"]*9) + ["IN_PROGRESS","IN_PROGRESS","SCRAPPED"]
random.shuffle(statuses)
for lid in range(1, 13):
    prod = random.randint(1, 6)
    start = base_day + timedelta(days=(lid-1)*14 + random.randint(0,5))
    status = statuses[lid-1]
    priority = random.choice(["HOT","STANDARD","STANDARD","STANDARD","LOW"])
    finish = (start + timedelta(days=random.randint(10,18))).isoformat() if status == "COMPLETED" else None
    lots.append((lid, f"LOT{2025000+lid}", prod, start.isoformat(), finish, priority, status, 25))
    lot_meta[lid] = dict(product=prod, start=start, status=status)
add("lots",
    ["lot_id","lot_number","product_id","start_date","finish_date","priority","status","wafer_count"],
    lots)

wafers = []
wafer_meta = {}  # wafer_id -> dict(lot, slot, etch_tool, reached_step)
wid = 0
for lid in range(1, 13):
    status_lot = lot_meta[lid]["status"]
    for slot in range(1, 26):
        wid += 1
        # how far this wafer progressed
        if status_lot == "COMPLETED":
            reached = 12
        elif status_lot == "IN_PROGRESS":
            reached = random.choice([5, 6, 7, 8])
        else:  # SCRAPPED somewhere
            reached = random.choice([4, 5, 6])
        # etch tool used at ETCH-GATE (the RCA hook)
        etch_tool = random.choice([3, 4, 4, 5])  # ETCH-02 over-represented
        wstatus = "GOOD"
        if status_lot == "SCRAPPED":
            wstatus = random.choice(["SCRAP","SCRAP","REWORK"])
        elif random.random() < 0.04:
            wstatus = random.choice(["REWORK","SCRAP"])
        wafers.append((wid, lid, slot, wstatus))
        wafer_meta[wid] = dict(lot=lid, slot=slot, etch_tool=etch_tool,
                               reached=reached, status=wstatus,
                               start=lot_meta[lid]["start"])
add("wafers", ["wafer_id","lot_id","slot_number","status"], wafers)

def pick_tool(op_type, etch_tool=None):
    if op_type == "ETCH" and etch_tool is not None:
        return etch_tool
    cands = TOOLS_BY_TYPE.get(op_type, [])
    if not cands:
        cands = TOOLS_BY_TYPE["METROLOGY"]
    return random.choice(cands)[0]

def op_for_time(dt):
    # crude shift assignment by hour
    h = dt.hour
    shift = "A" if h < 8 else ("B" if h < 16 else "C")
    return random.choice(OPS_BY_SHIFT[shift])[0]

# ------------------------------------------------------------------
# 4. RUN HISTORY
# ------------------------------------------------------------------
run_rows = []
rid = 0
for w in wafers:
    wmid = wafer_meta[w[0]]
    t = datetime.combine(wmid["start"], datetime.min.time()) + timedelta(hours=random.randint(0,12))
    for s in steps:
        if s[2] > wmid["reached"]:
            break
        rid += 1
        op_type = s[3]
        tool_id = pick_tool(op_type, wmid["etch_tool"] if s[0] in (4,11) else None)
        dur = timedelta(hours=random.uniform(0.5, 4.0))
        start_t = t
        end_t = t + dur
        target = s[5]; tol = s[6]
        if target and tol and tol > 0:
            mv = random.gauss(target, tol/2.5)
            pf = "PASS" if abs(mv - target) <= tol else "FAIL"
        else:
            mv = None; pf = "PASS"
        chamber = random.randint(1, TOOLS_BY_TYPE_count := next(tt[4] for tt in tools if tt[0]==tool_id))
        op_id = op_for_time(start_t)
        run_rows.append((rid, w[0], s[0], tool_id, op_id, chamber,
                         start_t.strftime("%Y-%m-%d %H:%M:%S"),
                         end_t.strftime("%Y-%m-%d %H:%M:%S"),
                         round(mv,3) if mv is not None else None, pf))
        t = end_t + timedelta(hours=random.uniform(1, 10))
add("run_history",
    ["run_id","wafer_id","step_id","tool_id","operator_id","chamber_id",
     "start_time","end_time","measured_value","pass_fail"],
    run_rows)

# ------------------------------------------------------------------
# 5. INSPECTIONS + DEFECTS
# ------------------------------------------------------------------
WAFER_R = 150.0  # 300mm wafer radius
SIG_TYPES = ["CENTER","EDGE_RING","SCRATCH","PARTICLE","RANDOM"]

def assign_signature(wmid):
    # ETCH-02 wafers biased to EDGE_RING + higher counts
    if wmid["etch_tool"] == BAD_ETCH_ID:
        return random.choices(SIG_TYPES, weights=[10,45,10,20,15])[0]
    return random.choices(SIG_TYPES, weights=[18,12,12,28,30])[0]

def gen_defect_coords(sig):
    if sig == "CENTER":
        r = abs(random.gauss(0, 22)); r = min(r, WAFER_R-2)
    elif sig == "EDGE_RING":
        r = random.uniform(WAFER_R-12, WAFER_R-1)
    elif sig == "SCRATCH":
        # handled separately (line); fallback random
        r = random.uniform(0, WAFER_R-1)
    elif sig == "PARTICLE":
        r = WAFER_R * math.sqrt(random.random())  # uniform over area
    else:
        r = WAFER_R * math.sqrt(random.random())
    th = random.uniform(0, 2*math.pi)
    return r*math.cos(th), r*math.sin(th)

insp_rows = []
def_rows = []
iid = 0
did = 0
for w in wafers:
    wmid = wafer_meta[w[0]]
    sig = assign_signature(wmid)
    wmid["signature"] = sig
    # base defect rate
    bad = (wmid["etch_tool"] == BAD_ETCH_ID)
    edge = wmid["slot"] in (1,2,24,25)
    base = 6 + (8 if bad else 0) + (3 if edge else 0)
    # a fixed scratch line for this wafer if SCRATCH
    if sig == "SCRATCH":
        ang = random.uniform(0, math.pi)
        dx, dy = math.cos(ang), math.sin(ang)
    for s in INSPECT_STEPS:
        if s[2] > wmid["reached"]:
            break
        iid += 1
        n = max(0, int(random.gauss(base, base*0.35)))
        n = min(n, 70)
        insp_tool = pick_tool("METROLOGY")
        idt = (datetime.combine(wmid["start"], datetime.min.time())
               + timedelta(days=s[2], hours=random.randint(0,12)))
        # generate defects
        for _ in range(n):
            did += 1
            dtype = sig if random.random() < 0.75 else random.choice(SIG_TYPES)
            if dtype == "SCRATCH" and sig == "SCRATCH":
                tparam = random.uniform(-WAFER_R*0.8, WAFER_R*0.8)
                jitter = random.gauss(0, 2.0)
                x = dx*tparam - dy*jitter
                y = dy*tparam + dx*jitter
            else:
                x, y = gen_defect_coords(dtype)
            size = round(math.exp(random.gauss(0.4, 0.7)), 3)  # ~0.3..6um
            killer = 1 if (size > 2.5 or (dtype=="CENTER" and abs(x)<10 and abs(y)<10 and random.random()<0.4)) else 0
            def_rows.append((did, iid, w[0], round(x,3), round(y,3), size,
                             dtype, STEP_LAYER[s[0]], killer))
        insp_rows.append((iid, w[0], s[0], insp_tool, idt.strftime("%Y-%m-%d %H:%M:%S"),
                          n, round(math.pi*WAFER_R*WAFER_R, 1)))
add("inspections",
    ["inspection_id","wafer_id","step_id","tool_id","inspection_date","total_defect_count","scan_area_mm2"],
    insp_rows)
add("defects",
    ["defect_id","inspection_id","wafer_id","x_coord_mm","y_coord_mm","defect_size_um","defect_type","layer","killer_flag"],
    def_rows)

# total defect count per wafer (for yield model)
defcount = {}
for d in def_rows:
    defcount[d[2]] = defcount.get(d[2], 0) + 1

# ------------------------------------------------------------------
# 6. YIELD (only wafers that reached FINAL-INSPECT, step 12)
# ------------------------------------------------------------------
yield_rows = []
yid = 0
for w in wafers:
    wmid = wafer_meta[w[0]]
    if wmid["reached"] < 12 or wmid["status"] == "SCRAP":
        continue
    prod = PROD[lot_meta[wmid["lot"]]["product"]]
    die_size = prod[4]
    usable_area = math.pi * (WAFER_R - 3)**2
    total_die = int(usable_area / die_size)
    base = prod[5] / 100.0
    dc = defcount.get(w[0], 0)
    bad = (wmid["etch_tool"] == BAD_ETCH_ID)
    edge = wmid["slot"] in (1,2,24,25)
    factor = base - 0.0016*dc - (0.08 if bad else 0) - (0.03 if edge else 0)
    factor += random.gauss(0, 0.03)
    factor = max(0.20, min(0.99, factor))
    good = int(total_die * factor)
    fails = total_die - good
    # split fail bins
    edge_fail = int(fails * (0.35 if edge else 0.12))
    defect_fail = int(fails * 0.45)
    param_fail = fails - edge_fail - defect_fail
    if param_fail < 0:
        param_fail = 0
    yid += 1
    tdate = (datetime.combine(wmid["start"], datetime.min.time()) + timedelta(days=14)).date().isoformat()
    yield_rows.append((yid, w[0], wmid["lot"], total_die, good,
                       round(100.0*good/total_die, 2),
                       param_fail, defect_fail, edge_fail, tdate))
add("yield_data",
    ["yield_id","wafer_id","lot_id","total_die","good_die","yield_pct","param_fail","defect_fail","edge_fail","test_date"],
    yield_rows)

# ------------------------------------------------------------------
# 7. MAINTENANCE  (ETCH-02 gets extra unscheduled downtime)
# ------------------------------------------------------------------
techs = ["T. Nair","S. Iyer","M. Bose","K. Reddy","A. Khan"]
maint_rows = []
mid = 0
for t in tools:
    tool_id = t[0]
    bad = (tool_id == BAD_ETCH_ID)
    # monthly PM
    for month in range(8, 14):  # Aug 2025 .. Jan 2026
        y = 2025 if month <= 12 else 2026
        m = month if month <= 12 else month-12
        start = datetime(y, m, random.randint(2, 26), random.randint(0,23))
        dur = random.uniform(2, 6)
        mid += 1
        maint_rows.append((mid, tool_id, "PM",
                           start.strftime("%Y-%m-%d %H:%M:%S"),
                           (start+timedelta(hours=dur)).strftime("%Y-%m-%d %H:%M:%S"),
                           round(dur,2), random.choice(techs),
                           "Scheduled preventive maintenance", 1))
    # unscheduled events
    n_uns = random.randint(4, 7) if bad else random.randint(0, 2)
    for _ in range(n_uns):
        m = random.randint(8, 13); y = 2025 if m <= 12 else 2026
        m = m if m <= 12 else m-12
        start = datetime(y, m, random.randint(1, 28), random.randint(0,23))
        dur = random.uniform(3, 14) if bad else random.uniform(1, 6)
        mid += 1
        desc = random.choice(["Chamber pressure excursion","RF match failure",
                              "Particle excursion - chamber clean","Endpoint detector fault",
                              "Gas flow MFC drift"])
        maint_rows.append((mid, tool_id, "UNSCHEDULED",
                           start.strftime("%Y-%m-%d %H:%M:%S"),
                           (start+timedelta(hours=dur)).strftime("%Y-%m-%d %H:%M:%S"),
                           round(dur,2), random.choice(techs), desc, 1))
add("maintenance",
    ["maint_id","tool_id","maint_type","start_time","end_time","duration_hours","technician","description","downtime_flag"],
    maint_rows)

conn.commit()

# ------------------------------------------------------------------
# 8. WRITE PORTABLE .sql
# ------------------------------------------------------------------
def sql_val(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"

order = ["products","tools","process_steps","operators","lots","wafers",
         "run_history","inspections","defects","yield_data","maintenance"]

with open(SQL_PATH, "w") as f:
    f.write("-- ================================================================\n")
    f.write("-- Synthetic Semiconductor Fab Database  (for SQL practice)\n")
    f.write("-- Generated by generate_fab_db.py  |  All data is fabricated.\n")
    f.write("-- Tested on SQLite. Portable to PostgreSQL/MySQL/SQL Server\n")
    f.write("-- (TEXT timestamps; adjust types/quoting per dialect if needed).\n")
    f.write("-- ================================================================\n\n")
    f.write(DDL.strip() + "\n\n")
    for tbl in order:
        cols, rows = TABLES[tbl]
        f.write(f"-- {tbl}: {len(rows)} rows\n")
        B = 200
        for i in range(0, len(rows), B):
            chunk = rows[i:i+B]
            f.write(f"INSERT INTO {tbl} ({','.join(cols)}) VALUES\n")
            f.write(",\n".join("(" + ",".join(sql_val(v) for v in r) + ")" for r in chunk))
            f.write(";\n")
        f.write("\n")

# ------------------------------------------------------------------
# 9. SUMMARY / VALIDATION
# ------------------------------------------------------------------
print("ROW COUNTS")
for tbl in order:
    c = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"  {tbl:16s} {c:6d}")

print("\nDEFECT TYPE DISTRIBUTION")
for row in cur.execute("SELECT defect_type, COUNT(*) FROM defects GROUP BY defect_type ORDER BY 2 DESC"):
    print(f"  {row[0]:10s} {row[1]:6d}")

print("\nPLANTED SIGNAL #1 - avg yield by ETCH-GATE tool")
q = """
SELECT t.tool_name, ROUND(AVG(y.yield_pct),2) avg_yield, COUNT(*) n_wafers
FROM yield_data y
JOIN run_history r ON r.wafer_id=y.wafer_id AND r.step_id=4
JOIN tools t ON t.tool_id=r.tool_id
GROUP BY t.tool_name ORDER BY avg_yield;
"""
for row in cur.execute(q):
    print(f"  {row[0]:10s} avg_yield={row[1]:6.2f}  n={row[2]}")

print("\nPLANTED SIGNAL #2 - unscheduled maintenance hours by tool (top 5)")
q2 = """
SELECT t.tool_name, ROUND(SUM(m.duration_hours),1) hrs, COUNT(*) events
FROM maintenance m JOIN tools t ON t.tool_id=m.tool_id
WHERE m.maint_type='UNSCHEDULED'
GROUP BY t.tool_name ORDER BY hrs DESC LIMIT 5;
"""
for row in cur.execute(q2):
    print(f"  {row[0]:10s} unscheduled_hrs={row[1]:6.1f}  events={row[2]}")

print("\nPLANTED SIGNAL #3 - corr check: avg defects vs yield bucket")
q3 = """
WITH wd AS (
  SELECT y.wafer_id, y.yield_pct,
         (SELECT COUNT(*) FROM defects d WHERE d.wafer_id=y.wafer_id) dc
  FROM yield_data y)
SELECT CASE WHEN yield_pct>=85 THEN 'high(>=85)'
            WHEN yield_pct>=70 THEN 'mid(70-85)'
            ELSE 'low(<70)' END bucket,
       ROUND(AVG(dc),1) avg_defects, COUNT(*) n
FROM wd GROUP BY bucket ORDER BY avg_defects;
"""
for row in cur.execute(q3):
    print(f"  {row[0]:12s} avg_defects={row[1]:6.1f}  n={row[2]}")

sql_size = os.path.getsize(SQL_PATH)/1024
print(f"\nWrote {DB_PATH}")
print(f"Wrote {SQL_PATH}  ({sql_size:.0f} KB)")
conn.close()

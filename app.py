
import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
from io import BytesIO

# ------- CONFIG -------
st.set_page_config(page_title="BOM Builder", page_icon="🧰", layout="wide")

DB_PATH = "bom.db"
DEFAULT_VAT = 20.0   # UK VAT %
DEFAULT_MARKUP = 0.0 # % on cost (set per BOM)
CURRENCY = "£"       # display only

# ------- DB LAYER -------
@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS suppliers(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        contact TEXT,
        currency TEXT DEFAULT 'GBP',
        lead_time_days INTEGER DEFAULT 0
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS materials(
        id INTEGER PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL,
        unit TEXT NOT NULL,
        unit_cost_ex_vat REAL NOT NULL,
        supplier_id INTEGER,
        notes TEXT,
        FOREIGN KEY(supplier_id) REFERENCES suppliers(id) ON DELETE SET NULL
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS packs(
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        notes TEXT
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pack_items(
        id INTEGER PRIMARY KEY,
        pack_id INTEGER NOT NULL,
        material_id INTEGER NOT NULL,
        qty REAL NOT NULL,
        FOREIGN KEY(pack_id) REFERENCES packs(id) ON DELETE CASCADE,
        FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE RESTRICT
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS boms(
        id INTEGER PRIMARY KEY,
        project_name TEXT NOT NULL,
        customer TEXT,
        ref TEXT,
        created_on TEXT NOT NULL,
        markup_pct REAL NOT NULL,
        vat_pct REAL NOT NULL
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bom_items(
        id INTEGER PRIMARY KEY,
        bom_id INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK(item_type IN ('material','pack')),
        ref_id INTEGER NOT NULL,
        qty REAL NOT NULL,
        notes TEXT,
        FOREIGN KEY(bom_id) REFERENCES boms(id) ON DELETE CASCADE
    );""")
    conn.commit()

conn = get_conn()
init_db(conn)

def df(sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)

def run(sql, params=()):
    conn.execute(sql, params)
    conn.commit()

# ------- HELPERS -------
def ddmmyyyy(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y")

def pack_cost(pack_id: int) -> float:
    q = """
    SELECT SUM(pi.qty * m.unit_cost_ex_vat) AS cost
    FROM pack_items pi
    JOIN materials m ON m.id = pi.material_id
    WHERE pi.pack_id = ?
    """
    row = df(q, (pack_id,)).iloc[0]
    return float(row["cost"] or 0.0)

def expand_bom_to_materials(bom_id: int) -> pd.DataFrame:
    direct = df("""
        SELECT m.id AS material_id, m.code, m.description, m.unit, m.unit_cost_ex_vat,
               bi.qty AS qty
        FROM bom_items bi
        JOIN materials m ON m.id = bi.ref_id
        WHERE bi.bom_id = ? AND bi.item_type = 'material';
    """, (bom_id,))
    from_packs = df("""
        SELECT m.id AS material_id, m.code, m.description, m.unit, m.unit_cost_ex_vat,
               (bi.qty * pi.qty) AS qty
        FROM bom_items bi
        JOIN pack_items pi ON pi.pack_id = bi.ref_id
        JOIN materials m ON m.id = pi.material_id
        WHERE bi.bom_id = ? AND bi.item_type = 'pack';
    """, (bom_id,))
    allm = pd.concat([direct, from_packs], ignore_index=True)
    if allm.empty:
        return allm
    grouped = (allm
               .groupby(["material_id","code","description","unit","unit_cost_ex_vat"], as_index=False)
               .agg({"qty":"sum"}))
    grouped["line_cost"] = grouped["qty"] * grouped["unit_cost_ex_vat"]
    return grouped

def bom_totals(bom_id: int):
    mats = expand_bom_to_materials(bom_id)
    base_cost = float(mats["line_cost"].sum()) if not mats.empty else 0.0
    b = df("SELECT markup_pct, vat_pct FROM boms WHERE id = ?", (bom_id,))
    if b.empty:
        return dict(base_cost=0, markup=0, subtotal=0, vat=0, total=0)
    markup_pct = float(b.iloc[0]["markup_pct"])
    vat_pct = float(b.iloc[0]["vat_pct"])
    markup_val = base_cost * (markup_pct/100.0)
    subtotal = base_cost + markup_val
    vat_val = subtotal * (vat_pct/100.0)
    total = subtotal + vat_val
    return dict(base_cost=base_cost, markup=markup_val, subtotal=subtotal, vat=vat_val, total=total)

def money(x): return f"{CURRENCY}{x:,.2f}"

# ------- UI -------
st.title("🧰 BOM Builder for Engineering Installs")
st.write("Use this tool to build Bills of Materials (BOMs) for installations, with install packs, suppliers, and material tracking.")

tabs = st.tabs(["📦 BOMs", "🧩 Packs", "🧱 Materials", "🏭 Suppliers", "📤 Export"])

# (Omitted for brevity — rest of the UI code as in the previous version)

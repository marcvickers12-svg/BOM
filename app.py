import os, zipfile, textwrap, re

corrected_code = textwrap.dedent("""
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
    cur.execute(\"\"\"
    CREATE TABLE IF NOT EXISTS suppliers(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        contact TEXT,
        currency TEXT DEFAULT 'GBP',
        lead_time_days INTEGER DEFAULT 0
    );\"\"\" )
    cur.execute(\"\"\"
    CREATE TABLE IF NOT EXISTS materials(
        id INTEGER PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL,
        unit TEXT NOT NULL,
        unit_cost_ex_vat REAL NOT NULL,
        supplier_id INTEGER,
        notes TEXT,
        FOREIGN KEY(supplier_id) REFERENCES suppliers(id) ON DELETE SET NULL
    );\"\"\" )
    cur.execute(\"\"\"
    CREATE TABLE IF NOT EXISTS packs(
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        notes TEXT
    );\"\"\" )
    cur.execute(\"\"\"
    CREATE TABLE IF NOT EXISTS pack_items(
        id INTEGER PRIMARY KEY,
        pack_id INTEGER NOT NULL,
        material_id INTEGER NOT NULL,
        qty REAL NOT NULL,
        FOREIGN KEY(pack_id) REFERENCES packs(id) ON DELETE CASCADE,
        FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE RESTRICT
    );\"\"\" )
    cur.execute(\"\"\"
    CREATE TABLE IF NOT EXISTS boms(
        id INTEGER PRIMARY KEY,
        project_name TEXT NOT NULL,
        customer TEXT,
        ref TEXT,
        created_on TEXT NOT NULL,
        markup_pct REAL NOT NULL,
        vat_pct REAL NOT NULL
    );\"\"\" )
    cur.execute(\"\"\"
    CREATE TABLE IF NOT EXISTS bom_items(
        id INTEGER PRIMARY KEY,
        bom_id INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK(item_type IN ('material','pack')),
        ref_id INTEGER NOT NULL,
        qty REAL NOT NULL,
        notes TEXT,
        FOREIGN KEY(bom_id) REFERENCES boms(id) ON DELETE CASCADE
    );\"\"\" )
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
    q = \"\"\"
    SELECT SUM(pi.qty * m.unit_cost_ex_vat) AS cost
    FROM pack_items pi
    JOIN materials m ON m.id = pi.material_id
    WHERE pi.pack_id = ?
    \"\"\"
    row = df(q, (pack_id,)).iloc[0]
    return float(row["cost"] or 0.0)

def expand_bom_to_materials(bom_id: int) -> pd.DataFrame:
    \"\"\"
    Returns a flat list of materials required for the BOM, expanding packs.
    Columns: material_id, code, description, unit, unit_cost_ex_vat, qty, line_cost
    \"\"\"
    # Direct materials
    direct = df(\"\"\"
        SELECT m.id AS material_id, m.code, m.description, m.unit, m.unit_cost_ex_vat,
               bi.qty AS qty
        FROM bom_items bi
        JOIN materials m ON m.id = bi.ref_id
        WHERE bi.bom_id = ? AND bi.item_type = 'material';
    \"\"\", (bom_id,))

    # Materials from packs
    from_packs = df(\"\"\"
        SELECT m.id AS material_id, m.code, m.description, m.unit, m.unit_cost_ex_vat,
               (bi.qty * pi.qty) AS qty
        FROM bom_items bi
        JOIN pack_items pi ON pi.pack_id = bi.ref_id
        JOIN materials m ON m.id = pi.material_id
        WHERE bi.bom_id = ? AND bi.item_type = 'pack';
    \"\"\", (bom_id,))

    allm = pd.concat([direct, from_packs], ignore_index=True)
    if allm.empty:
        return allm
    grouped = (allm
               .groupby([\"material_id\",\"code\",\"description\",\"unit\",\"unit_cost_ex_vat\"], as_index=False)
               .agg({\"qty\":\"sum\"}))
    grouped[\"line_cost\"] = grouped[\"qty\"] * grouped[\"unit_cost_ex_vat\"]
    return grouped

def bom_totals(bom_id: int):
    mats = expand_bom_to_materials(bom_id)
    base_cost = float(mats[\"line_cost\"].sum()) if not mats.empty else 0.0
    b = df(\"SELECT markup_pct, vat_pct FROM boms WHERE id = ?\", (bom_id,))
    if b.empty:
        return dict(base_cost=0, markup=0, subtotal=0, vat=0, total=0)
    markup_pct = float(b.iloc[0][\"markup_pct\"])
    vat_pct = float(b.iloc[0][\"vat_pct\"])
    markup_val = base_cost * (markup_pct/100.0)
    subtotal = base_cost + markup_val
    vat_val = subtotal * (vat_pct/100.0)
    total = subtotal + vat_val
    return dict(base_cost=base_cost, markup=markup_val, subtotal=subtotal, vat=vat_val, total=total)

def money(x): return f\"{CURRENCY}{x:,.2f}\"

# ------- UI -------
st.title(\"🧰 BOM Builder for Engineering Installs\")

tabs = st.tabs([\"📦 BOMs\", \"🧩 Packs\", \"🧱 Materials\", \"🏭 Suppliers\", \"📤 Export\"])

# ---- Suppliers ----
with tabs[3]:
    st.subheader(\"Suppliers\")
    with st.form(\"add_supplier\", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2,2,1,1])
        name = c1.text_input(\"Name *\")
        contact = c2.text_input(\"Contact (email/phone)\")
        currency = c3.selectbox(\"Currency\", [\"GBP\",\"EUR\",\"USD\"], index=0)
        lead = c4.number_input(\"Lead time (days)\", 0, 365, 0)
        if st.form_submit_button(\"Add supplier\"):
            if name.strip():
                run(\"INSERT INTO suppliers(name, contact, currency, lead_time_days) VALUES(?,?,?,?)\",
                    (name.strip(), contact.strip(), currency, int(lead)))
                st.success(\"Supplier added.\")
            else:
                st.error(\"Name is required.\")
    st.dataframe(df(\"SELECT id, name, contact, currency, lead_time_days AS lead_days FROM suppliers ORDER BY name;\"),
                 use_container_width=True)

# ---- Materials ----
with tabs[2]:
    st.subheader(\"Materials\")
    suppliers = df(\"SELECT id, name FROM suppliers ORDER BY name;\")
    with st.form(\"add_material\", clear_on_submit=True):
        c1, c2 = st.columns([1,2])
        code = c1.text_input(\"Code/SKU *\")
        desc = c2.text_input(\"Description *\")
        c3, c4, c5 = st.columns([1,1,2])
        unit = c3.text_input(\"Unit (m, pcs, set, etc.) *\", value=\"pcs\")
        cost = c4.number_input(\"Unit cost (ex-VAT) *\", min_value=0.0, step=0.01, format=\"%.2f\")
        supplier_id = c5.selectbox(\"Supplier\", [\"(none)\"] + suppliers[\"name\"].tolist())
        notes = st.text_input(\"Notes\")
        if st.form_submit_button(\"Add material\"):
            if not code.strip() or not desc.strip() or not unit.strip():
                st.error(\"Code, Description and Unit are required.\")
            else:
                sup_id = None
                if supplier_id != \"(none)\":
                    sup_id = int(suppliers.loc[suppliers[\"name\"]==supplier_id, \"id\"].iloc[0])
                try:
                    run(\"\"\"INSERT INTO materials(code, description, unit, unit_cost_ex_vat, supplier_id, notes)
                           VALUES(?,?,?,?,?,?)\"\"\", (code.strip(), desc.strip(), unit.strip(), float(cost), sup_id, notes.strip()))
                    st.success(\"Material added.\")
                except sqlite3.IntegrityError:
                    st.error(\"Code/SKU must be unique.\")
    st.dataframe(df(\"\"\"
        SELECT m.id, m.code, m.description, m.unit,
               ROUND(m.unit_cost_ex_vat,2) AS unit_cost,
               COALESCE(s.name,'') AS supplier
        FROM materials m
        LEFT JOIN suppliers s ON s.id = m.supplier_id
        ORDER BY m.code;
    \"\"\"), use_container_width=True)

# ---- Packs ----
with tabs[1]:
    st.subheader(\"Install Packs\")
    # Create pack
    with st.form(\"add_pack\", clear_on_submit=True):
        name = st.text_input(\"Pack name *\")
        notes = st.text_input(\"Notes\")
        if st.form_submit_button(\"Create pack\"):
            if not name.strip():
                st.error(\"Pack name is required.\")
            else:
                try:
                    run(\"INSERT INTO packs(name, notes) VALUES(?,?)\", (name.strip(), notes.strip()))
                    st.success(\"Pack created.\")
                except sqlite3.IntegrityError:
                    st.error(\"Pack name must be unique.\")
    # Select pack to edit
    packs = df(\"SELECT id, name FROM packs ORDER BY name;\")
    if not packs.empty:
        pack_label = st.selectbox(\"Select a pack to edit\", packs[\"name\"].tolist())
        pack_id = int(packs.loc[packs[\"name\"]==pack_label, \"id\"].iloc[0])

        # Add item to pack
        mats = df(\"SELECT id, code, description, unit, unit_cost_ex_vat FROM materials ORDER BY code;\")
        if mats.empty:
            st.info(\"Add materials first to populate pack items.\")
        else:
            with st.form(\"add_pack_item\", clear_on_submit=True):
                c1, c2, c3 = st.columns([3,1,1])
                mat_label = c1.selectbox(\"Material\", (mats[\"code\"] + \" — \" + mats[\"description\"]).tolist())
                qty = c2.number_input(\"Qty\", min_value=0.0, step=1.0, value=1.0)
                if c3.form_submit_button(\"Add to pack\"):
                    mid = int(mats.iloc[(mats[\"code\"] + \" — \" + mats[\"description\"] == mat_label).idxmax()][\"id\"])
                    run(\"INSERT INTO pack_items(pack_id, material_id, qty) VALUES(?,?,?)\", (pack_id, mid, float(qty)))
                    st.success(\"Item added.\")

        # Show pack items and cost
        st.markdown(\"**Pack contents**\")
        pack_items = df(\"\"\"
            SELECT pi.id, m.code, m.description, pi.qty, m.unit,
                   ROUND(m.unit_cost_ex_vat,2) AS unit_cost,
                   ROUND(pi.qty * m.unit_cost_ex_vat,2) AS line_cost
            FROM pack_items pi
            JOIN materials m ON m.id = pi.material_id
            WHERE pi.pack_id = ?
            ORDER BY m.code;
        \"\"\", (pack_id,))
        st.dataframe(pack_items, use_container_width=True)
        st.caption(f\"Pack cost (ex-VAT): {money(pack_cost(pack_id))}\")
    else:
        st.info(\"No packs yet. Create one above.\")

# ---- BOMs ----
with tabs[0]:
    st.subheader(\"BOMs (Projects)\")
    # Create BOM
    with st.form(\"create_bom\", clear_on_submit=True):
        c1, c2, c3 = st.columns([2,2,1])
        project = c1.text_input(\"Project name *\")
        customer = c2.text_input(\"Customer\")
        ref = c3.text_input(\"Ref\")
        c4, c5 = st.columns(2)
        markup = c4.number_input(\"Markup %\", min_value=0.0, step=1.0, value=DEFAULT_MARKUP)
        vat = c5.number_input(\"VAT %\", min_value=0.0, step=1.0, value=DEFAULT_VAT)
        if st.form_submit_button(\"Create BOM\"):
            if project.strip():
                run(\"\"\"INSERT INTO boms(project_name, customer, ref, created_on, markup_pct, vat_pct)
                       VALUES(?,?,?,?,?,?)\"\"\", (project.strip(), customer.strip(), ref.strip(), ddmmyyyy(datetime.now()), float(markup), float(vat)))
                st.success(\"BOM created.\")
            else:
                st.error(\"Project name is required.\")

    boms = df(\"SELECT id, created_on, project_name, customer, ref, markup_pct, vat_pct FROM boms ORDER BY id DESC;\")
    if boms.empty:
        st.info(\"Create a BOM above.\")
    else:
        col1, col2 = st.columns([2,1])
        bom_label = col1.selectbox(\"Select BOM\", (boms[\"project_name\"] + \" — \" + boms[\"created_on\"]).tolist())
        bom_id = int(boms.iloc[(boms[\"project_name\"] + \" — \" + boms[\"created_on\"] == bom_label).idxmax()][\"id\"])
        col2.write(\"\")

        st.markdown(\"### Add items to BOM\")
        mats = df(\"SELECT id, code, description FROM materials ORDER BY code;\")
        packs_df = df(\"SELECT id, name FROM packs ORDER BY name;\")
        c1, c2 = st.columns(2)
        with c1.form(\"add_bom_material\", clear_on_submit=True):
            if mats.empty:
                st.info(\"Add materials first.\")
            else:
                mlabel = st.selectbox(\"Material\", (mats[\"code\"] + \" — \" + mats[\"description\"]).tolist())
                mqty = st.number_input(\"Qty\", min_value=0.0, step=1.0, value=1.0)
                mnotes = st.text_input(\"Notes\", value=\"\")
                if st.form_submit_button(\"Add material\"):
                    mid = int(mats.iloc[(mats[\"code\"] + \" — \" + mats[\"description\"] == mlabel).idxmax()][\"id\"])
                    run(\"\"\"INSERT INTO bom_items(bom_id, item_type, ref_id, qty, notes)
                           VALUES(?,?,?,?,?)\"\"\", (bom_id, \"material\", mid, float(mqty), mnotes.strip()))
                    st.success(\"Material added to BOM.\")
        with c2.form(\"add_bom_pack\", clear_on_submit=True):
            if packs_df.empty:
                st.info(\"Create packs first.\")
            else:
                plabel = st.selectbox(\"Pack\", packs_df[\"name\"].tolist())
                pqty = st.number_input(\"Qty\", min_value=0.0, step=1.0, value=1.0)
                pnotes = st.text_input(\"Notes\", value=\"\")
                if st.form_submit_button(\"Add pack\"):
                    pid = int(packs_df.loc[packs_df[\"name\"]==plabel, \"id\"].iloc[0])
                    run(\"\"\"INSERT INTO bom_items(bom_id, item_type, ref_id, qty, notes)
                           VALUES(?,?,?,?,?)\"\"\", (bom_id, \"pack\", pid, float(pqty), pnotes.strip()))
                    st.success(\"Pack added to BOM.\")

        st.markdown(\"### BOM contents\")
        bom_items = df(\"\"\"
            SELECT bi.id, bi.item_type AS type,
                   CASE WHEN bi.item_type='material' THEN m.code
                        ELSE p.name END AS code_or_name,
                   bi.qty, bi.notes
            FROM bom_items bi
            LEFT JOIN materials m ON (bi.item_type='material' AND m.id = bi.ref_id)
            LEFT JOIN packs p     ON (bi.item_type='pack'     AND p.id = bi.ref_id)
            WHERE bi.bom_id = ?
            ORDER BY bi.id DESC;
        \"\"\", (bom_id,))
        st.dataframe(bom_items, use_container_width=True)

        mats_expanded = expand_bom_to_materials(bom_id)
        st.markdown(\"**Expanded material requirements**\")
        st.dataframe(mats_expanded, use_container_width=True)

        totals = bom_totals(bom_id)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric(\"Cost (ex-VAT)\", money(totals[\"base_cost\"]))
        c2.metric(\"Markup\", money(totals[\"markup\"]))
        c3.metric(\"Subtotal\", money(totals[\"subtotal\"]))
        c4.metric(\"VAT\", money(totals[\"vat\"]))
        c5.metric(\"Grand total\", money(totals[\"total\"]))

# ---- Export ----
with tabs[4]:
    st.subheader(\"Export BOM to Excel / CSV\")
    boms = df(\"SELECT id, project_name, created_on FROM boms ORDER BY id DESC;\")
    if boms.empty:
        st.info(\"No BOMs to export yet.\")
    else:
        choice = st.selectbox(\"Choose BOM\", (boms[\"project_name\"] + \" — \" + boms[\"created_on\"]).tolist())
        bom_id = int(boms.iloc[(boms[\"project_name\"] + \" — \" + boms[\"created_on\"] == choice).idxmax()][\"id\"])
        mats_expanded = expand_bom_to_materials(bom_id)
        totals = bom_totals(bom_id)

        # Excel export (one sheet summary + one sheet materials)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine=\"openpyxl\") as writer:
            # Summary
            summary = pd.DataFrame([
                [\"Project\", df(\"SELECT project_name FROM boms WHERE id=?\", (bom_id,)).iloc[0][0]],
                [\"Created\", df(\"SELECT created_on FROM boms WHERE id=?\", (bom_id,)).iloc[0][0]],
                [\"Cost (ex-VAT)\", totals[\"base_cost\"]],
                [\"Markup\", totals[\"markup\"]],
                [\"Subtotal\", totals[\"subtotal\"]],
                [\"VAT\", totals[\"vat\"]],
                [\"Grand Total\", totals[\"total\"]],
            ], columns=[\"Field\",\"Value\"])
            summary.to_excel(writer, index=False, sheet_name=\"Summary\")

            if not mats_expanded.empty:
                mats_out = mats_expanded.copy()
                mats_out.rename(columns={
                    \"code\":\"Code\",
                    \"description\":\"Description\",
                    \"unit\":\"Unit\",
                    \"unit_cost_ex_vat\":\"Unit Cost ex-VAT\",
                    \"qty\":\"Qty\",
                    \"line_cost\":\"Line Cost ex-VAT\"
                }, inplace=True)
                mats_out.to_excel(writer, index=False, sheet_name=\"Materials\")

        st.download_button(
            label=\"⬇️ Download Excel (xlsx)\",
            data=buffer.getvalue(),
            file_name=f\"BOM_{choice.replace(' ','_')}.xlsx\",
            mime=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\"
        )

        # CSV export (materials only)
        if not mats_expanded.empty:
            csv = mats_expanded.to_csv(index=False)
            st.download_button(
                label=\"⬇️ Download CSV (materials)\",
                data=csv,
                file_name=f\"BOM_{choice.replace(' ','_')}_materials.csv\",
                mime=\"text/csv\"
            )
""")

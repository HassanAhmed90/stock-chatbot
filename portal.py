"""
=============================================================================
portal.py - Multi-user portals (5 zone users + 1 recovery + 1 admin)
=============================================================================
Backend data & business logic layer (SQLite data/portal.db + InventoryManager).
* Login with username / password (passwords stored as salted PBKDF2 hashes).
* Zone user portal : order requests for the cities of THEIR zone only,
                     delivery tracking, returns, ITR requests, stock view.
* Recovery portal  : payment recovery across all zones + payment hold release.
* Admin portal     : approvals, ITR management, zone dashboard, statistics,
                     user & city-zone management, and order reset.
=============================================================================
"""

import os
import re
import glob
import math
import shutil
import hmac
import sqlite3
import hashlib
import secrets
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from inventory_manager import MAIN_WAREHOUSES

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "portal.db")

ZONES = ["Zone I", "Zone II", "Zone III", "Zone IV", "Zone V"]

# 3 SUBGROUPS of every record
SUBGROUPS = ["Order", "Recovery", "Delivery"]
STOCK_SUBGROUPS = {"Order"}

# Stored status of a request. "New" and "In process" are both stored as PENDING:
# the app decides which one it is from the age of the request (see display_status).
PENDING, OK, HOLD, REJECTED, CANCELLED = "PENDING", "OK", "HOLD", "REJECTED", "CANCELLED"
NEW_WINDOW_MIN = 60
STATUSES = ["New", "In process", "OK", "Hold"]
STATUS_LABEL = {
    "New": "🆕 New",
    "In process": "⏳ In process",
    "OK": "✅ OK",
    "Hold": "⛔ Hold (payment issue)",
    "Rejected": "❌ Rejected",
    "Cancelled": "🚫 Cancelled by the consumer",
}
ALL_STATES = STATUSES + ["Rejected", "Cancelled"]

# Delivery states of an approved order (set from the zone's Delivery tab)
D_DISPATCHED, D_DELIVERED, D_NOT_YET, D_CANCELLED = (
    "Dispatched", "Delivered", "Not delivered yet", "Cancelled by the consumer"
)
D_RETURNED, D_PART_RETURN = "Returned", "Partially returned"
RETURN_STATES = [D_RETURNED, D_PART_RETURN]
DELIVERY_STATES = [D_DISPATCHED, D_DELIVERED, D_NOT_YET, D_RETURNED, D_PART_RETURN, D_CANCELLED]

# ITR = inter-warehouse transfer: stock moves from one main warehouse to another
ITR_PENDING, ITR_TRANSIT, ITR_RECEIVED, ITR_REJECTED, ITR_CANCELLED = (
    "PENDING", "IN_TRANSIT", "RECEIVED", "REJECTED", "CANCELLED"
)
ITR_LABEL = {
    ITR_PENDING: "⏳ Waiting for admin",
    ITR_TRANSIT: "🚛 In transit",
    ITR_RECEIVED: "✅ Received",
    ITR_REJECTED: "❌ Rejected",
    ITR_CANCELLED: "🚫 Cancelled",
}

WAREHOUSE_ZONE = {
    "Karachi": "Zone I",
    "Multan": "Zone II",
    "Lahore": "Zone III",
    "Rawalpindi": "Zone IV",
    "Peshawar": "Zone V",
}
DEFAULT_GROUPS = ["ASPIRE GROUP", "PROTECTIVE GROUP", "SUPPORTIVE GROUP", "TITAN GROUP"]


def display_status(row: Dict[str, Any]) -> str:
    """OK = approved · Hold = payment issue · In process = waiting approval · New = waiting < 1 hour."""
    st_ = row["status"]
    if st_ == OK:
        return "OK"
    if st_ == HOLD:
        return "Hold"
    if st_ == REJECTED:
        return "Rejected"
    if st_ == CANCELLED:
        return "Cancelled"
    try:
        age_min = (datetime.now() - datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
    except Exception:
        age_min = 9999
    return "New" if age_min < NEW_WINDOW_MIN else "In process"


def with_status(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["state"] = [display_status(r) for _, r in df.iterrows()] if len(df) else []
    return df


_BASE_CITIES = [
    "Karachi", "Lahore", "Faisalabad", "Rawalpindi", "Islamabad", "Gujranwala", "Peshawar", "Multan",
    "Hyderabad", "Quetta", "Sialkot", "Bahawalpur", "Sargodha", "Sukkur", "Larkana", "Sheikhupura",
    "Rahim Yar Khan", "Jhang", "Gujrat", "Mardan", "Kasur", "Dera Ghazi Khan", "Sahiwal", "Okara",
    "Wah Cantt", "Mingora", "Nawabshah", "Mirpur Khas", "Chiniot", "Kamoke", "Sadiqabad", "Burewala",
    "Jacobabad", "Muzaffargarh", "Jhelum", "Khanpur", "Khanewal", "Hafizabad", "Kohat", "Dadu",
    "Daska", "Gojra", "Muridke", "Bahawalnagar", "Samundri", "Pakpattan", "Chakwal", "Mansehra",
    "Abbottabad", "Swat", "Bannu", "Dera Ismail Khan", "Charsadda", "Nowshera", "Swabi", "Attock",
    "Vehari", "Layyah", "Mianwali", "Khushab", "Toba Tek Singh", "Narowal", "Lodhran", "Sanghar",
    "Thatta", "Badin", "Shikarpur", "Khairpur", "Gwadar", "Turbat", "Khuzdar", "Sibi", "Zhob",
    "Muzaffarabad", "Mirpur", "Gilgit", "Skardu", "Kotli", "Rawalakot", "Haripur", "Tando Allahyar",
    "Tando Adam", "Ghotki", "Umerkot", "Kandhkot", "Mandi Bahauddin", "Lala Musa", "Wazirabad",
    "Kamalia", "Arifwala", "Haroonabad", "Taxila", "Murree", "Hasilpur", "Chishtian", "Rajanpur",
]

DEFAULT_ZONE_CITIES = {
    "Zone I": [
        "Karachi", "Hub", "Hyderabad", "Kotri", "Jamshoro", "Sukkur", "Larkana", "Nawabshah", "Mirpur Khas",
        "Jacobabad", "Dadu", "Shikarpur", "Khairpur", "Sanghar", "Thatta", "Badin", "Tando Allahyar",
        "Tando Adam", "Tando Muhammad Khan", "Matiari", "Ghotki", "Umerkot", "Kandhkot", "Kashmore",
        "Sehwan", "Mithi", "Naushahro Feroze", "Shahdadpur",
    ],
    "Zone II": [
        "Multan", "Bahawalpur", "Dera Ghazi Khan", "Rahim Yar Khan", "Sadiqabad", "Khanpur", "Muzaffargarh",
        "Layyah", "Rajanpur", "Vehari", "Lodhran", "Khanewal", "Bahawalnagar", "Haroonabad", "Chishtian",
        "Hasilpur", "Burewala", "Kot Addu", "Ahmedpur East", "Liaquatpur", "Jampur", "Taunsa", "Mailsi",
        "Kabirwala",
    ],
    "Zone III": [
        "Lahore", "Faisalabad", "Sheikhupura", "Kasur", "Okara", "Sahiwal", "Pakpattan", "Jhang", "Chiniot",
        "Toba Tek Singh", "Gojra", "Kamalia", "Arifwala", "Samundri", "Muridke",
    ],
    "Zone IV": [
        "Rawalpindi", "Islamabad", "Gujranwala", "Kamoke", "Daska", "Wazirabad", "Sialkot", "Narowal",
        "Gujrat", "Mandi Bahauddin", "Lala Musa", "Hafizabad", "Jhelum", "Chakwal", "Attock", "Taxila",
        "Wah Cantt", "Murree", "Sargodha", "Khushab", "Mianwali",
    ],
    "Zone V": [
        "Peshawar", "Mardan", "Mingora", "Swat", "Kohat", "Bannu", "Dera Ismail Khan", "Abbottabad",
        "Mansehra", "Charsadda", "Nowshera", "Swabi", "Haripur", "Karak", "Hangu", "Lakki Marwat",
        "Chitral", "Timergara", "Battagram",
    ],
}
PAKISTAN_CITIES = sorted(set(_BASE_CITIES) | {c for v in DEFAULT_ZONE_CITIES.values() for c in v})


# =============================================================================
# DATABASE
# =============================================================================
@contextmanager
def _db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    """Creates tables and seeds 1 admin + 5 zone users + 1 recovery user on the very first run."""
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            username TEXT PRIMARY KEY, full_name TEXT, salt TEXT, pw_hash TEXT,
            role TEXT NOT NULL, zone TEXT, active INTEGER DEFAULT 1, must_change INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS zone_cities(city TEXT PRIMARY KEY, zone TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, username TEXT, zone TEXT,
            party TEXT, city TEXT, grp TEXT, item_code TEXT, item_name TEXT, qty INTEGER,
            status TEXT, admin_note TEXT, decided_by TEXT, decided_at TEXT,
            order_id TEXT, released_qty INTEGER, delivered_at TEXT, subgroup TEXT DEFAULT 'Order',
            challan_no TEXT, delivery_status TEXT, dispatched_at TEXT, delivery_note TEXT,
            ref_no TEXT, amount REAL, return_qty INTEGER, return_reason TEXT,
            return_challan TEXT, returned_at TEXT);
        CREATE TABLE IF NOT EXISTS itr_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, username TEXT, to_zone TEXT,
            from_store TEXT, to_store TEXT, grp TEXT, item_code TEXT, item_name TEXT,
            qty INTEGER, approved_qty INTEGER, status TEXT, note TEXT, admin_note TEXT,
            decided_by TEXT, decided_at TEXT, received_by TEXT, received_at TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        cols = [r["name"] for r in c.execute("PRAGMA table_info(requests)")]
        if "subgroup" not in cols:
            c.execute("ALTER TABLE requests ADD COLUMN subgroup TEXT DEFAULT 'Order'")
        for col, typ in (
            ("challan_no", "TEXT"), ("delivery_status", "TEXT"), ("dispatched_at", "TEXT"),
            ("delivery_note", "TEXT"), ("ref_no", "TEXT"), ("amount", "REAL"),
            ("return_qty", "INTEGER"), ("return_reason", "TEXT"), ("return_challan", "TEXT"),
            ("returned_at", "TEXT"),
        ):
            if col not in cols:
                c.execute(f"ALTER TABLE requests ADD COLUMN {col} {typ}")
        c.execute("UPDATE requests SET status='OK' WHERE status IN ('APPROVED','DELIVERED')")
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            seeds = [("admin", "Administrator", "admin123", "admin", None)]
            seeds += [(f"zone{i + 1}", f"{z} User", f"zone{i + 1}@123", "user", z) for i, z in enumerate(ZONES)]
            for username, name, pw, role, zone in seeds:
                salt = secrets.token_hex(16)
                c.execute(
                    "INSERT INTO users VALUES(?,?,?,?,?,?,1,1)",
                    (username, name, salt, _hash(pw, salt), role, zone),
                )
        c.execute("UPDATE users SET zone=NULL WHERE role='recovery'")
        c.execute(
            "UPDATE users SET active=0 WHERE role='recovery' AND must_change=1 "
            "AND username IN ('recovery2','recovery3','recovery4','recovery5')"
        )
        if c.execute("SELECT COUNT(*) FROM users WHERE role='recovery' AND active=1").fetchone()[0] == 0:
            salt = secrets.token_hex(16)
            c.execute(
                "INSERT OR IGNORE INTO users VALUES(?,?,?,?,?,?,1,1)",
                ("recovery", "Recovery (all zones)", salt, _hash("recovery@123", salt), "recovery", None),
            )


def seed_default_zones(force: bool = False) -> int:
    """Assigns default cities to zones that have none (or all unassigned if force=True)."""
    added = 0
    with _db() as c:
        for zone, cities in DEFAULT_ZONE_CITIES.items():
            has = c.execute("SELECT COUNT(*) FROM zone_cities WHERE zone=?", (zone,)).fetchone()[0]
            if has and not force:
                continue
            for city in cities:
                cur = c.execute("INSERT OR IGNORE INTO zone_cities VALUES(?,?)", (city, zone))
                added += cur.rowcount
    return added


def get_setting(key: str, default: str = "") -> str:
    with _db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: Any) -> None:
    with _db() as c:
        c.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# ----------------------------- users ----------------------------------------
def get_user(username: str) -> Optional[Dict[str, Any]]:
    with _db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(row) if row else None


def safe_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """User dictionary without password salt/hash."""
    return {
        "username": user["username"],
        "full_name": user.get("full_name") or user["username"],
        "role": user["role"],
        "zone": user.get("zone"),
        "active": int(user.get("active", 1)),
        "must_change": int(user.get("must_change", 0)),
    }


def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    with _db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
    if not row or not row["active"]:
        return None
    if hmac.compare_digest(_hash(password, row["salt"]), row["pw_hash"]):
        return dict(row)
    return None


def set_password(username: str, password: str, must_change: int = 0) -> None:
    salt = secrets.token_hex(16)
    with _db() as c:
        c.execute(
            "UPDATE users SET salt=?, pw_hash=?, must_change=? WHERE username=?",
            (salt, _hash(password, salt), must_change, username),
        )


def create_user(username: str, full_name: str, password: str, zone: Optional[str], role: str = "user") -> Tuple[bool, str]:
    username = username.strip().lower()
    if not username or not password:
        return False, "Username and password are required."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    salt = secrets.token_hex(16)
    if role == "recovery":
        zone = None
    try:
        with _db() as c:
            c.execute(
                "INSERT INTO users VALUES(?,?,?,?,?,?,1,1)",
                (username, full_name.strip(), salt, _hash(password, salt), role, zone),
            )
    except sqlite3.IntegrityError:
        return False, "This username already exists."
    return True, "User created. They must change the password at first login."


def list_users() -> pd.DataFrame:
    with _db() as c:
        rows = c.execute("SELECT username, full_name, role, zone, active FROM users ORDER BY role, zone").fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def update_user(username: str, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in ("full_name", "zone", "active")}
    if not allowed:
        return
    sets = ", ".join(f"{k}=?" for k in allowed)
    with _db() as c:
        c.execute(f"UPDATE users SET {sets} WHERE username=?", (*allowed.values(), username))


# ----------------------------- zones / cities -------------------------------
def city_owner_map() -> Dict[str, str]:
    with _db() as c:
        return {r["city"]: r["zone"] for r in c.execute("SELECT city, zone FROM zone_cities")}


def get_zone_cities(zone: str) -> List[str]:
    return sorted(city for city, z in city_owner_map().items() if z == zone)


def set_zone_cities(zone: str, cities: List[str]) -> None:
    with _db() as c:
        c.execute("DELETE FROM zone_cities WHERE zone=?", (zone,))
        for city in cities:
            c.execute("INSERT OR REPLACE INTO zone_cities VALUES(?,?)", (city, zone))


# ----------------------------- requests -------------------------------------
def get_request(req_id: int) -> Optional[Dict[str, Any]]:
    with _db() as c:
        row = c.execute("SELECT * FROM requests WHERE id=?", (int(req_id),)).fetchone()
    return dict(row) if row else None


def create_request(user: dict, party, city, grp, item_code, item_name, qty, subgroup="Order") -> int:
    with _db() as c:
        cur = c.execute(
            "INSERT INTO requests(created_at, username, zone, party, city, grp, item_code, item_name, qty,"
            " status, subgroup) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), user["username"], user["zone"], party, city, grp, item_code, item_name, int(qty), PENDING, subgroup),
        )
        return cur.lastrowid


def list_requests(username: str = None, status=None, zone: str = None, subgroup: str = None) -> pd.DataFrame:
    q, args = "SELECT * FROM requests WHERE 1=1", []
    if subgroup:
        q += " AND COALESCE(subgroup, 'Order')=?"
        args.append(subgroup)
    if zone:
        q += " AND zone=?"
        args.append(zone)
    if username:
        q += " AND username=?"
        args.append(username)
    if status:
        statuses = [status] if isinstance(status, str) else list(status)
        q += f" AND status IN ({','.join('?' * len(statuses))})"
        args += statuses
    q += " ORDER BY id DESC"
    with _db() as c:
        cur = c.execute(q, args)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame([dict(r) for r in rows], columns=cols)
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            if col in ("qty", "released_qty", "amount", "return_qty"):
                df[col] = df[col].fillna(0)
        else:
            df[col] = df[col].astype(object).where(df[col].notna(), None)
    return df


def create_recovery(user: dict, zone, party, city, grp, amount, ref_no, note) -> int:
    """A recovery (payment collection) entry made from the Recovery portal."""
    with _db() as c:
        cur = c.execute(
            "INSERT INTO requests(created_at, username, zone, party, city, grp, item_code, item_name, qty,"
            " status, subgroup, amount, ref_no, delivery_note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _now(), user["username"], zone, party, city, grp, "", "Recovery", 0,
                PENDING, "Recovery", float(amount), ref_no, note,
            ),
        )
        return cur.lastrowid


def set_dispatched(req_id: int, challan_no: str) -> None:
    update_request(req_id, challan_no=challan_no.strip(), delivery_status=D_DISPATCHED, dispatched_at=_now())


def register_return(row: Dict[str, Any], qty: int, reason: str, return_challan: str) -> str:
    """Registers a full or partial return on a delivered order."""
    released = int(row["released_qty"] or row["qty"] or 0)
    state = D_RETURNED if int(qty) >= released else D_PART_RETURN
    update_request(
        row["id"],
        delivery_status=state,
        return_qty=int(qty),
        return_reason=reason.strip(),
        return_challan=(return_challan or "").strip() or None,
        returned_at=_now(),
    )
    return state


def cancel_by_consumer(mgr, row: Dict[str, Any]) -> Tuple[bool, str]:
    """Consumer cancelled: deletes released order file (restores stock) and closes request."""
    note = "Order cancelled by the consumer"
    stock_msg = ""
    order_id = row.get("order_id")
    if order_id:
        ok, res = mgr.delete_order(order_id)
        if ok:
            lines = res.get("restored_lines") or []
            stock_msg = f" Stock returned: {', '.join(lines)}." if lines else ""
        elif "Could not delete" in str(res):
            return False, str(res)
        else:
            removed = []
            for path in glob.glob(os.path.join(mgr.orders_dir, f"Order_{order_id}_*")):
                if path.endswith((".xlsx", ".csv")):
                    try:
                        os.remove(path)
                        removed.append(os.path.basename(path))
                    except Exception as error:
                        return False, f"Could not delete '{os.path.basename(path)}' - is it open in Excel? ({error})"
            stock_msg = (
                " The order file was removed, so its stock is back."
                if removed
                else " No order file was found, so stock was not changed."
            )
    update_request(
        row["id"],
        status=CANCELLED,
        delivery_status=D_CANCELLED,
        delivery_note=note,
        admin_note=note,
        decided_at=_now(),
    )
    return True, f"{note}. The order was cancelled automatically.{stock_msg}"


def pending_units(item_code: str) -> int:
    """Units of one product already waiting for approval (all zones, Order type)."""
    with _db() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(qty),0) FROM requests WHERE item_code=? AND status IN (?,?) "
            "AND COALESCE(subgroup,'Order')='Order'",
            (item_code, PENDING, HOLD),
        ).fetchone()
    return int(row[0])


def update_request(req_id: int, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with _db() as c:
        c.execute(f"UPDATE requests SET {sets} WHERE id=?", (*fields.values(), int(req_id)))


# ----------------------------- ITR ------------------------------------------
def get_itr(itr_id: int) -> Optional[Dict[str, Any]]:
    with _db() as c:
        row = c.execute("SELECT * FROM itr_requests WHERE id=?", (int(itr_id),)).fetchone()
    return dict(row) if row else None


def create_itr(username, to_zone, from_store, to_store, grp, item_code, item_name, qty, note="") -> int:
    with _db() as c:
        cur = c.execute(
            "INSERT INTO itr_requests(created_at, username, to_zone, from_store, to_store, grp, item_code,"
            " item_name, qty, status, note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), username, to_zone, from_store, to_store, grp, item_code, item_name, int(qty), ITR_PENDING, note or None),
        )
        return cur.lastrowid


def update_itr(itr_id: int, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with _db() as c:
        c.execute(f"UPDATE itr_requests SET {sets} WHERE id=?", (*fields.values(), int(itr_id)))


def list_itr(status=None, zone: str = None, stores=None) -> pd.DataFrame:
    """zone (+ its warehouses) -> every ITR coming to or leaving that zone."""
    q, args = "SELECT * FROM itr_requests WHERE 1=1", []
    if zone:
        stores = list(stores or [])
        q += " AND (to_zone=?" + (f" OR from_store IN ({','.join('?' * len(stores))})" if stores else "") + ")"
        args += [zone] + stores
    if status:
        statuses = [status] if isinstance(status, str) else list(status)
        q += f" AND status IN ({','.join('?' * len(statuses))})"
        args += statuses
    q += " ORDER BY id DESC"
    with _db() as c:
        cur = c.execute(q, args)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame([dict(r) for r in rows], columns=cols)
    for col in df.columns:
        if col in ("qty", "approved_qty"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        else:
            df[col] = df[col].astype(object).where(df[col].notna(), None)
    return df


# =============================================================================
# STOCK & WAREHOUSE HELPERS
# =============================================================================
def store_availability(mgr, item_code: str, safety_pct: float) -> Dict[str, Tuple[int, int]]:
    """{store: (stock, available_for_sale)} for one product across every store holding it."""
    out = {}
    for store, df in mgr.get_all_stores().items():
        rows = df[df["Item_Code"] == item_code]
        if rows.empty:
            continue
        stock = int(rows.iloc[0]["Total_Stock"])
        buffer = math.ceil(stock * safety_pct / 100) if stock > 0 else 0
        out[store] = (stock, max(0, stock - buffer))
    return out


def store_zone(store: str, owner: Dict[str, str]) -> Optional[str]:
    """Zone a store belongs to: 'Lahore Office' -> Lahore -> Zone III."""
    low = {c.lower(): z for c, z in owner.items()}
    name = re.sub(r"\s+office$", "", store.strip(), flags=re.IGNORECASE).lower()
    return low.get(name) or low.get(store.strip().lower())


def wh_zone(wh: str, owner: Dict[str, str]) -> Optional[str]:
    return store_zone(wh, owner) or WAREHOUSE_ZONE.get(wh)


def zone_warehouses(zone: str, owner: Dict[str, str]) -> List[str]:
    return [w for w in MAIN_WAREHOUSES if wh_zone(w, owner) == zone]


def warehouse_matrix(mgr, item_code: str, safety: float, owner: Dict[str, str]) -> List[Dict[str, Any]]:
    """The 5 main warehouses for ONE product: stock and what can be sent after safety buffer."""
    low = {k.lower(): v for k, v in store_availability(mgr, item_code, safety).items()}
    rows = []
    for w in MAIN_WAREHOUSES:
        stock, avail = low.get(w.lower(), (0, 0))
        rows.append({"Warehouse": w, "Zone": wh_zone(w, owner) or "—", "Stock": int(stock), "Available for ITR": int(avail)})
    return rows


def main_warehouse_table(mgr) -> Dict[str, Any]:
    """Every product x the 5 main warehouses (current stock)."""
    items = mgr.get_all_items()
    if not items:
        return {"columns": [], "rows": [], "groups": []}
    base = pd.DataFrame(items)[["Product_ID", "Item_Code", "Item_Name", "Group"]]
    low = {k.lower(): df for k, df in mgr.get_all_stores().items()}
    for w in MAIN_WAREHOUSES:
        df = low.get(w.lower())
        col = df.drop_duplicates("Item_Code").set_index("Item_Code")["Total_Stock"] if df is not None else pd.Series(dtype=int)
        base[w] = base["Item_Code"].map(col).fillna(0).astype(int)
    base["Total"] = base[MAIN_WAREHOUSES].sum(axis=1)
    base = base.drop(columns=["Item_Code"]).sort_values("Product_ID")
    groups = sorted(base["Group"].dropna().unique().tolist())
    return {
        "columns": list(base.columns),
        "rows": base.to_dict(orient="records"),
        "groups": groups,
    }


def itr_receive(mgr, r: Dict[str, Any], who: str) -> Tuple[bool, str]:
    qty = int(r["approved_qty"] or r["qty"])
    ok, msg = mgr.write_itr_line(r["id"], "IN", r["to_store"], r["item_code"], r["item_name"], qty)
    if not ok:
        return False, msg
    update_itr(r["id"], status=ITR_RECEIVED, received_by=who, received_at=_now())
    return True, f"ITR #{r['id']}: {qty:,} units received in {r['to_store']} — stock added."


def all_zones_stock_table(mgr, safety: float) -> Dict[str, Any]:
    """Read-only stock across every store with Available for Sale after safety buffer."""
    stores = mgr.get_all_stores()
    frames = []
    for office, df in stores.items():
        if df.empty:
            continue
        part = df[["Product_ID", "Item_Name", "Group", "Total_Stock"]].copy()
        part["Office"] = office
        part["Avail"] = part["Total_Stock"].map(
            lambda v: max(0, int(v) - (math.ceil(v * safety / 100) if v > 0 else 0))
        )
        frames.append(part)
    if not frames:
        return {"columns": [], "rows": [], "groups": []}
    allp = pd.concat(frames)
    wide = allp.pivot_table(
        index=["Product_ID", "Item_Name", "Group"], columns="Office",
        values="Total_Stock", aggfunc="sum", fill_value=0,
    )
    wide["Total Stock"] = wide.sum(axis=1)
    wide["Available for Sale"] = allp.groupby(["Product_ID", "Item_Name", "Group"])["Avail"].sum()
    wide = wide.reset_index().sort_values("Product_ID")
    for col in wide.columns:
        if col not in ("Item_Name", "Group"):
            wide[col] = wide[col].astype(int)
    return {
        "columns": list(wide.columns),
        "rows": wide.to_dict(orient="records"),
        "groups": sorted(wide["Group"].dropna().unique().tolist()),
    }


# =============================================================================
# ADMIN WORKFLOWS (APPROVALS, ITR, DASHBOARD, STATS, RESET)
# =============================================================================
def get_admin_approvals(mgr, safety_pct: float) -> List[Dict[str, Any]]:
    waiting = list_requests(status=[PENDING, HOLD])
    if waiting.empty:
        return []
    waiting = with_status(waiting).sort_values("id")
    owner = city_owner_map()
    out = []
    for _, r in waiting.iterrows():
        rec = r.to_dict()
        rec["status_label"] = STATUS_LABEL.get(rec["state"], rec["state"])
        subgroup = rec.get("subgroup") or "Order"
        rec["subgroup"] = subgroup
        is_order = subgroup in STOCK_SUBGROUPS
        rec["is_order"] = is_order
        rec["is_recovery"] = subgroup == "Recovery"
        if is_order:
            qty = int(rec["qty"] or 0)
            dest_zone = owner.get(rec["city"]) or rec["zone"]
            avail = store_availability(mgr, rec["item_code"], safety_pct)
            zone_stores = [s_ for s_ in avail if store_zone(s_, owner) == dest_zone]
            zone_avail = sum(avail[s_][1] for s_ in zone_stores)
            plan_info: Dict[str, Any] = {
                "dest_zone": dest_zone,
                "zone_stores": zone_stores,
                "zone_avail": int(zone_avail),
                "has_any_stock": bool(avail),
                "plan": None,
                "can_approve": False,
            }
            if avail and zone_stores:
                plan = mgr.plan_order(rec["item_code"], qty, safety_pct=safety_pct, only_store=zone_stores)
                if plan.get("success"):
                    plan["allocations"] = [
                        a for a in plan["allocations"] if a["Dispatch_Qty"] > 0 or a["Current_Stock"] > 0
                    ]
                    plan_info["plan"] = plan
                    plan_info["can_approve"] = plan.get("fulfilled_qty", 0) > 0
                else:
                    plan_info["error"] = plan.get("error", "Plan failed.")
            rec["allocation"] = plan_info
        else:
            rec["allocation"] = {"can_approve": True}
        out.append(rec)
    return out


def approve_request(mgr, req_id: int, admin: dict, note: str, safety_pct: float) -> Tuple[bool, str]:
    r = get_request(req_id)
    if not r:
        return False, "Request not found."
    if r["status"] not in (PENDING, HOLD):
        return False, f"Request #{req_id} is already {r['status']}."
    subgroup = r.get("subgroup") or "Order"
    if subgroup in STOCK_SUBGROUPS:
        owner = city_owner_map()
        dest_zone = owner.get(r["city"]) or r["zone"]
        avail = store_availability(mgr, r["item_code"], safety_pct)
        zone_stores = [s_ for s_ in avail if store_zone(s_, owner) == dest_zone]
        if not zone_stores:
            return False, f"{dest_zone} warehouse has no stock of this product. Use ITR first."
        plan = mgr.plan_order(r["item_code"], int(r["qty"]), safety_pct=safety_pct, only_store=zone_stores)
        if not plan.get("success") or plan.get("fulfilled_qty", 0) <= 0:
            return False, plan.get("error", "No stock available in the zone warehouse.")
        plan["group"] = r["grp"]
        plan["destination_city"] = r["city"]
        ok, res = mgr.confirm_and_release_order(f"{r['party']} ({r['zone']})", [plan])
        if not ok:
            return False, f"Could not release: {res}"
        update_request(
            r["id"],
            status=OK,
            order_id=res["order_id"],
            released_qty=res["total_released"],
            admin_note=(note or "").strip() or None,
            decided_by=admin["username"],
            decided_at=_now(),
        )
        return True, f"Order #{r['id']} approved & released ({res['order_id']}, {res['total_released']:,} units)."
    update_request(
        r["id"],
        status=OK,
        admin_note=(note or "").strip() or None,
        decided_by=admin["username"],
        decided_at=_now(),
    )
    return True, f"Request #{r['id']} marked OK."


def get_admin_itr_data(mgr, safety_pct: float) -> Dict[str, Any]:
    owner = city_owner_map()
    pend_df = list_itr(status=ITR_PENDING)
    pending_items = []
    if not pend_df.empty:
        for _, r in pend_df.sort_values("id").iterrows():
            rec = r.to_dict()
            mat = warehouse_matrix(mgr, rec["item_code"], safety_pct, owner)
            avail_by = {m["Warehouse"]: m["Available for ITR"] for m in mat}
            srcs = [w for w in MAIN_WAREHOUSES if w.lower() != str(rec["to_store"]).lower()]
            rec["matrix"] = mat
            rec["sources"] = [{"warehouse": w, "available": int(avail_by.get(w, 0))} for w in srcs]
            pending_items.append(rec)

    transit_df = list_itr(status=ITR_TRANSIT)
    transit_items = transit_df.sort_values("id").to_dict(orient="records") if not transit_df.empty else []

    hist_df = list_itr()
    if not hist_df.empty:
        hist_df["status_label"] = hist_df["status"].map(ITR_LABEL)
    history_items = hist_df.to_dict(orient="records") if not hist_df.empty else []
    return {"pending": pending_items, "transit": transit_items, "history": history_items}


def approve_itr(mgr, itr_id: int, from_store: str, send_qty: int, note: str, admin: dict, safety_pct: float) -> Tuple[bool, str]:
    r = get_itr(itr_id)
    if not r or r["status"] != ITR_PENDING:
        return False, "ITR request not found or not pending."
    owner = city_owner_map()
    mat = warehouse_matrix(mgr, r["item_code"], safety_pct, owner)
    avail_by = {m["Warehouse"]: m["Available for ITR"] for m in mat}
    src_av = int(avail_by.get(from_store, 0))
    if send_qty < 1 or send_qty > src_av:
        return False, f"{from_store} only has {src_av:,} units available for ITR."
    ok, msg = mgr.write_itr_line(r["id"], "OUT", from_store, r["item_code"], r["item_name"], int(send_qty))
    if not ok:
        return False, msg
    update_itr(
        r["id"],
        status=ITR_TRANSIT,
        from_store=from_store,
        approved_qty=int(send_qty),
        admin_note=(note or "").strip() or None,
        decided_by=admin["username"],
        decided_at=_now(),
    )
    return True, f"ITR #{r['id']} approved: {int(send_qty):,} units left {from_store} (in transit to {r['to_store']})."


def _df_pivot_records(pv: pd.DataFrame, index_name: str = "Zone") -> Dict[str, Any]:
    pv = pv.copy()
    pv.insert(0, index_name, pv.index.astype(str))
    pv = pv.reset_index(drop=True)
    return {"columns": list(pv.columns), "rows": pv.to_dict(orient="records")}


def get_admin_dashboard(
    mgr,
    f_ord: Optional[List[str]] = None,
    f_rec: Optional[List[str]] = None,
    f_del: Optional[List[str]] = None,
    zones_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    df = list_requests()
    if df.empty:
        return {"empty": True}
    df = with_status(df)
    df["subgroup"] = df["subgroup"].fillna("Order")
    orders = df[df.subgroup == "Order"]
    recov = df[df.subgroup == "Recovery"]
    deliv = orders[orders["challan_no"].fillna("") != ""].copy()
    groups = list(dict.fromkeys(list(mgr.get_groups()) + [g for g in df["grp"].dropna().unique()]))

    f_ord = f_ord if f_ord is not None else STATUSES
    f_rec = f_rec if f_rec is not None else STATUSES
    f_del = f_del if f_del is not None else DELIVERY_STATES
    zones_filter = zones_filter if zones_filter is not None else ZONES

    order_kpis = [
        {"state": name, "label": STATUS_LABEL[name], "count": int((orders.state == name).sum())}
        for name in ALL_STATES
    ]
    by_status = (
        orders.pivot_table(index="zone", columns="state", values="id", aggfunc="count", fill_value=0)
        .reindex(index=ZONES, columns=ALL_STATES, fill_value=0)
    )
    by_status["Total"] = by_status.sum(axis=1)
    by_status.loc["Total"] = by_status.sum()
    by_status = by_status.astype(int)

    def zone_group_table(part: pd.DataFrame) -> Dict[str, Any]:
        pv = (
            part.pivot_table(index="zone", columns="grp", values="id", aggfunc="count", fill_value=0)
            .reindex(index=ZONES, columns=groups, fill_value=0)
        )
        pv["Total"] = pv.sum(axis=1)
        pv.loc["Total"] = pv.sum()
        return _df_pivot_records(pv.astype(int))

    dv = (
        deliv.pivot_table(index="zone", columns="delivery_status", values="id", aggfunc="count", fill_value=0)
        .reindex(index=ZONES, columns=DELIVERY_STATES, fill_value=0)
    )
    awaiting = orders[(orders.status == OK) & (orders["challan_no"].fillna("") == "")].groupby("zone")["id"].count()
    dv.insert(0, "Awaiting challan", awaiting.reindex(ZONES, fill_value=0))
    dv = dv.astype(int)

    amt = (
        recov[recov.state == "OK"].groupby("zone")["amount"].sum().reindex(ZONES, fill_value=0)
        .rename("Rs. recovered").to_frame()
    )
    amt.loc["Total"] = amt.sum()
    amt = amt.round(0).astype(int)

    cols = [
        "id", "created_at", "zone", "subgroup", "username", "party", "item_name",
        "grp", "city", "qty", "amount", "ref_no", "state", "released_qty",
        "order_id", "challan_no", "delivery_status", "return_qty", "return_reason", "admin_note",
    ]
    all_rows = df[df.zone.isin(zones_filter)][cols].to_dict(orient="records")

    return {
        "empty": False,
        "new_window_min": NEW_WINDOW_MIN,
        "order_kpis": order_kpis,
        "by_status": _df_pivot_records(by_status),
        "zone_group_order": zone_group_table(orders[orders.state.isin(f_ord)]),
        "zone_group_recovery": zone_group_table(recov[recov.state.isin(f_rec)]),
        "zone_group_delivery": zone_group_table(deliv[deliv.delivery_status.isin(f_del)]),
        "delivery_by_zone": _df_pivot_records(dv),
        "recovered_by_zone": _df_pivot_records(amt),
        "all_requests": all_rows,
    }


def _last_activity(part: pd.DataFrame) -> str:
    vals = []
    for col in ("created_at", "decided_at", "dispatched_at", "delivered_at"):
        if col in part:
            vals += [v for v in part[col].tolist() if v]
    return max(vals) if vals else "—"


def get_admin_stats(period: str = "All time") -> Dict[str, Any]:
    df = list_requests()
    users = list_users()
    if df.empty:
        return {"empty": True}
    df = with_status(df)
    df["subgroup"] = df["subgroup"].fillna("Order")
    if period != "All time":
        days = {"Today": 0, "Last 7 days": 6, "Last 30 days": 29}.get(period, 0)
        start = (datetime.now() - pd.Timedelta(days=days)).strftime("%Y-%m-%d") + " 00:00:00"
        df = df[df["created_at"] >= start]

    orders = df[df.subgroup == "Order"]
    recov = df[df.subgroup == "Recovery"]
    deliv = orders[orders["challan_no"].fillna("") != ""]

    kpis = {
        "orders": int(len(orders)),
        "units_released": int(orders.loc[orders.status == OK, "released_qty"].sum()),
        "dispatched": int(len(deliv)),
        "delivered": int((deliv.delivery_status == D_DELIVERED).sum()),
        "recovery_entries": int(len(recov)),
        "recovered_ok": float(recov.loc[recov.state == "OK", "amount"].sum()),
    }

    glance_rows = []
    for z in ZONES:
        part = orders[orders.zone == z]
        n_users = int(((users.role == "user") & (users.zone == z) & (users.active == 1)).sum()) if not users.empty else 0
        row = {"Portal": f"🏷️ Order Portal — {z}", "Users": n_users, "Entries": int(len(part))}
        row.update({name: int((part.state == name).sum()) for name in ALL_STATES})
        row["Last activity"] = _last_activity(part)
        glance_rows.append(row)
    n_rec = int(((users.role == "recovery") & (users.active == 1)).sum()) if not users.empty else 0
    rec_row = {"Portal": "💰 Recovery Portal (all zones)", "Users": n_rec, "Entries": int(len(recov))}
    rec_row.update({name: int((recov.state == name).sum()) for name in ALL_STATES})
    rec_row["Last activity"] = _last_activity(recov)
    glance_rows.append(rec_row)

    ord_rows = []
    for z in ZONES:
        part = orders[orders.zone == z]
        dz = part[part["challan_no"].fillna("") != ""]
        ord_rows.append({
            "Zone": z,
            "Orders": int(len(part)),
            "Units requested": int(part.loc[part.state != "Cancelled", "qty"].sum()),
            "Units released": int(part.loc[part.status == OK, "released_qty"].sum()),
            "Awaiting challan": int(((part.status == OK) & (part["challan_no"].fillna("") == "")).sum()),
            "Dispatched": int((dz.delivery_status == D_DISPATCHED).sum()),
            "Not delivered yet": int((dz.delivery_status == D_NOT_YET).sum()),
            "Delivered": int((dz.delivery_status == D_DELIVERED).sum()),
            "Returned": int(dz.delivery_status.isin(RETURN_STATES).sum()),
            "Units returned": int(dz.loc[dz.delivery_status.isin(RETURN_STATES), "return_qty"].sum()),
            "Cancelled by consumer": int((part.state == "Cancelled").sum()),
        })
    ord_tbl = pd.DataFrame(ord_rows)
    ord_total = {"Zone": "Total", **{c: int(ord_tbl[c].sum()) for c in ord_tbl.columns if c != "Zone"}}
    ord_tbl = pd.concat([ord_tbl, pd.DataFrame([ord_total])])

    rec_rows = []
    for z in ZONES:
        part = recov[recov.zone == z]
        amt = lambda names: float(part.loc[part.state.isin(names), "amount"].sum())
        rec_rows.append({
            "Zone": z,
            "Entries": int(len(part)),
            "OK": amt(["OK"]),
            "Waiting (New + In process)": amt(["New", "In process"]),
            "Hold": amt(["Hold"]),
            "Rejected": amt(["Rejected"]),
        })
    rec_tbl = pd.DataFrame(rec_rows)
    rec_total = {"Zone": "Total", **{c: rec_tbl[c].sum() for c in rec_tbl.columns if c != "Zone"}}
    rec_tbl = pd.concat([rec_tbl, pd.DataFrame([rec_total])])
    for c in rec_tbl.columns:
        if c not in ("Zone", "Entries"):
            rec_tbl[c] = rec_tbl[c].map(lambda v: f"{v:,.0f}")

    user_rows = []
    for _, u in users.iterrows():
        if u["role"] == "admin":
            continue
        mine = df[df.username == u["username"]]
        portal_name = "Recovery Portal" if u["role"] == "recovery" else f"Order Portal — {u['zone']}"
        user_rows.append({
            "User": u["username"],
            "Name": u["full_name"],
            "Portal": portal_name,
            "Active": "Yes" if u["active"] else "No",
            "Entries made": int(len(mine)),
            "Last entry": mine["created_at"].max() if len(mine) else "—",
        })

    return {
        "empty": False,
        "kpis": kpis,
        "glance": {"columns": list(pd.DataFrame(glance_rows).columns), "rows": glance_rows},
        "order_portals": {"columns": list(ord_tbl.columns), "rows": ord_tbl.to_dict(orient="records")},
        "recovery_portals": {"columns": list(rec_tbl.columns), "rows": rec_tbl.to_dict(orient="records")},
        "users_activity": {
            "columns": ["User", "Name", "Portal", "Active", "Entries made", "Last entry"],
            "rows": user_rows,
        },
    }


def count_orders_for_reset(mgr) -> Tuple[int, List[str]]:
    with _db() as c:
        n_req = c.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        n_req += c.execute("SELECT COUNT(*) FROM itr_requests").fetchone()[0]
    files = [f for f in glob.glob(os.path.join(mgr.orders_dir, "Order_*")) if f.endswith((".xlsx", ".csv"))]
    files += [f for f in glob.glob(os.path.join(mgr.itr_dir, "ITR_*")) if f.endswith(".xlsx")]
    return n_req, files


def reset_all_orders(mgr, keep_backup: bool = True) -> Dict[str, Any]:
    """Deletes EVERY order, recovery, delivery, return and ITR + released files."""
    n_req, files = count_orders_for_reset(mgr)
    backup_dir = None
    if keep_backup:
        backup_dir = os.path.join(
            os.path.dirname(DB_PATH), "backup_before_reset_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        os.makedirs(backup_dir, exist_ok=True)
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(os.path.join(backup_dir, "portal.db"))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    removed = 0
    for path in files:
        try:
            if backup_dir:
                shutil.move(path, os.path.join(backup_dir, os.path.basename(path)))
            else:
                os.remove(path)
            removed += 1
        except Exception as error:
            return {"ok": False, "error": f"Could not remove '{os.path.basename(path)}' - is it open in Excel? ({error})"}
    with _db() as c:
        c.execute("DELETE FROM requests")
        c.execute("DELETE FROM itr_requests")
        c.execute("DELETE FROM sqlite_sequence WHERE name IN ('requests','itr_requests')")
    return {"ok": True, "requests": n_req, "files": removed, "backup": backup_dir}

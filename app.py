"""
=============================================================================
FastAPI Web Application (app.py) — Runs natively on Uvicorn
=============================================================================
Enterprise Multi-Store Inventory & Order Allocation System
Run with:
    uvicorn app:app --reload
    uvicorn app:app --host 0.0.0.0 --port 8000
=============================================================================
"""

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import portal
from inventory_manager import InventoryManager, StockReportError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
SECRET_FILE = os.path.join(BASE_DIR, "data", ".session_secret")

app = FastAPI(title="Multi-Store Stock & Order Allocation System", version="2.0.0")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

mgr = InventoryManager()
mgr_lock = threading.RLock()

# Initialize database & default zone-city mappings at startup
portal.init_db()
portal.seed_default_zones()


def _load_secret() -> bytes:
    os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
    if os.path.isfile(SECRET_FILE):
        try:
            with open(SECRET_FILE, "rb") as f:
                data = f.read().strip()
                if len(data) >= 32:
                    return data
        except OSError:
            pass
    key = secrets.token_hex(32).encode("utf-8")
    try:
        with open(SECRET_FILE, "wb") as f:
            f.write(key)
    except OSError:
        pass
    return key


SESSION_SECRET = _load_secret()
COOKIE_NAME = "stock_portal_session"

WELCOME_MSG = (
    "👋 **Welcome! I am your Order Allocation Assistant to:**\n\n"
    "• **Real-Time Stock Check**\n\n"
    "• **Context Memory**\n\n"
    "• **Auto-Cascading Dispatch**\n\n"
    "• **Safety Stock Reserve**\n\n"
    "• **Integrated Knowledge Base**"
)

_CHAT_SESSIONS: Dict[str, Dict[str, Any]] = {}
_CHAT_LOCK = threading.Lock()


def _new_chat_state() -> Dict[str, Any]:
    return {
        "context": {
            "last_item_code": None,
            "last_item_name": None,
            "last_party_name": None,
            "last_quantity": None,
            "pending_plan": None,
            "last_order_id": None,
            "last_order_details": None,
        },
        "messages": [{"role": "assistant", "content": WELCOME_MSG}],
    }


def _get_chat_state(sid: str) -> Dict[str, Any]:
    with _CHAT_LOCK:
        if sid not in _CHAT_SESSIONS:
            _CHAT_SESSIONS[sid] = _new_chat_state()
        return _CHAT_SESSIONS[sid]


# =============================================================================
# HELPERS & AUTHENTICATION
# =============================================================================
def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return [_to_jsonable(r) for r in obj.to_dict(orient="records")]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        val = float(obj)
        return None if math.isnan(val) or math.isinf(val) else val
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, (datetime, pd.Timestamp)):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    if obj is pd.NA:
        return None
    return obj


def _json_ok(payload: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=_to_jsonable(payload), status_code=status_code)


def _sign_token(payload: Dict[str, str]) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    sig = hmac.new(SESSION_SECRET, raw.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _verify_token(token: Optional[str]) -> Optional[Dict[str, str]]:
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET, raw.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        if isinstance(data, dict) and data.get("u") and data.get("sid"):
            return data
    except Exception:
        return None
    return None


def _get_session_user(request: Request, allow_must_change: bool = False) -> Dict[str, Any]:
    token_data = _verify_token(request.cookies.get(COOKIE_NAME))
    if not token_data:
        raise HTTPException(status_code=401, detail="Not authenticated")
    fresh = portal.get_user(token_data["u"])
    if not fresh or not fresh["active"]:
        raise HTTPException(status_code=401, detail="Account is unavailable or disabled")
    if fresh.get("must_change") and not allow_must_change:
        raise HTTPException(status_code=403, detail="Password change required before continuing")
    return {**portal.safe_user(fresh), "_sid": token_data["sid"]}


def require_user(request: Request) -> Dict[str, Any]:
    return _get_session_user(request, allow_must_change=False)


def require_admin(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_zone_user(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    if user["role"] != "user":
        raise HTTPException(status_code=403, detail="Zone user access required")
    return user


def require_recovery_user(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    if user["role"] != "recovery":
        raise HTTPException(status_code=403, detail="Recovery user access required")
    return user


def _safety_pct() -> float:
    try:
        val = float(portal.get_setting("safety_pct", "15"))
        return max(10.0, min(30.0, val))
    except ValueError:
        return 15.0


# =============================================================================
# REQUEST MODELS
# =============================================================================
class LoginBody(BaseModel):
    username: str
    password: str


class ForcePasswordBody(BaseModel):
    new_password: str
    confirm_password: str


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str


class SafetyBody(BaseModel):
    safety_pct: int


class ChatMsgBody(BaseModel):
    message: str


class ManualOrderPlanBody(BaseModel):
    party_name: str
    item_code: str
    quantity: int


class NoteBody(BaseModel):
    note: str = ""


class ApproveItrBody(BaseModel):
    from_store: str
    send_qty: int
    note: str = ""


class CreateUserBody(BaseModel):
    username: str
    full_name: str
    password: str
    zone: Optional[str] = None
    role: str = "user"


class UpdateUserBody(BaseModel):
    zone: Optional[str] = None
    active: Optional[int] = None


class ResetPwBody(BaseModel):
    new_password: str


class ZoneCitiesBody(BaseModel):
    cities: List[str]
    new_city: str = ""


class ResetAllBody(BaseModel):
    confirm_text: str
    keep_backup: bool = True


class ZoneOrderBody(BaseModel):
    party: str
    item_code: str
    qty: int
    city: str


class ZoneAvailBody(BaseModel):
    item_code: str
    qty: int = 1


class DispatchBody(BaseModel):
    challan_no: str


class DeliveryStatusBody(BaseModel):
    status: str


class ReturnBody(BaseModel):
    qty: int
    reason: str
    return_challan: str = ""


class ZoneItrBody(BaseModel):
    item_code: str
    from_store: str
    to_store: str
    qty: int
    note: str = ""


class RecoveryEntryBody(BaseModel):
    party: str
    zone: str
    city: str
    grp: str
    amount: float
    ref_no: str = ""
    note: str = ""


# =============================================================================
# ROOT PAGE
# =============================================================================
@app.get("/", response_class=HTMLResponse)
def index():
    html_path = os.path.join(TEMPLATES_DIR, "index.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="Frontend template not found")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/health")
def health():
    return {"status": "ok", "service": "stock-inventory-chatbot"}


# =============================================================================
# AUTH ENDPOINTS
# =============================================================================
@app.get("/api/auth/me")
def auth_me(request: Request):
    token_data = _verify_token(request.cookies.get(COOKIE_NAME))
    if not token_data:
        return _json_ok({"authenticated": False, "user": None})
    fresh = portal.get_user(token_data["u"])
    if not fresh or not fresh["active"]:
        resp = _json_ok({"authenticated": False, "user": None})
        resp.delete_cookie(COOKIE_NAME)
        return resp
    return _json_ok({"authenticated": True, "user": portal.safe_user(fresh)})


@app.post("/api/auth/login")
def auth_login(body: LoginBody):
    found = portal.authenticate(body.username, body.password)
    if not found:
        raise HTTPException(status_code=401, detail="Wrong username or password, or the account is disabled.")
    sid = secrets.token_hex(16)
    token = _sign_token({"u": found["username"], "sid": sid})
    resp = _json_ok({"ok": True, "user": portal.safe_user(found)})
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=86400 * 7)
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    token_data = _verify_token(request.cookies.get(COOKIE_NAME))
    if token_data:
        with _CHAT_LOCK:
            _CHAT_SESSIONS.pop(token_data["sid"], None)
    resp = _json_ok({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.post("/api/auth/force-password")
def auth_force_password(body: ForcePasswordBody, request: Request):
    user = _get_session_user(request, allow_must_change=True)
    p1, p2 = body.new_password, body.confirm_password
    if len(p1) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    if p1 != p2:
        raise HTTPException(status_code=400, detail="The two passwords do not match.")
    if p1 == "admin123" or (p1.lower().startswith(("zone", "recovery")) and p1.endswith("@123")):
        raise HTTPException(status_code=400, detail="Please do not reuse the default password.")
    portal.set_password(user["username"], p1, must_change=0)
    fresh = portal.get_user(user["username"])
    return _json_ok({"ok": True, "user": portal.safe_user(fresh)})


@app.post("/api/auth/change-password")
def auth_change_password(body: ChangePasswordBody, user: Dict[str, Any] = Depends(require_user)):
    if not portal.authenticate(user["username"], body.old_password):
        raise HTTPException(status_code=400, detail="Current password is wrong.")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters.")
    portal.set_password(user["username"], body.new_password)
    return _json_ok({"ok": True, "message": "Password updated."})


# =============================================================================
# ADMIN SIDEBAR & SYSTEM OVERVIEW
# =============================================================================
@app.get("/api/admin/overview")
def admin_overview(user: Dict[str, Any] = Depends(require_admin)):
    with mgr_lock:
        stores = mgr.get_all_stores()
        total_units = sum(int(df["Total_Stock"].sum()) for df in stores.values()) if stores else 0
        load_info = mgr.last_load_info
        report_files = mgr.get_report_files()
        deducted = mgr.get_order_deductions()
        items = mgr.get_all_items()
    files_info = []
    for fi in load_info.get("files", []):
        age_days = 0
        if fi.get("date"):
            try:
                age_days = (datetime.now().date() - datetime.strptime(fi["date"], "%Y-%m-%d").date()).days
            except ValueError:
                age_days = 0
        files_info.append({**fi, "age_days": age_days})
    return _json_ok({
        "safety_pct": int(_safety_pct()),
        "active_stores": len(stores),
        "total_units": total_units,
        "store_names": list(stores.keys()),
        "items": items,
        "deducted_units": deducted,
        "load_error": load_info.get("error"),
        "warnings": load_info.get("warnings", []),
        "loaded_files": files_info,
        "report_files": report_files,
    })


@app.post("/api/admin/safety")
def set_safety(body: SafetyBody, user: Dict[str, Any] = Depends(require_admin)):
    val = max(10, min(30, int(body.safety_pct)))
    portal.set_setting("safety_pct", val)
    return _json_ok({"ok": True, "safety_pct": val})


# =============================================================================
# CHATBOT & ORDER DISPATCH (ADMIN TAB)
# =============================================================================
@app.get("/api/chat/state")
def chat_state(user: Dict[str, Any] = Depends(require_admin)):
    st = _get_chat_state(user["_sid"])
    with mgr_lock:
        items = mgr.get_all_items()
    return _json_ok({
        "messages": st["messages"],
        "context": st["context"],
        "items": items,
        "safety_pct": int(_safety_pct()),
    })


@app.post("/api/chat/message")
def chat_message(body: ChatMsgBody, user: Dict[str, Any] = Depends(require_admin)):
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    st = _get_chat_state(user["_sid"])
    safety = _safety_pct()
    with mgr_lock:
        result = mgr.process_chat_message(user_input=text, context=st["context"], safety_pct=safety)
    with _CHAT_LOCK:
        st["messages"].append({"role": "user", "content": text})
        st["context"] = result["updated_context"]
        st["messages"].append({"role": "assistant", "content": result["reply"]})
    return _json_ok({
        "messages": st["messages"],
        "context": st["context"],
        "action": result["action"],
    })


@app.post("/api/chat/manual-plan")
def chat_manual_plan(body: ManualOrderPlanBody, user: Dict[str, Any] = Depends(require_admin)):
    party = body.party_name.strip() or "Valued Client"
    if body.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1.")
    st = _get_chat_state(user["_sid"])
    safety = _safety_pct()
    with mgr_lock:
        plan = mgr.plan_order(body.item_code, int(body.quantity), safety_pct=safety)
    if not plan.get("success"):
        raise HTTPException(status_code=400, detail=plan.get("error", "Could not build allocation plan."))
    with _CHAT_LOCK:
        ctx = st["context"]
        ctx["last_item_code"] = plan["item_code"]
        ctx["last_item_name"] = plan["item_name"]
        ctx["last_party_name"] = party
        ctx["last_quantity"] = int(body.quantity)
        ctx["pending_plan"] = {"party": party, "plan": plan, "plans": [plan]}
        st["messages"].append({
            "role": "assistant",
            "content": (
                f"📋 **Direct Order Proposal for {party}:**\n\n"
                f"• **Product:** {plan['item_name']} (`{plan['item_code']}`)\n"
                f"• **Requested:** {plan['requested_qty']:,} units · **Fulfillable:** {plan['fulfilled_qty']:,} units\n\n"
                f"{plan['split_summary']}"
            ),
        })
    return _json_ok({"messages": st["messages"], "context": st["context"]})


@app.post("/api/chat/confirm")
def chat_confirm(user: Dict[str, Any] = Depends(require_admin)):
    st = _get_chat_state(user["_sid"])
    pending = st["context"].get("pending_plan")
    if not pending:
        raise HTTPException(status_code=400, detail="No active order proposal to confirm.")
    party = pending.get("party", "Valued Client")
    plans = pending.get("plans") or [pending["plan"]]
    with mgr_lock:
        success, res = mgr.confirm_and_release_order(party, plans)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to release order: {res}")
    with _CHAT_LOCK:
        st["context"]["last_order_id"] = res["order_id"]
        st["context"]["last_order_details"] = res
        st["context"]["pending_plan"] = None
        msg = (
            f"🎉 **Order Released Successfully!**\n\n"
            f"• **Order ID:** `{res['order_id']}`\n"
            f"• **Client / Party:** {res['party_name']}\n"
            f"• **Products:** {res['item_name']}\n"
            f"• **Total Dispatched:** {res['total_released']:,} units\n"
            f"• **Fulfilling Stores:** {', '.join(res['stores_involved'])}\n\n"
            f"**System Actions Completed:**\n"
            f"1. Units deducted from allocated store sheets.\n"
            f"2. Excel file created in the orders folder: `{res['file_name']}`"
        )
        st["messages"].append({"role": "assistant", "content": msg})
    return _json_ok({"ok": True, "result": res, "messages": st["messages"], "context": st["context"]})


@app.post("/api/chat/cancel")
def chat_cancel(user: Dict[str, Any] = Depends(require_admin)):
    st = _get_chat_state(user["_sid"])
    with _CHAT_LOCK:
        st["context"]["pending_plan"] = None
        st["messages"].append({"role": "assistant", "content": "🛑 The order proposal was cancelled."})
    return _json_ok({"ok": True, "messages": st["messages"], "context": st["context"]})


@app.post("/api/chat/reset")
def chat_reset(user: Dict[str, Any] = Depends(require_admin)):
    with _CHAT_LOCK:
        _CHAT_SESSIONS[user["_sid"]] = _new_chat_state()
        st = _CHAT_SESSIONS[user["_sid"]]
    return _json_ok({"messages": st["messages"], "context": st["context"]})


# =============================================================================
# STOCK REPORT MANAGEMENT & VIEW (ADMIN)
# =============================================================================
@app.post("/api/stock/upload")
async def stock_upload(files: List[UploadFile] = File(...), user: Dict[str, Any] = Depends(require_admin)):
    ok_lines, bad_lines = [], []
    for uf in files:
        fbytes = await uf.read()
        fname = uf.filename or "report.xlsx"
        try:
            with mgr_lock:
                parsed = mgr.save_uploaded_report(fbytes, fname)
            ok_lines.append(
                f"{fname} — report date {parsed['report_date'] or 'not found in file'}, "
                f"offices: {', '.join(parsed['stores'])}, {int(parsed['batches']['Total_Stock'].sum()):,} units."
            )
        except StockReportError as err:
            bad_lines.append(f"{fname}: {err}")
    if ok_lines:
        st = _get_chat_state(user["_sid"])
        with _CHAT_LOCK:
            st["context"]["pending_plan"] = None
    return _json_ok({"ok_lines": ok_lines, "bad_lines": bad_lines})


@app.get("/api/stock/download/{filename}")
def stock_download(filename: str, user: Dict[str, Any] = Depends(require_admin)):
    safe = os.path.basename(filename)
    path = os.path.join(mgr.data_dir, safe)
    if not safe.lower().endswith((".xlsx", ".xlsm")) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Report file not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=safe,
    )


@app.delete("/api/stock/file/{filename}")
def stock_delete_file(filename: str, user: Dict[str, Any] = Depends(require_admin)):
    with mgr_lock:
        ok, msg = mgr.delete_report_file(filename)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    st = _get_chat_state(user["_sid"])
    with _CHAT_LOCK:
        st["context"]["pending_plan"] = None
    return _json_ok({"ok": True, "message": msg})


@app.delete("/api/stock/files/all")
def stock_delete_all(user: Dict[str, Any] = Depends(require_admin)):
    with mgr_lock:
        ok, msg = mgr.delete_all_report_files()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    st = _get_chat_state(user["_sid"])
    with _CHAT_LOCK:
        st["context"]["pending_plan"] = None
    return _json_ok({"ok": True, "message": msg})


@app.get("/api/stock/view")
def stock_view(
    store: str = Query("ALL"),
    search: str = Query(""),
    user: Dict[str, Any] = Depends(require_admin),
):
    safety_pct = _safety_pct()
    ratio = safety_pct / 100.0
    with mgr_lock:
        stores = mgr.get_all_stores()
        batches = mgr.get_stock_batches() if stores else pd.DataFrame()

    if not stores:
        return _json_ok({"empty": True})

    if store == "ALL" or store not in stores:
        combined = pd.concat([df.assign(Office=name) for name, df in stores.items()], ignore_index=True)
        wide = combined.pivot_table(
            index=["Product_ID", "Item_Name", "Group"], columns="Office", values="Total_Stock",
            aggfunc="sum", fill_value=0,
        )
        wide["Total"] = wide.sum(axis=1)
        wide = wide.reset_index().sort_values("Product_ID")
        if search.strip():
            wide = wide[wide["Item_Name"].str.contains(search.strip(), case=False, na=False)]
        return _json_ok({
            "empty": False,
            "mode": "ALL",
            "products_count": int(len(wide)),
            "total_units": int(wide["Total"].sum()) if not wide.empty else 0,
            "columns": list(wide.columns),
            "rows": wide.to_dict(orient="records"),
        })

    df_sel = stores[store].copy()
    df_sel["Safety_Buffer"] = df_sel["Total_Stock"].apply(lambda s: int(s * ratio) if s > 0 else 0)
    df_sel["Available_for_Sale"] = (df_sel["Total_Stock"] - df_sel["Safety_Buffer"]).clip(lower=0)
    total_stock = int(df_sel["Total_Stock"].sum())
    avail_stock = int(df_sel["Available_for_Sale"].sum())

    if search.strip():
        df_sel = df_sel[df_sel["Item_Name"].str.contains(search.strip(), case=False, na=False)]
    cols = ["Product_ID", "Item_Name", "Group", "Total_Stock", "Safety_Buffer", "Available_for_Sale"]
    detail = batches[batches["Store_Name"] == store] if not batches.empty else pd.DataFrame()
    if not detail.empty and search.strip():
        detail = detail[detail["Item_Name"].str.contains(search.strip(), case=False, na=False)]
    batch_cols = ["Product_ID", "Item_Name", "Batch_No", "Expiry_Date", "Opening_Stock", "Sales", "Total_Stock", "Remarks"]
    detail_out = detail[batch_cols].rename(columns={"Total_Stock": "Closing_Balance"}) if not detail.empty else pd.DataFrame(columns=batch_cols)

    return _json_ok({
        "empty": False,
        "mode": "SINGLE",
        "office": store,
        "total_stock": total_stock,
        "available_stock": avail_stock,
        "columns": cols,
        "rows": df_sel[cols].to_dict(orient="records"),
        "batch_columns": list(detail_out.columns),
        "batch_rows": detail_out.to_dict(orient="records"),
    })


# =============================================================================
# RELEASED ORDERS HISTORY (ADMIN)
# =============================================================================
@app.get("/api/orders/history")
def orders_history(user: Dict[str, Any] = Depends(require_admin)):
    with mgr_lock:
        hist = mgr.get_order_history()
        summaries = mgr.get_order_summaries()
    files = []
    if os.path.exists(mgr.orders_dir):
        files = sorted(
            [f for f in os.listdir(mgr.orders_dir) if f.startswith("Order_") and f.endswith((".xlsx", ".csv"))],
            reverse=True,
        )
    return _json_ok({
        "empty": hist.empty,
        "columns": list(hist.columns) if not hist.empty else [],
        "rows": hist.to_dict(orient="records") if not hist.empty else [],
        "summaries": summaries,
        "files": files,
    })


@app.delete("/api/orders/{order_id}")
def orders_delete(order_id: str, user: Dict[str, Any] = Depends(require_admin)):
    with mgr_lock:
        ok, res = mgr.delete_order(order_id)
    if not ok:
        raise HTTPException(status_code=400, detail=str(res))
    st = _get_chat_state(user["_sid"])
    with _CHAT_LOCK:
        ctx_last = st["context"].get("last_order_details")
        if ctx_last and ctx_last.get("order_id") == res["order_id"]:
            st["context"]["last_order_id"] = None
            st["context"]["last_order_details"] = None
    stock_note = (
        f" Stock returned: {', '.join(res['restored_lines'])}."
        if res["stock_restored"] and res["restored_lines"]
        else " Stock was not changed (a newer stock report is loaded)."
    )
    return _json_ok({"ok": True, "message": f"Order {res['order_id']} ({res['party_name']}) was deleted.{stock_note}"})


@app.get("/api/orders/download/{filename}")
def orders_download(filename: str, user: Dict[str, Any] = Depends(require_admin)):
    safe = os.path.basename(filename)
    path = os.path.join(mgr.orders_dir, safe)
    if not safe.startswith("Order_") or not safe.endswith((".xlsx", ".csv")) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Order file not found")
    mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if safe.endswith(".xlsx")
        else "text/csv"
    )
    return FileResponse(path, media_type=mime, filename=safe)


# =============================================================================
# KNOWLEDGE BASE
# =============================================================================
@app.get("/api/kb")
def kb_list(q: str = Query(""), user: Dict[str, Any] = Depends(require_user)):
    match = mgr.kb.search(q) if q.strip() else None
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for art in mgr.kb.articles:
        grouped.setdefault(art["category"], []).append(art)
    return _json_ok({"match": match, "categories": grouped})


# =============================================================================
# ADMIN PORTAL ENDPOINTS (APPROVALS, ITR, DASHBOARD, STATS, USERS, ZONES, RESET)
# =============================================================================
@app.get("/api/admin/approvals")
def admin_approvals_list(user: Dict[str, Any] = Depends(require_admin)):
    with mgr_lock:
        items = portal.get_admin_approvals(mgr, _safety_pct())
    return _json_ok({"items": items})


@app.post("/api/admin/approvals/{req_id}/approve")
def admin_approval_ok(req_id: int, body: NoteBody, user: Dict[str, Any] = Depends(require_admin)):
    with mgr_lock:
        ok, msg = portal.approve_request(mgr, req_id, user, body.note, _safety_pct())
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return _json_ok({"ok": True, "message": msg})


@app.post("/api/admin/approvals/{req_id}/hold")
def admin_approval_hold(req_id: int, body: NoteBody, user: Dict[str, Any] = Depends(require_admin)):
    if not body.note.strip():
        raise HTTPException(status_code=400, detail="Please write the payment issue in the note box.")
    r = portal.get_request(req_id)
    if not r:
        raise HTTPException(status_code=404, detail="Request not found.")
    portal.update_request(
        req_id, status=portal.HOLD, admin_note=body.note.strip(),
        decided_by=user["username"], decided_at=portal._now(),
    )
    return _json_ok({"ok": True, "message": f"Request #{req_id} put on Hold."})


@app.post("/api/admin/approvals/{req_id}/reject")
def admin_approval_reject(req_id: int, body: NoteBody, user: Dict[str, Any] = Depends(require_admin)):
    if not body.note.strip():
        raise HTTPException(status_code=400, detail="Please write a reason in the note box.")
    r = portal.get_request(req_id)
    if not r:
        raise HTTPException(status_code=404, detail="Request not found.")
    portal.update_request(
        req_id, status=portal.REJECTED, admin_note=body.note.strip(),
        decided_by=user["username"], decided_at=portal._now(),
    )
    return _json_ok({"ok": True, "message": f"Request #{req_id} rejected."})


@app.get("/api/admin/itr")
def admin_itr_list(user: Dict[str, Any] = Depends(require_admin)):
    with mgr_lock:
        data = portal.get_admin_itr_data(mgr, _safety_pct())
    return _json_ok(data)


@app.post("/api/admin/itr/{itr_id}/approve")
def admin_itr_approve(itr_id: int, body: ApproveItrBody, user: Dict[str, Any] = Depends(require_admin)):
    with mgr_lock:
        ok, msg = portal.approve_itr(mgr, itr_id, body.from_store, body.send_qty, body.note, user, _safety_pct())
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return _json_ok({"ok": True, "message": msg})


@app.post("/api/admin/itr/{itr_id}/reject")
def admin_itr_reject(itr_id: int, body: NoteBody, user: Dict[str, Any] = Depends(require_admin)):
    if not body.note.strip():
        raise HTTPException(status_code=400, detail="Please write a reason in the note box.")
    r = portal.get_itr(itr_id)
    if not r or r["status"] != portal.ITR_PENDING:
        raise HTTPException(status_code=400, detail="ITR request not found or not pending.")
    portal.update_itr(
        itr_id, status=portal.ITR_REJECTED, admin_note=body.note.strip(),
        decided_by=user["username"], decided_at=portal._now(),
    )
    return _json_ok({"ok": True, "message": f"ITR #{itr_id} rejected."})


@app.post("/api/admin/itr/{itr_id}/receive")
def admin_itr_receive(itr_id: int, user: Dict[str, Any] = Depends(require_admin)):
    r = portal.get_itr(itr_id)
    if not r or r["status"] != portal.ITR_TRANSIT:
        raise HTTPException(status_code=400, detail="ITR is not in transit.")
    with mgr_lock:
        ok, msg = portal.itr_receive(mgr, r, user["username"])
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return _json_ok({"ok": True, "message": msg})


@app.post("/api/admin/itr/{itr_id}/cancel")
def admin_itr_cancel(itr_id: int, user: Dict[str, Any] = Depends(require_admin)):
    r = portal.get_itr(itr_id)
    if not r or r["status"] != portal.ITR_TRANSIT:
        raise HTTPException(status_code=400, detail="ITR is not in transit.")
    with mgr_lock:
        ok, msg = mgr.delete_itr_files(itr_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    portal.update_itr(
        itr_id, status=portal.ITR_CANCELLED,
        admin_note="Cancelled by admin while in transit", decided_at=portal._now(),
    )
    return _json_ok({"ok": True, "message": f"ITR #{itr_id} cancelled — stock is back in {r['from_store']}."})


@app.get("/api/admin/dashboard")
def admin_dashboard(
    ord_states: Optional[str] = Query(None),
    rec_states: Optional[str] = Query(None),
    del_states: Optional[str] = Query(None),
    zones: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(require_admin),
):
    split_or_none = lambda s: [x for x in s.split(",") if x] if s is not None else None
    with mgr_lock:
        data = portal.get_admin_dashboard(
            mgr,
            f_ord=split_or_none(ord_states),
            f_rec=split_or_none(rec_states),
            f_del=split_or_none(del_states),
            zones_filter=split_or_none(zones),
        )
    data["meta"] = {
        "zones": portal.ZONES,
        "statuses": portal.STATUSES,
        "all_states": portal.ALL_STATES,
        "delivery_states": portal.DELIVERY_STATES,
    }
    return _json_ok(data)


@app.get("/api/admin/stats")
def admin_stats(period: str = Query("All time"), user: Dict[str, Any] = Depends(require_admin)):
    return _json_ok(portal.get_admin_stats(period))


@app.get("/api/admin/users")
def admin_users_list(user: Dict[str, Any] = Depends(require_admin)):
    df = portal.list_users()
    return _json_ok({"users": df.to_dict(orient="records"), "zones": portal.ZONES})


@app.post("/api/admin/users")
def admin_users_create(body: CreateUserBody, user: Dict[str, Any] = Depends(require_admin)):
    role = "recovery" if body.role == "recovery" else "user"
    ok, msg = portal.create_user(body.username, body.full_name, body.password, body.zone, role)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return _json_ok({"ok": True, "message": msg})


@app.patch("/api/admin/users/{username}")
def admin_users_update(username: str, body: UpdateUserBody, user: Dict[str, Any] = Depends(require_admin)):
    if username == user["username"]:
        raise HTTPException(status_code=400, detail="Cannot edit your own active admin account here.")
    fields: Dict[str, Any] = {}
    if body.zone is not None:
        fields["zone"] = body.zone
    if body.active is not None:
        fields["active"] = int(body.active)
    portal.update_user(username, **fields)
    return _json_ok({"ok": True, "message": "User updated."})


@app.post("/api/admin/users/{username}/reset-password")
def admin_users_reset_pw(username: str, body: ResetPwBody, user: Dict[str, Any] = Depends(require_admin)):
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    portal.set_password(username, body.new_password, must_change=1)
    return _json_ok({"ok": True, "message": "Password reset. User must change it at next login."})


@app.get("/api/admin/zones")
def admin_zones_get(user: Dict[str, Any] = Depends(require_admin)):
    owner = portal.city_owner_map()
    unassigned = [c for c in portal.PAKISTAN_CITIES if c not in owner]
    by_zone = {}
    for z in portal.ZONES:
        mine = sorted(c for c, v in owner.items() if v == z)
        free = sorted(c for c in portal.PAKISTAN_CITIES if owner.get(c) in (None, z))
        available = sorted(set(free + mine))
        by_zone[z] = {"assigned": mine, "available": available}
    summary = [
        {"Zone": z, "Cities": len(by_zone[z]["assigned"]), "List": ", ".join(by_zone[z]["assigned"])}
        for z in portal.ZONES
    ]
    return _json_ok({
        "zones": portal.ZONES,
        "by_zone": by_zone,
        "unassigned": unassigned,
        "summary": summary,
    })


@app.post("/api/admin/zones/seed-defaults")
def admin_zones_seed(user: Dict[str, Any] = Depends(require_admin)):
    n_added = portal.seed_default_zones(force=True)
    msg = f"{n_added} city(ies) added." if n_added else "All default cities are already assigned."
    return _json_ok({"ok": True, "message": msg})


@app.put("/api/admin/zones/{zone}/cities")
def admin_zones_save(zone: str, body: ZoneCitiesBody, user: Dict[str, Any] = Depends(require_admin)):
    if zone not in portal.ZONES:
        raise HTTPException(status_code=404, detail="Unknown zone.")
    owner = portal.city_owner_map()
    final = list(body.cities)
    nc = body.new_city.strip().title()
    if nc:
        if owner.get(nc) not in (None, zone):
            raise HTTPException(status_code=400, detail=f"{nc} already belongs to {owner[nc]}.")
        final.append(nc)
    unique = sorted(set(final))
    portal.set_zone_cities(zone, unique)
    return _json_ok({"ok": True, "message": f"{zone} now has {len(unique)} cities."})


@app.get("/api/admin/reset/preview")
def admin_reset_preview(user: Dict[str, Any] = Depends(require_admin)):
    n_req, files = portal.count_orders_for_reset(mgr)
    return _json_ok({"requests_count": n_req, "files_count": len(files)})


@app.post("/api/admin/reset")
def admin_reset_execute(body: ResetAllBody, user: Dict[str, Any] = Depends(require_admin)):
    if body.confirm_text.strip() != "DELETE ALL":
        raise HTTPException(status_code=400, detail='Type DELETE ALL to confirm.')
    with mgr_lock:
        res = portal.reset_all_orders(mgr, keep_backup=body.keep_backup)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["error"])
    msg = f"Done. {res['requests']} entries and {res['files']} order file(s) deleted."
    if res.get("backup"):
        msg += f" Backup saved in: {res['backup']}"
    return _json_ok({"ok": True, "message": msg})


# =============================================================================
# ZONE USER PORTAL ENDPOINTS
# =============================================================================
@app.get("/api/zone/overview")
def zone_overview(user: Dict[str, Any] = Depends(require_zone_user)):
    zone = user["zone"]
    cities = portal.get_zone_cities(zone)
    with mgr_lock:
        items = mgr.get_all_items()
    return _json_ok({
        "zone": zone,
        "cities": cities,
        "items": items,
        "safety_pct": _safety_pct(),
    })


@app.post("/api/zone/availability")
def zone_availability(body: ZoneAvailBody, user: Dict[str, Any] = Depends(require_zone_user)):
    zone = user["zone"]
    safety = _safety_pct()
    owner = portal.city_owner_map()
    with mgr_lock:
        by_store = portal.store_availability(mgr, body.item_code, safety)
    zone_av = sum(a for s_, (_, a) in by_store.items() if portal.store_zone(s_, owner) == zone)
    other_av = sum(a for s_, (_, a) in by_store.items() if portal.store_zone(s_, owner) != zone)
    waiting = portal.pending_units(body.item_code)
    return _json_ok({
        "zone": zone,
        "zone_avail": int(zone_av),
        "other_avail": int(other_av),
        "pending_units": int(waiting),
        "sufficient": zone_av >= body.qty,
    })


@app.post("/api/zone/orders")
def zone_create_order(body: ZoneOrderBody, user: Dict[str, Any] = Depends(require_zone_user)):
    zone = user["zone"]
    if not body.party.strip():
        raise HTTPException(status_code=400, detail="Please enter the client / party name.")
    if not body.city or body.city not in portal.get_zone_cities(zone):
        raise HTTPException(status_code=400, detail="Please select a valid city in your zone.")
    if body.qty < 1 or body.qty > 5000:
        raise HTTPException(status_code=400, detail="Order quantity must be between 1 and 5,000.")
    with mgr_lock:
        items = mgr.get_all_items()
    item = next((i for i in items if i["Item_Code"] == body.item_code), None)
    if not item:
        raise HTTPException(status_code=404, detail="Selected product not found.")
    grp = item.get("Group") or "—"
    portal.create_request(user, body.party.strip(), body.city, grp, item["Item_Code"], item["Item_Name"], body.qty, "Order")
    return _json_ok({"ok": True, "message": "Order sent to admin — status: New."})


@app.get("/api/zone/orders")
def zone_my_orders(user: Dict[str, Any] = Depends(require_zone_user)):
    df = portal.list_requests(zone=user["zone"], subgroup="Order")
    if df.empty:
        return _json_ok({"empty": True, "kpis": [], "rows": []})
    df = portal.with_status(df)
    kpis = [{"state": name, "count": int((df.state == name).sum())} for name in portal.ALL_STATES]
    df["status_label"] = df["state"].map(portal.STATUS_LABEL)
    return _json_ok({"empty": False, "kpis": kpis, "rows": df.to_dict(orient="records")})


@app.get("/api/zone/delivery")
def zone_delivery_list(user: Dict[str, Any] = Depends(require_zone_user)):
    df = portal.list_requests(zone=user["zone"], subgroup="Order", status=[portal.OK, portal.CANCELLED])
    if df.empty:
        return _json_ok({"empty": True, "overview": [], "delivered": [], "active": []})
    rows = df.to_dict(orient="records")
    for r in rows:
        r["effective_delivery_status"] = (
            r["delivery_status"]
            if r.get("delivery_status")
            else ("Awaiting challan" if r["status"] == portal.OK else portal.D_CANCELLED)
        )
    delivered = [
        r for r in sorted(rows, key=lambda x: x["id"])
        if r["status"] == portal.OK and r.get("delivery_status") == portal.D_DELIVERED
    ]
    active = [
        r for r in sorted(rows, key=lambda x: x["id"])
        if r["status"] == portal.OK and r.get("delivery_status") not in ([portal.D_DELIVERED, portal.D_CANCELLED] + portal.RETURN_STATES)
    ]
    return _json_ok({"empty": False, "overview": rows, "delivered": delivered, "active": active})


@app.post("/api/zone/delivery/{req_id}/dispatch")
def zone_delivery_dispatch(req_id: int, body: DispatchBody, user: Dict[str, Any] = Depends(require_zone_user)):
    r = portal.get_request(req_id)
    if not r or r["zone"] != user["zone"] or r["status"] != portal.OK:
        raise HTTPException(status_code=404, detail="Approved order not found in your zone.")
    if not body.challan_no.strip():
        raise HTTPException(status_code=400, detail="Please enter the delivery challan no.")
    portal.set_dispatched(req_id, body.challan_no)
    return _json_ok({"ok": True, "message": f"Order #{req_id} dispatched (challan {body.challan_no.strip()})."})


@app.post("/api/zone/delivery/{req_id}/status")
def zone_delivery_status(req_id: int, body: DeliveryStatusBody, user: Dict[str, Any] = Depends(require_zone_user)):
    r = portal.get_request(req_id)
    if not r or r["zone"] != user["zone"] or r["status"] != portal.OK:
        raise HTTPException(status_code=404, detail="Approved order not found in your zone.")
    if body.status == "Delivered":
        portal.update_request(req_id, delivery_status=portal.D_DELIVERED, delivered_at=portal._now())
        return _json_ok({"ok": True, "message": f"Order #{req_id} marked Delivered."})
    if body.status == "Not delivered yet":
        portal.update_request(req_id, delivery_status=portal.D_NOT_YET)
        return _json_ok({"ok": True, "message": f"Order #{req_id}: not delivered yet."})
    raise HTTPException(status_code=400, detail="Invalid delivery status.")


@app.post("/api/zone/delivery/{req_id}/cancel")
def zone_delivery_cancel(req_id: int, user: Dict[str, Any] = Depends(require_zone_user)):
    r = portal.get_request(req_id)
    if not r or r["zone"] != user["zone"] or r["status"] != portal.OK:
        raise HTTPException(status_code=404, detail="Approved order not found in your zone.")
    with mgr_lock:
        ok, msg = portal.cancel_by_consumer(mgr, r)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return _json_ok({"ok": True, "message": msg})


@app.post("/api/zone/delivery/{req_id}/return")
def zone_delivery_return(req_id: int, body: ReturnBody, user: Dict[str, Any] = Depends(require_zone_user)):
    r = portal.get_request(req_id)
    if not r or r["zone"] != user["zone"] or r.get("delivery_status") != portal.D_DELIVERED:
        raise HTTPException(status_code=404, detail="Delivered order not found in your zone.")
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="Please write the reason for the return.")
    released = int(r["released_qty"] or r["qty"] or 0)
    if body.qty < 1 or body.qty > max(released, 1):
        raise HTTPException(status_code=400, detail=f"Returned quantity must be between 1 and {released}.")
    state = portal.register_return(r, body.qty, body.reason, body.return_challan)
    return _json_ok({"ok": True, "message": f"Order #{req_id}: {body.qty:,} of {released:,} units returned — status: {state}."})


@app.get("/api/zone/itr")
def zone_itr_list(user: Dict[str, Any] = Depends(require_zone_user)):
    zone = user["zone"]
    owner = portal.city_owner_map()
    mine = portal.zone_warehouses(zone, owner)
    with mgr_lock:
        items = mgr.get_all_items()
        wide = portal.main_warehouse_table(mgr)
    df = portal.list_itr(zone=zone, stores=mine)
    rows = []
    if not df.empty:
        df["direction"] = ["⬇️ Incoming" if z == zone else "⬆️ Outgoing" for z in df["to_zone"]]
        df["status_label"] = df["status"].map(portal.ITR_LABEL)
        rows = df.to_dict(orient="records")
    return _json_ok({
        "zone": zone,
        "warehouses": mine,
        "items": items,
        "wide": wide,
        "itrs": rows,
    })


@app.post("/api/zone/itr/matrix")
def zone_itr_matrix(body: ZoneAvailBody, user: Dict[str, Any] = Depends(require_zone_user)):
    owner = portal.city_owner_map()
    with mgr_lock:
        mat = portal.warehouse_matrix(mgr, body.item_code, _safety_pct(), owner)
    return _json_ok({"matrix": mat})


@app.post("/api/zone/itr")
def zone_itr_create(body: ZoneItrBody, user: Dict[str, Any] = Depends(require_zone_user)):
    zone = user["zone"]
    owner = portal.city_owner_map()
    mine = portal.zone_warehouses(zone, owner)
    if body.to_store not in mine:
        raise HTTPException(status_code=400, detail="Destination warehouse does not belong to your zone.")
    with mgr_lock:
        items = mgr.get_all_items()
        item = next((i for i in items if i["Item_Code"] == body.item_code), None)
        if not item:
            raise HTTPException(status_code=404, detail="Product not found.")
        mat = portal.warehouse_matrix(mgr, body.item_code, _safety_pct(), owner)
    src_row = next((m for m in mat if m["Warehouse"] == body.from_store), None)
    if not src_row or body.qty < 1 or body.qty > src_row["Available for ITR"]:
        avail = src_row["Available for ITR"] if src_row else 0
        raise HTTPException(status_code=400, detail=f"{body.from_store} has only {avail:,} units available for ITR.")
    new_id = portal.create_itr(
        user["username"], zone, body.from_store, body.to_store,
        item.get("Group") or "—", item["Item_Code"], item["Item_Name"],
        int(body.qty), body.note.strip(),
    )
    return _json_ok({
        "ok": True,
        "message": f"ITR #{new_id} sent to admin: {body.qty:,} × {item['Item_Name']} from {body.from_store} to {body.to_store}.",
    })


@app.post("/api/zone/itr/{itr_id}/receive")
def zone_itr_receive(itr_id: int, user: Dict[str, Any] = Depends(require_zone_user)):
    r = portal.get_itr(itr_id)
    if not r or r["to_zone"] != user["zone"] or r["status"] != portal.ITR_TRANSIT:
        raise HTTPException(status_code=404, detail="In-transit ITR not found for your zone.")
    with mgr_lock:
        ok, msg = portal.itr_receive(mgr, r, user["username"])
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return _json_ok({"ok": True, "message": msg})


@app.post("/api/zone/itr/{itr_id}/cancel")
def zone_itr_cancel(itr_id: int, user: Dict[str, Any] = Depends(require_zone_user)):
    r = portal.get_itr(itr_id)
    if not r or r["username"] != user["username"] or r["status"] != portal.ITR_PENDING:
        raise HTTPException(status_code=404, detail="Pending ITR request not found.")
    portal.update_itr(
        itr_id, status=portal.ITR_CANCELLED,
        admin_note="Cancelled by the requester", decided_at=portal._now(),
    )
    return _json_ok({"ok": True, "message": f"ITR #{itr_id} cancelled."})


@app.get("/api/zone/stock")
def zone_stock_all(user: Dict[str, Any] = Depends(require_zone_user)):
    with mgr_lock:
        tbl = portal.all_zones_stock_table(mgr, _safety_pct())
    return _json_ok(tbl)


# =============================================================================
# RECOVERY PORTAL ENDPOINTS
# =============================================================================
@app.get("/api/recovery/meta")
def recovery_meta(user: Dict[str, Any] = Depends(require_recovery_user)):
    with mgr_lock:
        groups = list(mgr.get_groups()) or portal.DEFAULT_GROUPS
    zone_cities = {z: portal.get_zone_cities(z) for z in portal.ZONES}
    return _json_ok({"zones": portal.ZONES, "zone_cities": zone_cities, "groups": groups})


@app.post("/api/recovery/entries")
def recovery_create(body: RecoveryEntryBody, user: Dict[str, Any] = Depends(require_recovery_user)):
    if not body.party.strip():
        raise HTTPException(status_code=400, detail="Please enter the client / party name.")
    if body.zone not in portal.ZONES:
        raise HTTPException(status_code=400, detail="Please select a valid zone.")
    if not body.city:
        raise HTTPException(status_code=400, detail="Please select the city.")
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Please enter the recovered amount.")
    portal.create_recovery(
        user, body.zone, body.party.strip(), body.city, body.grp,
        float(body.amount), body.ref_no.strip(), body.note.strip(),
    )
    return _json_ok({"ok": True, "message": f"Recovery ({body.zone}) sent to admin — status: New."})


@app.get("/api/recovery/entries")
def recovery_list(zones: Optional[str] = Query(None), user: Dict[str, Any] = Depends(require_recovery_user)):
    df = portal.list_requests(subgroup="Recovery")
    if df.empty:
        return _json_ok({"empty": True, "kpis": [], "recovered_ok": 0, "rows": []})
    df = portal.with_status(df)
    if zones:
        zlist = [z for z in zones.split(",") if z]
        if zlist:
            df = df[df.zone.isin(zlist)]
    kpis = [{"state": name, "count": int((df.state == name).sum())} for name in ["New", "In process", "OK", "Hold", "Rejected"]]
    rec_ok = float(df.loc[df.state == "OK", "amount"].sum()) if not df.empty else 0.0
    df["status_label"] = df["state"].map(portal.STATUS_LABEL)
    return _json_ok({"empty": False, "kpis": kpis, "recovered_ok": rec_ok, "rows": df.to_dict(orient="records")})


@app.get("/api/recovery/holds")
def recovery_holds(user: Dict[str, Any] = Depends(require_recovery_user)):
    df = portal.list_requests(subgroup="Order", status=portal.HOLD)
    rows = df.sort_values("id").to_dict(orient="records") if not df.empty else []
    return _json_ok({"holds": rows})


@app.post("/api/recovery/holds/{req_id}/release")
def recovery_hold_release(req_id: int, body: NoteBody, user: Dict[str, Any] = Depends(require_recovery_user)):
    r = portal.get_request(req_id)
    if not r or r["status"] != portal.HOLD:
        raise HTTPException(status_code=404, detail="Hold order not found.")
    portal.update_request(
        req_id,
        status=portal.PENDING,
        created_at=portal._now(),
        admin_note=f"Payment received ({user['username']}): {body.note.strip() or '—'}",
    )
    return _json_ok({"ok": True, "message": f"Order #{req_id} sent back to the admin."})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)

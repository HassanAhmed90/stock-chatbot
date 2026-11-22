"""
=============================================================================
Inventory Manager Module (inventory_manager.py)
=============================================================================
This module handles all core business logic for multi-store inventory management:
1. Loading store inventory sheets (from Excel or CSV files).
2. Searching products by SKU code or product name.
3. Calculating dynamic safety stock buffers (e.g. 15%).
4. Multi-store cascading order fulfillment (allocating from multiple stores
   if one store does not have enough salable stock).
5. Deducting stock across stores in real time.
6. Generating standalone order release documents and master audit trails.
7. Natural language understanding with conversation context memory.
8. Integrated Knowledge Base for policies, store locations, and FAQs.
=============================================================================
"""

import io
import json
import os
import glob
import math
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
import pandas as pd

from knowledge_base import KnowledgeBase


# =============================================================================
# STOCK REPORT READER (daily uploaded Excel file)
# Report layout: product header row -> one row per office/batch -> Total row.
# Only visible sheets with "Products" + "Closing Balance" headers are read;
# the stock figure is the Closing Balance column.
# =============================================================================

BATCH_COLUMNS = [
    "Store_Name", "Category", "Item_Code", "Item_Name", "Product", "Packing",
    "Batch_No", "Expiry_Date", "Opening_Stock", "Received", "Sales",
    "Total_Stock", "Remarks",
]


class StockReportError(Exception):
    """Raised when an uploaded file cannot be understood as a stock report."""


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------
def _clean(value: Any) -> str:
    """Cell -> trimmed single-line string ('' for empty cells)."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _num(value: Any) -> Optional[float]:
    """Cell -> number, or None if the cell is empty / not numeric."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else float(value)
    text = _clean(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")


def normalize_store_name(raw: str) -> str:
    """'Rawalpindi Office' -> 'Rawalpindi', 'Lahore Office' -> 'Lahore'."""
    return re.sub(r"\s+office$", "", _clean(raw), flags=re.IGNORECASE)


def _find_header(rows: List[List[Any]]) -> Tuple[Optional[int], Dict[str, int]]:
    """Locate the header row and map column names -> column index."""
    for r_idx, row in enumerate(rows[:25]):
        cells = [_clean(c).lower() for c in row]
        if "products" in cells and "closing balance" in cells:
            cols: Dict[str, int] = {}
            for c_idx, text in enumerate(cells):
                if text == "s.no":
                    cols["sno"] = c_idx
                elif text == "products":
                    cols["product"] = c_idx
                elif text == "packing":
                    cols["packing"] = c_idx
                elif text.startswith("opening"):
                    cols["opening"] = c_idx
                elif text.startswith("received"):
                    cols["received"] = c_idx
                elif text == "total stock":
                    cols["total"] = c_idx
                elif text == "sales":
                    cols["sales"] = c_idx
                elif text == "closing balance":
                    cols["closing"] = c_idx
                elif text == "batch no":
                    cols["batch"] = c_idx
                elif text.startswith("expiry"):
                    cols["expiry"] = c_idx
                elif text == "remarks":
                    cols["remarks"] = c_idx
            return r_idx, cols
    return None, {}


def _report_date(rows: List[List[Any]]) -> Optional[str]:
    """Reads 'Stock Report as on 19-11-2026' from the top of a sheet."""
    for row in rows[:6]:
        for cell in row:
            m = re.search(r"as on\s+(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", _clean(cell), re.IGNORECASE)
            if m:
                d, mth, y = (int(g) for g in m.groups())
                try:
                    return datetime(y, mth, d).strftime("%Y-%m-%d")
                except ValueError:
                    return None
    return None


# -----------------------------------------------------------------------------
# Sheet parser
# -----------------------------------------------------------------------------
def _parse_sheet(
    sheet_name: str, rows: List[List[Any]], header_idx: int, cols: Dict[str, int],
    warnings: List[str],
) -> List[Dict[str, Any]]:
    def cell(row: List[Any], key: str) -> Any:
        i = cols.get(key)
        return row[i] if i is not None and i < len(row) else ""

    records: List[Dict[str, Any]] = []
    product = packing = office = None
    product_rows: List[Dict[str, Any]] = []
    product_label = ""

    def check_total(total_row: List[Any]) -> None:
        """Compare our sum for the product with the report's own Total row."""
        reported = _num(cell(total_row, "closing"))
        ours = sum(r["Total_Stock"] for r in product_rows)
        if reported is not None and product_rows and int(reported) != int(ours):
            warnings.append(
                f"'{sheet_name}': {product_label} adds up to {int(ours)} but the report's "
                f"Total row says {int(reported)}."
            )

    for row in rows[header_idx + 1:]:
        name = _clean(cell(row, "product"))
        pack = _clean(cell(row, "packing"))
        has_sno = _num(cell(row, "sno")) is not None

        if not name:
            # Blank name: either an empty spacer row, or another batch of the current office
            if not (product and office):
                continue
            closing = _num(cell(row, "closing"))
            batch = _clean(cell(row, "batch"))
            if closing is None or (closing == 0 and not batch):
                continue
        elif has_sno:
            # New product header
            product, packing, office = name, pack, None
            product_rows = []
            product_label = f"{name} {pack}".strip()
            continue
        elif name.lower().startswith("total") or pack:
            # Total row. Some are typed 'Tota ...' or have no 'Total' word, but a
            # Total row always carries the packing and an office row never does.
            check_total(row)
            continue
        else:
            # Office row (it also carries the first batch of that office)
            office = normalize_store_name(name)
            if not product:
                continue

        closing = _num(cell(row, "closing"))
        if closing is None:
            total, sales = _num(cell(row, "total")), _num(cell(row, "sales")) or 0
            closing = (total - sales) if total is not None else 0.0

        expiry = cell(row, "expiry")
        if hasattr(expiry, "strftime"):
            expiry = expiry.strftime("%Y-%m-%d")
        else:
            expiry = _clean(expiry)

        rec = {
            "Store_Name": office,
            "Category": sheet_name,
            "Item_Code": _slug(product) + ("-" + _slug(packing) if packing else ""),
            "Item_Name": f"{product} {packing}".strip(),
            "Product": product,
            "Packing": packing,
            "Batch_No": _clean(cell(row, "batch")),
            "Expiry_Date": expiry,
            "Opening_Stock": int(_num(cell(row, "opening")) or 0),
            "Received": int(_num(cell(row, "received")) or 0),
            "Sales": int(_num(cell(row, "sales")) or 0),
            "Total_Stock": int(closing),
            "Remarks": _clean(cell(row, "remarks")),
        }
        records.append(rec)
        product_rows.append(rec)
    return records


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def parse_stock_report(source: Any) -> Dict[str, Any]:
    """
    Parse a stock report workbook.

    Args:
        source: file path, or the bytes of the .xlsx file.

    Returns dict with:
        batches      DataFrame, one row per office / product / batch
        stores       {office: DataFrame(Item_Code, Item_Name, Category, Total_Stock, Unit_Price, Last_Updated)}
        report_date  'YYYY-MM-DD' (from 'Stock Report as on ...') or None
        sheets_read  list of sheet names that were read
        warnings     list of data-quality messages
    """
    try:
        from python_calamine import CalamineWorkbook
    except ImportError as exc:
        raise StockReportError(
            "The 'python-calamine' package is missing. Run:  pip install python-calamine"
        ) from exc

    try:
        if isinstance(source, (bytes, bytearray)):
            wb = CalamineWorkbook.from_filelike(io.BytesIO(source))
        else:
            wb = CalamineWorkbook.from_path(str(source))
    except Exception as exc:
        raise StockReportError(f"This does not look like a valid Excel file ({exc}).") from exc

    warnings: List[str] = []
    records: List[Dict[str, Any]] = []
    sheets_read: List[str] = []
    report_date: Optional[str] = None

    for meta in wb.sheets_metadata:
        if "visible" not in str(meta.visible).lower() or "hidden" in str(meta.visible).lower():
            continue  # hidden working sheets are ignored
        rows = wb.get_sheet_by_name(meta.name).to_python(skip_empty_area=False)
        header_idx, cols = _find_header(rows)
        if header_idx is None:
            continue
        sheets_read.append(meta.name)
        report_date = report_date or _report_date(rows)
        records.extend(_parse_sheet(meta.name, rows, header_idx, cols, warnings))

    if not sheets_read:
        raise StockReportError(
            "No sheet with the stock report columns ('Products' and 'Closing Balance') was found "
            "among the visible sheets."
        )
    if not records:
        raise StockReportError("The stock report was read but it contains no stock rows.")

    batches = pd.DataFrame(records, columns=BATCH_COLUMNS)

    # One table per office: add up the batches of the same product
    stamp = report_date or datetime.now().strftime("%Y-%m-%d")
    stores: Dict[str, pd.DataFrame] = {}
    for store, grp in batches.groupby("Store_Name", sort=True):
        agg = grp.groupby(["Item_Code", "Item_Name", "Category"], sort=False, as_index=False)["Total_Stock"].sum()
        agg["Unit_Price"] = 0.0          # the report has no prices
        agg["Last_Updated"] = stamp
        stores[store] = agg[["Item_Code", "Item_Name", "Category", "Total_Stock", "Unit_Price", "Last_Updated"]]

    return {
        "batches": batches,
        "stores": stores,
        "report_date": report_date,
        "sheets_read": sheets_read,
        "warnings": warnings,
    }


# -----------------------------------------------------------------------------
# The uploaded report lives in the data folder (nothing else is written there)
# -----------------------------------------------------------------------------
def find_current_report(data_dir: str) -> Optional[str]:
    """Path of the newest Excel file in the data folder (the latest upload), or None."""
    if not os.path.isdir(data_dir):
        return None
    files = [
        os.path.join(data_dir, f) for f in os.listdir(data_dir)
        if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$")
    ]
    return max(files, key=os.path.getmtime) if files else None


def save_uploaded_report(data_dir: str, file_bytes: bytes, original_name: str) -> Dict[str, Any]:
    """
    Validates the upload, then saves it in the data folder under its own file name.
    Nothing is written if the file is not a usable stock report.
    """
    parsed = parse_stock_report(file_bytes)  # raises StockReportError if unusable

    os.makedirs(data_dir, exist_ok=True)
    safe_name = re.sub(r"[^\w.\-() ]", "_", os.path.basename(original_name)) or "stock_report.xlsx"
    if not safe_name.lower().endswith((".xlsx", ".xlsm")):
        safe_name += ".xlsx"
    with open(os.path.join(data_dir, safe_name), "wb") as f:
        f.write(file_bytes)
    return parsed


def load_current_report(data_dir: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Reads the newest Excel file in the data folder.
    Returns (parsed_or_None, info). info has 'meta', 'error' and 'path'.
    """
    path = find_current_report(data_dir)
    info: Dict[str, Any] = {"path": path, "meta": {}, "error": None}
    if not path:
        return None, info
    info["meta"] = {
        "original_name": os.path.basename(path),
        "uploaded_at": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        return parse_stock_report(path), info
    except StockReportError as exc:
        info["error"] = str(exc)
        return None, info


def list_report_files(data_dir: str) -> List[Dict[str, Any]]:
    """
    Returns metadata for all Excel report files in data_dir, newest first.
    """
    if not os.path.isdir(data_dir):
        return []
    current_path = find_current_report(data_dir)
    results = []
    for f in os.listdir(data_dir):
        if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$"):
            full_path = os.path.join(data_dir, f)
            if os.path.isfile(full_path):
                try:
                    st_stat = os.stat(full_path)
                    results.append({
                        "filename": f,
                        "path": full_path,
                        "size_bytes": st_stat.st_size,
                        "size_kb": round(st_stat.st_size / 1024, 1),
                        "modified_at": datetime.fromtimestamp(st_stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "is_current": (full_path == current_path),
                        "_mtime": st_stat.st_mtime,
                    })
                except OSError:
                    continue
    results.sort(key=lambda r: r["_mtime"], reverse=True)
    return results


def delete_report_file(data_dir: str, filename: str) -> Tuple[bool, str]:
    """
    Deletes an Excel file from the data folder.
    Returns (success_bool, message_str).
    """
    if not filename:
        return False, "No filename specified for deletion."
    safe_name = os.path.basename(filename)
    if not safe_name.lower().endswith((".xlsx", ".xlsm")):
        return False, f"Invalid file type: '{safe_name}'. Only Excel files (.xlsx, .xlsm) can be removed."
    target_path = os.path.join(data_dir, safe_name)
    if not os.path.isfile(target_path):
        return False, f"File '{safe_name}' does not exist in the data folder."
    try:
        os.remove(target_path)
        return True, f"Report file '{safe_name}' was permanently deleted from the data folder."
    except Exception as exc:
        return False, f"Could not delete '{safe_name}' - is it open in another program (e.g. Excel)? ({exc})"


class InventoryManager:
    """
    Manages regional store stock, safety buffer calculations,
    cascading order allocations, order release documents,
    conversational context memory, and knowledge base inquiries.
    """

    def __init__(self):
        """Initialize directory paths, knowledge base, and ensure required folders exist."""
        # Base paths
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.base_dir, "data")
        self.orders_dir = os.path.join(self.base_dir, "orders")

        # Stock comes ONLY from the daily Excel report uploaded into the data folder.
        # Each released order is one Excel file in the orders folder; the stock those
        # orders took is subtracted from the report when the stock is shown.

        # Knowledge Base instance
        self.kb = KnowledgeBase()

        # Automatically create storage directories if they do not exist
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.orders_dir, exist_ok=True)

        # Parsed-report cache (re-read only when the uploaded file changes)
        self._cache_key = None
        self._cache_value = None
        self._orders_cache_key = None
        self._orders_cache_value = pd.DataFrame()
        self.last_load_info: Dict[str, Any] = {}

    # -------------------------------------------------------------------------
    # 1. INVENTORY DATA LOADING  (daily uploaded stock report only)
    # -------------------------------------------------------------------------
    def _load_report(self) -> Optional[Dict[str, Any]]:
        """Reads the newest Excel file in the data folder (cached until it changes)."""
        path = find_current_report(self.data_dir)
        try:
            st = os.stat(path) if path else None
            key = (path, st.st_mtime_ns, st.st_size) if st else None
        except OSError:
            key = None

        if key is not None and key == self._cache_key:
            return self._cache_value

        parsed, info = load_current_report(self.data_dir)
        if parsed:
            self._assign_product_ids(parsed)
        self._cache_key = key
        self._cache_value = parsed
        self.last_load_info = {
            "path": path,
            "meta": info.get("meta", {}),
            "error": info.get("error"),
            "has_file": path is not None,
            "report_date": parsed["report_date"] if parsed else None,
            "sheets_read": parsed["sheets_read"] if parsed else [],
            "warnings": parsed["warnings"] if parsed else [],
        }
        return parsed

    def _assign_product_ids(self, parsed: Dict[str, Any]) -> None:
        """
        Gives every product a short numeric ID (1, 2, 3 ...) to order by.
        IDs are remembered in product_ids.json (next to the app files, not in the
        data folder), so a product keeps the same ID
        from one daily report to the next; new products get the next free number.
        """
        path = os.path.join(self.base_dir, "product_ids.json")
        try:
            with open(path, encoding="utf-8") as f:
                registry = {str(k): int(v) for k, v in json.load(f).items()}
        except Exception:
            registry = {}

        changed = False
        next_id = max(registry.values(), default=0) + 1
        for code in parsed["batches"]["Item_Code"].drop_duplicates():
            if code not in registry:
                registry[code] = next_id
                next_id += 1
                changed = True
        if changed:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(registry, f, indent=1)
            except Exception:
                pass

        parsed["batches"].insert(0, "Product_ID", parsed["batches"]["Item_Code"].map(registry))
        for df in parsed["stores"].values():
            df.insert(0, "Product_ID", df["Item_Code"].map(registry))

    def save_uploaded_report(self, file_bytes: bytes, original_name: str) -> Dict[str, Any]:
        """Validates and stores an uploaded daily report (raises StockReportError if unusable)."""
        parsed = save_uploaded_report(self.data_dir, file_bytes, original_name)
        self._cache_key = None  # force a re-read
        self._load_report()
        return parsed

    def get_report_files(self) -> List[Dict[str, Any]]:
        """Lists all Excel report files present in the data folder, newest first."""
        return list_report_files(self.data_dir)

    def delete_report_file(self, filename: str) -> Tuple[bool, str]:
        """
        Deletes a specific Excel report file from the data folder,
        invalidates internal cache, and reloads current report state.
        """
        ok, msg = delete_report_file(self.data_dir, filename)
        if ok:
            self._cache_key = None
            self._cache_value = None
            self.last_load_info = {}
            self._load_report()
        return ok, msg

    def delete_all_report_files(self) -> Tuple[bool, str]:
        """
        Deletes all Excel report files from the data folder.
        """
        files = self.get_report_files()
        if not files:
            return False, "No Excel files found in the data folder."
        deleted = []
        errors = []
        for f in files:
            ok, msg = delete_report_file(self.data_dir, f["filename"])
            if ok:
                deleted.append(f["filename"])
            else:
                errors.append(msg)
        self._cache_key = None
        self._cache_value = None
        self.last_load_info = {}
        self._load_report()
        if errors:
            return False, f"Removed {len(deleted)} file(s), but encountered errors: {'; '.join(errors)}"
        return True, f"All {len(deleted)} Excel report file(s) were removed from the data folder."

    def get_report_key(self) -> str:
        """Identifies the uploaded report that ledger entries belong to."""
        self._load_report()
        info = self.last_load_info
        return str(info.get("report_date") or info.get("meta", {}).get("uploaded_at") or "unknown")

    def get_stock_batches(self) -> pd.DataFrame:
        """Batch / expiry level detail of the uploaded report (before order deductions)."""
        parsed = self._load_report()
        return parsed["batches"].copy() if parsed else pd.DataFrame()

    # ---- stock taken by orders released in this app --------------------------
    def _current_order_lines(self) -> pd.DataFrame:
        """Order lines that were released against the report that is loaded now."""
        orders = self.get_order_history()
        if orders.empty or "Stock_Report_Date" not in orders.columns:
            return pd.DataFrame()
        return orders[orders["Stock_Report_Date"].astype(str) == self.get_report_key()]

    def get_order_deductions(self) -> int:
        """Units taken out by orders released in this app since the current report was uploaded."""
        lines = self._current_order_lines()
        return int(lines["Quantity_Released"].sum()) if not lines.empty else 0

    def get_all_stores(self) -> Dict[str, pd.DataFrame]:
        """
        Loads inventory for all stores (offices) from the uploaded stock report.

        Returns:
            dict: {store_name: DataFrame} with Item_Code, Item_Name, Category,
                  Total_Stock, Unit_Price, Last_Updated.

        Stock = Closing Balance of the uploaded report, minus whatever orders
        were released in this app since that report was uploaded. When no report
        has been uploaded (or the file is unreadable) no stores are returned;
        see self.last_load_info for the reason.
        """
        parsed = self._load_report()
        if not parsed:
            return {}

        stores = {name: df.copy() for name, df in parsed["stores"].items()}

        lines = self._current_order_lines()
        if not lines.empty:
            taken = lines.groupby(["Store_Name", "Item_Code"])["Quantity_Released"].sum()
            for (store, code), qty in taken.items():
                df = stores.get(store)
                if df is not None:
                    mask = df["Item_Code"] == code
                    df.loc[mask, "Total_Stock"] = (df.loc[mask, "Total_Stock"] - int(qty)).clip(lower=0)
        return stores

    def get_all_items(self) -> List[Dict[str, Any]]:
        """
        Retrieves the unique product catalog across all stores, sorted by ID.

        Returns:
            list: dictionaries with 'Product_ID', 'Item_Code', 'Item_Name', 'Category'.
        """
        frames = [
            df[["Product_ID", "Item_Code", "Item_Name", "Category"]]
            for df in self.get_all_stores().values()
            if not df.empty
        ]
        if not frames:
            return []
        catalog = pd.concat(frames).drop_duplicates("Item_Code").sort_values("Product_ID")
        return catalog.to_dict(orient="records")

    # "ID 12", "id12", "ID: 12", "#12", "sku 12", "code 12"
    ID_PATTERN = re.compile(r'(?:\b(?:id|sku|code)\s*[:#\-]?\s*|#\s*)(\d+)\b', re.IGNORECASE)

    def find_item(self, query_text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Finds a product matching the query by Item Code (SKU) or Item Name.

        Args:
            query_text (str): User input text or product search keyword.

        Returns:
            tuple: (item_code, item_name) if matched, else (None, None).
        """
        if not query_text:
            return None, None

        items = self.get_all_items()
        clean_query = str(query_text).lower().strip()

        # Step 0: Product ID, e.g. "ID 7" or "#7"
        id_match = self.ID_PATTERN.search(clean_query)
        if id_match:
            wanted = int(id_match.group(1))
            for item in items:
                if int(item["Product_ID"]) == wanted:
                    return item["Item_Code"], item["Item_Name"]
            return None, None   # an ID was given but no product has it

        # Step 1: Exact or substring match for SKU or Name
        for item in items:
            code = str(item["Item_Code"]).lower()
            name = str(item["Item_Name"]).lower()
            if code in clean_query or name in clean_query:
                return item["Item_Code"], item["Item_Name"]

        # Step 2: Token-based keyword matching (e.g. "cotton fabric" or "denim")
        query_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', clean_query))
        best_match = None
        best_score = 0

        for item in items:
            name_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', item["Item_Name"].lower()))
            overlap = len(query_words.intersection(name_words))
            if overlap > best_score:
                best_score = overlap
                best_match = item

        if best_match and best_score >= 1:
            return best_match["Item_Code"], best_match["Item_Name"]

        return None, None

    # -------------------------------------------------------------------------
    # 2. ORDER ALLOCATION & CASCADING LOGIC
    # -------------------------------------------------------------------------
    def plan_order(self, item_query: str, order_qty: int, safety_pct: float = 15.0) -> Dict[str, Any]:
        """
        Calculates multi-store allocation with safety buffer protection:
        1. Reserves safety stock per store: buffer = ceil(stock * safety_pct / 100).
        2. Computes available stock per store: available = max(0, stock - buffer).
        3. Sorts stores by highest available stock (greedy approach).
        4. Cascades order across stores if one store does not have enough stock,
           fulfilling 100% of the request whenever possible.

        Args:
            item_query (str): Product name or SKU code.
            order_qty (int): Requested order quantity.
            safety_pct (float): Safety stock reserve percentage (e.g. 15%).

        Returns:
            dict: Detailed allocation plan including store breakdown and status.
        """
        # Step 1: Validate product existence
        item_code, item_name = self.find_item(item_query)
        if not item_code:
            return {
                "success": False,
                "error": f"Item '{item_query}' could not be found in the catalog."
            }

        stores = self.get_all_stores()
        needed_qty = int(order_qty)
        buffer_ratio = float(safety_pct) / 100.0

        total_system_stock = 0
        total_safety_buffer = 0
        total_available = 0

        # Step 2: Inspect stock levels across all regional stores for this item
        store_candidates = []
        for store_name, df in stores.items():
            matching_rows = df[df["Item_Code"] == item_code]
            if not matching_rows.empty:
                current_stock = int(matching_rows.iloc[0]["Total_Stock"])
                unit_price = float(matching_rows.iloc[0].get("Unit_Price", 0.0))

                # Calculate reserved safety buffer and salable stock
                safety_buffer = math.ceil(current_stock * buffer_ratio) if current_stock > 0 else 0
                salable_stock = max(0, current_stock - safety_buffer)

                total_system_stock += current_stock
                total_safety_buffer += safety_buffer
                total_available += salable_stock

                store_candidates.append({
                    "store_name": store_name,
                    "stock": current_stock,
                    "safety_buffer": safety_buffer,
                    "available": salable_stock,
                    "unit_price": unit_price
                })

        # Step 3: Prioritize stores with the highest salable stock first
        store_candidates.sort(key=lambda item: item["available"], reverse=True)

        # Step 4: Cascade fulfillment across stores
        allocations = []
        dispatched_stores_count = 0
        split_details = []
        remaining_needed = needed_qty

        for store in store_candidates:
            if store["available"] > 0 and remaining_needed > 0:
                take_qty = min(remaining_needed, store["available"])
                new_stock = store["stock"] - take_qty

                # Store status explanation
                if store["available"] - take_qty == 0:
                    status_note = "Stock Depleted (Buffer Preserved)"
                else:
                    status_note = "Stock Available"

                allocations.append({
                    "Store_Name": store["store_name"],
                    "Current_Stock": store["stock"],
                    "Available_Before": store["available"],
                    "Dispatch_Qty": take_qty,
                    "Remaining_Stock": new_stock,
                    "Safety_Buffer": store["safety_buffer"],
                    "Unit_Price": store["unit_price"],
                    "Store_Status": status_note
                })

                remaining_needed -= take_qty
                dispatched_stores_count += 1
                split_details.append(f"{store['store_name']}: {take_qty} units")
            else:
                allocations.append({
                    "Store_Name": store["store_name"],
                    "Current_Stock": store["stock"],
                    "Available_Before": store["available"],
                    "Dispatch_Qty": 0,
                    "Remaining_Stock": store["stock"],
                    "Safety_Buffer": store["safety_buffer"],
                    "Unit_Price": store["unit_price"],
                    "Store_Status": "Untouched"
                })

        fulfilled_qty = needed_qty - remaining_needed
        shortage = remaining_needed

        # Determine overall fulfillment status
        if shortage == 0:
            status = "FULL"
        elif fulfilled_qty > 0:
            status = "PARTIAL"
        else:
            status = "OUT_OF_STOCK"

        # Construct readable summary of multi-store dispatch
        if dispatched_stores_count > 1:
            split_summary = (
                f"Multi-Store Dispatch: Order split across {dispatched_stores_count} stores "
                f"({', '.join(split_details)}) to complete 100% fulfillment."
            )
        elif dispatched_stores_count == 1:
            split_summary = f"Single Store Dispatch: Fulfilled completely from {split_details[0]}."
        else:
            split_summary = "Out of stock across all stores."

        return {
            "success": True,
            "status": status,
            "item_code": item_code,
            "item_name": item_name,
            "requested_qty": needed_qty,
            "fulfilled_qty": fulfilled_qty,
            "shortage": shortage,
            "total_system_stock": total_system_stock,
            "total_safety_buffer": total_safety_buffer,
            "total_available": total_available,
            "dispatched_stores_count": dispatched_stores_count,
            "split_summary": split_summary,
            "allocations": allocations
        }

    # -------------------------------------------------------------------------
    # 3. ORDER CONFIRMATION & FILE GENERATION
    # -------------------------------------------------------------------------
    def confirm_and_release_order(self, party_name: str, plan: Any) -> Tuple[bool, Any]:
        """
        Executes order confirmation for ONE plan or a LIST of plans
        (multi-product order released under a single Order ID):
        1. Deducts dispatched stock from all allocated store sheets and CSV files.
        2. Creates a standalone release order file in 'orders/Order_...'.
        3. Appends release details to the master orders Excel file and history log.

        Args:
            party_name (str): Name of client or purchasing party.
            plan (dict | list[dict]): Allocation plan(s) returned by plan_order().

        Returns:
            tuple: (success_bool, details_dict_or_error_string)
        """
        plans = plan if isinstance(plan, list) else [plan]
        dispatch_lines = [
            (p, a) for p in plans for a in p["allocations"] if a["Dispatch_Qty"] > 0
        ]
        if not dispatch_lines:
            return False, "No stock is available to dispatch for this order."

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        date_today = now.strftime("%Y-%m-%d")
        order_id = f"ORD-{now.strftime('%Y%m%d-%H%M%S')}"
        clean_party_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', party_name).strip("_")

        # The uploaded report is never edited. The order's Excel file (written below)
        # is what deducts the stock, so it is tied to the report that is loaded now.
        report_key = self.get_report_key()

        # Step 2: Build release order records (one row per product per store)
        order_records = []
        for p, dispatch in dispatch_lines:
            qty = dispatch["Dispatch_Qty"]
            price = dispatch.get("Unit_Price", 0.0)
            order_records.append({
                "Order_ID": order_id,
                "Release_Date": timestamp,
                "Party_Name": party_name,
                "Item_Code": p["item_code"],
                "Item_Name": p["item_name"],
                "Store_Name": dispatch["Store_Name"],
                "Quantity_Released": qty,
                "Remaining_Stock": dispatch["Remaining_Stock"],
                "Safety_Buffer_Kept": dispatch["Safety_Buffer"],
                "Unit_Price": price,
                "Total_Amount": qty * price,
                "Stock_Report_Date": report_key
            })

        df_order = pd.DataFrame(order_records)

        # Step 3: Create the order's Excel file in the orders folder
        release_filename = f"Order_{order_id}_{clean_party_name}"
        order_xlsx_path = os.path.join(self.orders_dir, f"{release_filename}.xlsx")
        try:
            with pd.ExcelWriter(order_xlsx_path, engine="openpyxl") as writer:
                df_order.to_excel(writer, sheet_name="Release_Details", index=False)
        except Exception as error:
            return False, f"The order file could not be saved, so nothing was released ({error})."

        # Per-product totals for the confirmation message
        items_summary = []
        for p in plans:
            released = sum(a["Dispatch_Qty"] for a in p["allocations"])
            if released > 0:
                items_summary.append({"item_name": p["item_name"], "quantity": released})

        return True, {
            "order_id": order_id,
            "party_name": party_name,
            "item_name": ", ".join(i["item_name"] for i in items_summary),
            "items": items_summary,
            "total_released": sum(d["Dispatch_Qty"] for _, d in dispatch_lines),
            "stores_involved": list(dict.fromkeys(d["Store_Name"] for _, d in dispatch_lines)),
            "file_name": os.path.basename(order_xlsx_path),
        }

    def get_order_history(self) -> pd.DataFrame:
        """
        Loads all released orders by reading the Order_*.xlsx files in the orders folder.

        Returns:
            pd.DataFrame: One row per product per store, or an empty DataFrame if none found.
        """
        files = sorted(glob.glob(os.path.join(self.orders_dir, "Order_*.xlsx")))
        try:
            key = tuple((f, os.stat(f).st_mtime_ns, os.stat(f).st_size) for f in files)
        except OSError:
            key = None
        if key is not None and key == self._orders_cache_key:
            return self._orders_cache_value.copy()

        frames = []
        for path in files:
            try:
                df = pd.read_excel(path, sheet_name="Release_Details", dtype=str)
            except Exception:
                continue
            if "Order_ID" in df.columns:
                for col in ("Quantity_Released", "Remaining_Stock", "Safety_Buffer_Kept", "Unit_Price", "Total_Amount"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                frames.append(df)
        history = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not history.empty:
            history = history.sort_values("Release_Date", kind="stable").reset_index(drop=True)
        self._orders_cache_key = key
        self._orders_cache_value = history
        return history.copy()

    def get_order_summaries(self) -> List[Dict[str, Any]]:
        """
        Returns one summary row per released order (newest first), used by the
        'Delete Order' selector in the web UI.
        """
        history = self.get_order_history()
        if history.empty or "Order_ID" not in history.columns:
            return []

        summaries = []
        for order_id, group in history.groupby("Order_ID", sort=False):
            summaries.append({
                "order_id": order_id,
                "party_name": str(group["Party_Name"].iloc[0]),
                "item_name": ", ".join(dict.fromkeys(group["Item_Name"].astype(str))),
                "total_units": int(group["Quantity_Released"].sum()),
                "stores": list(group["Store_Name"].astype(str)),
                "release_date": str(group["Release_Date"].iloc[0]),
            })
        summaries.sort(key=lambda o: o["release_date"], reverse=True)
        return summaries

    def delete_order(self, order_id: str) -> Tuple[bool, Any]:
        """
        Deletes a released order (e.g. when the party cancels it) by removing its
        Excel file from the orders folder. Because stock is worked out from the order
        files, the units go back to the stores automatically, as long as the stock
        report that was loaded when the order was placed is still the current one.
        (A newer daily report already reflects what really left the stores.)
        """
        history = self.get_order_history()
        if history.empty or "Order_ID" not in history.columns:
            return False, "No released orders were found."

        order_rows = history[history["Order_ID"] == order_id]
        if order_rows.empty:
            return False, f"Order '{order_id}' was not found in the order history."

        party_name = str(order_rows["Party_Name"].iloc[0])
        item_name = ", ".join(dict.fromkeys(order_rows["Item_Name"].astype(str)))
        total_units = int(order_rows["Quantity_Released"].sum())
        stock_restored = (
            "Stock_Report_Date" in order_rows.columns
            and str(order_rows["Stock_Report_Date"].iloc[0]) == self.get_report_key()
        )
        restored_lines = (
            [f"{r['Store_Name']}: +{int(r['Quantity_Released'])}" for _, r in order_rows.iterrows()]
            if stock_restored else []
        )

        deleted_files = []
        for file_path in glob.glob(os.path.join(self.orders_dir, f"Order_{order_id}_*")):
            if file_path.endswith((".xlsx", ".csv")):
                try:
                    os.remove(file_path)
                    deleted_files.append(os.path.basename(file_path))
                except Exception as error:
                    return False, (
                        f"Could not delete '{os.path.basename(file_path)}' - is it open in Excel? ({error})"
                    )

        return True, {
            "order_id": order_id,
            "party_name": party_name,
            "item_name": item_name,
            "total_units": total_units,
            "stock_restored": stock_restored,
            "restored_lines": restored_lines,
            "deleted_files": deleted_files,
        }

    # -------------------------------------------------------------------------
    # 4. CONVERSATIONAL CONTEXT & NLP PARSING (ENGLISH)
    # -------------------------------------------------------------------------
    def parse_user_order(self, text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Extracts Party/Customer Name, Quantity, and Product Item from user text.
        Supports multi-turn context memory:
          - If the user uses relative terms like "that", "this", "it", "same item",
            or specifies a quantity without an item name, it retrieves the product
            from the active conversation context.
          - If the user omits party name, it checks if a party was previously stored.

        Args:
            text (str): Free-text input from chat box.
            context (dict, optional): Conversation context dictionary.

        Returns:
            dict: {
                "party_name": str,
                "quantity": int or None,
                "item_code": str or None,
                "item_name": str or None,
                "context_used": bool
            }
        """
        clean_text = text.strip()
        context = context or {}
        context_used = False

        # Any product ID that does not exist stops the whole message (never guess another product)
        known_ids = {int(i["Product_ID"]) for i in self.get_all_items()}
        unknown_ids = [n for n in dict.fromkeys(self.ID_PATTERN.findall(clean_text)) if int(n) not in known_ids]
        if unknown_ids:
            return {
                "party_name": None, "quantity": None, "item_code": None, "item_name": None,
                "items": [], "context_used": False, "unknown_ids": unknown_ids,
            }

        # Step 1: Extract numeric quantity
        quantity = None
        qty_patterns = [
            r'\b(\d+)\s*(?:units?|rolls?|boxes?|cones?|cartons?|pieces?|pcs?|kg|m)?\b',
            r'\b(\d+)\b'
        ]
        text_without_ids = self.ID_PATTERN.sub(" ", clean_text)   # "ID 7" is not a quantity
        for pattern in qty_patterns:
            match = re.search(pattern, text_without_ids, re.IGNORECASE)
            if match:
                quantity = int(match.group(1))
                break

        # Step 2: Extract party or client name
        party_name = None
        party_patterns = [
            r'(?:party|client|customer|buyer)\s*[:\s\-]+([a-zA-Z0-9\s\.\-_]+?)(?:\s+(?:wants?|requires?|needs?|needed|ordered|orders?|buys?|would|for|\d)|\,|$)',
            r'\b(?:for|to)\s+([a-zA-Z0-9\s\.\-_]+?)(?:\s+(?:wants?|requires?|needs?|needed|ordered|orders?)|\,|$)'
        ]
        for pattern in party_patterns:
            party_match = re.search(pattern, clean_text, re.IGNORECASE)
            if party_match:
                extracted = party_match.group(1).strip()
                if extracted.lower() not in {"order", "stock", "dispatch", "me", "us", "please", "this", "that"}:
                    party_name = extracted
                    break

        # Fall back to context party name if not explicitly provided
        if not party_name:
            if context.get("last_party_name"):
                party_name = context["last_party_name"]
                context_used = True
            else:
                party_name = "Valued Client"

        # Multi-product order (e.g. "150 Bubble Wrap, 200 Denim, 400 Lawn")
        multi_items = self._extract_multi_items(clean_text)
        if len(multi_items) >= 2:
            return {
                "party_name": party_name,
                "quantity": multi_items[-1]["quantity"],
                "item_code": multi_items[-1]["item_code"],
                "item_name": multi_items[-1]["item_name"],
                "items": multi_items,
                "context_used": context_used
            }

        # Step 3: Match product SKU or Name
        item_code, item_name = self.find_item(clean_text)

        # Context Resolution: If product not explicitly in text, check for relative pronouns
        if not item_code:
            relative_keywords = [
                "that", "this", "it", "same item", "same product", "this item",
                "of this", "of that", "the product", "this fabric", "this yarn", "this denim"
            ]
            has_relative_ref = any(re.search(rf'\b{re.escape(k)}\b', clean_text, re.IGNORECASE) for k in relative_keywords)

            # If user refers to previous item OR user entered only a quantity (e.g. "order 200")
            if (has_relative_ref or quantity is not None) and context.get("last_item_code"):
                item_code = context["last_item_code"]
                item_name = context.get("last_item_name", item_code)
                context_used = True

        return {
            "party_name": party_name,
            "quantity": quantity,
            "item_code": item_code,
            "item_name": item_name,
            "items": [],
            "context_used": context_used
        }

    def _extract_multi_items(self, text: str) -> List[Dict[str, Any]]:
        """
        Finds every (quantity, product) pair in a message that lists several
        products, e.g. '150 Bubble Wrap Roll 100m, 200 Raw Denim 14oz and 400 Lawn'.

        The text is split at commas / 'and' / ';' / '&' / '+' / new lines, then each
        piece is matched to a product and its own quantity. Digits that belong to the
        product name (100m, 14oz) are removed first so they are never read as quantities.
        Repeated products are merged. Returns [] when fewer than 2 pieces exist.
        """
        segments = [
            seg.strip()
            for seg in re.split(r'(?<!\d),|,(?!\d)|;|\n|\s&\s|\s\+\s|\band\b', text, flags=re.IGNORECASE)
            if seg and seg.strip()
        ]
        if len(segments) < 2:
            return []

        found: Dict[str, Dict[str, Any]] = {}
        for seg in segments:
            code, name = self.find_item(seg)
            if not code:
                continue

            remainder = self.ID_PATTERN.sub(" ", seg)
            for token in (name, code):
                remainder = re.sub(re.escape(token), " ", remainder, flags=re.IGNORECASE)
            name_words = set(re.findall(r'[a-zA-Z]{3,}', name.lower()))
            remainder = re.sub(
                r'[a-zA-Z]{3,}',
                lambda m: " " if m.group(0).lower() in name_words else m.group(0),
                remainder
            )

            qty_match = re.search(r'\d[\d,]*', remainder)
            if not qty_match:
                continue
            qty = int(qty_match.group(0).replace(",", ""))
            if qty <= 0:
                continue

            if code in found:
                found[code]["quantity"] += qty
            else:
                found[code] = {"item_code": code, "item_name": name, "quantity": qty}

        return list(found.values())

    # -------------------------------------------------------------------------
    # 5. SMART CHAT MESSAGE PROCESSOR (WITH CONTEXT & KNOWLEDGE BASE)
    # -------------------------------------------------------------------------
    def process_chat_message(
        self,
        user_input: str,
        context: Dict[str, Any],
        safety_pct: float = 15.0
    ) -> Dict[str, Any]:
        """
        End-to-end conversation processor:
        1. Checks for confirmation intents ("confirm", "proceed", "release order").
        2. Checks for cancellation intents ("cancel", "abort").
        3. Checks for order status / history inquiries ("what was the order id", "last order").
        4. Searches the Knowledge Base for operational policies, locations, FAQs.
        5. Handles product stock inquiries.
        6. Handles multi-store order planning with context memory.

        Args:
            user_input (str): User's raw chat message.
            context (dict): Conversation memory dictionary.
            safety_pct (float): Active safety stock buffer percentage.

        Returns:
            dict: {
                "reply": str,
                "action": str,
                "pending_plan": dict or None,
                "updated_context": dict
            }
        """
        clean_input = user_input.strip()
        lower_input = clean_input.lower()
        context = dict(context or {})

        # ---------------------------------------------------------------------
        # INTENT 1: ORDER CONFIRMATION
        # ---------------------------------------------------------------------
        confirm_triggers = [
            "confirm", "confirm order", "proceed", "release it", "yes confirm",
            "release order", "dispatch it", "approve", "confirm dispatch", "yes please"
        ]
        if any(re.search(rf'\b{re.escape(trigger)}\b', lower_input) for trigger in confirm_triggers):
            pending = context.get("pending_plan")
            if pending:
                party = pending.get("party", "Valued Client")
                plan = pending.get("plans") or pending.get("plan")
                success, result = self.confirm_and_release_order(party, plan)

                if success:
                    # Update context with completed order details
                    context["last_order_id"] = result["order_id"]
                    context["last_order_details"] = result
                    context["pending_plan"] = None

                    reply = (
                        f"🎉 **Order Released Successfully via Chat!**\n\n"
                        f"• **Order ID:** `{result['order_id']}`\n"
                        f"• **Client / Party:** {result['party_name']}\n"
                        f"• **Product(s):** {result['item_name']}\n"
                        f"• **Total Units Released:** {result['total_released']:,} units\n"
                        f"• **Dispatched From Stores:** {', '.join(result['stores_involved'])}\n\n"
                        f"**Completed Actions:**\n"
                        f"1. Dispatched stock deducted from corresponding store sheets.\n"
                        f"2. Excel file created in the orders folder: `{result['file_name']}`"
                    )
                    return {
                        "reply": reply,
                        "action": "ORDER_CONFIRMED",
                        "pending_plan": None,
                        "updated_context": context
                    }
                else:
                    return {
                        "reply": f"⚠️ Could not confirm order: {result}",
                        "action": "ERROR",
                        "pending_plan": pending,
                        "updated_context": context
                    }
            else:
                return {
                    "reply": "ℹ️ There is no pending order proposal to confirm. You can place a new order anytime by typing product and quantity.",
                    "action": "INFO",
                    "pending_plan": None,
                    "updated_context": context
                }

        # ---------------------------------------------------------------------
        # INTENT 2: ORDER CANCELLATION
        # ---------------------------------------------------------------------
        cancel_triggers = ["cancel", "cancel order", "stop", "abort", "discard", "clear order"]
        if any(re.search(rf'\b{re.escape(trigger)}\b', lower_input) for trigger in cancel_triggers):
            if context.get("pending_plan"):
                context["pending_plan"] = None
                return {
                    "reply": "🛑 The pending order allocation has been cancelled.",
                    "action": "ORDER_CANCELLED",
                    "pending_plan": None,
                    "updated_context": context
                }
            else:
                return {
                    "reply": "ℹ️ No pending order found to cancel.",
                    "action": "INFO",
                    "pending_plan": None,
                    "updated_context": context
                }

        # ---------------------------------------------------------------------
        # INTENT 3: RECENT ORDER / STATUS INQUIRY
        # ---------------------------------------------------------------------
        status_triggers = [
            "last order", "order id", "what was the order", "order details",
            "which stores dispatched", "previous order", "track order"
        ]
        if any(re.search(rf'\b{re.escape(trigger)}\b', lower_input) for trigger in status_triggers):
            last_order = context.get("last_order_details")
            if last_order:
                reply = (
                    f"📦 **Most Recent Order Details (Session Memory):**\n\n"
                    f"• **Order ID:** `{last_order['order_id']}`\n"
                    f"• **Client / Party:** {last_order['party_name']}\n"
                    f"• **Product:** {last_order['item_name']}\n"
                    f"• **Total Released:** {last_order['total_released']:,} units\n"
                    f"• **Stores Dispatched:** {', '.join(last_order['stores_involved'])}\n"
                    f"• **Generated Document:** `{last_order['file_name']}`"
                )
                return {
                    "reply": reply,
                    "action": "STATUS_INFO",
                    "pending_plan": context.get("pending_plan"),
                    "updated_context": context
                }

        # ---------------------------------------------------------------------
        # INTENT 3B: STOCK REPORT FILE REMOVAL INQUIRY
        # ---------------------------------------------------------------------
        remove_report_triggers = [
            "remove excel", "delete excel", "remove stock report", "delete stock report",
            "remove file from data", "delete file from data", "remove data file", "delete data file"
        ]
        if any(re.search(rf'\b{re.escape(trigger)}\b', lower_input) for trigger in remove_report_triggers):
            return {
                "reply": (
                    "🗑️ **To remove an Excel stock report file from the data folder:**\n\n"
                    "1. Open the **🏬 Stock (Daily Report)** tab.\n"
                    "2. Open the **'Remove Excel File from `data` Folder'** section.\n"
                    "3. Select the file you wish to delete and check the confirmation box.\n"
                    "4. Click **'Remove File'**.\n\n"
                    "This permanently deletes the file from the server's `data/` folder and resets the stock inventory."
                ),
                "action": "INFO",
                "pending_plan": context.get("pending_plan"),
                "updated_context": context
            }

        # ---------------------------------------------------------------------
        # INTENT 4: KNOWLEDGE BASE SEARCH
        # ---------------------------------------------------------------------
        # If the input contains conversational questions or policy/store inquiries
        kb_result = self.kb.search(clean_input, context=context)
        is_question = bool(re.search(r'\b(what|why|how|where|when|who|which|policy|buffer|delivery|time|rules|faq|explain)\b', lower_input))
        
        # If strong KB match or explicitly asking an operational question
        # A weak keyword hit (e.g. just the word "stock") must not hide a message that names a real product
        names_a_product = bool(self.find_item(clean_input)[0])
        weak_kb_hit = bool(kb_result) and kb_result["score"] < 6 and not is_question and names_a_product
        if kb_result and not weak_kb_hit and (is_question or not re.search(r'\b\d+\b', clean_input)):
            return {
                "reply": kb_result["formatted_response"],
                "action": "KB_ANSWER",
                "pending_plan": context.get("pending_plan"),
                "updated_context": context
            }

        # ---------------------------------------------------------------------
        # INTENT 5: ORDER PARSING & ALLOCATION (WITH CONTEXT)
        # ---------------------------------------------------------------------
        parsed = self.parse_user_order(clean_input, context=context)

        if parsed.get("unknown_ids"):
            bad = ", ".join(f"ID {n}" for n in parsed["unknown_ids"])
            return {
                "reply": (
                    f"⚠️ I could not find a product with **{bad}**. Nothing was done.\n\n"
                    "Check the **Product_ID** column in the *Stock (Daily Report)* tab and try again."
                ),
                "action": "UNKNOWN_ID",
                "pending_plan": context.get("pending_plan"),
                "updated_context": context
            }

        # Scenario A2: Several products in one message -> one combined proposal
        if len(parsed.get("items") or []) >= 2:
            party = parsed["party_name"]
            plans, errors = [], []
            for entry in parsed["items"]:
                item_plan = self.plan_order(entry["item_code"], entry["quantity"], safety_pct=safety_pct)
                if item_plan["success"]:
                    plans.append(item_plan)
                else:
                    errors.append(item_plan["error"])

            if plans:
                context["last_items"] = [
                    {"code": p["item_code"], "name": p["item_name"], "quantity": p["requested_qty"]}
                    for p in plans
                ]
                context["last_item_code"] = plans[-1]["item_code"]
                context["last_item_name"] = plans[-1]["item_name"]
                context["last_party_name"] = party
                context["last_quantity"] = plans[-1]["requested_qty"]
                context["pending_plan"] = {"party": party, "plans": plans, "plan": plans[0]}

                icons = {"FULL": "✅", "PARTIAL": "⚠️", "OUT_OF_STOCK": "❌"}
                reply = f"📋 **Multi-Product Order Proposal for {party}** ({len(plans)} products):\n\n"
                for idx, p in enumerate(plans, start=1):
                    reply += (
                        f"{idx}. {icons[p['status']]} **{p['item_name']}** (`{p['item_code']}`) — "
                        f"requested {p['requested_qty']:,}, fulfillable {p['fulfilled_qty']:,} units\n"
                    )
                    if p["dispatched_stores_count"] > 1:
                        reply += f"   ℹ️ {p['split_summary']}\n"
                reply += f"\n**Safety Stock Reserved:** {safety_pct}% in every store\n\n"
                if errors:
                    reply += "❌ **Not found in catalog:** " + "; ".join(errors) + "\n\n"
                if all(p["status"] == "FULL" for p in plans):
                    reply += (
                        "✅ **All products can be 100% fulfilled!** Review the breakdown on the right and click "
                        "**'Confirm & Release'**, or reply **'confirm'** here. All products are released under one Order ID."
                    )
                else:
                    reply += (
                        "⚠️ **Some products are only partly available.** Reply **'confirm'** to release what is "
                        "available, or **'cancel'** to adjust."
                    )
                return {
                    "reply": reply,
                    "action": "ORDER_PROPOSAL",
                    "pending_plan": context["pending_plan"],
                    "updated_context": context
                }

            return {
                "reply": "❌ " + "; ".join(errors),
                "action": "ERROR",
                "pending_plan": context.get("pending_plan"),
                "updated_context": context
            }

        # Scenario A: Product and Quantity identified
        if parsed["item_code"] and parsed["quantity"]:
            party = parsed["party_name"]
            item_code = parsed["item_code"]
            qty = parsed["quantity"]

            plan = self.plan_order(item_code, qty, safety_pct=safety_pct)

            if plan["success"]:
                # Save into conversational context memory
                context["last_item_code"] = item_code
                context["last_item_name"] = plan["item_name"]
                context["last_party_name"] = party
                context["last_quantity"] = qty
                context["pending_plan"] = {"party": party, "plan": plan, "plans": [plan]}
                context["last_items"] = [
                    {"code": item_code, "name": plan["item_name"], "quantity": qty}
                ]

                context_note = ""
                if parsed.get("context_used"):
                    context_note = f"*(Referenced previous product: **{plan['item_name']}**)*\n\n"

                reply = (
                    f"{context_note}📋 **Order Allocation Proposal for {party}:**\n\n"
                    f"• **Product:** {plan['item_name']} (`{plan['item_code']}`)\n"
                    f"• **Requested Quantity:** {plan['requested_qty']:,} units\n"
                    f"• **Fulfillable Quantity:** {plan['fulfilled_qty']:,} units\n"
                    f"• **Total System Stock:** {plan['total_system_stock']:,} units\n"
                    f"• **Safety Stock Reserved ({safety_pct}%):** {plan['total_safety_buffer']:,} units\n"
                    f"• **Available for Allocation:** {plan['total_available']:,} units\n\n"
                )

                if plan["dispatched_stores_count"] > 1:
                    reply += f"ℹ️ **Multi-Store Fulfillment:** {plan['split_summary']}\n\n"

                if plan["status"] == "FULL":
                    reply += (
                        "✅ **Order can be 100% fulfilled!**\n"
                        "Review the store dispatch breakdown on the right and click **'Confirm & Release'**, "
                        "or simply reply **'confirm'** here in the chat."
                    )
                elif plan["status"] == "PARTIAL":
                    reply += (
                        f"⚠️ **Partial Fulfillment:** Across all regional stores, only "
                        f"**{plan['fulfilled_qty']:,} units** are salable ({plan['shortage']:,} units shortage). "
                        f"Reply **'confirm'** to dispatch the partial quantity, or **'cancel'** to adjust."
                    )
                else:
                    reply += (
                        "❌ **Out of Stock:** All current stock is reserved under safety buffers "
                        "or fully exhausted across all stores."
                    )

                return {
                    "reply": reply,
                    "action": "ORDER_PROPOSAL",
                    "pending_plan": context["pending_plan"],
                    "updated_context": context
                }
            else:
                return {
                    "reply": f"❌ {plan['error']}",
                    "action": "ERROR",
                    "pending_plan": context.get("pending_plan"),
                    "updated_context": context
                }

        # Scenario B: Only Product identified (Stock Level Inquiry)
        elif parsed["item_code"] and not parsed["quantity"]:
            item_code = parsed["item_code"]
            item_name = parsed["item_name"]

            # Save in context memory
            context["last_item_code"] = item_code
            context["last_item_name"] = item_name
            context["last_items"] = [{"code": item_code, "name": item_name, "quantity": None}]

            stores_data = self.get_all_stores()
            pid = next((i["Product_ID"] for i in self.get_all_items() if i["Item_Code"] == item_code), "?")
            reply = f"🔍 **Current Stock Levels for {item_name} (ID {pid}):**\n\n"
            
            total_found = 0
            for s_name, df_store in stores_data.items():
                matching = df_store[df_store["Item_Code"] == item_code]
                if not matching.empty:
                    s_stock = int(matching.iloc[0]["Total_Stock"])
                    total_found += s_stock
                    reply += f"• **{s_name}:** {s_stock:,} units\n"
            
            reply += f"\n**Total Network Stock:** {total_found:,} units\n\n"
            reply += "💡 *Tip: You can now say 'Order 200 of this for Party Bilal' without retyping the product name!*"

            return {
                "reply": reply,
                "action": "STOCK_INFO",
                "pending_plan": context.get("pending_plan"),
                "updated_context": context
            }

        # Scenario C: Fallback to Knowledge Base or General Help
        if kb_result:
            return {
                "reply": kb_result["formatted_response"],
                "action": "KB_ANSWER",
                "pending_plan": context.get("pending_plan"),
                "updated_context": context
            }

        help_reply = (
            "❓ I could not identify a product or matching knowledge article.\n\n"
            "**Here are things you can do:**\n"
            "• **Check Stock:** *'Check stock for Avitryl Oral Solution'* or *'Aquamin stock'*\n"
            "• **Place Order:** *'Party Bilal wants 50 of ID 7'* (or use the product name)\n"
            "• **Several Products:** *'Party Bilal wants 50 of ID 7, 20 of ID 12 and 100 of ID 30'*\n"
            "• **Use Context Memory:** After checking stock, say *'Order 200 of this for Acme Corp'*\n"
            "• **Ask Policies & Hubs:** *'What is safety stock?'*, *'Where are your stores?'*, *'Delivery time?'*\n"
            "• **Confirm Order:** Reply *'confirm'* when a proposal is active."
        )
        return {
            "reply": help_reply,
            "action": "UNKNOWN",
            "pending_plan": context.get("pending_plan"),
            "updated_context": context
        }
"""
=============================================================================
Streamlit Web Application (app.py)
=============================================================================
Enterprise Multi-Store Inventory & Order Allocation Chatbot
- Multi-store inventory monitoring across regional store sheets.
- Multi-turn conversation context memory (remembers products, parties, and orders).
- Integrated Knowledge Base (policies, hub locations, lead times, and FAQs).
- Automatic cascading order allocation (cascades from other stores if stock ends).
- Dynamic safety buffer protection (15% - 20% reserved for other clients).
- Automatic inventory deduction upon confirmation and release file creation.
=============================================================================
"""

import os
import hashlib
from datetime import datetime
import streamlit as st
import pandas as pd
from inventory_manager import InventoryManager, StockReportError, find_current_report

# -----------------------------------------------------------------------------
# 1. PAGE CONFIGURATION & SETUP
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Multi-Store Stock & Order Allocation System",
    page_icon="📦",
    layout="wide"
)

# Initialize Session State Objects
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

if "mgr" not in st.session_state:
    st.session_state.mgr = InventoryManager()

if "context" not in st.session_state:
    st.session_state.context = {
        "last_item_code": None,
        "last_item_name": None,
        "last_party_name": None,
        "last_quantity": None,
        "pending_plan": None,
        "last_order_id": None,
        "last_order_details": None
    }

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "👋 **Welcome! I am your Order Allocation Assistant to:**\n\n"
                "• **Real-Time Stock Check**\n\n"
                "• **Context Memory**\n\n"
                "• **Auto-Cascading Dispatch**\n\n"
                "• **Safety Stock Reserve**\n\n"
                "• **Integrated Knowledge Base**"
            )
        }
    ]

mgr: InventoryManager = st.session_state.mgr


# -----------------------------------------------------------------------------
# 2. SIDEBAR CONFIGURATION & CONTEXT MEMORY MONITOR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("⚙️ Control Panel")
    
    # Safety stock percentage slider
    safety_pct = st.slider(
        "Safety Stock Buffer (%)",
        min_value=10,
        max_value=30,
        value=15,
        step=1,
        help="Percentage of stock reserved in each regional store for other parties."
    )
    st.caption(f"🛡️ Reserving {safety_pct}% of total stock in each store as a safety buffer.")

    st.divider()
    st.subheader("📊 System Stock Overview")
    
    stores = mgr.get_all_stores()
    total_units = sum(df["Total_Stock"].sum() for df in stores.values()) if stores else 0

    metric_col1, metric_col2 = st.columns(2)
    metric_col1.metric("Active Stores", len(stores))
    metric_col2.metric("Total Inventory", f"{int(total_units):,} units")

    # --- Conversational Context Inspector ---
    st.divider()
    st.subheader("🧠 Conversation Memory")
    ctx = st.session_state.context
    
    with st.expander("Active Memory Context", expanded=True):
        mem_items = ctx.get("last_items") or []
        if len(mem_items) > 1:
            st.markdown(f"• **Products ({len(mem_items)}):**")
            for mem in mem_items:
                qty_txt = f" × {mem['quantity']:,}" if mem.get("quantity") else ""
                st.markdown(f"   – {mem['name']} (`{mem['code']}`){qty_txt}")
        elif ctx.get("last_item_name"):
            st.markdown(f"• **Product:** {ctx['last_item_name']} (`{ctx['last_item_code']}`)")
        else:
            st.markdown("• **Product:** *None*")

        if ctx.get("last_party_name"):
            st.markdown(f"• **Client / Party:** {ctx['last_party_name']}")
        else:
            st.markdown("• **Client / Party:** *None*")

        if ctx.get("last_order_id"):
            st.markdown(f"• **Last Order Placed:** `{ctx['last_order_id']}`")

        if ctx.get("pending_plan"):
            st.markdown("• **Pending Proposal:** 🟡 *Waiting Confirmation*")
        else:
            st.markdown("• **Pending Proposal:** ⚪ *None*")

    st.divider()
    st.subheader("⚡ Quick Actions")

    if st.button("🔄 Refresh Data", use_container_width=True):
        st.rerun()

    if st.button("🗑️ Clear Chat & Memory", use_container_width=True):
        st.session_state.messages = [st.session_state.messages[0]]
        st.session_state.context = {
            "last_item_code": None,
            "last_item_name": None,
            "last_party_name": None,
            "last_quantity": None,
            "pending_plan": None,
            "last_order_id": None,
            "last_order_details": None
        }
        st.rerun()


# -----------------------------------------------------------------------------
# 3. MAIN HEADER & TAB NAVIGATION
# -----------------------------------------------------------------------------
st.title("📦 Inventory & Order Allocation Assistant")
st.caption(
    "AI-powered multi-store order dispatcher with multi-turn conversation memory, "
    "knowledge base support, and automatic cascading order release."
)

tab1, tab2, tab3, tab4 = st.tabs([
    "💬 Chatbot & Order Dispatch",
    "🏬 Stock (Daily Report)",
    "📄 Released Orders History",
    "📚 Knowledge Base & FAQs"
])


# =============================================================================
# TAB 1: CHATBOT & ORDER DISPATCH (CONTEXT-AWARE)
# =============================================================================
with tab1:
    chat_col, order_col = st.columns([1.6, 1.0], gap="large")

    # -------------------------------------------------------------------------
    # LEFT COLUMN: CONVERSATION INTERFACE
    # -------------------------------------------------------------------------
    with chat_col:
        st.subheader("💬 Assistant Conversation")

        # Display conversation messages
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"].replace("\n", "  \n"))

        # Chat input box
        user_input = st.chat_input("Enter order, ask questions, or say 'confirm'")

        if user_input:
            # 1. Add user message to history
            st.session_state.messages.append({"role": "user", "content": user_input})
            
            # 2. Process turn via context-aware chat processor
            result = mgr.process_chat_message(
                user_input=user_input,
                context=st.session_state.context,
                safety_pct=safety_pct
            )

            # 3. Update session state with returned context and plan
            st.session_state.context = result["updated_context"]
            st.session_state.messages.append({"role": "assistant", "content": result["reply"]})
            st.rerun()

    # -------------------------------------------------------------------------
    # RIGHT COLUMN: ORDER CONFIRMATION & DIRECT ENTRY
    # -------------------------------------------------------------------------
    with order_col:
        st.subheader("📑 Order Dispatch Panel")
        pending = st.session_state.context.get("pending_plan")

        # Display active allocation proposal if available
        if pending:
            plans = pending.get("plans") or [pending["plan"]]
            party = pending["party"]

            product_lines = "  \n".join(
                f"**{p['item_name']}:** {p['fulfilled_qty']:,} / {p['requested_qty']:,} units"
                for p in plans
            )
            st.info(f"**Client / Party:** {party}  \n{product_lines}")

            for p in plans:
                # Multi-store cascading alert
                if p["dispatched_stores_count"] > 1:
                    st.warning(
                        f"🔄 **Auto-Cascading Active ({p['item_name']}):** remaining units are split "
                        f"across {p['dispatched_stores_count']} stores to achieve 100% dispatch!"
                    )

            for p in plans:
                st.write(f"**Store Dispatch Breakdown — {p['item_name']}:**")
                breakdown_rows = []
                for alloc in p["allocations"]:
                    if alloc["Dispatch_Qty"] > 0 or alloc["Current_Stock"] > 0:
                        breakdown_rows.append({
                            "Store Sheet": alloc["Store_Name"],
                            "Dispatch Qty": alloc["Dispatch_Qty"],
                            "Remaining": alloc["Remaining_Stock"],
                            "Safety Buffer": alloc["Safety_Buffer"],
                            "Store Status": alloc["Store_Status"]
                        })
                st.dataframe(pd.DataFrame(breakdown_rows), hide_index=True, use_container_width=True)

            action_col1, action_col2 = st.columns(2)
            
            with action_col1:
                if st.button("✅ Confirm & Release", type="primary", use_container_width=True):
                    success, res = mgr.confirm_and_release_order(party, plans)
                    if success:
                        # Update context memory
                        st.session_state.context["last_order_id"] = res["order_id"]
                        st.session_state.context["last_order_details"] = res
                        st.session_state.context["pending_plan"] = None

                        success_message = (
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
                        st.session_state.messages.append({"role": "assistant", "content": success_message})
                        st.success("Order dispatched and store inventory updated!")
                        st.rerun()
                    else:
                        st.error(f"Failed to release order: {res}")

            with action_col2:
                if st.button("❌ Cancel Order", use_container_width=True):
                    st.session_state.context["pending_plan"] = None
                    st.session_state.messages.append({"role": "assistant", "content": "🛑 The order proposal was cancelled."})
                    st.rerun()

        # If no active proposal, show direct order selection form
        elif not mgr.get_all_items():
            st.info("No stock loaded yet. Upload today's stock report in the **Stock (Daily Report)** tab first.")

        else:
            st.write("Or calculate allocation directly using the form:")
            with st.form("manual_order_form"):
                default_party = st.session_state.context.get("last_party_name") 
                party_input = st.text_input("Client / Party Name", value=default_party)
                
                items = mgr.get_all_items()
                product_mapping = {
                    f"ID {item['Product_ID']} — {item['Item_Name']}": item["Item_Code"]
                    for item in items
                }
                
                # Default selection to last mentioned item if exists in context
                item_labels = list(product_mapping.keys())
                default_idx = 0
                if st.session_state.context.get("last_item_code"):
                    for idx, label in enumerate(item_labels):
                        if st.session_state.context["last_item_code"] in label:
                            default_idx = idx
                            break

                selected_label = st.selectbox("Select Product", item_labels)
                qty_input = st.number_input(
                    "Order Quantity (units)",
                    min_value=1,
                    max_value=5000,
                    value=st.session_state.context.get("last_quantity"),
                    step=10
                )

                submit_pressed = st.form_submit_button("⚡ Calculate Allocation Plan", use_container_width=True)
                if submit_pressed:
                    selected_sku = product_mapping[selected_label]
                    plan = mgr.plan_order(selected_sku, qty_input, safety_pct=safety_pct)
                    if plan["success"]:
                        # Save in session context
                        st.session_state.context["last_item_code"] = selected_sku
                        st.session_state.context["last_item_name"] = plan["item_name"]
                        st.session_state.context["last_party_name"] = party_input
                        st.session_state.context["last_quantity"] = qty_input
                        st.session_state.context["pending_plan"] = {"party": party_input, "plan": plan}
                        st.rerun()
                    else:
                        st.error(plan["error"])


# =============================================================================
# TAB 2: STOCK FROM THE DAILY UPLOADED REPORT
# =============================================================================
with tab2:
    st.subheader("🏬 Stock — from the daily Excel report")
    st.caption(
        "Upload the daily stock report here. It is saved in the `data` folder and is the only "
        "source of stock for this app (Closing Balance column). Use a product's **Product_ID** to order it in the chat."
    )

    # ---- Upload -----------------------------------------------------------------
    uploaded = st.file_uploader(
        "Upload today's stock report (.xlsx)",
        type=["xlsx", "xlsm"],
        key=f"stock_report_uploader_{st.session_state.uploader_key}"
    )
    if uploaded is not None:
        file_bytes = uploaded.getvalue()
        upload_id = hashlib.md5(file_bytes).hexdigest()
        if st.session_state.get("processed_upload_id") != upload_id:
            try:
                parsed_upload = mgr.save_uploaded_report(file_bytes, uploaded.name)
                st.session_state.processed_upload_id = upload_id
                st.session_state.context["pending_plan"] = None   # old proposal used old stock
                st.session_state.upload_flash = (
                    f"Loaded **{uploaded.name}** — report date "
                    f"**{parsed_upload['report_date'] or 'not found in file'}**, "
                    f"{len(parsed_upload['stores'])} offices, "
                    f"{int(parsed_upload['batches']['Total_Stock'].sum()):,} units."
                )
                st.rerun()
            except StockReportError as err:
                st.session_state.processed_upload_id = upload_id
                st.error(f"Could not use this file: {err}  \nThe previous report (if any) is still in use.")

    if st.session_state.get("upload_flash"):
        st.success(st.session_state.pop("upload_flash"))
    if st.session_state.get("report_delete_flash"):
        st.success(st.session_state.pop("report_delete_flash"))
    if st.session_state.get("report_delete_error"):
        st.error(st.session_state.pop("report_delete_error"))

    # ---- Current report status & management -------------------------------------
    stores = mgr.get_all_stores()
    load_info = mgr.last_load_info
    report_files = mgr.get_report_files()

    if load_info.get("error"):
        st.error(f"The stored report could not be read: {load_info['error']}")

    if not stores and not report_files:
        st.info("No stock report has been uploaded yet. Upload today's Excel file above.")
    else:
        if stores:
            report_date = load_info.get("report_date")
            meta = load_info.get("meta", {})
            status = f"📅 Stock as on **{report_date or 'unknown date'}**"
            if meta.get("original_name"):
                status += f" · file `{meta['original_name']}` uploaded {meta.get('uploaded_at', '')}"

            col_status, col_dl = st.columns([3, 1])
            with col_status:
                st.markdown(status)
            with col_dl:
                current_path = find_current_report(mgr.data_dir)
                if current_path and os.path.exists(current_path):
                    try:
                        with open(current_path, "rb") as f_obj:
                            st.download_button(
                                label="📥 Download Excel",
                                data=f_obj.read(),
                                file_name=os.path.basename(current_path),
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="dl_current_report",
                                use_container_width=True
                            )
                    except Exception:
                        pass

            if report_date:
                age_days = (datetime.now().date() - datetime.strptime(report_date, "%Y-%m-%d").date()).days
                if age_days > 0:
                    st.warning(f"This report is {age_days} day(s) old. Upload today's file to refresh the stock.")
            deducted = mgr.get_order_deductions()
            if deducted:
                st.caption(f"ℹ️ Figures below already subtract {deducted:,} units released in this app since this report was uploaded.")
            for msg in load_info.get("warnings", []):
                st.warning(f"Report check: {msg}")

        # ---- Manage / Remove report files from data folder ------------------
        if report_files:
            with st.expander(
                f"🗑️ **Remove Excel File from `data` Folder** ({len(report_files)} file{'s' if len(report_files) > 1 else ''} present)",
                expanded=(not stores)
            ):
                st.caption(
                    "Permanently delete stock report Excel file(s) from the server's `data/` directory. "
                    "This immediately removes the file and resets/updates the loaded stock inventory."
                )

                file_options = {
                    f"{rf['filename']} ({rf['size_kb']} KB, updated {rf['modified_at']})" + (" — [CURRENT ACTIVE]" if rf["is_current"] else ""): rf["filename"]
                    for rf in report_files
                }

                selected_file_label = st.selectbox(
                    "Select Excel file to remove:",
                    options=list(file_options.keys()),
                    key="delete_report_file_select"
                )
                selected_filename = file_options[selected_file_label]

                col_conf, col_btn = st.columns([2, 1])
                with col_conf:
                    confirm_del = st.checkbox(
                        f"I confirm I want to permanently remove `{selected_filename}` from `data/`",
                        key="delete_report_file_confirm"
                    )
                with col_btn:
                    if st.button(
                        "🗑️ Remove File",
                        type="primary",
                        disabled=not confirm_del,
                        key="btn_delete_report_file",
                        use_container_width=True
                    ):
                        ok, msg = mgr.delete_report_file(selected_filename)
                        if ok:
                            st.session_state.processed_upload_id = None
                            st.session_state.uploader_key = st.session_state.get("uploader_key", 0) + 1
                            st.session_state.context["pending_plan"] = None
                            st.session_state.report_delete_flash = msg
                            st.rerun()
                        else:
                            st.session_state.report_delete_error = msg
                            st.rerun()

                if len(report_files) > 1:
                    st.divider()
                    st.caption("Bulk cleanup:")
                    col_all_conf, col_all_btn = st.columns([2, 1])
                    with col_all_conf:
                        confirm_all = st.checkbox(
                            "I confirm I want to delete ALL Excel report files in `data/`",
                            key="delete_all_reports_confirm"
                        )
                    with col_all_btn:
                        if st.button(
                            "🗑️ Remove All Files",
                            disabled=not confirm_all,
                            key="btn_delete_all_reports",
                            use_container_width=True
                        ):
                            ok, msg = mgr.delete_all_report_files()
                            if ok:
                                st.session_state.processed_upload_id = None
                                st.session_state.uploader_key = st.session_state.get("uploader_key", 0) + 1
                                st.session_state.context["pending_plan"] = None
                                st.session_state.report_delete_flash = msg
                                st.rerun()
                            else:
                                st.session_state.report_delete_error = msg
                                st.rerun()

        if stores:
            # ---- Office selector + metrics -------------------------------------------
            ALL_OFFICES = "All offices (combined)"
            selected_store = st.selectbox(
                "View stock for:", [ALL_OFFICES] + list(stores.keys()), key="store_sheet_select"
            )
            ratio = float(safety_pct) / 100.0

            if selected_store == ALL_OFFICES:
                combined = pd.concat(
                    [df.assign(Office=name) for name, df in stores.items()], ignore_index=True
                )
                wide = combined.pivot_table(
                    index=["Product_ID", "Item_Name", "Category"], columns="Office", values="Total_Stock",
                    aggfunc="sum", fill_value=0
                )
                wide["Total"] = wide.sum(axis=1)
                wide = wide.reset_index().sort_values("Product_ID")
                m1, m2 = st.columns(2)
                m1.metric("Products", len(wide))
                m2.metric("Total Stock (all offices)", f"{int(wide['Total'].sum()):,} units")
                st.dataframe(wide, hide_index=True, use_container_width=True)
            else:
                df_selected = stores[selected_store].copy()
                df_selected["Safety_Buffer"] = df_selected["Total_Stock"].apply(
                    lambda s: int(s * ratio) if s > 0 else 0
                )
                df_selected["Available_for_Sale"] = (
                    df_selected["Total_Stock"] - df_selected["Safety_Buffer"]
                ).clip(lower=0)

                m1, m2, m3 = st.columns(3)
                m1.metric("Office", selected_store)
                m2.metric("Total Stock", f"{int(df_selected['Total_Stock'].sum()):,} units")
                m3.metric("Available for Dispatch", f"{int(df_selected['Available_for_Sale'].sum()):,} units")

                search = st.text_input("🔍 Filter products", key="stock_filter")
                if search:
                    df_selected = df_selected[df_selected["Item_Name"].str.contains(search, case=False, na=False)]
                st.dataframe(
                    df_selected[["Product_ID", "Item_Name", "Category", "Total_Stock", "Safety_Buffer", "Available_for_Sale"]],
                    hide_index=True, use_container_width=True
                )

                # Batch & expiry detail straight from the report
                with st.expander("📦 Batch & expiry details (as in the uploaded report)"):
                    batches = mgr.get_stock_batches()
                    detail = batches[batches["Store_Name"] == selected_store]
                    if search:
                        detail = detail[detail["Item_Name"].str.contains(search, case=False, na=False)]
                    st.dataframe(
                        detail[["Product_ID", "Item_Name", "Batch_No", "Expiry_Date", "Opening_Stock", "Sales", "Total_Stock", "Remarks"]]
                        .rename(columns={"Total_Stock": "Closing_Balance"}),
                        hide_index=True, use_container_width=True
                    )
                    st.caption("Batch figures are the report's own; order deductions are applied to the office totals above.")


# =============================================================================
# TAB 3: RELEASED ORDERS HISTORY
# =============================================================================
with tab3:
    st.subheader("📄 Released Orders Audit Trail")

    def handle_delete_order(order_id: str):
        """Runs BEFORE the page redraws, so it may safely reset widget values."""
        ok, res = mgr.delete_order(order_id)
        if ok:
            # Clear chat memory if it referred to this order
            ctx_last = st.session_state.context.get("last_order_details")
            if ctx_last and ctx_last.get("order_id") == res["order_id"]:
                st.session_state.context["last_order_id"] = None
                st.session_state.context["last_order_details"] = None

            stock_note = (
                f" Stock returned: {', '.join(res['restored_lines'])}."
                if res["stock_restored"] and res["restored_lines"]
                else " Stock was not changed (a newer stock report is loaded)."
            )
            st.session_state.delete_flash = (
                f"Order {res['order_id']} ({res['party_name']}) was deleted.{stock_note}"
            )
            # Reset the form so the next order is never pre-confirmed
            st.session_state.delete_confirm_checkbox = False
        else:
            st.session_state.delete_error = str(res)

    # Show result of a previous delete action (survives the rerun)
    if st.session_state.get("delete_flash"):
        st.success(st.session_state.pop("delete_flash"))
    if st.session_state.get("delete_error"):
        st.error(st.session_state.pop("delete_error"))

    history_df = mgr.get_order_history()

    if history_df.empty:
        st.info("No orders released yet. Place an order in Tab 1 to see release history.")
    else:
        st.dataframe(history_df, hide_index=True, use_container_width=True)

        # ---------------------------------------------------------------------
        # DELETE A RELEASED ORDER (e.g. cancelled by the party)
        # ---------------------------------------------------------------------
        st.divider()
        st.write("🗑️ **Delete a Released Order (cancelled by party):**")
        st.caption("Deleting removes the order's Excel file from the orders folder and returns its units to stock.")

        order_summaries = mgr.get_order_summaries()
        order_labels = {
            f"{o['order_id']} — {o['party_name']} — {o['item_name']} ({o['total_units']:,} units)": o
            for o in order_summaries
        }

        if order_labels:
            selected_order_label = st.selectbox(
                "Select order to delete:", list(order_labels.keys()), key="delete_order_select"
            )
            chosen = order_labels[selected_order_label]

            confirm_delete = st.checkbox(
                f"I confirm I want to permanently delete order {chosen['order_id']}",
                key="delete_confirm_checkbox"
            )

            st.button(
                "🗑️ Delete Order",
                type="primary",
                disabled=not confirm_delete,
                key="delete_order_btn",
                on_click=handle_delete_order,
                args=(chosen["order_id"],),
            )

        st.divider()
        st.write("📥 **Download Released Order Documents:**")

        if os.path.exists(mgr.orders_dir):
            order_files = [
                f for f in os.listdir(mgr.orders_dir)
                if f.startswith("Order_") and (f.endswith(".xlsx") or f.endswith(".csv"))
            ]
            
            if order_files:
                for filename in reversed(order_files):
                    file_path = os.path.join(mgr.orders_dir, filename)
                    col_name, col_btn = st.columns([3, 1])
                    col_name.write(f"📄 `{filename}`")
                    
                    mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if filename.endswith(".xlsx") else "text/csv"
                    try:
                        with open(file_path, "rb") as f_obj:
                            col_btn.download_button(
                                label=f"Download",
                                data=f_obj.read(),
                                file_name=filename,
                                mime=mime_type,
                                key=f"dl_{filename}"
                            )
                    except Exception:
                        pass
            else:
                st.caption("No individual release documents found in 'orders/' directory.")


# =============================================================================
# TAB 4: KNOWLEDGE BASE & SYSTEM FAQS
# =============================================================================
with tab4:
    st.subheader("📚 System Knowledge Base & Operations Guide")
    st.caption("Browse official policies, nationwide hub locations, delivery lead times, and FAQs.")

    kb_search_query = st.text_input("🔍 Search Knowledge Base Articles:", placeholder="e.g. safety stock, delivery times, store locations, payments...")

    if kb_search_query:
        search_match = mgr.kb.search(kb_search_query)
        if search_match:
            art = search_match["article"]
            st.success(f"**Found Article:** {art['title']} *(Category: {art['category']})*")
            st.markdown(art["summary"])
            st.divider()
        else:
            st.info("No direct keyword match found. Showing all articles below.")

    # Group and display all articles by category
    articles_by_cat = {}
    for art in mgr.kb.articles:
        cat = art["category"]
        articles_by_cat.setdefault(cat, []).append(art)

    for category, art_list in articles_by_cat.items():
        st.markdown(f"### 📂 {category}")
        for art in art_list:
            with st.expander(f"📌 {art['title']}"):
                st.markdown(art["summary"])
                st.caption(f"Tags: {', '.join(art['keywords'])}")
"""
=============================================================================
Stock Inventory Setup Script (setup_data.py)
=============================================================================
Initializes the master multi-sheet inventory workbook: 'data/stock_inventory.xlsx'
where each regional store has its own dedicated sheet containing product stock.
Also generates individual store CSV files in 'data/stores/' for direct access.
=============================================================================
"""

import os
import pandas as pd

# Define system directory paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STORES_DIR = os.path.join(DATA_DIR, "stores")
ORDERS_DIR = os.path.join(BASE_DIR, "orders")
EXCEL_PATH = os.path.join(DATA_DIR, "stock_inventory.xlsx")

# Master Product Catalog (22 Products across Fabrics, Yarn, Denim, Accessories, Packaging)
MASTER_PRODUCTS = [
    ("PRD-101", "Cotton Fabric Roll 100m", "Fabrics", 12000),
    ("PRD-102", "Silk Fabric Roll 50m", "Fabrics", 18500),
    ("PRD-103", "Polyester Yarn Box 24kg", "Yarn", 4800),
    ("PRD-104", "Denim Jeans Fabric Roll", "Denim", 15000),
    ("PRD-105", "Linen Shirt Fabric Roll", "Fabrics", 14000),
    ("PRD-106", "Woolen Blend Fabric 50m", "Fabrics", 21000),
    ("PRD-107", "Spun Yarn Cone 50s", "Yarn", 3200),
    ("PRD-108", "Cotton Combed Yarn 20kg", "Yarn", 3900),
    ("PRD-109", "Nylon Filament Thread", "Yarn", 2600),
    ("PRD-110", "Stretch Twill Denim Roll", "Denim", 16500),
    ("PRD-111", "Heavyweight Raw Denim 14oz", "Denim", 17500),
    ("PRD-112", "Poplin Cotton Grey 120m", "Fabrics", 9500),
    ("PRD-113", "Lawn Printed Premium Roll", "Fabrics", 13500),
    ("PRD-114", "Chiffon Dyed Roll 50m", "Fabrics", 16000),
    ("PRD-115", "Georgette Fabric Roll", "Fabrics", 14500),
    ("PRD-116", "Viscose Rayon Fabric 100m", "Fabrics", 12500),
    ("PRD-117", "Lycra Elastic Ribbon Box", "Accessories", 850),
    ("PRD-118", "Metal Zipper 24-inch Box", "Accessories", 2200),
    ("PRD-119", "Polyester Sewing Thread Box", "Accessories", 1100),
    ("PRD-120", "Corrugated Packing Carton XL", "Packaging", 350),
    ("PRD-121", "Bubble Wrap Roll 100m", "Packaging", 1800),
    ("PRD-122", "Industrial Stretch Film Roll", "Packaging", 1450),
]

# Baseline Inventory Quantities for 10 Regional Stores
STORE_INVENTORY_MAP = {
    "Karachi_Central": [
        450, 220, 600, 350, 180, 110, 520, 400, 250, 280,
        210, 500, 320, 140, 160, 220, 480, 400, 650, 850, 300, 400
    ],
    "Karachi_Korangi": [
        300, 150, 500, 420, 130, 80, 380, 310, 290, 310,
        260, 350, 210, 90, 110, 180, 650, 320, 700, 600, 250, 450
    ],
    "Lahore_Badami_Bagh": [
        380, 160, 480, 240, 190, 120, 310, 350, 200, 220,
        180, 420, 280, 150, 130, 210, 350, 350, 480, 600, 200, 300
    ],
    "Lahore_Gulberg": [
        250, 190, 320, 190, 140, 90, 220, 260, 180, 190,
        150, 310, 390, 210, 170, 230, 400, 280, 550, 450, 180, 250
    ],
    "Faisalabad_Hub": [
        750, 300, 900, 510, 280, 190, 850, 720, 490, 460,
        380, 820, 600, 280, 260, 410, 800, 600, 950, 1200, 500, 650
    ],
    "Rawalpindi_Hub": [
        180, 95, 210, 140, 110, 160, 180, 210, 140, 150,
        120, 250, 190, 85, 95, 140, 250, 180, 300, 350, 120, 160
    ],
    "Multan_Depot": [
        420, 180, 380, 260, 150, 90, 440, 510, 220, 240,
        200, 390, 310, 130, 140, 200, 380, 290, 420, 450, 190, 260
    ],
    "Peshawar_Store": [
        150, 70, 180, 110, 80, 220, 140, 160, 120, 130,
        110, 180, 150, 120, 90, 130, 200, 160, 250, 280, 140, 150
    ],
    "Sialkot_Hub": [
        220, 110, 250, 280, 120, 140, 200, 240, 360, 290,
        240, 280, 210, 95, 110, 160, 520, 430, 610, 400, 180, 320
    ],
    "Gujranwala_Outlet": [
        210, 120, 270, 180, 110, 85, 230, 290, 170, 190,
        150, 260, 220, 110, 100, 150, 310, 250, 340, 380, 160, 210
    ]
}


def initialize_stock():
    """
    Creates directories and generates store data:
    1. Writes an individual CSV file for each regional store in 'data/stores/'.
    2. Writes a master Excel file ('data/stock_inventory.xlsx') with 10 dedicated sheets.
    """
    # Ensure directories exist
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(STORES_DIR, exist_ok=True)
    os.makedirs(ORDERS_DIR, exist_ok=True)

    store_dataframes = {}

    # Build DataFrame for each regional store
    for store_name, stock_quantities in STORE_INVENTORY_MAP.items():
        records = []
        for index, product in enumerate(MASTER_PRODUCTS):
            item_code, item_name, category, unit_price = product
            records.append({
                "Item_Code": item_code,
                "Item_Name": item_name,
                "Category": category,
                "Total_Stock": stock_quantities[index],
                "Unit_Price": unit_price,
                "Last_Updated": "2026-11-20"
            })
        
        df_store = pd.DataFrame(records)
        store_dataframes[store_name] = df_store

        # Write individual CSV file for this store
        csv_file_path = os.path.join(STORES_DIR, f"{store_name}.csv")
        df_store.to_csv(csv_file_path, index=False)

    # Write multi-sheet master Excel workbook
    try:
        with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl") as writer:
            for store_name, df_store in store_dataframes.items():
                df_store.to_excel(writer, sheet_name=store_name, index=False)
        print(f"[SUCCESS] Master Excel created: {EXCEL_PATH} with {len(store_dataframes)} store sheets.")
    except Exception as error:
        print(f"[INFO] Excel writer notice: {error}. Individual store CSVs generated in {STORES_DIR}.")


if __name__ == "__main__":
    initialize_stock()

# Stock Inventory & Order Allocation Portal

A FastAPI-based stock management system for daily Excel inventory reports, multi-store order allocation, zone operations, payment recovery, inter-warehouse transfers (ITR), delivery tracking, and returns.

The application provides a browser portal for five zone users, one recovery user, and an administrator. It uses SQLite for users and operational records, and Excel files as the source for daily stock data and generated order documents.

## Features

- Role-based authentication with salted PBKDF2 password hashes and signed session cookies
- First-login password change and administrator password reset
- Daily stock report upload and removal
- Product and batch-level stock views
- Configurable 10%-30% safety buffer, with 15% as the default
- Multi-store cascading allocation from the highest-available stores
- Natural-language and manual order planning
- Confirmed Excel order documents and order history
- Reversible order cancellation with automatic stock restoration
- Five operational zones with configurable city assignments
- Zone order requests, delivery challans, status updates, returns, and ITR requests
- Admin approvals for orders and ITR transfers
- Payment hold/recovery workflow
- Knowledge-base search
- SQLite dashboard and activity reporting

## Technology

- Python 3.10+
- FastAPI
- Uvicorn
- SQLite
- Pandas and OpenPyXL
- python-calamine
- Vanilla HTML, CSS, and JavaScript

## Application Roles

| Role | Capabilities |
|---|---|
| Administrator | Upload stock reports, configure safety stock, plan and release orders through chat, approve/reject/hold requests, manage ITR transfers, view dashboards and statistics, manage users and cities, and reset operational data |
| Zone user | View stock available in the assigned zone, submit orders, track delivery, set challan/delivery status, process returns, and create or receive ITR requests |
| Recovery user | Create and view payment recovery entries across zones and release held payment orders |

There are five predefined zones: `Zone I`, `Zone II`, `Zone III`, `Zone IV`, and `Zone V`.

## Default Accounts

The database creates the following accounts only when no users exist:

| Username | Password | Role |
|---|---|---|
| `admin` | `admin123` | Administrator |
| `zone1` | `zone1@123` | Zone I |
| `zone2` | `zone2@123` | Zone II |
| `zone3` | `zone3@123` | Zone III |
| `zone4` | `zone4@123` | Zone IV |
| `zone5` | `zone5@123` | Zone V |
| `recovery` | `recovery@123` | Recovery |

All seeded accounts are required to change their password on first login. Change these credentials before exposing the application beyond a trusted local network.

## Project Structure

```text
stock-chatbot/
├── app.py                 FastAPI application and HTTP API
├── portal.py              Authentication, SQLite models, and portal workflows
├── inventory_manager.py   Excel parsing, allocation, orders, ITR, and stock logic
├── knowledge_base.py      Searchable operations knowledge base
├── setup_data.py          Optional legacy mock-data workbook generator
├── templates/
│   └── index.html         Browser application
├── static/
│   ├── app.js             Frontend behavior and API calls
│   └── style.css          Frontend styling
├── data/
│   ├── portal.db          Created automatically
│   ├── .session_secret    Created automatically
│   └── *.xlsx / *.xlsm    Uploaded daily stock reports
├── orders/                Generated order Excel files
├── itr/                   Generated ITR Excel files
├── requirements.txt
├── run_app.bat
└── README.md
```

The `orders/` and `itr/` directories are created automatically if missing.

## Installation

### Prerequisites

- Python 3.10 or newer
- Microsoft Windows is recommended for the included launcher, but the application can also run on other platforms supported by Python
- A stock report with supported Excel headers

### Windows Setup

```powershell
cd D:\Side-projects\stock_inventory_chatbot\stock_inventory_chatbot\stock-chatbot

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, use Command Prompt:

```bat
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

### Run the Application

With the virtual environment active:

```powershell
python app.py
```

Or:

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000>.

A health endpoint is available at <http://127.0.0.1:8000/health>.

### Windows One-Click Launcher

Double-click `run_app.bat`. It launches Uvicorn on port `8000`, opens the browser, and uses `.venv\Scripts\uvicorn.exe` when available.

## Stock Reports

1. Sign in as `admin`.
2. Open the stock report section.
3. Upload one or more `.xlsx` or `.xlsm` files.
4. The inventory engine parses the files and loads the store/offices, products, batches, quantities, and dates found in the supported sheets.

Reports can also be placed directly in `data/`. Place `Products.xlsx` in `data/` to provide product groups and catalog metadata.

The system keeps uploaded reports unchanged. Released order quantities are deducted dynamically in stock calculations; deleting an order restores its quantities when the stock report has not been replaced.

## Order Workflow

1. The administrator checks stock or submits a chat/manual order.
2. The system calculates the safety buffer for every store:
   - `Safety buffer = ceil(store stock * configured percentage)`
   - `Available stock = max(0, store stock - safety buffer)`
3. The allocation engine sorts stores by available stock and cascades quantities across stores until the request is fulfilled or network stock is exhausted.
4. The administrator reviews the proposed allocation.
5. Confirmation generates an Excel file under `orders/` and dynamically deducts the allocated quantities.
6. Zone users, the recovery portal, and the admin portal track the resulting operational request and delivery status.
7. Deleting a released order removes its Excel file and restores the dispatched quantities.

Example chat prompts:

```text
Check stock for product name
Stock for ID 7
Party Bilal wants 50 of ID 7
Party Bilal wants 50 of ID 7 and 20 of ID 12
Confirm
Cancel
What is the safety stock policy?
```

## Zone Portal Workflow

Zone users can:

- Submit orders for cities assigned to their zone
- Check zone and network availability before submission
- View pending, approved, held, rejected, and cancelled orders
- Add challan numbers after approval
- Mark orders as dispatched, delivered, not delivered, or cancelled
- Register full or partial returns
- Request stock transfers from a main warehouse
- Receive approved ITR transfers

City ownership is configurable from the administrator zone-management screen.

## Recovery Portal Workflow

The recovery portal records payment collection entries for parties and cities across all zones. Recovery entries include:

- Party name
- Zone and city
- Product group
- Amount
- Reference number
- Optional note

Administrators can move held orders back to pending after a payment issue is resolved.

## Inter-Warehouse Transfer Workflow

1. A zone user requests an ITR from a source warehouse to a destination warehouse.
2. The administrator reviews the request and available stock.
3. The administrator approves and specifies the source and transfer quantity.
4. Approved stock is deducted and the ITR moves to `IN_TRANSIT`.
5. The zone or administrator marks it received to transfer the quantity to the destination.
6. Rejection or cancellation stops or reverses the transfer according to its current state.

## Data and Storage

| Location | Purpose |
|---|---|
| `data/portal.db` | Users, zones, requests, recovery entries, ITR records, and settings |
| `data/.session_secret` | Persistent random key used to sign session cookies |
| `data/*.xlsx` / `*.xlsm` | Daily stock report files |
| `data/Products.xlsx` | Optional product catalog/group metadata |
| `product_ids.json` | Persistent product ID registry |
| `orders/` | Released order workbooks |
| `itr/` | Inter-warehouse transfer workbooks |

Generated SQLite and Excel backup/reset behavior is controlled by the administrator.

## Resetting Operational Data

The administrator reset screen previews the number of database entries and release files that will be removed.

To execute a reset, the administrator must type `DELETE ALL`. By default, a timestamped database backup is created under `data/` before operational records and order/ITR files are deleted.

Reset does not remove user accounts or zone-city mappings.

## API Overview

The FastAPI server exposes browser and API endpoints under `/api/`.

| Area | Main endpoints |
|---|---|
| Authentication | `/api/auth/login`, `/api/auth/me`, `/api/auth/logout`, `/api/auth/change-password` |
| Chat and orders | `/api/chat/*`, `/api/orders/*` |
| Stock | `/api/stock/upload`, `/api/stock/view`, `/api/stock/download/*` |
| Zone portal | `/api/zone/*` |
| Recovery portal | `/api/recovery/*` |
| Administration | `/api/admin/*` |
| Knowledge base | `/api/kb?q=...` |
| Health check | `/health` |

Interactive API documentation is available at:

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>

## Optional Mock Setup

`setup_data.py` is a legacy utility that creates an example `data/stock_inventory.xlsx` and store CSV files. It does not replace or initialize the portal's current SQLite-backed workflow.

```powershell
python setup_data.py
```

## Troubleshooting

- **Login fails:** Confirm the account is active and has not been disabled in the admin user screen.
- **No stock appears:** Upload a supported stock workbook or place it in `data/`.
- **An uploaded report is rejected:** Keep the report format compatible with the parser and include recognizable product/store/stock columns or sheets.
- **A port is already in use:** Start on another port, for example `--port 8001`.
- **An Excel file cannot be removed:** Close the file in Excel and retry.
- **Users must change a password:** Use the first-login password form or have an administrator reset the password.

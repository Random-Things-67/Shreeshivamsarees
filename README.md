# Stock Manager — Unified Billing & Inventory MVP

Stock Manager combines the existing Stock Manager product/inventory workspace with the Momobills-style POS and digital billing features in one app. It uses Python 3 standard library and SQLite; no pip packages or hosted database are required for local/LAN use.

## Start

1. Extract this project folder.
2. Open a terminal in the folder.
3. Run `./start.sh` (or `bash start.sh`; alternatively `python3 server.py`).
4. Open `http://127.0.0.1:8000/` and sign in. The app opens at `/mvp`.
5. For another device on the same trusted Wi-Fi, use the LAN address printed in the terminal.
6. To rename the app, open **Settings** in the sidebar, enter the display name, then choose **Save app name**. The name is stored in `saree_store.sqlite3` and also appears on the sign-in screen. The app starts as **Stock Manager** by default.
7. Use the small arrow button at the top of the left sidebar to collapse it to an icon rail or expand it to show names. Your choice is saved automatically.

Default local workspace password: `Molu@1234`. Change it before business use:

```bash
SAREE_ADMIN_PASSWORD='use-a-long-unique-password' \
SAREE_SESSION_SECRET='use-a-long-random-secret-string' \
./start.sh
```

Keep the terminal open while using the app. Set `SAREE_PORT=8080` before starting to use a different port. Login is a shared local password, not phone OTP.

## Included screens and features

### Dashboard
- Total product records and combined current quantity.
- Total investment in current inventory at purchase cost.
- Realized profit from recorded sales after production costs.
- Expected profit margin in currently unsold inventory, shown separately from realized profit.
- Low-stock overview and quick links to Inventory, Karigars, Customers, and Sold & Production.

### Inventory
- Add/edit/archive products with name, category, SKU (auto-generated when blank), cost/selling prices, unit, opening/current quantity, low-stock threshold, description, and multiple photos.
- Link a stock item to a saved karigar.
- Stock in/out adjustments, purchase-cost tracking, inventory valuation, photo previews, and quantity safeguards.
- Every active product row has Delete; products with transaction/history links are archived instead of hard-deleted, and can be restored from Archived products.
- Existing product and photo records are preserved when the app starts.

### Karigars
- Contacts previously saved by the legacy Saree Stock page are automatically recovered from the old SQLite `kv` store on startup. Names, phone numbers and addresses migrate into the current Karigars list without duplicating existing contacts. Legacy stock items are linked to their matching karigar where no newer link exists.
- Add/edit/delete karigar records with name, phone, address, notes, and linked-stock count.
- Linked karigars are removed from active choices but archived for safety; restore them in the Deleted / archived filter. Unlinked karigars can be permanently deleted.
- Assign a karigar to an inventory item, then change the assignment per sold order when needed.
- Archiving preserves historical assignments and records.

### Sold & Production
- Select an existing inventory product and see a visual preview before saving: product photos, category, current stock, purchase price, default selling price, and design notes. Click a preview photo or focus it and press Space/Enter to open the resizable Quick Look.
- Add multiple different saree designs to one sale/invoice with **＋ Add another saree**. Each line has its own product dropdown, photo/category/stock preview, quantity, and sale price; add as many lines as needed and remove extra lines before saving.
- The compact stock-choice popup lists every saree line and combined quantity. Choosing Deduct stock checks stock for all lines together; Keep stock saves all lines without changing inventory.
- Each Sold row has Delete sale. It removes the entire invoice and its production/payment rows; previously deducted quantities are restored. The confirmation explicitly states this is an invoice-level deletion.
- Every new sale opens a compact in-app choice: **Deduct stock**, **Keep stock**, or **Cancel sale**. The sale is only saved after selecting Deduct or Keep.
- Deducting stock checks availability, updates the inventory balance, and records a stock-movement row. Keeping stock saves the sale and production order without changing the current balance.
- Track Calendar, Fal, Piko, Cutting, Jalar, Dag, Finishing, Die, Raffu and Polish per sold line. Each stage can be Pending, In Progress, Completed, or Not Required, with an individual cost.
- Assign a karigar to the sold production order, review total production costs, and see when all required work is completed.

### Customers
- Every customer row has Delete. Contacts are removed from the customer list; if there is sale history, invoice snapshots and sold records are preserved. Deleted customers can be re-added later.

### Settings
- Rename the app from the Settings sidebar page. The custom name updates the sidebar brand, browser tab title, sign-in heading, and export filenames.
- App display name is separate from your shop/business identity on customer invoices. The legacy default business label `StockPilot` is migrated to `Stock Manager` only if it was still the unchanged default; custom shop names are preserved.
- Add/edit customer name, phone, email, address, and notes.
- See order count, total purchases, outstanding balance, last purchase, and purchase history for each customer.
- Customer records from existing sale history remain discoverable.

## Accounting notes
- Realized profit is based on recorded sale gross profit less tracked production-work costs.
- Expected profit is the current active stock quantity multiplied by selling price minus cost price. It is potential margin, not earned profit.
- Total investment is the cost price multiplied by current inventory quantity.
- A sale where the user chooses “Keep stock” remains in sales history, but does not reduce current inventory. The user should deduct stock later when the physical stock has actually left inventory.

## Data and backup

- Database: `saree_store.sqlite3`.
- All business data and uploaded images (product photos, expense receipts, logo/signature and other app images) are stored in `saree_store.sqlite3` in SQLite tables.
- On first startup after this update, the app imports existing files from `static/uploads/` into the database and removes each filesystem copy only after verifying that the exact bytes are safely stored in SQLite. New uploads are saved directly into the database. Existing image URLs, records, and photos are preserved.
- **Backup: only `saree_store.sqlite3` is required.** Stop the server before manually copying the file so SQLite can close cleanly. After installing this version, start the app once and confirm existing photos display before relying on database-only backups. Verify current quantities and costs; perform a physical stock audit if needed.

## Security

This is a local/LAN MVP, not intended for public internet exposure. It binds to `0.0.0.0` for trusted devices on the same Wi-Fi. Do not port-forward port 8000. Use a long password and session secret.

## Stack

- UI: responsive HTML, CSS, vanilla JavaScript
- Server/API: Python 3 standard library
- Database: SQLite with transactional sale and stock updates
- PDF and XLSX exports generated by the Python standard library

## Smoke-test checklist

1. Sign in and confirm Dashboard, Inventory, Karigars, Sold & Production, Customers, and Settings are visible.
2. Add a product with photos and assign a karigar.
3. Record a sale and test both choices: Deduct stock and Keep stock.
4. Open that sold line’s Production form and update stage statuses/costs.
5. Add a customer, then verify the customer purchase history and dashboard totals.
6. Open Settings, change the app name, save, refresh, and confirm the sidebar and sign-in page use the chosen name.
7. Stop the server and back up `saree_store.sqlite3` (this file includes records and image bytes).

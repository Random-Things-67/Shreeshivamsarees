# Saree Stock — Local IP Edition (Linux)

A local-network website using your existing Saree Stock Manager admin UI and customer catalogue. It uses Python 3's standard library and SQLite; no internet, cloud account, pip packages, or external database is needed.

## Run it

1. Extract this folder somewhere permanent on the Linux host computer.
2. Open a terminal in `saree-local-store`.
3. Run `./start.sh` (or `python3 server.py`).
4. Open the **Admin panel** URL printed in the terminal, then open the **Customer store** URL from another device on the same Wi-Fi.
5. Add/edit products in Admin. The catalogue reads the same SQLite-backed data. The Admin > Fields section lets you rename, delete, reorder, show/hide, and change editable field types; edit dropdown options; and add custom text/number/date/dropdown fields; customer-friendly custom fields are shown in product details. Product photos are stored as data URLs in the database, matching the original app's photo handling.

If another device cannot connect, run `./LAN-CHECK.sh` and follow its diagnostics. The server binds to `0.0.0.0` by default, which allows LAN connections. Use the real IPv4 address printed by the server (not `127.0.0.1` or `0.0.0.0`). Confirm the phone and host are on the same non-guest Wi-Fi and router client isolation is off. Allow TCP port 8000 through the host firewall only on your trusted private/LAN network. For UFW, on a trusted home/office network you can use `sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp` after replacing the subnet with your actual LAN subnet. Do not expose port 8000 to the public internet.

## Data and backup

- Database file: `saree_store.sqlite3` in this folder. Stop the server before copying it for a backup.
- Runs without internet after Python 3 is installed.
- Keep the host computer powered on and the server running while other devices use the store.
- To use a different port: `SAREE_PORT=8080 python3 server.py`.

## Important notes

- The admin page requires a password (default: `Molu@1234`), protects both `/admin` and `/admin.html`, and has a working POST-based Log out button. Homepage copy is saved in the shared SQLite-backed key/value table (the same storage layer used by catalogue fields). Change the password by setting `SAREE_ADMIN_PASSWORD` before launching. Use only on a trusted LAN and do not port-forward this service. The admin password is shared by anyone you tell it to.
- The original admin app initializes with two sample records on first run. Delete those sample entries in Admin if you do not want them shown in the customer store.
- Existing records saved in the browser's old `localStorage` are not automatically migrated into SQLite. If you have real inventory in the old standalone HTML, open that old admin in its original browser and export/copy your records before switching, or tell me and I can help create a one-time migration step. The included server starts with a fresh SQLite database.
- Customer store has responsive category browsing, search, sorting, availability filters, product photos, prices and a product-details view that displays customer-facing custom fields from Admin. It does not process payments or create orders; enquiries are still a lightweight placeholder, not a delivered order.
- This is a lightweight LAN setup, not an internet-facing production deployment. Do not use on untrusted networks.

## Karigar features

The Admin panel includes **Karigar Details** for saved names, mobile numbers and addresses; contact autofill when entering stock; purchase history; and a print / Save as PDF purchase form filtered by one date, a date range, or all dates. The Homepage tab edits customer-facing storefront copy and includes a Homepage images manager for uploading JPG, PNG, WEBP and GIF images, selecting the hero image, showing images in a homepage gallery, and removing uploads. Uploaded homepage images are stored in `static/uploads/`; keep that folder when backing up the project.

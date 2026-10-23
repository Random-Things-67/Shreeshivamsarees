"""Database-backed billing and inventory endpoints for the Stock Manager local/LAN MVP.
Uses only Python's standard library. Not intended to be exposed directly to the public internet.
"""
from __future__ import annotations
import base64
import csv
import io
import json
import math
import re
import sqlite3
import uuid
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import parse_qs
from xml.sax.saxutils import escape


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _today() -> str:
    return date.today().isoformat()


def _money(value, label="amount") -> float:
    try:
        d = Decimal(str(value if value not in (None, "") else 0))
        if not d.is_finite() or d < 0:
            raise ValueError(f"{label} must be a non-negative number")
        return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a non-negative number")


def _qty(value, label="quantity") -> float:
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d <= 0:
            raise ValueError(f"{label} must be greater than zero")
        return float(d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a positive number")


def _date(value, label="date") -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except Exception:
        raise ValueError(f"{label} must be YYYY-MM-DD")


def _connect(db_path):
    c = sqlite3.connect(db_path, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=15000")
    return c


def init_db(db_path):
    """Create MVP tables and import current legacy product records once."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS mvp_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        -- All uploaded images are stored as BLOBs in SQLite so the database file
        -- is a self-contained backup. URLs remain stable (/uploads/<filename>).
        CREATE TABLE IF NOT EXISTS mvp_media(
          filename TEXT PRIMARY KEY, mime_type TEXT NOT NULL, content BLOB NOT NULL,
          size INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS mvp_products(
          id TEXT PRIMARY KEY, legacy_id TEXT, sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT 'pcs',
          images TEXT NOT NULL DEFAULT '[]', quantity REAL NOT NULL DEFAULT 0 CHECK(quantity >= -0.000001),
          cost_price NUMERIC NOT NULL DEFAULT 0 CHECK(cost_price >= 0),
          selling_price NUMERIC NOT NULL DEFAULT 0 CHECK(selling_price >= 0),
          reorder_level REAL NOT NULL DEFAULT 1 CHECK(reorder_level >= 0),
          karigar_id TEXT NOT NULL DEFAULT '',
          is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS mvp_parties(
          id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('customer','supplier')),
          name TEXT NOT NULL COLLATE NOCASE, phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
          address TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(kind,name)
        );
        CREATE TABLE IF NOT EXISTS mvp_karigars(
          id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
          is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        -- Deleted customer contacts are hidden from the customer picker/list while
        -- old sale invoice snapshots remain available for history and accounting.
        CREATE TABLE IF NOT EXISTS mvp_hidden_customers(
          name_key TEXT PRIMARY KEY, display_name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
          hidden_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS mvp_purchases(
          id TEXT PRIMARY KEY, invoice_no TEXT NOT NULL, supplier_name TEXT NOT NULL DEFAULT '',
          purchase_date TEXT NOT NULL, subtotal NUMERIC NOT NULL, discount NUMERIC NOT NULL DEFAULT 0,
          tax NUMERIC NOT NULL DEFAULT 0, total NUMERIC NOT NULL, notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS mvp_purchase_items(
          id TEXT PRIMARY KEY, purchase_id TEXT NOT NULL REFERENCES mvp_purchases(id),
          product_id TEXT NOT NULL REFERENCES mvp_products(id), product_name TEXT NOT NULL,
          qty REAL NOT NULL CHECK(qty > 0), unit_cost NUMERIC NOT NULL CHECK(unit_cost >= 0),
          line_total NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mvp_sales(
          id TEXT PRIMARY KEY, invoice_no TEXT NOT NULL, customer_name TEXT NOT NULL DEFAULT '',
          sale_date TEXT NOT NULL, payment_status TEXT NOT NULL DEFAULT 'Pending', payment_method TEXT NOT NULL DEFAULT 'Cash',
          subtotal NUMERIC NOT NULL, discount NUMERIC NOT NULL DEFAULT 0, tax NUMERIC NOT NULL DEFAULT 0,
          total NUMERIC NOT NULL, cogs NUMERIC NOT NULL, gross_profit NUMERIC NOT NULL, notes TEXT NOT NULL DEFAULT '',
          customer_phone TEXT NOT NULL DEFAULT '', amount_paid NUMERIC NOT NULL DEFAULT 0,
          tax_type TEXT NOT NULL DEFAULT 'None', tax_rate NUMERIC NOT NULL DEFAULT 0,
          discount_type TEXT NOT NULL DEFAULT 'flat', discount_value NUMERIC NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS mvp_sale_items(
          id TEXT PRIMARY KEY, sale_id TEXT NOT NULL REFERENCES mvp_sales(id),
          product_id TEXT NOT NULL REFERENCES mvp_products(id), product_name TEXT NOT NULL,
          qty REAL NOT NULL CHECK(qty > 0), unit_price NUMERIC NOT NULL CHECK(unit_price >= 0),
          unit_cost NUMERIC NOT NULL CHECK(unit_cost >= 0), line_total NUMERIC NOT NULL,
          cogs NUMERIC NOT NULL, gross_profit NUMERIC NOT NULL,
          karigar_id TEXT NOT NULL DEFAULT '', production_work TEXT NOT NULL DEFAULT '{}',
          stock_deducted INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS mvp_stock_movements(
          id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES mvp_products(id), product_name TEXT NOT NULL,
          movement_type TEXT NOT NULL, qty_delta REAL NOT NULL, quantity_after REAL NOT NULL,
          unit_cost NUMERIC NOT NULL DEFAULT 0, reference_type TEXT NOT NULL DEFAULT '', reference_id TEXT NOT NULL DEFAULT '',
          reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS mvp_expenses(
          id TEXT PRIMARY KEY, expense_date TEXT NOT NULL, category TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '', amount NUMERIC NOT NULL CHECK(amount >= 0),
          payment_method TEXT NOT NULL DEFAULT 'Cash', notes TEXT NOT NULL DEFAULT '', receipt_image TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_mvp_products_name ON mvp_products(name);
        CREATE INDEX IF NOT EXISTS idx_mvp_movements_created ON mvp_stock_movements(created_at);
        CREATE INDEX IF NOT EXISTS idx_mvp_sales_date ON mvp_sales(sale_date);
        CREATE INDEX IF NOT EXISTS idx_mvp_purchases_date ON mvp_purchases(purchase_date);
        CREATE INDEX IF NOT EXISTS idx_mvp_expenses_date ON mvp_expenses(expense_date);
        CREATE INDEX IF NOT EXISTS idx_mvp_sale_items_sale ON mvp_sale_items(sale_id);
        CREATE INDEX IF NOT EXISTS idx_mvp_purchase_items_purchase ON mvp_purchase_items(purchase_id);
        CREATE TABLE IF NOT EXISTS mvp_sale_custom_items(
          id TEXT PRIMARY KEY, sale_id TEXT NOT NULL REFERENCES mvp_sales(id) ON DELETE CASCADE,
          item_name TEXT NOT NULL, qty REAL NOT NULL CHECK(qty>0), unit_price NUMERIC NOT NULL CHECK(unit_price>=0),
          line_total NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mvp_payments(
          id TEXT PRIMARY KEY, sale_id TEXT NOT NULL REFERENCES mvp_sales(id), payment_date TEXT NOT NULL,
          amount NUMERIC NOT NULL CHECK(amount>0), payment_method TEXT NOT NULL DEFAULT 'Cash', notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_mvp_payments_sale ON mvp_payments(sale_id);
        CREATE TABLE IF NOT EXISTS mvp_business_profile(
          id INTEGER PRIMARY KEY CHECK(id=1), shop_name TEXT NOT NULL DEFAULT 'Stock Manager', address TEXT NOT NULL DEFAULT '',
          phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', gst_number TEXT NOT NULL DEFAULT '',
          logo_url TEXT NOT NULL DEFAULT '', signature_url TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT 'INR',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO mvp_business_profile(id,shop_name) VALUES(1,'Stock Manager');
        CREATE TABLE IF NOT EXISTS mvp_app_settings(
          id INTEGER PRIMARY KEY CHECK(id=1), app_name TEXT NOT NULL DEFAULT 'Stock Manager',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO mvp_app_settings(id,app_name) VALUES(1,'Stock Manager');
        """)
        # Backward-compatible, additive migrations: never delete existing business records.
        # Rename the seeded default product brand, without overwriting a user's custom business name.
        c.execute("UPDATE mvp_business_profile SET shop_name='Stock Manager', updated_at=CURRENT_TIMESTAMP WHERE id=1 AND shop_name='StockPilot'")
        product_columns = {row[1] for row in c.execute("PRAGMA table_info(mvp_products)")}
        if 'images' not in product_columns:
            c.execute("ALTER TABLE mvp_products ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")
        if 'karigar_id' not in product_columns:
            c.execute("ALTER TABLE mvp_products ADD COLUMN karigar_id TEXT NOT NULL DEFAULT ''")
        sale_item_columns = {row[1] for row in c.execute("PRAGMA table_info(mvp_sale_items)")}
        for col, ddl in {
            'karigar_id': "TEXT NOT NULL DEFAULT ''",
            'production_work': "TEXT NOT NULL DEFAULT '{}'",
            'stock_deducted': "INTEGER NOT NULL DEFAULT 1"
        }.items():
            if col not in sale_item_columns:
                c.execute(f'ALTER TABLE mvp_sale_items ADD COLUMN {col} {ddl}')
        # Safe additive migrations for databases created by earlier MVP builds.
        sale_columns = {row[1] for row in c.execute("PRAGMA table_info(mvp_sales)")}
        for col, ddl in {
            'customer_phone': "TEXT NOT NULL DEFAULT ''", 'amount_paid': "NUMERIC NOT NULL DEFAULT 0",
            'tax_type': "TEXT NOT NULL DEFAULT 'None'", 'tax_rate': "NUMERIC NOT NULL DEFAULT 0",
            'discount_type': "TEXT NOT NULL DEFAULT 'flat'", 'discount_value': "NUMERIC NOT NULL DEFAULT 0"
        }.items():
            if col not in sale_columns: c.execute(f'ALTER TABLE mvp_sales ADD COLUMN {col} {ddl}')
        expense_columns = {row[1] for row in c.execute("PRAGMA table_info(mvp_expenses)")}
        if 'receipt_image' not in expense_columns: c.execute("ALTER TABLE mvp_expenses ADD COLUMN receipt_image TEXT NOT NULL DEFAULT ''")
        c.execute("CREATE TABLE IF NOT EXISTS mvp_sale_custom_items(id TEXT PRIMARY KEY, sale_id TEXT NOT NULL REFERENCES mvp_sales(id) ON DELETE CASCADE, item_name TEXT NOT NULL, qty REAL NOT NULL CHECK(qty>0), unit_price NUMERIC NOT NULL CHECK(unit_price>=0), line_total NUMERIC NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS mvp_payments(id TEXT PRIMARY KEY, sale_id TEXT NOT NULL REFERENCES mvp_sales(id), payment_date TEXT NOT NULL, amount NUMERIC NOT NULL CHECK(amount>0), payment_method TEXT NOT NULL DEFAULT 'Cash', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mvp_payments_sale ON mvp_payments(sale_id)")
        c.execute("CREATE TABLE IF NOT EXISTS mvp_business_profile(id INTEGER PRIMARY KEY CHECK(id=1), shop_name TEXT NOT NULL DEFAULT 'Stock Manager', address TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', gst_number TEXT NOT NULL DEFAULT '', logo_url TEXT NOT NULL DEFAULT '', signature_url TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT 'INR', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        c.execute("INSERT OR IGNORE INTO mvp_business_profile(id,shop_name) VALUES(1,'Stock Manager')")
        already = c.execute("SELECT value FROM mvp_meta WHERE key='legacy_products_seeded'").fetchone()
        if not already:
            row = c.execute("SELECT value FROM kv WHERE key='saree-items-v1'").fetchone()
            imported = 0
            if row:
                try:
                    legacy_items = json.loads(row[0])
                except Exception:
                    legacy_items = []
                for item in legacy_items if isinstance(legacy_items, list) else []:
                    values = item.get('values') or {}
                    legacy_id = str(item.get('id') or _id('legacy'))[:120]
                    raw_sku = ''.join(ch for ch in legacy_id.upper() if ch.isalnum() or ch in '-_')[:35]
                    sku = ('OLD-' + raw_sku)[:50] or ('OLD-' + uuid.uuid4().hex[:8].upper())
                    name = str(values.get('description') or '').strip()
                    category = str(values.get('category') or '').strip()
                    if not name:
                        name = f"{category} saree" if category else f"Imported item {legacy_id}"
                    try: qty = max(0.0, float(values.get('quantity', 1) or 0))
                    except Exception: qty = 1.0
                    try: cost = _money(values.get('costPrice') if values.get('costPrice') not in (None, '') else values.get('itemPrice', 0), 'cost price')
                    except ValueError: cost = 0.0
                    try: price = _money(values.get('price', 0) or 0, 'selling price')
                    except ValueError: price = 0.0
                    pid = 'prod_' + uuid.uuid4().hex[:12]
                    c.execute("""INSERT OR IGNORE INTO mvp_products
                      (id,legacy_id,sku,name,category,description,unit,quantity,cost_price,selling_price,reorder_level,is_active)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""",
                      (pid, legacy_id, sku, name, category, str(values.get('description') or ''), 'pcs', qty, cost, price, 1.0))
                    inserted = c.execute("SELECT id, name, quantity, cost_price FROM mvp_products WHERE sku=?", (sku,)).fetchone()
                    if inserted and qty > 0 and not c.execute("SELECT 1 FROM mvp_stock_movements WHERE reference_type='opening' AND reference_id=?", (inserted['id'],)).fetchone():
                        c.execute("INSERT INTO mvp_stock_movements(id,product_id,product_name,movement_type,qty_delta,quantity_after,unit_cost,reference_type,reference_id,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                  (_id('mov'), inserted['id'], inserted['name'], 'OPENING', qty, qty, float(inserted['cost_price']), 'opening', inserted['id'], 'Imported opening quantity from existing stock record'))
                    imported += 1
            c.execute("INSERT INTO mvp_meta(key,value) VALUES('legacy_products_seeded',?)", (str(imported),))
        # Import legacy karigar contacts from the old Saree Stock manager into
        # the current Karigars section. The legacy UI stored these records in kv,
        # so older contacts can exist even when mvp_karigars is empty. This runs
        # idempotently on every startup: it never creates a duplicate by name and
        # only fills missing phone/address values on an existing contact.
        def _legacy_karigar_name_key(value):
            return ' '.join(str(value or '').split()).casefold()

        legacy_karigar_names = {}
        contact_row = c.execute("SELECT value FROM kv WHERE key='saree-karigar-contacts-v1'").fetchone()
        if contact_row:
            try:
                legacy_contacts = json.loads(contact_row[0])
            except Exception:
                legacy_contacts = {}
            if isinstance(legacy_contacts, dict):
                contact_iter = legacy_contacts.items()
            elif isinstance(legacy_contacts, list):
                contact_iter = [(str(i), item) for i, item in enumerate(legacy_contacts)]
            else:
                contact_iter = []
            for contact_key, raw_contact in contact_iter:
                if not isinstance(raw_contact, dict):
                    continue
                karigar_name = ' '.join(str(raw_contact.get('name') or contact_key or '').split())
                if not karigar_name:
                    continue
                phone = str(raw_contact.get('phone') or '').strip()
                address = str(raw_contact.get('address') or '').strip()
                name_key = _legacy_karigar_name_key(karigar_name)
                existing = c.execute(
                    "SELECT id, phone, address FROM mvp_karigars WHERE lower(trim(name))=? ORDER BY is_active DESC LIMIT 1",
                    (name_key,)
                ).fetchone()
                if existing:
                    c.execute(
                        "UPDATE mvp_karigars SET phone=CASE WHEN trim(phone)='' THEN ? ELSE phone END, "
                        "address=CASE WHEN trim(address)='' THEN ? ELSE address END, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (phone, address, existing['id'])
                    )
                    legacy_karigar_names[name_key] = existing['id']
                else:
                    kid = _id('karigar')
                    c.execute(
                        "INSERT OR IGNORE INTO mvp_karigars(id,name,phone,address,notes) VALUES(?,?,?,?,?)",
                        (kid, karigar_name, phone, address, '')
                    )
                    created = c.execute(
                        "SELECT id FROM mvp_karigars WHERE lower(trim(name))=? ORDER BY is_active DESC LIMIT 1",
                        (name_key,)
                    ).fetchone()
                    if created:
                        legacy_karigar_names[name_key] = created['id']

        # Some older databases had a karigar name on each stock record but no
        # separate contact card. Recover those names too, then link the imported
        # inventory item to the matching karigar without changing any current link.
        old_items_row = c.execute("SELECT value FROM kv WHERE key='saree-items-v1'").fetchone()
        if old_items_row:
            try:
                old_items_for_karigars = json.loads(old_items_row[0])
            except Exception:
                old_items_for_karigars = []
            for old_item in old_items_for_karigars if isinstance(old_items_for_karigars, list) else []:
                if not isinstance(old_item, dict):
                    continue
                old_values = old_item.get('values') if isinstance(old_item.get('values'), dict) else {}
                old_name = ' '.join(str(old_values.get('karigar') or '').split())
                if not old_name:
                    continue
                name_key = _legacy_karigar_name_key(old_name)
                found = c.execute(
                    "SELECT id FROM mvp_karigars WHERE lower(trim(name))=? ORDER BY is_active DESC LIMIT 1",
                    (name_key,)
                ).fetchone()
                if not found:
                    kid = _id('karigar')
                    c.execute(
                        "INSERT OR IGNORE INTO mvp_karigars(id,name,phone,address,notes) VALUES(?,?,?,?,?)",
                        (kid, old_name, str(old_values.get('karigarPhone') or '').strip(),
                         str(old_values.get('karigarAddress') or '').strip(), '')
                    )
                    found = c.execute(
                        "SELECT id FROM mvp_karigars WHERE lower(trim(name))=? ORDER BY is_active DESC LIMIT 1",
                        (name_key,)
                    ).fetchone()
                if found:
                    legacy_karigar_names[name_key] = found['id']
                    legacy_id = str(old_item.get('id') or '').strip()
                    if legacy_id:
                        c.execute(
                            "UPDATE mvp_products SET karigar_id=? WHERE legacy_id=? AND trim(COALESCE(karigar_id,''))=''",
                            (found['id'], legacy_id)
                        )

        # Restore legacy product photos into the MVP photo store. The old saree
        # catalogue kept photos as data:image/... base64 values in kv, while the
        # newer editor expects /uploads/product-<uuid>.<ext> URLs. This migration
        # is safe to run on every startup: it only fills products whose image list
        # is still empty and never overwrites photos already managed in the MVP.
        legacy_row = c.execute("SELECT value FROM kv WHERE key='saree-items-v1'").fetchone()
        if legacy_row:
            try:
                legacy_items = json.loads(legacy_row[0])
            except Exception:
                legacy_items = []
            upload_dir = Path(db_path).parent / 'static' / 'uploads'
            signatures = (
                ('.jpg', lambda b: b.startswith(b'\xff\xd8\xff')),
                ('.png', lambda b: b.startswith(b'\x89PNG\r\n\x1a\n')),
                ('.gif', lambda b: b.startswith((b'GIF87a', b'GIF89a'))),
                ('.webp', lambda b: len(b) >= 12 and b.startswith(b'RIFF') and b[8:12] == b'WEBP'),
            )
            for item in legacy_items if isinstance(legacy_items, list) else []:
                if not isinstance(item, dict):
                    continue
                legacy_id = str(item.get('id') or '')
                if not legacy_id:
                    continue
                product = c.execute('SELECT id, images FROM mvp_products WHERE legacy_id=?', (legacy_id,)).fetchone()
                if not product:
                    continue
                try:
                    current_images = json.loads(product['images'] or '[]')
                except Exception:
                    current_images = []
                if current_images:
                    continue
                photo_values = item.get('photo') or item.get('photos')
                if photo_values is None and isinstance(item.get('values'), dict):
                    photo_values = item['values'].get('photo') or item['values'].get('photos')
                if isinstance(photo_values, str):
                    photo_values = [photo_values]
                if not isinstance(photo_values, list):
                    continue
                urls = []
                for photo_value in photo_values:
                    if not isinstance(photo_value, str):
                        continue
                    match = re.fullmatch(r'data:image/(?:jpeg|jpg|png|webp|gif);base64,([A-Za-z0-9+/=\r\n]+)', photo_value.strip(), re.IGNORECASE)
                    if not match:
                        continue
                    try:
                        image_bytes = base64.b64decode(re.sub(r'\s+', '', match.group(1)), validate=True)
                    except (ValueError, base64.binascii.Error):
                        continue
                    if not image_bytes or len(image_bytes) > 8_000_000:
                        continue
                    kind = next(((ext, check) for ext, check in signatures if check(image_bytes)), None)
                    if not kind:
                        continue
                    ext, _ = kind
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    filename = 'product-' + uuid.uuid4().hex + ext
                    target = upload_dir / filename
                    temp = upload_dir / (filename + '.tmp')
                    try:
                        temp.write_bytes(image_bytes)
                        temp.replace(target)
                    except OSError:
                        try:
                            temp.unlink(missing_ok=True)
                        except OSError:
                            pass
                        continue
                    urls.append('/uploads/' + filename)
                if urls:
                    c.execute('UPDATE mvp_products SET images=? WHERE id=? AND (images IS NULL OR images=? OR images=?)',
                              (json.dumps(urls, separators=(',', ':')), product['id'], '', '[]'))

        # Import any legacy/extracted upload files into the database. Once the bytes
        # are safely stored in mvp_media, remove the filesystem copy so backups only
        # need saree_store.sqlite3. Existing image URLs continue to work unchanged.
        upload_dir = Path(db_path).parent / 'static' / 'uploads'
        signatures = {
            '.jpg': ('image/jpeg', lambda b: b.startswith(b'\xff\xd8\xff')),
            '.png': ('image/png', lambda b: b.startswith(b'\x89PNG\r\n\x1a\n')),
            '.gif': ('image/gif', lambda b: b.startswith((b'GIF87a', b'GIF89a'))),
            '.webp': ('image/webp', lambda b: len(b) >= 12 and b.startswith(b'RIFF') and b[8:12] == b'WEBP'),
        }
        allowed_media_name = re.compile(r'(?:home|product|expense|business)-[0-9a-f]{32}\.(?:jpg|png|webp|gif)\Z')
        if upload_dir.is_dir():
            for media_path in upload_dir.iterdir():
                if not media_path.is_file() or not allowed_media_name.fullmatch(media_path.name):
                    continue
                try:
                    image_bytes = media_path.read_bytes()
                except OSError:
                    continue
                ext = media_path.suffix.lower()
                kind = signatures.get(ext)
                if not image_bytes or not kind or not kind[1](image_bytes):
                    continue
                mime_type = kind[0]
                c.execute('INSERT OR IGNORE INTO mvp_media(filename,mime_type,content,size) VALUES(?,?,?,?)',
                          (media_path.name, mime_type, sqlite3.Binary(image_bytes), len(image_bytes)))
                stored = c.execute('SELECT content FROM mvp_media WHERE filename=?', (media_path.name,)).fetchone()
                # Only remove the local copy after verifying this exact image is in SQLite.
                if stored and bytes(stored[0]) == image_bytes:
                    try:
                        media_path.unlink()
                    except OSError:
                        pass

        # Ensure subsequent API activity never allows a negative physical balance.
        c.commit()


def _json(status, obj):
    return status, json.dumps(obj, ensure_ascii=False, separators=(',', ':')), 'application/json; charset=utf-8'


def _parse_date_filters(query):
    start = (query.get('start') or [''])[0].strip()
    end = (query.get('end') or [''])[0].strip()
    if start:
        start = _date(start, 'start date')
    if end:
        end = _date(end, 'end date')
    if start and end and start > end:
        raise ValueError('Start date must be on or before end date')
    return start, end


def _product_dict(row):
    return {k: row[k] for k in row.keys()}


def _product_images(value, fallback=None):
    """Validate stored product-photo URLs; image bytes are stored in SQLite."""
    if value is None:
        value = fallback if fallback is not None else []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            raise ValueError('Product photos must be a list of uploaded images')
    if not isinstance(value, list):
        raise ValueError('Product photos must be a list of uploaded images')
    urls = []
    for item in value:
        url = str(item or '').strip()
        if not re.fullmatch(r'/uploads/product-[0-9a-f]{32}\.(?:jpg|png|webp|gif)', url):
            raise ValueError('One of the product photos is invalid. Please upload it again.')
        if url not in urls:
            urls.append(url)
    return json.dumps(urls, separators=(',', ':'))


def _fetch_report(c, report, start='', end=''):
    where, args = [], []
    if start:
        where.append("sale_date >= ?"); args.append(start)
    if end:
        where.append("sale_date <= ?"); args.append(end)
    sales_where = (' WHERE ' + ' AND '.join(where)) if where else ''
    sales = [dict(r) for r in c.execute("SELECT * FROM mvp_sales" + sales_where + " ORDER BY sale_date DESC, created_at DESC", args)]
    pwhere, pargs = [], []
    if start:
        pwhere.append("purchase_date >= ?"); pargs.append(start)
    if end:
        pwhere.append("purchase_date <= ?"); pargs.append(end)
    purchases = [dict(r) for r in c.execute("SELECT * FROM mvp_purchases" + ((' WHERE ' + ' AND '.join(pwhere)) if pwhere else '') + " ORDER BY purchase_date DESC, created_at DESC", pargs)]
    products = [dict(r) for r in c.execute("SELECT * FROM mvp_products WHERE is_active=1 ORDER BY name COLLATE NOCASE")]
    low = [p for p in products if float(p['quantity']) <= float(p['reorder_level'])]
    sales_total = round(sum(float(s['subtotal']) - float(s['discount']) for s in sales), 2)
    tax_total = round(sum(float(s['tax']) for s in sales), 2)
    cogs = round(sum(float(s['cogs']) for s in sales), 2)
    profit = round(sum(float(s['gross_profit']) for s in sales), 2)
    purchase_total = round(sum(float(p['total']) for p in purchases), 2)
    valuation = round(sum(float(p['quantity']) * float(p['cost_price']) for p in products), 2)
    ewhere, eargs = [], []
    if start:
        ewhere.append('expense_date >= ?'); eargs.append(start)
    if end:
        ewhere.append('expense_date <= ?'); eargs.append(end)
    expenses = [dict(r) for r in c.execute('SELECT * FROM mvp_expenses'+((' WHERE '+' AND '.join(ewhere)) if ewhere else '')+' ORDER BY expense_date DESC,created_at DESC', eargs)]
    expense_total = round(sum(float(e['amount']) for e in expenses), 2)
    paywhere, payargs = [], []
    if start: paywhere.append('payment_date >= ?'); payargs.append(start)
    if end: paywhere.append('payment_date <= ?'); payargs.append(end)
    payment_rows = [dict(r) for r in c.execute('SELECT payment_method, COALESCE(SUM(amount),0) AS total FROM mvp_payments'+((' WHERE '+' AND '.join(paywhere)) if paywhere else '')+' GROUP BY payment_method', payargs)]
    payment_totals = {str(x['payment_method']): round(float(x['total']),2) for x in payment_rows}
    cash_collected = payment_totals.get('Cash',0.0)
    online_collected = round(sum(v for k,v in payment_totals.items() if k in ('UPI','Card','Bank transfer')),2)
    credit_other_collected = round(sum(v for k,v in payment_totals.items() if k in ('Credit','Other')),2)
    return {
      'start': start, 'end': end, 'sales_count': len(sales), 'purchases_count': len(purchases),
      'sales': sales_total, 'sales_tax': tax_total, 'cogs': cogs, 'gross_profit': profit,
      'purchases': purchase_total, 'inventory_value': valuation, 'expense_total': expense_total,
      'operating_profit': round(profit-expense_total,2), 'expenses': expenses, 'product_count': len(products),
      'total_units': round(sum(float(p['quantity']) for p in products), 3),
      'low_stock_count': len(low), 'low_stock': low, 'sales_rows': sales, 'purchase_rows': purchases,
      'stock_rows': products, 'payment_method_totals': payment_totals, 'cash_collected': cash_collected,
      'online_collected': online_collected, 'credit_other_collected': credit_other_collected
    }


def _report_export_rows(c, report, start='', end=''):
    data = _fetch_report(c, report, start, end)
    if report == 'sales':
        return ['Invoice', 'Date', 'Customer', 'Payment status', 'Payment method', 'Subtotal', 'Discount', 'Tax', 'Total', 'COGS', 'Gross profit'], [[x['invoice_no'],x['sale_date'],x['customer_name'],x['payment_status'],x['payment_method'],x['subtotal'],x['discount'],x['tax'],x['total'],x['cogs'],x['gross_profit']] for x in data['sales_rows']]
    if report == 'purchases':
        return ['Invoice', 'Date', 'Supplier', 'Subtotal', 'Discount', 'Tax', 'Total'], [[x['invoice_no'],x['purchase_date'],x['supplier_name'],x['subtotal'],x['discount'],x['tax'],x['total']] for x in data['purchase_rows']]
    if report == 'profit':
        return ['Metric', 'Value'], [['Net sales (excl. tax)',data['sales']],['COGS',data['cogs']],['Gross profit',data['gross_profit']],['Operating expenses',data['expense_total']],['Operating profit estimate',data['operating_profit']],['Sales tax collected',data['sales_tax']],['Purchases (invoice total)',data['purchases']],['Current inventory valuation',data['inventory_value']]]
    if report == 'taxes':
        return ['Invoice','Date','Customer','Tax type','Tax rate %','Subtotal','Discount','Tax collected','Total'], [[x['invoice_no'],x['sale_date'],x['customer_name'],x.get('tax_type','None'),x.get('tax_rate',0),x['subtotal'],x['discount'],x['tax'],x['total']] for x in data['sales_rows']]
    if report == 'summary':
        rows=[['Summary','Sales count',data['sales_count']],['Summary','Net sales excluding tax',data['sales']],['Summary','Tax collected',data['sales_tax']],['Summary','Gross profit estimate',data['gross_profit']],['Summary','Expenses',data['expense_total']],['Summary','Operating profit estimate',data['operating_profit']],['Summary','Inventory valuation',data['inventory_value']],['Summary','Low-stock items',data['low_stock_count']]]
        payment_where, payment_args = [], []
        if start: payment_where.append('payment_date >= ?'); payment_args.append(start)
        if end: payment_where.append('payment_date <= ?'); payment_args.append(end)
        payment_sql = 'SELECT payment_method, COALESCE(SUM(amount),0) AS total FROM mvp_payments' + ((' WHERE ' + ' AND '.join(payment_where)) if payment_where else '') + ' GROUP BY payment_method ORDER BY payment_method'
        payment_totals = {str(r['payment_method']): round(float(r['total']), 2) for r in c.execute(payment_sql, payment_args)}
        cash_total = payment_totals.get('Cash', 0)
        online_total = round(sum(v for k,v in payment_totals.items() if k in ('UPI','Card','Bank transfer')), 2)
        credit_total = round(sum(v for k,v in payment_totals.items() if k in ('Credit','Other')), 2)
        rows += [['Collections','Cash received',cash_total],['Collections','Online / bank received (UPI, card, transfer)',online_total],['Collections','Credit / other received',credit_total]]
        rows += [['Collections by method',method,total] for method,total in sorted(payment_totals.items())]
        rows += [['Stock',x['name'],f"Qty {x['quantity']} {x['unit']} · value INR {round(float(x['quantity'])*float(x['cost_price']),2):.2f}"] for x in data['stock_rows']]
        rows += [['Sale',x['invoice_no'],f"{x['sale_date']} · {x['customer_name']} · total INR {x['total']:.2f}"] for x in data['sales_rows']]
        return ['Section','Record','Value / Details'],rows
    rows=data['stock_rows']
    if report == 'low-stock': rows=[x for x in rows if float(x['quantity'])<=float(x['reorder_level'])]
    return ['SKU', 'Product', 'Category', 'Quantity', 'Unit', 'Cost price', 'Selling price', 'Inventory value', 'Reorder level', 'Status'], [[x['sku'],x['name'],x['category'],x['quantity'],x['unit'],x['cost_price'],x['selling_price'],round(float(x['quantity'])*float(x['cost_price']),2),x['reorder_level'],'Low stock' if float(x['quantity'])<=float(x['reorder_level']) else 'OK'] for x in rows]


def _xlsx_bytes(headers, rows, sheet_name='Report'):
    def col(n):
        s=''
        while n:
            n,r=divmod(n-1,26); s=chr(65+r)+s
        return s
    all_rows=[headers]+rows
    xml_rows=[]
    for ri,row in enumerate(all_rows,1):
        cells=[]
        for ci,val in enumerate(row,1):
            ref=f'{col(ci)}{ri}'
            if val is None: cells.append(f'<c r="{ref}"/>'); continue
            if isinstance(val,(int,float)) and not isinstance(val,bool) and math.isfinite(float(val)):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                text=str(val)
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
        xml_rows.append('<row r="'+str(ri)+'">'+''.join(cells)+'</row>')
    sheet=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData>{''.join(xml_rows)}</sheetData></worksheet>'''
    workbook=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{escape(sheet_name[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    buff=io.BytesIO()
    with zipfile.ZipFile(buff,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        z.writestr('_rels/.rels','''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        z.writestr('xl/workbook.xml',workbook)
        z.writestr('xl/_rels/workbook.xml.rels','''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''')
        z.writestr('xl/worksheets/sheet1.xml',sheet)
    return buff.getvalue()


def _pdf_bytes(title, headers, rows, generated_date):
    # Small dependency-free, valid PDF with Helvetica; non-Latin text is replaced safely.
    lines=[title, f"Generated: {generated_date}", '']
    lines.append(' | '.join(str(x) for x in headers))
    lines.append('-'*105)
    for row in rows:
        lines.append(' | '.join(str(x) for x in row))
    if len(lines)==4: lines.append('No records found for this report.')
    pages=[lines[i:i+47] for i in range(0,len(lines),47)] or [['No records found']]
    objs=[]
    objs.append('<< /Type /Catalog /Pages 2 0 R >>')
    page_ids=[4+2*i for i in range(len(pages))]
    kids=' '.join(f'{pid} 0 R' for pid in page_ids)
    objs.append(f'<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>')
    objs.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    for page_index, lines_page in enumerate(pages):
        page_obj=4+2*page_index
        content_obj=page_obj+1
        stream=[]
        y=560
        for idx,line in enumerate(lines_page):
            raw=str(line).encode('cp1252','replace').decode('cp1252','replace')
            raw=raw.encode('latin-1','replace').decode('latin-1')
            safe=raw.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
            size=14 if idx==0 else 8
            stream.append(f'BT /F1 {size} Tf 24 {y} Td ({safe[:160]}) Tj ET')
            y-=16 if idx==0 else 11
        content='\n'.join(stream).encode('latin-1')
        objs.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_obj} 0 R >>')
        objs.append(f'<< /Length {len(content)} >>\nstream\n'+content.decode('latin-1')+'\nendstream')
    pdf=bytearray(b'%PDF-1.4\n% Stock Manager MVP\n')
    offsets=[0]
    for i,obj in enumerate(objs,1):
        offsets.append(len(pdf)); pdf.extend(f'{i} 0 obj\n'.encode()); pdf.extend(obj.encode('latin-1','replace')); pdf.extend(b'\nendobj\n')
    xref=len(pdf); pdf.extend(f'xref\n0 {len(objs)+1}\n'.encode()); pdf.extend(b'0000000000 65535 f \n')
    for off in offsets[1:]: pdf.extend(f'{off:010d} 00000 n \n'.encode())
    pdf.extend(f'trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    return bytes(pdf)


def _do_export(db_path, query):
    report=(query.get('report') or ['stock'])[0].lower()
    fmt=(query.get('format') or ['xlsx'])[0].lower()
    if report not in ('stock','sales','purchases','profit','taxes','low-stock','summary'):
        raise ValueError('Unknown report type')
    if fmt not in ('xlsx','pdf'):
        raise ValueError('Export format must be xlsx or pdf')
    start,end=_parse_date_filters(query)
    with _connect(db_path) as c:
        headers, rows = _report_export_rows(c, report, start, end)
    stamp=datetime.now().strftime('%Y%m%d-%H%M')
    if fmt=='xlsx':
        data=_xlsx_bytes(headers, rows, report.title())
        return 200, data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', f'attachment; filename="stock-manager-{report}-{stamp}.xlsx"'
    data=_pdf_bytes(f'Stock Manager - {report.title()} Report', headers, rows, date.today().isoformat())
    return 200, data, 'application/pdf', f'attachment; filename="stock-manager-{report}-{stamp}.pdf"'


def _do_invoice_export(db_path, sale_id):
    with _connect(db_path) as c:
        sale = c.execute('SELECT * FROM mvp_sales WHERE id=?', (sale_id,)).fetchone()
        if not sale:
            raise ValueError('Invoice not found')
        profile = c.execute('SELECT * FROM mvp_business_profile WHERE id=1').fetchone()
        items = [dict(r) for r in c.execute('SELECT product_name AS item_name,qty,unit_price,line_total FROM mvp_sale_items WHERE sale_id=?', (sale_id,))]
        items += [dict(r) for r in c.execute('SELECT item_name,qty,unit_price,line_total FROM mvp_sale_custom_items WHERE sale_id=?', (sale_id,))]
        payments = [dict(r) for r in c.execute('SELECT payment_date,amount,payment_method FROM mvp_payments WHERE sale_id=? ORDER BY payment_date', (sale_id,))]
    profile = dict(profile) if profile else {'shop_name':'Stock Manager'}
    headers = ['Item', 'Qty', 'Unit price (INR)', 'Amount (INR)']
    rows = [[x['item_name'], x['qty'], f"{float(x['unit_price']):.2f}", f"{float(x['line_total']):.2f}"] for x in items]
    rows += [['', '', 'Subtotal', f"{float(sale['subtotal']):.2f}"], ['', '', 'Discount', f"-{float(sale['discount']):.2f}"]]
    tax_type = sale['tax_type'] if 'tax_type' in sale.keys() else 'None'
    tax_rate = float(sale['tax_rate'] or 0) if 'tax_rate' in sale.keys() else 0
    if tax_type == 'CGST+SGST' and tax_rate:
        rows += [['', '', f'CGST ({tax_rate/2:g}%)', f"{float(sale['tax'])/2:.2f}"], ['', '', f'SGST ({tax_rate/2:g}%)', f"{float(sale['tax'])/2:.2f}"]]
    elif tax_type == 'IGST' and tax_rate:
        rows += [['', '', f'IGST ({tax_rate:g}%)', f"{float(sale['tax']):.2f}"]]
    else:
        rows += [['', '', 'Tax', f"{float(sale['tax']):.2f}"]]
    rows += [['', '', 'Grand total', f"{float(sale['total']):.2f}"], ['', '', 'Amount paid', f"{float(sale['amount_paid'] or 0):.2f}"], ['', '', 'Balance due', f"{max(0,float(sale['total'])-float(sale['amount_paid'] or 0)):.2f}"]]
    lines = [
        f"Shop: {profile.get('shop_name') or 'Stock Manager'}",
        f"Address: {profile.get('address') or '-'}",
        f"Phone: {profile.get('phone') or '-'} | Email: {profile.get('email') or '-'}",
        f"GSTIN: {profile.get('gst_number') or '-'}",
        f"Invoice: {sale['invoice_no']} | Date: {sale['sale_date']}",
        f"Customer: {sale['customer_name'] or 'Walk-in'} | Phone: {sale['customer_phone'] or '-'}",
        f"Payment: {sale['payment_method']} / {sale['payment_status']}",
    ]
    # Produce a business-specific invoice PDF using the portable built-in PDF writer.
    pdf = _pdf_bytes(f"Tax Invoice - {sale['invoice_no']} - {profile.get('shop_name') or 'Stock Manager'}", ['Details'], [[x] for x in lines] + [['']] + [[f"{' | '.join(map(str, row))}"] for row in [headers]+rows] + [[''], ['Payment history:']]+[[f"{x['payment_date']} · {x['payment_method']} · INR {float(x['amount']):.2f}"] for x in payments], date.today().isoformat())
    return 200, pdf, 'application/pdf', f'attachment; filename="invoice-{sale["invoice_no"].replace(chr(34), "")}.pdf"'

def get_route(db_path, path, query_string=''):
    query = parse_qs(query_string) if isinstance(query_string, str) else query_string
    try:
        if path == '/api/mvp/export':
            status, data, ctype, disposition = _do_export(db_path, query)
            return status, data, ctype, {'Content-Disposition': disposition}
        if path == '/api/mvp/invoice':
            status, data, ctype, disposition = _do_invoice_export(db_path, (query.get('id') or [''])[0])
            return status, data, ctype, {'Content-Disposition': disposition}
        with _connect(db_path) as c:
            if path == '/api/mvp/bootstrap':
                products=[_product_dict(r) for r in c.execute("SELECT p.*, k.name AS karigar_name FROM mvp_products p LEFT JOIN mvp_karigars k ON k.id=p.karigar_id WHERE p.is_active=1 ORDER BY p.name COLLATE NOCASE")]
                low=[p for p in products if float(p['quantity']) <= float(p['reorder_level'])]
                today=_today(); month=today[:7]+'-01'
                month_sales=c.execute("SELECT COUNT(*) AS bill_count, COALESCE(SUM(subtotal-discount),0) sales, COALESCE(SUM(cogs),0) cogs, COALESCE(SUM(gross_profit),0) profit FROM mvp_sales WHERE sale_date>=?",(month,)).fetchone()
                inv=round(sum(float(p['quantity'])*float(p['cost_price']) for p in products),2)
                total_units=round(sum(float(p['quantity']) for p in products),3)
                expected_profit=round(sum(float(p['quantity'])*(float(p['selling_price'])-float(p['cost_price'])) for p in products),2)
                realized_profit=sum(float(r['gross_profit'] or 0) for r in c.execute('SELECT gross_profit FROM mvp_sales'))
                work_cost=0.0
                for work_row in c.execute('SELECT production_work FROM mvp_sale_items'):
                    try:
                        work=json.loads(work_row['production_work'] or '{}')
                        if isinstance(work,dict): work_cost += sum(float(v.get('cost',0) or 0) for v in work.values() if isinstance(v,dict))
                    except Exception:
                        pass
                realized_profit=round(realized_profit-work_cost,2)
                stats={
                  'product_count':len(products),'total_records':len(products),'total_units':total_units,'total_quantity':total_units,
                  'low_stock_count':len(low),'inventory_value':inv,'total_investment':inv,
                  'realized_profit':realized_profit,'expected_profit':expected_profit,
                  'month_sales_count':int(month_sales['bill_count']),'month_sales':round(float(month_sales['sales']),2),
                  'month_cogs':round(float(month_sales['cogs']),2),'month_gross_profit':round(float(month_sales['profit']),2)
                }
                movements=[dict(r) for r in c.execute('SELECT * FROM mvp_stock_movements ORDER BY created_at DESC,rowid DESC LIMIT 8')]
                sales=[dict(r) for r in c.execute('SELECT * FROM mvp_sales ORDER BY sale_date DESC,created_at DESC LIMIT 5')]
                purchases=[dict(r) for r in c.execute('SELECT * FROM mvp_purchases ORDER BY purchase_date DESC,created_at DESC LIMIT 5')]
                due_row=c.execute('SELECT COALESCE(SUM(total-amount_paid),0) FROM mvp_sales WHERE total>amount_paid').fetchone()
                stats['credit_due']=round(float(due_row[0] or 0),2)
                profile=dict(c.execute('SELECT * FROM mvp_business_profile WHERE id=1').fetchone())
                app_settings=dict(c.execute('SELECT * FROM mvp_app_settings WHERE id=1').fetchone())
                return _json(200,{'ok':True,'stats':stats,'products':products,'low_stock':low,'recent_movements':movements,'recent_sales':sales,'recent_purchases':purchases,'profile':profile,'app_settings':app_settings})
            if path == '/api/mvp/settings':
                row=c.execute('SELECT * FROM mvp_app_settings WHERE id=1').fetchone()
                return _json(200,{'ok':True,'settings':dict(row) if row else {'app_name':'Stock Manager'}})
            if path == '/api/mvp/profile':
                row=c.execute('SELECT * FROM mvp_business_profile WHERE id=1').fetchone()
                return _json(200,{'ok':True,'profile':dict(row) if row else {}})
            if path == '/api/mvp/khata':
                customer=(query.get('customer') or [''])[0].strip()
                sql='SELECT *, ROUND(total-amount_paid,2) AS balance_due FROM mvp_sales WHERE total>amount_paid'
                args=[]
                if customer:
                    sql += ' AND (customer_name LIKE ? OR customer_phone LIKE ?)'; args.extend([f'%{customer}%',f'%{customer}%'])
                sql += ' ORDER BY sale_date ASC, created_at ASC'
                due=[dict(r) for r in c.execute(sql,args)]
                return _json(200,{'ok':True,'due_sales':due,'total_due':round(sum(float(x['balance_due']) for x in due),2)})
            if path == '/api/mvp/payments':
                sale_id=(query.get('sale_id') or [''])[0]
                if sale_id:
                    rows=[dict(r) for r in c.execute('SELECT * FROM mvp_payments WHERE sale_id=? ORDER BY payment_date DESC,created_at DESC',(sale_id,))]
                else:
                    rows=[dict(r) for r in c.execute('SELECT * FROM mvp_payments ORDER BY payment_date DESC,created_at DESC LIMIT 500')]
                return _json(200,{'ok':True,'payments':rows})
            if path == '/api/mvp/products':
                q=(query.get('q') or [''])[0].strip()
                show_all=(query.get('all') or ['0'])[0]=='1'
                sql='SELECT p.*, k.name AS karigar_name FROM mvp_products p LEFT JOIN mvp_karigars k ON k.id=p.karigar_id WHERE 1=1'; args=[]
                if not show_all: sql+=' AND p.is_active=1'
                if q:
                    sql+=' AND (p.name LIKE ? OR p.sku LIKE ? OR p.category LIKE ?)'; args.extend([f'%{q}%']*3)
                sql+=' ORDER BY p.name COLLATE NOCASE'
                return _json(200,{'ok':True,'products':[dict(r) for r in c.execute(sql,args)]})
            if path == '/api/mvp/parties':
                kind=(query.get('kind') or [''])[0]
                sql='SELECT * FROM mvp_parties'; args=[]
                if kind in ('customer','supplier'): sql+=' WHERE kind=?'; args.append(kind)
                sql+=' ORDER BY name COLLATE NOCASE'
                return _json(200,{'ok':True,'parties':[dict(r) for r in c.execute(sql,args)]})
            if path == '/api/mvp/karigars':
                show_all=(query.get('all') or ['0'])[0]=='1'
                sql='SELECT k.*, (SELECT COUNT(*) FROM mvp_products p WHERE p.karigar_id=k.id) AS linked_products, (SELECT COUNT(*) FROM mvp_sale_items si WHERE si.karigar_id=k.id) AS linked_sold_items FROM mvp_karigars k'
                if not show_all: sql+=' WHERE k.is_active=1'
                sql+=' ORDER BY k.name COLLATE NOCASE'
                return _json(200,{'ok':True,'karigars':[dict(r) for r in c.execute(sql)]})
            if path == '/api/mvp/customers':
                hidden={str(r['name_key']).strip().casefold() for r in c.execute('SELECT name_key FROM mvp_hidden_customers')}
                parties=[dict(r) for r in c.execute("SELECT * FROM mvp_parties WHERE kind='customer' ORDER BY name COLLATE NOCASE") if str(r['name']).strip().casefold() not in hidden]
                by_name={str(x['name']).strip().casefold():x for x in parties}
                sales_rows=c.execute("SELECT customer_name, MAX(customer_phone) AS customer_phone, COUNT(*) AS order_count, COALESCE(SUM(total),0) AS total_spent, COALESCE(SUM(total-amount_paid),0) AS balance_due, MAX(sale_date) AS last_purchase FROM mvp_sales WHERE TRIM(customer_name)<>'' GROUP BY LOWER(TRIM(customer_name)) ORDER BY customer_name COLLATE NOCASE").fetchall()
                for r in sales_rows:
                    key=str(r['customer_name']).strip().casefold()
                    if key in hidden: continue
                    target=by_name.get(key)
                    if target is None:
                        target={'id':'','kind':'customer','name':r['customer_name'],'phone':r['customer_phone'] or '', 'email':'','address':'','notes':'','created_at':'','is_legacy':True}
                        parties.append(target);by_name[key]=target
                    target['order_count']=int(r['order_count']);target['total_spent']=round(float(r['total_spent'] or 0),2);target['balance_due']=round(float(r['balance_due'] or 0),2);target['last_purchase']=r['last_purchase'] or ''
                    if not target.get('phone'): target['phone']=r['customer_phone'] or ''
                parties.sort(key=lambda x:str(x.get('name') or '').casefold())
                return _json(200,{'ok':True,'customers':parties})
            if path == '/api/mvp/sold-items':
                rows=[]
                for r in c.execute('''SELECT si.*, s.invoice_no, s.customer_name, s.customer_phone, s.sale_date, s.payment_status,
                    s.payment_method, s.total AS invoice_total, s.amount_paid, p.category, p.unit, p.images,
                    k.name AS karigar_name
                    FROM mvp_sale_items si JOIN mvp_sales s ON s.id=si.sale_id
                    LEFT JOIN mvp_products p ON p.id=si.product_id
                    LEFT JOIN mvp_karigars k ON k.id=si.karigar_id
                    ORDER BY s.sale_date DESC, s.created_at DESC, si.rowid DESC'''):
                    item=dict(r)
                    try:
                        work=json.loads(item.get('production_work') or '{}')
                        if not isinstance(work,dict): work={}
                    except Exception: work={}
                    item['production_work']=work
                    item['production_cost']=round(sum(float(v.get('cost',0) or 0) for v in work.values() if isinstance(v,dict)),2)
                    stages=[v for v in work.values() if isinstance(v,dict) and v.get('required',True)]
                    item['production_status']='Ready' if (not stages or all(v.get('status')=='Completed' for v in stages)) else ('In progress' if any(v.get('status')=='In Progress' for v in stages) else 'Pending')
                    item['net_profit']=round(float(item.get('gross_profit') or 0)-item['production_cost'],2)
                    rows.append(item)
                return _json(200,{'ok':True,'items':rows})
            if path == '/api/mvp/movements':
                limit=max(1,min(500,int((query.get('limit') or ['200'])[0])))
                return _json(200,{'ok':True,'movements':[dict(r) for r in c.execute('SELECT * FROM mvp_stock_movements ORDER BY created_at DESC,rowid DESC LIMIT ?', (limit,))]})
            if path == '/api/mvp/sales':
                start,end=_parse_date_filters(query); conditions=[]; args=[]
                if start: conditions.append('sale_date>=?'); args.append(start)
                if end: conditions.append('sale_date<=?'); args.append(end)
                sql='SELECT * FROM mvp_sales'+((' WHERE '+' AND '.join(conditions)) if conditions else '')+' ORDER BY sale_date DESC,created_at DESC'
                return _json(200,{'ok':True,'sales':[dict(r) for r in c.execute(sql,args)]})
            if path == '/api/mvp/purchases':
                start,end=_parse_date_filters(query); conditions=[]; args=[]
                if start: conditions.append('purchase_date>=?'); args.append(start)
                if end: conditions.append('purchase_date<=?'); args.append(end)
                sql='SELECT * FROM mvp_purchases'+((' WHERE '+' AND '.join(conditions)) if conditions else '')+' ORDER BY purchase_date DESC,created_at DESC'
                return _json(200,{'ok':True,'purchases':[dict(r) for r in c.execute(sql,args)]})
            if path == '/api/mvp/expenses':
                start,end=_parse_date_filters(query); conditions=[]; args=[]
                if start: conditions.append('expense_date>=?'); args.append(start)
                if end: conditions.append('expense_date<=?'); args.append(end)
                sql='SELECT * FROM mvp_expenses'+((' WHERE '+' AND '.join(conditions)) if conditions else '')+' ORDER BY expense_date DESC,created_at DESC'
                return _json(200,{'ok':True,'expenses':[dict(r) for r in c.execute(sql,args)]})
            if path == '/api/mvp/reports':
                start,end=_parse_date_filters(query)
                return _json(200,{'ok':True,'report':_fetch_report(c,'profit',start,end)})
        return _json(404,{'ok':False,'error':'Endpoint not found'})
    except ValueError as e:
        return _json(400,{'ok':False,'error':str(e)})
    except Exception:
        return _json(500,{'ok':False,'error':'Could not load requested inventory data'})


def post_route(db_path, path, body):
    body = body if isinstance(body, dict) else {}
    try:
        with _connect(db_path) as c:
            if path == '/api/mvp/settings':
                raw_name=str(body.get('app_name') or '')
                app_name=re.sub(r'\s+', ' ', raw_name).strip()
                if not app_name: raise ValueError('Application name is required')
                if len(app_name)>60: raise ValueError('Application name must be 60 characters or fewer')
                if any(ord(ch)<32 for ch in app_name): raise ValueError('Application name contains unsupported characters')
                c.execute("INSERT INTO mvp_app_settings(id,app_name,updated_at) VALUES(1,?,CURRENT_TIMESTAMP) ON CONFLICT(id) DO UPDATE SET app_name=excluded.app_name,updated_at=CURRENT_TIMESTAMP",(app_name,))
                return _json(200,{'ok':True,'settings':dict(c.execute('SELECT * FROM mvp_app_settings WHERE id=1').fetchone())})
            if path == '/api/mvp/profile':
                keys=('shop_name','address','phone','email','gst_number','logo_url','signature_url')
                values={key:str(body.get(key) or '').strip()[:500] for key in keys}
                if not values['shop_name']: raise ValueError('Business / shop name is required')
                for key in ('logo_url','signature_url'):
                    if values[key] and not re.fullmatch(r'/uploads/business-[0-9a-f]{32}\.(?:jpg|png|webp|gif)',values[key]): raise ValueError('Business logo/signature image is invalid')
                c.execute('UPDATE mvp_business_profile SET shop_name=?,address=?,phone=?,email=?,gst_number=?,logo_url=?,signature_url=?,updated_at=CURRENT_TIMESTAMP WHERE id=1',tuple(values[key] for key in keys))
                return _json(200,{'ok':True,'profile':dict(c.execute('SELECT * FROM mvp_business_profile WHERE id=1').fetchone())})
            if path == '/api/mvp/payments':
                sale_id=str(body.get('sale_id') or '').strip()
                payment_date=_date(body.get('payment_date') or _today(),'payment date')
                amount=_money(body.get('amount'),'Payment amount')
                if amount<=0: raise ValueError('Payment amount must be greater than zero')
                method=str(body.get('payment_method') or 'Cash')
                if method not in ('Cash','UPI','Card','Bank transfer','Credit','Other'): raise ValueError('Invalid payment method')
                sale=c.execute('SELECT * FROM mvp_sales WHERE id=?',(sale_id,)).fetchone()
                if not sale: return _json(404,{'ok':False,'error':'Invoice not found'})
                due=round(float(sale['total'])-float(sale['amount_paid'] or 0),2)
                if amount>due+0.000001: raise ValueError(f'Payment exceeds the outstanding balance of INR {due:.2f}')
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT INTO mvp_payments(id,sale_id,payment_date,amount,payment_method,notes) VALUES(?,?,?,?,?,?)',(_id('pay'),sale_id,payment_date,amount,method,str(body.get('notes') or '').strip()[:1000]))
                paid=round(float(sale['amount_paid'] or 0)+amount,2)
                status='Paid' if paid>=float(sale['total'])-0.005 else 'Partial'
                c.execute('UPDATE mvp_sales SET amount_paid=?,payment_status=? WHERE id=?',(paid,status,sale_id))
                return _json(200,{'ok':True,'amount_paid':paid,'balance_due':round(float(sale['total'])-paid,2),'payment_status':status})
            if path == '/api/mvp/audit':
                items=body.get('items')
                if not isinstance(items,list) or not items: raise ValueError('Enter physical counts for at least one product')
                c.execute('BEGIN IMMEDIATE')
                changed=[]
                for item in items:
                    pid=str(item.get('product_id') or '')
                    try: physical=float(Decimal(str(item.get('physical_qty'))))
                    except Exception: raise ValueError('Physical quantity must be a valid number')
                    if not math.isfinite(physical) or physical<0 or physical>1_000_000_000: raise ValueError('Physical quantity must be zero or greater')
                    product=c.execute('SELECT * FROM mvp_products WHERE id=? AND is_active=1',(pid,)).fetchone()
                    if not product: raise ValueError('An audited product is missing or inactive')
                    old=float(product['quantity']); delta=round(physical-old,3)
                    if abs(delta)>0.000001:
                        c.execute('UPDATE mvp_products SET quantity=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(round(physical,3),pid))
                        c.execute('INSERT INTO mvp_stock_movements(id,product_id,product_name,movement_type,qty_delta,quantity_after,unit_cost,reference_type,reference_id,reason) VALUES(?,?,?,?,?,?,?,?,?,?)',(_id('mov'),pid,product['name'],'AUDIT_IN' if delta>0 else 'AUDIT_OUT',delta,round(physical,3),float(product['cost_price']),'audit',_id('audit'),'Physical stock count adjustment'))
                    changed.append({'product_id':pid,'name':product['name'],'previous':old,'physical':round(physical,3),'difference':delta})
                return _json(200,{'ok':True,'changed':changed,'changed_count':sum(1 for x in changed if abs(x['difference'])>0.000001)})
            if path == '/api/mvp/expenses':
                when=_date(body.get('expense_date') or _today(),'expense date')
                category=str(body.get('category') or '').strip()[:120]
                if not category: raise ValueError('Expense category is required')
                description=str(body.get('description') or '').strip()[:200]
                amount=_money(body.get('amount'),'Expense amount')
                if amount <= 0: raise ValueError('Expense amount must be greater than zero')
                method=str(body.get('payment_method') or 'Cash')
                if method not in ('Cash','UPI','Card','Bank transfer','Other'): raise ValueError('Invalid payment method')
                notes=str(body.get('notes') or '').strip()[:1000]
                receipt_image=str(body.get('receipt_image') or '').strip()
                if receipt_image and not re.fullmatch(r'/uploads/expense-[0-9a-f]{32}\.(?:jpg|png|webp|gif)',receipt_image): raise ValueError('Expense receipt photo is invalid')
                eid=_id('exp')
                c.execute('INSERT INTO mvp_expenses(id,expense_date,category,description,amount,payment_method,notes,receipt_image) VALUES(?,?,?,?,?,?,?,?)',(eid,when,category,description,amount,method,notes,receipt_image))
                return _json(201,{'ok':True,'expense_id':eid})
            if path == '/api/mvp/products':
                name=str(body.get('name') or '').strip()[:200]
                sku=str(body.get('sku') or '').strip()[:50]
                if not name: raise ValueError('Please enter a product name')
                # SKU is an internal code; create one automatically when the user skips it.
                if not sku:
                    for _ in range(8):
                        candidate='SKU-'+uuid.uuid4().hex[:8].upper()
                        if not c.execute('SELECT 1 FROM mvp_products WHERE sku=?',(candidate,)).fetchone():
                            sku=candidate
                            break
                    if not sku: raise ValueError('Could not generate a product code. Please try again.')
                category=str(body.get('category') or '').strip()[:120]
                description=str(body.get('description') or '').strip()[:2000]
                # Keep unit setup optional in the UI, but support stock units required by retail.
                unit=str(body.get('unit') or 'pcs').strip().lower()
                valid_units={'pcs','kg','g','l','box','packet','m'}
                if unit not in valid_units: raise ValueError('Choose a supported stock unit')
                cost=_money(body.get('cost_price',0),'Cost price'); price=_money(body.get('selling_price',0),'Selling price')
                reorder=_money(body.get('reorder_level',1),'Reorder level')
                images=_product_images(body.get('images', []))
                karigar_id=str(body.get('karigar_id') or '').strip()
                if karigar_id and not c.execute('SELECT 1 FROM mvp_karigars WHERE id=? AND is_active=1',(karigar_id,)).fetchone(): raise ValueError('Choose an active karigar or leave the field blank')
                # Allow a physical current count at setup; record it as an opening movement for auditability.
                try: initial=Decimal(str(body.get('initial_quantity',0) or 0))
                except Exception: raise ValueError('Current stock count must be a valid number')
                if not initial.is_finite() or initial < 0 or initial > 1_000_000_000: raise ValueError('Current stock count must be zero or greater')
                initial=float(initial.quantize(Decimal('0.001'),rounding=ROUND_HALF_UP))
                pid=_id('prod')
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT INTO mvp_products(id,sku,name,category,description,unit,images,quantity,cost_price,selling_price,reorder_level,karigar_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                          (pid,sku,name,category,description,unit,images,initial,cost,price,reorder,karigar_id))
                if initial > 0:
                    c.execute('INSERT INTO mvp_stock_movements(id,product_id,product_name,movement_type,qty_delta,quantity_after,unit_cost,reference_type,reference_id,reason) VALUES(?,?,?,?,?,?,?,?,?,?)',
                              (_id('mov'),pid,name,'OPENING',initial,initial,cost,'opening',pid,'Initial stock count at product setup'))
                return _json(201,{'ok':True,'product':dict(c.execute('SELECT * FROM mvp_products WHERE id=?',(pid,)).fetchone())})
            if path == '/api/mvp/products/update':
                pid=str(body.get('id') or '')
                old=c.execute('SELECT * FROM mvp_products WHERE id=?',(pid,)).fetchone()
                if not old: return _json(404,{'ok':False,'error':'Product not found'})
                name=str(body.get('name') or '').strip()[:200]; sku=str(body.get('sku') or '').strip()[:50]
                if not name: raise ValueError('Please enter a product name')
                if not sku:
                    for _ in range(8):
                        candidate='SKU-'+uuid.uuid4().hex[:8].upper()
                        if candidate == str(old['sku']) or not c.execute('SELECT 1 FROM mvp_products WHERE sku=? AND id<>?',(candidate,pid)).fetchone():
                            sku=candidate
                            break
                    if not sku: raise ValueError('Could not generate a product code. Please try again.')
                category=str(body.get('category') or '').strip()[:120]; description=str(body.get('description') or '').strip()[:2000]
                unit=str(body.get('unit',old['unit']) or 'pcs').strip().lower()
                if unit not in {'pcs','kg','g','l','box','packet','m'}: raise ValueError('Choose a supported stock unit')
                cost=_money(body.get('cost_price',old['cost_price']),'Cost price'); price=_money(body.get('selling_price',old['selling_price']),'Selling price')
                reorder=_money(body.get('reorder_level',old['reorder_level']),'Reorder level')
                images=_product_images(body.get('images'), fallback=old['images'] if 'images' in old.keys() else '[]')
                karigar_id=str(body.get('karigar_id',old['karigar_id'] if 'karigar_id' in old.keys() else '') or '').strip()
                if karigar_id and not c.execute('SELECT 1 FROM mvp_karigars WHERE id=? AND is_active=1',(karigar_id,)).fetchone(): raise ValueError('Choose an active karigar or leave the field blank')
                c.execute('BEGIN IMMEDIATE')
                c.execute('UPDATE mvp_products SET sku=?,name=?,category=?,description=?,unit=?,images=?,cost_price=?,selling_price=?,reorder_level=?,karigar_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                          (sku,name,category,description,unit,images,cost,price,reorder,karigar_id,pid))
                return _json(200,{'ok':True,'product':dict(c.execute('SELECT * FROM mvp_products WHERE id=?',(pid,)).fetchone())})
            if path == '/api/mvp/products/delete':
                pid=str(body.get('id') or '').strip()
                if not pid: raise ValueError('Product ID is required')
                product=c.execute('SELECT * FROM mvp_products WHERE id=?',(pid,)).fetchone()
                if not product: return _json(404,{'ok':False,'error':'Product not found'})
                # Preserve accounting and audit history: products referenced by sales,
                # purchases or stock movements can be archived, but not hard-deleted.
                refs={
                    'sales': c.execute('SELECT COUNT(*) FROM mvp_sale_items WHERE product_id=?',(pid,)).fetchone()[0],
                    'purchases': c.execute('SELECT COUNT(*) FROM mvp_purchase_items WHERE product_id=?',(pid,)).fetchone()[0],
                    'stock movements': c.execute('SELECT COUNT(*) FROM mvp_stock_movements WHERE product_id=?',(pid,)).fetchone()[0],
                }
                used=[label for label,count in refs.items() if count]
                if used:
                    return _json(409,{'ok':False,'error':'This product has '+', '.join(used)+' history. Archive it instead so reports and records stay correct.'})
                try:
                    images=json.loads(product['images'] or '[]')
                except Exception:
                    images=[]
                if not isinstance(images,list): images=[]
                images=[url for url in images if isinstance(url,str) and re.fullmatch(r'/uploads/product-[0-9a-f]{32}\.(?:jpg|png|webp|gif)',url)]
                c.execute('DELETE FROM mvp_products WHERE id=?',(pid,))
                return _json(200,{'ok':True,'deleted':True,'images':images,'name':product['name'],'quantity':product['quantity']})
            if path in ('/api/mvp/products/deactivate','/api/mvp/products/activate'):
                pid=str(body.get('id') or '')
                active=1 if path == '/api/mvp/products/activate' else 0
                res=c.execute('UPDATE mvp_products SET is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(active,pid))
                if not res.rowcount: return _json(404,{'ok':False,'error':'Product not found'})
                return _json(200,{'ok':True})
            if path == '/api/mvp/karigars':
                name=str(body.get('name') or '').strip()[:200]
                if not name: raise ValueError('Karigar name is required')
                phone=str(body.get('phone') or '').strip()[:50]
                address=str(body.get('address') or '').strip()[:500]
                notes=str(body.get('notes') or '').strip()[:1000]
                kid=_id('karigar')
                c.execute('INSERT INTO mvp_karigars(id,name,phone,address,notes) VALUES(?,?,?,?,?)',(kid,name,phone,address,notes))
                return _json(201,{'ok':True,'karigar':dict(c.execute('SELECT * FROM mvp_karigars WHERE id=?',(kid,)).fetchone())})
            if path == '/api/mvp/karigars/update':
                kid=str(body.get('id') or '').strip()
                name=str(body.get('name') or '').strip()[:200]
                if not kid or not name: raise ValueError('Karigar ID and name are required')
                phone=str(body.get('phone') or '').strip()[:50]; address=str(body.get('address') or '').strip()[:500]; notes=str(body.get('notes') or '').strip()[:1000]
                res=c.execute('UPDATE mvp_karigars SET name=?,phone=?,address=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND is_active=1',(name,phone,address,notes,kid))
                if not res.rowcount: return _json(404,{'ok':False,'error':'Karigar not found or archived'})
                return _json(200,{'ok':True,'karigar':dict(c.execute('SELECT * FROM mvp_karigars WHERE id=?',(kid,)).fetchone())})
            if path == '/api/mvp/karigars/delete':
                kid=str(body.get('id') or '').strip()
                if not kid: raise ValueError('Karigar ID is required')
                karigar=c.execute('SELECT * FROM mvp_karigars WHERE id=?',(kid,)).fetchone()
                if not karigar: return _json(404,{'ok':False,'error':'Karigar not found'})
                linked_products=int(c.execute('SELECT COUNT(*) FROM mvp_products WHERE karigar_id=?',(kid,)).fetchone()[0])
                linked_sold=int(c.execute('SELECT COUNT(*) FROM mvp_sale_items WHERE karigar_id=?',(kid,)).fetchone()[0])
                if linked_products or linked_sold:
                    c.execute('UPDATE mvp_karigars SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?',(kid,))
                    return _json(200,{'ok':True,'archived':True,'name':karigar['name'],'linked_products':linked_products,'linked_sold_items':linked_sold})
                c.execute('DELETE FROM mvp_karigars WHERE id=?',(kid,))
                return _json(200,{'ok':True,'deleted':True,'name':karigar['name']})
            if path == '/api/mvp/karigars/activate':
                kid=str(body.get('id') or '').strip()
                if not kid: raise ValueError('Karigar ID is required')
                res=c.execute('UPDATE mvp_karigars SET is_active=1,updated_at=CURRENT_TIMESTAMP WHERE id=?',(kid,))
                if not res.rowcount: return _json(404,{'ok':False,'error':'Karigar not found'})
                return _json(200,{'ok':True,'restored':True})
            if path == '/api/mvp/customers/delete':
                cid=str(body.get('id') or '').strip()
                name=str(body.get('name') or '').strip()[:200]
                phone=str(body.get('phone') or '').strip()[:50]
                party=None
                if cid:
                    party=c.execute("SELECT * FROM mvp_parties WHERE id=? AND kind='customer'",(cid,)).fetchone()
                if party:
                    name=str(party['name']).strip(); phone=str(party['phone'] or phone)
                if not name: raise ValueError('Customer name is required')
                history_count=int(c.execute("SELECT COUNT(*) FROM mvp_sales WHERE LOWER(TRIM(customer_name))=LOWER(TRIM(?))",(name,)).fetchone()[0])
                if history_count:
                    key=name.casefold()
                    c.execute('INSERT INTO mvp_hidden_customers(name_key,display_name,phone) VALUES(?,?,?) ON CONFLICT(name_key) DO UPDATE SET display_name=excluded.display_name,phone=excluded.phone,hidden_at=CURRENT_TIMESTAMP',(key,name,phone))
                if party:
                    c.execute("DELETE FROM mvp_parties WHERE id=? AND kind='customer'",(party['id'],))
                else:
                    c.execute("DELETE FROM mvp_parties WHERE kind='customer' AND name=? COLLATE NOCASE",(name,))
                return _json(200,{'ok':True,'deleted':True,'history_preserved':bool(history_count),'name':name})
            if path == '/api/mvp/sales/delete':
                sale_id=str(body.get('sale_id') or '').strip()
                if not sale_id: raise ValueError('Sale ID is required')
                c.execute('BEGIN IMMEDIATE')
                sale=c.execute('SELECT * FROM mvp_sales WHERE id=?',(sale_id,)).fetchone()
                if not sale: return _json(404,{'ok':False,'error':'Sale/invoice not found'})
                items=[dict(r) for r in c.execute('SELECT * FROM mvp_sale_items WHERE sale_id=?',(sale_id,))]
                restored=0.0; affected_products=set()
                # Reverse only quantities that this sale actually deducted.
                for item in items:
                    if int(item.get('stock_deducted') or 0):
                        c.execute('UPDATE mvp_products SET quantity=ROUND(quantity+?,3),updated_at=CURRENT_TIMESTAMP WHERE id=?',(float(item['qty']),item['product_id']))
                        restored+=float(item['qty']); affected_products.add(item['product_id'])
                # The user explicitly requested deletion: remove stock ledger rows tied
                # to this invoice along with the invoice and its production/payments.
                c.execute("DELETE FROM mvp_stock_movements WHERE reference_type='sale' AND reference_id=?",(sale_id,))
                c.execute('DELETE FROM mvp_payments WHERE sale_id=?',(sale_id,))
                c.execute('DELETE FROM mvp_sale_custom_items WHERE sale_id=?',(sale_id,))
                c.execute('DELETE FROM mvp_sale_items WHERE sale_id=?',(sale_id,))
                c.execute('DELETE FROM mvp_sales WHERE id=?',(sale_id,))
                # Keep the remaining movement snapshots consistent after removing the
                # deleted invoice's ledger rows. Current inventory quantity is authoritative.
                for product_id in affected_products:
                    movements=c.execute('SELECT id,qty_delta,quantity_after FROM mvp_stock_movements WHERE product_id=? ORDER BY created_at,rowid',(product_id,)).fetchall()
                    if movements:
                        running=float(movements[0]['quantity_after'])-float(movements[0]['qty_delta'])
                        for movement in movements:
                            running=round(running+float(movement['qty_delta']),3)
                            c.execute('UPDATE mvp_stock_movements SET quantity_after=? WHERE id=?',(running,movement['id']))
                return _json(200,{'ok':True,'deleted':True,'invoice_no':sale['invoice_no'],'item_count':len(items),'restored_quantity':round(restored,3)})
            if path == '/api/mvp/customers':
                name=str(body.get('name') or '').strip()[:200]
                if not name: raise ValueError('Customer name is required')
                phone=str(body.get('phone') or '').strip()[:50]; address=str(body.get('address') or '').strip()[:500]
                email=str(body.get('email') or '').strip()[:200]; notes=str(body.get('notes') or '').strip()[:1000]
                cid=str(body.get('id') or '').strip()
                if cid:
                    existing=c.execute("SELECT id FROM mvp_parties WHERE id=? AND kind='customer'",(cid,)).fetchone()
                    if existing:
                        if c.execute("SELECT 1 FROM mvp_parties WHERE kind='customer' AND name=? COLLATE NOCASE AND id<>?",(name,cid)).fetchone(): raise ValueError('A customer with this name already exists')
                        c.execute("UPDATE mvp_parties SET name=?,phone=?,address=?,email=?,notes=? WHERE id=? AND kind='customer'",(name,phone,address,email,notes,cid))
                        c.execute('DELETE FROM mvp_hidden_customers WHERE name_key=?',(name.casefold(),))
                        row=c.execute("SELECT * FROM mvp_parties WHERE id=?",(cid,)).fetchone()
                        return _json(200,{'ok':True,'customer':dict(row)})
                c.execute('''INSERT INTO mvp_parties(id,kind,name,phone,email,address,notes) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(kind,name) DO UPDATE SET phone=excluded.phone,email=excluded.email,address=excluded.address,notes=excluded.notes''',(_id('party'),'customer',name,phone,email,address,notes))
                c.execute('DELETE FROM mvp_hidden_customers WHERE name_key=?',(name.casefold(),))
                row=c.execute("SELECT * FROM mvp_parties WHERE kind='customer' AND name=?",(name,)).fetchone()
                return _json(200,{'ok':True,'customer':dict(row)})
            if path == '/api/mvp/production/update':
                item_id=str(body.get('sale_item_id') or '').strip()
                if not item_id: raise ValueError('Sold item is required')
                old=c.execute('SELECT * FROM mvp_sale_items WHERE id=?',(item_id,)).fetchone()
                if not old: return _json(404,{'ok':False,'error':'Sold item not found'})
                stages=('Calendar','Fal','Piko','Cutting','Jalar','Dag','Finishing','Die','Raffu','Polish')
                raw=body.get('production_work')
                if not isinstance(raw,dict): raise ValueError('Production work details are required')
                clean={}
                for stage in stages:
                    v=raw.get(stage,{})
                    if not isinstance(v,dict): v={}
                    required=bool(v.get('required',True))
                    status=str(v.get('status') or ('Pending' if required else 'Not Required'))
                    if status not in ('Pending','In Progress','Completed','Not Required'): raise ValueError('Invalid production status for '+stage)
                    cost=_money(v.get('cost',0),stage+' cost')
                    if not required:
                        status='Not Required';cost=0.0
                    elif status=='Not Required':
                        required=False;cost=0.0
                    clean[stage]={'required':required,'status':status,'cost':cost}
                karigar_id=str(body.get('karigar_id') or '').strip()
                if karigar_id and not c.execute('SELECT 1 FROM mvp_karigars WHERE id=?',(karigar_id,)).fetchone(): raise ValueError('Selected karigar was not found')
                c.execute('UPDATE mvp_sale_items SET karigar_id=?,production_work=? WHERE id=?',(karigar_id,json.dumps(clean,separators=(',',':')),item_id))
                return _json(200,{'ok':True,'sale_item_id':item_id,'production_cost':round(sum(x['cost'] for x in clean.values()),2),'production_work':clean})
            if path == '/api/mvp/parties':
                kind=str(body.get('kind') or '')
                name=str(body.get('name') or '').strip()[:200]
                if kind not in ('customer','supplier') or not name: raise ValueError('Party type and name are required')
                phone=str(body.get('phone') or '').strip()[:50]; email=str(body.get('email') or '').strip()[:200]; address=str(body.get('address') or '').strip()[:500]; notes=str(body.get('notes') or '').strip()[:1000]
                c.execute('''INSERT INTO mvp_parties(id,kind,name,phone,email,address,notes) VALUES(?,?,?,?,?,?,?)
                  ON CONFLICT(kind,name) DO UPDATE SET phone=excluded.phone,email=excluded.email,address=excluded.address,notes=excluded.notes''',
                  (_id('party'),kind,name,phone,email,address,notes))
                row=c.execute('SELECT * FROM mvp_parties WHERE kind=? AND name=?',(kind,name)).fetchone()
                return _json(200,{'ok':True,'party':dict(row)})
            if path == '/api/mvp/adjustments':
                pid=str(body.get('product_id') or '')
                direction=str(body.get('direction') or '').lower()
                qty=_qty(body.get('qty'),'Quantity')
                reason=str(body.get('reason') or '').strip()[:500]
                if direction not in ('in','out'): raise ValueError('Direction must be in or out')
                c.execute('BEGIN IMMEDIATE')
                p=c.execute('SELECT * FROM mvp_products WHERE id=? AND is_active=1',(pid,)).fetchone()
                if not p: return _json(404,{'ok':False,'error':'Active product not found'})
                oldq=float(p['quantity']); delta=qty if direction=='in' else -qty; newq=round(oldq+delta,3)
                if newq < -0.000001: raise ValueError(f"Not enough stock. Available: {oldq:g} {p['unit']}")
                cost=_money(body.get('unit_cost',p['cost_price']),'Unit cost') if direction=='in' else float(p['cost_price'])
                if direction=='in' and newq>0:
                    cost=round(((oldq*float(p['cost_price']))+(qty*cost))/newq,2)
                    c.execute('UPDATE mvp_products SET quantity=?,cost_price=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(newq,cost,pid))
                else:
                    c.execute('UPDATE mvp_products SET quantity=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(newq,pid))
                c.execute('INSERT INTO mvp_stock_movements(id,product_id,product_name,movement_type,qty_delta,quantity_after,unit_cost,reference_type,reference_id,reason) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (_id('mov'),pid,p['name'],'ADJUSTMENT_IN' if direction=='in' else 'ADJUSTMENT_OUT',delta,newq,cost,'adjustment','',reason or 'Manual stock adjustment'))
                return _json(200,{'ok':True,'quantity':newq,'product_id':pid})
            if path == '/api/mvp/purchases':
                supplier=str(body.get('supplier_name') or '').strip()[:200]
                purdate=_date(body.get('purchase_date') or _today(),'purchase date')
                invoice=str(body.get('invoice_no') or ('PUR-'+datetime.now().strftime('%Y%m%d-')+uuid.uuid4().hex[:6].upper())).strip()[:80]
                discount=_money(body.get('discount',0),'Discount'); tax=_money(body.get('tax',0),'Tax')
                notes=str(body.get('notes') or '').strip()[:2000]
                items=body.get('items')
                if not isinstance(items,list) or not items: raise ValueError('Add at least one purchase item')
                clean=[]; subtotal=0
                for line in items:
                    pid=str(line.get('product_id') or '')
                    qty=_qty(line.get('qty'),'Purchase quantity')
                    cost=_money(line.get('unit_cost',0),'Unit cost')
                    p=c.execute('SELECT * FROM mvp_products WHERE id=? AND is_active=1',(pid,)).fetchone()
                    if not p: raise ValueError('A selected product is missing or inactive')
                    line_total=round(qty*cost,2); subtotal=round(subtotal+line_total,2)
                    clean.append({'product':p,'qty':qty,'cost':cost,'line_total':line_total})
                if discount>subtotal: raise ValueError('Discount cannot be greater than subtotal')
                total=round(subtotal-discount+tax,2); pid=_id('pur')
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT INTO mvp_purchases(id,invoice_no,supplier_name,purchase_date,subtotal,discount,tax,total,notes) VALUES(?,?,?,?,?,?,?,?,?)',
                          (pid,invoice,supplier,purdate,subtotal,discount,tax,total,notes))
                if supplier:
                    c.execute("INSERT INTO mvp_parties(id,kind,name) VALUES(?,?,?) ON CONFLICT(kind,name) DO NOTHING",(_id('party'),'supplier',supplier))
                for line in clean:
                    p=c.execute('SELECT * FROM mvp_products WHERE id=?',(line['product']['id'],)).fetchone()
                    oldq=float(p['quantity']); qty=line['qty']; newq=round(oldq+qty,3)
                    # Apply document discount proportionally to cost; tax is kept separately for MVP reporting.
                    landed_cost=round(line['cost']*(1-(discount/subtotal if subtotal else 0)),2)
                    avg=round(((oldq*float(p['cost_price']))+(qty*landed_cost))/newq,2) if newq else landed_cost
                    c.execute('UPDATE mvp_products SET quantity=?,cost_price=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(newq,avg,p['id']))
                    c.execute('INSERT INTO mvp_purchase_items(id,purchase_id,product_id,product_name,qty,unit_cost,line_total) VALUES(?,?,?,?,?,?,?)',
                              (_id('pli'),pid,p['id'],p['name'],qty,line['cost'],line['line_total']))
                    c.execute('INSERT INTO mvp_stock_movements(id,product_id,product_name,movement_type,qty_delta,quantity_after,unit_cost,reference_type,reference_id,reason) VALUES(?,?,?,?,?,?,?,?,?,?)',
                              (_id('mov'),p['id'],p['name'],'PURCHASE_IN',qty,newq,landed_cost,'purchase',pid,invoice))
                return _json(201,{'ok':True,'purchase_id':pid,'invoice_no':invoice,'subtotal':subtotal,'total':total})
            if path == '/api/mvp/sales':
                customer=str(body.get('customer_name') or '').strip()[:200]
                customer_phone=str(body.get('customer_phone') or '').strip()[:50]
                saledate=_date(body.get('sale_date') or _today(),'sale date')
                invoice=str(body.get('invoice_no') or ('BILL-'+datetime.now().strftime('%Y%m%d-')+uuid.uuid4().hex[:6].upper())).strip()[:80]
                method=str(body.get('payment_method') or 'Cash')
                if method not in ('Cash','UPI','Card','Bank transfer','Credit','Other'): raise ValueError('Invalid payment method')
                notes=str(body.get('notes') or '').strip()[:2000]
                tax_type=str(body.get('tax_type') or 'None')
                if tax_type not in ('None','CGST+SGST','IGST'): raise ValueError('Choose a valid GST treatment')
                tax_rate=_money(body.get('tax_rate',0),'Tax rate')
                if tax_rate>100: raise ValueError('Tax rate cannot exceed 100%')
                discount_type=str(body.get('discount_type') or 'flat')
                if discount_type not in ('flat','percentage'): raise ValueError('Discount must be flat or percentage')
                discount_value=_money(body.get('discount_value',body.get('discount',0)),'Discount')
                if discount_type=='percentage' and discount_value>100: raise ValueError('Discount percentage cannot exceed 100%')
                deduct_stock = body.get('deduct_stock', True) not in (False, 0, '0', 'false', 'False', 'no', 'No')
                lines=body.get('items')
                if not isinstance(lines,list) or not lines: raise ValueError('Add at least one sale item')
                clean=[]; subtotal=0.0; aggregate={}
                for line in lines:
                    qty_value=_qty(line.get('qty'),'Sale quantity')
                    price=_money(line.get('unit_price',0),'Selling price')
                    if price<=0: raise ValueError('Item price must be greater than zero')
                    custom_name=str(line.get('custom_name') or '').strip()[:200]
                    pid=str(line.get('product_id') or '').strip()
                    if custom_name and not pid:
                        line_total=round(qty_value*price,2)
                        clean.append({'kind':'custom','name':custom_name,'qty':qty_value,'price':price,'line_total':line_total,'cogs':0.0,'profit':line_total})
                    else:
                        p=c.execute('SELECT * FROM mvp_products WHERE id=? AND is_active=1',(pid,)).fetchone()
                        if not p: raise ValueError('A selected product is missing or inactive')
                        clean.append({'kind':'product','pid':pid,'product':p,'name':p['name'],'qty':qty_value,'price':price,'line_total':round(qty_value*price,2)})
                        aggregate[pid]=aggregate.get(pid,0)+qty_value
                    subtotal=round(subtotal+qty_value*price,2)
                discount=round(subtotal*discount_value/100,2) if discount_type=='percentage' else discount_value
                if 'discount_value' not in body and 'discount' in body: discount=_money(body.get('discount',0),'Discount')
                if discount>subtotal: raise ValueError('Discount cannot be greater than subtotal')
                taxable=round(subtotal-discount,2)
                tax=round(taxable*tax_rate/100,2) if tax_type!='None' else _money(body.get('tax',0),'Tax')
                if tax_type=='None': tax_rate=0
                total=round(taxable+tax,2)
                if deduct_stock:
                    for pid,required in aggregate.items():
                        p=c.execute('SELECT name,quantity,unit FROM mvp_products WHERE id=?',(pid,)).fetchone()
                        if required>float(p['quantity'])+0.000001: raise ValueError(f"Not enough stock for {p['name']}. Available: {float(p['quantity']):g} {p['unit']}; requested: {required:g}")
                # Cash received now drives Khata automatically. For older API clients, Paid defaults to full payment.
                if 'amount_paid' in body and body.get('amount_paid') not in ('',None):
                    amount_paid=_money(body.get('amount_paid'),'Amount paid')
                else:
                    legacy_status=str(body.get('payment_status') or 'Pending')
                    amount_paid=total if legacy_status=='Paid' else 0.0
                if amount_paid>total+0.000001: raise ValueError('Amount paid cannot exceed the invoice total')
                amount_paid=round(min(amount_paid,total),2)
                status='Paid' if amount_paid>=total-0.005 else ('Pending' if amount_paid<=0.005 else 'Partial')
                saleid=_id('sale')
                c.execute('BEGIN IMMEDIATE')
                if deduct_stock:
                    for pid,required in aggregate.items():
                        p=c.execute('SELECT name,quantity FROM mvp_products WHERE id=? AND is_active=1',(pid,)).fetchone()
                        if not p or required>float(p['quantity'])+0.000001: raise ValueError(f"Stock changed before the bill could be saved for {p['name'] if p else 'a product'}")
                cogs=0.0
                normalized=[]
                for line in clean:
                    line_discount=round(discount*(line['line_total']/subtotal),2) if subtotal else 0
                    if line['kind']=='custom':
                        line_cogs=0.0; line_profit=round(line['line_total']-line_discount,2); unit_cost=0.0
                    else:
                        p=c.execute('SELECT * FROM mvp_products WHERE id=?',(line['pid'],)).fetchone()
                        unit_cost=round(float(p['cost_price']),2); line_cogs=round(line['qty']*unit_cost,2)
                        line_profit=round(line['line_total']-line_discount-line_cogs,2); line['product']=p
                    cogs=round(cogs+line_cogs,2)
                    line['unit_cost']=unit_cost; line['cogs']=line_cogs; line['profit']=line_profit
                    normalized.append(line)
                gross=round(subtotal-discount-cogs,2)
                c.execute('INSERT INTO mvp_sales(id,invoice_no,customer_name,sale_date,payment_status,payment_method,subtotal,discount,tax,total,cogs,gross_profit,notes,customer_phone,amount_paid,tax_type,tax_rate,discount_type,discount_value) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (saleid,invoice,customer,saledate,status,method,subtotal,discount,tax,total,cogs,gross,notes,customer_phone,amount_paid,tax_type,tax_rate,discount_type,discount_value))
                for line in normalized:
                    if line['kind']=='custom':
                        c.execute('INSERT INTO mvp_sale_custom_items(id,sale_id,item_name,qty,unit_price,line_total) VALUES(?,?,?,?,?,?)',(_id('sci'),saleid,line['name'],line['qty'],line['price'],line['line_total']))
                    else:
                        p=line['product']; pid=line['pid']; sale_item_id=_id('sli')
                        default_work={stage:{'required':True,'status':'Pending','cost':0} for stage in ('Calendar','Fal','Piko','Cutting','Jalar','Dag','Finishing','Die','Raffu','Polish')}
                        item_karigar=str(p['karigar_id'] or '') if 'karigar_id' in p.keys() else ''
                        c.execute('INSERT INTO mvp_sale_items(id,sale_id,product_id,product_name,qty,unit_price,unit_cost,line_total,cogs,gross_profit,karigar_id,production_work,stock_deducted) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                  (sale_item_id,saleid,pid,p['name'],line['qty'],line['price'],line['unit_cost'],line['line_total'],line['cogs'],line['profit'],item_karigar,json.dumps(default_work,separators=(',',':')),1 if deduct_stock else 0))
                        if deduct_stock:
                            latest=c.execute('SELECT quantity FROM mvp_products WHERE id=?',(pid,)).fetchone()
                            newq=round(float(latest['quantity'])-line['qty'],3)
                            c.execute('UPDATE mvp_products SET quantity=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(newq,pid))
                            c.execute('INSERT INTO mvp_stock_movements(id,product_id,product_name,movement_type,qty_delta,quantity_after,unit_cost,reference_type,reference_id,reason) VALUES(?,?,?,?,?,?,?,?,?,?)',(_id('mov'),pid,p['name'],'SALE_OUT',-line['qty'],newq,line['unit_cost'],'sale',saleid,invoice))
                if customer:
                    c.execute("DELETE FROM mvp_hidden_customers WHERE name_key=?",(customer.casefold(),))
                    c.execute("INSERT INTO mvp_parties(id,kind,name,phone) VALUES(?,?,?,?) ON CONFLICT(kind,name) DO UPDATE SET phone=CASE WHEN excluded.phone<>'' THEN excluded.phone ELSE mvp_parties.phone END",(_id('party'),'customer',customer,customer_phone))
                if amount_paid>0:
                    c.execute('INSERT INTO mvp_payments(id,sale_id,payment_date,amount,payment_method,notes) VALUES(?,?,?,?,?,?)',(_id('pay'),saleid,saledate,amount_paid,method,'Payment received at billing'))
                return _json(201,{'ok':True,'sale_id':saleid,'invoice_no':invoice,'subtotal':subtotal,'discount':discount,'tax':tax,'total':total,'amount_paid':amount_paid,'balance_due':round(total-amount_paid,2),'payment_status':status,'cogs':cogs,'gross_profit':gross,'stock_deducted':bool(deduct_stock)})
        return _json(404,{'ok':False,'error':'Endpoint not found'})
    except sqlite3.IntegrityError as e:
        msg=str(e)
        if 'UNIQUE constraint failed: mvp_products.sku' in msg: return _json(409,{'ok':False,'error':'That SKU already exists'})
        if 'UNIQUE constraint failed: mvp_parties.kind, mvp_parties.name' in msg: return _json(409,{'ok':False,'error':'That party already exists'})
        return _json(400,{'ok':False,'error':'This record conflicts with existing data'})
    except ValueError as e:
        return _json(400,{'ok':False,'error':str(e)})
    except Exception:
        return _json(500,{'ok':False,'error':'Could not save this inventory transaction'})

#!/usr/bin/env python3
"""Offline LAN web server for Stock Manager. Python standard library only."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import sqlite3, json, os, socket, subprocess, re, hmac, hashlib, time, mimetypes, uuid, html
from email.parser import BytesParser
from email.policy import default as email_policy
import inventory_mvp

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
DB = ROOT / 'saree_store.sqlite3'
HOST = os.environ.get('SAREE_HOST', '0.0.0.0')
PORT = int(os.environ.get('SAREE_PORT', '8000'))
ADMIN_PASSWORD = os.environ.get('SAREE_ADMIN_PASSWORD', 'Molu@1234')
SESSION_SECRET = os.environ.get('SAREE_SESSION_SECRET', 'local-saree-session-secret-change-me')
SESSION_SECONDS = 12 * 60 * 60
def session_token(expiry):
    payload=str(expiry); sig=hmac.new(SESSION_SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest(); return payload+'.'+sig
def valid_session(headers):
    m=re.search(r'(?:^|;\s*)saree_admin=([^;]+)',headers.get('Cookie',''))
    if not m: return False
    try:
        payload,sig=m.group(1).split('.',1); expiry=int(payload); expected=hmac.new(SESSION_SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()
        return expiry>int(time.time()) and hmac.compare_digest(sig,expected)
    except Exception: return False

SHIM = r'''<script>
// Same-origin localStorage bridge backed by SQLite on this host.
(function(){
 const native=window.localStorage;
 function request(method,key,value){
   const x=new XMLHttpRequest(); x.open(method,'/api/storage?key='+encodeURIComponent(key),false);
   if(method==='PUT') x.setRequestHeader('Content-Type','application/json');
   try{x.send(method==='PUT'?JSON.stringify({value:value}):null);}catch(e){throw new Error('Local database unavailable');}
   if(x.status<200||x.status>=300) throw new Error('Local database error ('+x.status+')');
   return x.responseText;
 }
 const bridge={getItem(k){const s=request('GET',String(k));if(!s)return null;try{return JSON.parse(s).value??null}catch(e){return null}},
 setItem(k,v){request('PUT',String(k),String(v))},
 removeItem(k){request('DELETE',String(k))},
 clear(){request('DELETE','__all__')},
 key(i){const s=request('GET','__keys__');try{return JSON.parse(s).keys[i]??null}catch(e){return null}},
 get length(){try{return JSON.parse(request('GET','__keys__')).keys.length}catch(e){return 0}}};
 try{Object.defineProperty(window,'localStorage',{configurable:true,value:bridge});}catch(e){console.error(e)}
})();
</script>'''

def render_login_page(error='LOGIN_ERROR'):
    """Render sign-in branding from the persistent app settings, even before login."""
    try:
        with sqlite3.connect(DB, timeout=3) as conn:
            row=conn.execute('SELECT app_name FROM mvp_app_settings WHERE id=1').fetchone()
            app_name=(row[0] if row and row[0] else 'Stock Manager')
    except Exception:
        app_name='Stock Manager'
    app_name=str(app_name)[:60]
    initial=html.escape((app_name[:1] or 'S').upper())
    page=(STATIC/'login.html').read_text(encoding='utf-8')
    return page.replace('APP_NAME',html.escape(app_name)).replace('APP_INITIAL',initial).replace('LOGIN_ERROR',html.escape(error)).encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    server_version = 'StockManagerLocal/1.0'
    def db(self):
        c=sqlite3.connect(DB, timeout=10)
        c.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        return c
    def send(self, status, body, ctype='application/json; charset=utf-8', extra_headers=None):
        data=body if isinstance(body,bytes) else body.encode('utf-8')
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        for key, value in (extra_headers or {}).items(): self.send_header(key, value)
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        u=urlparse(self.path)
        # The MVP is now the main business workspace. Old Admin URLs remain as
        # compatibility redirects, but no separate Admin page is served.
        if u.path in ('/admin','/admin/','/admin.html'):
            if not valid_session(self.headers):
                return self.send(200,render_login_page(),'text/html; charset=utf-8')
            self.send_response(303); self.send_header('Location','/mvp'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length','0'); self.end_headers(); return
        # The old Saree Records & Setup UI has been removed from the MVP.
        # Keep old bookmarks working by sending signed-in users to the main workspace.
        if u.path in ('/saree-records','/saree-records.html'):
            if not valid_session(self.headers):
                return self.send(200,render_login_page(),'text/html; charset=utf-8')
            self.send_response(303); self.send_header('Location','/mvp'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length','0'); self.end_headers(); return
        # The legacy public storefront is not part of the Stock Manager workspace.
        if u.path in ('/index.html','/store'):
            if not valid_session(self.headers):
                return self.send(200,render_login_page(),'text/html; charset=utf-8')
            self.send_response(303); self.send_header('Location','/mvp'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length','0'); self.end_headers(); return
        # Private business pages require the signed workspace session.
        if u.path in ('/mvp','/mvp/','/mvp.html') and not valid_session(self.headers):
            return self.send(200,render_login_page(),'text/html; charset=utf-8')
        if u.path.startswith('/api/mvp/'):
            if not valid_session(self.headers): return self.send(403, json.dumps({'ok':False,'error':'Sign-in required'}))
            status, body, ctype, *extras = inventory_mvp.get_route(DB, u.path, u.query)
            return self.send(status, body, ctype, extras[0] if extras else None)
        # Logout link fallback for older cached pages that used GET.
        if u.path=='/api/logout':
            self.send_response(303); self.send_header('Location','/mvp'); self.send_header('Set-Cookie','saree_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length','0'); self.end_headers(); return
        if u.path=='/api/health': return self.send(200,json.dumps({'ok':True,'database':'SQLite','offline':True}))
        if u.path=='/api/storage':
            from urllib.parse import parse_qs
            key=parse_qs(u.query).get('key',[''])[0]
            with self.db() as c:
                if key=='__keys__': val=json.dumps({'keys':[r[0] for r in c.execute('SELECT key FROM kv ORDER BY key')]})
                else:
                    row=c.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
                    val=json.dumps({'value':row[0]}) if row else ''
            return self.send(200,val)
        media_match = re.fullmatch(r'/uploads/((?:home|product|expense|business)-[0-9a-f]{32}\.(?:jpg|png|webp|gif))', u.path)
        if media_match:
            if not valid_session(self.headers):
                return self.send(403, 'Sign-in required', 'text/plain; charset=utf-8')
            filename = media_match.group(1)
            with sqlite3.connect(DB, timeout=10) as c:
                row = c.execute('SELECT mime_type, content FROM mvp_media WHERE filename=?', (filename,)).fetchone()
            if row:
                return self.send(200, bytes(row[1]), row[0], {'Cache-Control': 'private, max-age=3600'})
            # Compatibility fallback for a database opened before migration has completed.
            legacy_file = (STATIC / 'uploads' / filename).resolve()
            if str(legacy_file).startswith(str((STATIC / 'uploads').resolve())) and legacy_file.is_file():
                return self.send(200, legacy_file.read_bytes(), mimetypes.guess_type(filename)[0] or 'application/octet-stream')
            return self.send(404, 'Image not found', 'text/plain; charset=utf-8')
        if u.path == '/':
            if not valid_session(self.headers):
                return self.send(200,render_login_page(),'text/html; charset=utf-8')
            self.send_response(303); self.send_header('Location','/mvp'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length','0'); self.end_headers(); return
        path='/mvp.html' if u.path in ('/mvp','/mvp/') else ('/saree-records.html' if u.path in ('/saree-records','/saree-records.html') else (u.path if u.path!='/' else '/index.html'))
        if path.startswith('/'): path=path[1:]
        target=(STATIC/path).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file(): return self.send(404,'Not found','text/plain; charset=utf-8')
        data=target.read_bytes()
        if target.suffix=='.html': data=re.sub(br'<head(?:\s[^>]*)?>', lambda m: m.group(0)+b'\n'+SHIM.encode('utf-8'), data, count=1, flags=re.IGNORECASE)
        if target.suffix=='.html':
            ctype='text/html; charset=utf-8'
        else:
            ctype=mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        return self.send(200,data,ctype)
    def do_POST(self):
        u=urlparse(self.path)
        if u.path.startswith('/api/mvp/'):
            if not valid_session(self.headers): return self.send(403, json.dumps({'ok':False,'error':'Sign-in required'}))
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<=0 or length>2_000_000: return self.send(413, json.dumps({'ok':False,'error':'Request body is empty or too large'}))
                body=json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(body,dict): return self.send(400, json.dumps({'ok':False,'error':'JSON object required'}))
                status, payload, ctype=inventory_mvp.post_route(DB,u.path,body)
                return self.send(status,payload,ctype)
            except json.JSONDecodeError:
                return self.send(400,json.dumps({'ok':False,'error':'Invalid JSON body'}))
            except Exception:
                return self.send(400,json.dumps({'ok':False,'error':'Could not read request body'}))
        if u.path=='/api/login':
            length=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(min(length,10000)).decode('utf-8','replace')
            from urllib.parse import parse_qs
            password=parse_qs(raw).get('password',[''])[0]
            if not hmac.compare_digest(password,ADMIN_PASSWORD):
                return self.send(401,render_login_page('Incorrect password. Try again.'),'text/html; charset=utf-8')
            expiry=int(time.time())+SESSION_SECONDS
            self.send_response(303); self.send_header('Location','/mvp'); self.send_header('Set-Cookie',f'saree_admin={session_token(expiry)}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}'); self.send_header('Content-Length','0'); self.end_headers(); return
        if u.path=='/api/logout':
            self.send_response(303); self.send_header('Location','/mvp'); self.send_header('Set-Cookie','saree_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); self.send_header('Content-Length','0'); self.end_headers(); return
        if u.path in ('/api/home-images/upload','/api/product-images/upload','/api/expense-images/upload','/api/business-images/upload'):
            if not valid_session(self.headers): return self.send(403,json.dumps({'error':'Sign-in required'}))
            content_type=self.headers.get('Content-Type','')
            if not content_type.lower().startswith('multipart/form-data;'):
                return self.send(400,json.dumps({'error':'Upload must use multipart form data'}))
            try: length=int(self.headers.get('Content-Length','0'))
            except ValueError: length=0
            if length<=0: return self.send(400,json.dumps({'error':'Choose at least one image'}))
            if length>25_000_000: return self.send(413,json.dumps({'error':'Upload is too large. Please upload fewer or smaller images.'}))
            raw=self.rfile.read(length)
            try:
                message=BytesParser(policy=email_policy).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii')+raw)
                parts=[p for p in message.iter_parts() if p.get_filename()]
            except Exception:
                return self.send(400,json.dumps({'error':'Could not read the uploaded files'}))
            if not parts: return self.send(400,json.dumps({'error':'Choose at least one image'}))
            if len(parts)>8: return self.send(400,json.dumps({'error':'Upload up to 8 images at a time'}))
            prefix={'/api/home-images/upload':'home','/api/product-images/upload':'product','/api/expense-images/upload':'expense','/api/business-images/upload':'business'}[u.path]
            saved=[]; prepared=[]; total=0
            signatures=(
                ('.png', 'image/png', lambda b: b.startswith(b'\x89PNG\r\n\x1a\n')),
                ('.jpg', 'image/jpeg', lambda b: b.startswith(b'\xff\xd8\xff')),
                ('.gif', 'image/gif', lambda b: b.startswith((b'GIF87a',b'GIF89a'))),
                ('.webp', 'image/webp', lambda b: len(b)>=12 and b.startswith(b'RIFF') and b[8:12]==b'WEBP'),
            )
            try:
                for part in parts:
                    data=part.get_payload(decode=True) or b''
                    total+=len(data)
                    if not data: raise ValueError('An uploaded file is empty')
                    if len(data)>8_000_000: raise ValueError('Each image must be 8 MB or smaller')
                    if total>20_000_000: raise ValueError('Total upload must be 20 MB or smaller')
                    match=next(((ext,mime,check) for ext,mime,check in signatures if check(data)),None)
                    if not match: raise ValueError('Use JPG, PNG, WEBP or GIF image files only')
                    ext,mime_type,_=match
                    filename=prefix+'-'+uuid.uuid4().hex+ext
                    original=Path(part.get_filename() or 'image'+ext).name
                    prepared.append((filename,mime_type,data))
                    saved.append({'name':original[:120],'url':'/uploads/'+filename,'filename':filename,'size':len(data)})
                # Store each image as a BLOB in the same SQLite file as business data.
                # Therefore copying saree_store.sqlite3 alone also backs up all photos.
                with self.db() as c:
                    c.executemany('INSERT INTO mvp_media(filename,mime_type,content,size) VALUES(?,?,?,?)',
                                  [(name,mime,sqlite3.Binary(data),len(data)) for name,mime,data in prepared])
            except ValueError as e:
                return self.send(400,json.dumps({'error':str(e)}))
            except Exception:
                return self.send(500,json.dumps({'error':'Could not save the uploaded images to the database'}))
            return self.send(201,json.dumps({'ok':True,'images':saved}))
        return self.send(404,'{}')

    def do_PUT(self):
        u=urlparse(self.path)
        if u.path!='/api/storage': return self.send(404,'{}')
        if not valid_session(self.headers): return self.send(403,json.dumps({'error':'Sign-in required'}))
        from urllib.parse import parse_qs
        key=parse_qs(u.query).get('key',[''])[0]
        if not key or key.startswith('__'): return self.send(400,json.dumps({'error':'Invalid key'}))
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>30_000_000: return self.send(413,json.dumps({'error':'Payload too large'}))
            payload=json.loads(self.rfile.read(length) or b'{}'); value=payload.get('value')
            if not isinstance(value,str): return self.send(400,json.dumps({'error':'Value must be a string'}))
            with self.db() as c: c.execute('INSERT INTO kv(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP',(key,value))
            return self.send(200,'{"ok":true}')
        except Exception as e: return self.send(400,json.dumps({'error':'Invalid request'}))
    def do_DELETE(self):
        u=urlparse(self.path)
        if u.path in ('/api/home-images','/api/product-images','/api/expense-images','/api/business-images'):
            if not valid_session(self.headers): return self.send(403,json.dumps({'error':'Sign-in required'}))
            from urllib.parse import parse_qs
            name=parse_qs(u.query).get('name',[''])[0]
            prefix={'/api/home-images':'home','/api/product-images':'product','/api/expense-images':'expense','/api/business-images':'business'}[u.path]
            if not re.fullmatch(prefix+r'-[0-9a-f]{32}\.(?:jpg|png|webp|gif)',name):
                return self.send(400,json.dumps({'error':'Invalid image name'}))
            target=STATIC/'uploads'/name
            removed=False
            try:
                with self.db() as c:
                    res=c.execute('DELETE FROM mvp_media WHERE filename=?',(name,))
                    removed=bool(res.rowcount)
            except Exception:
                return self.send(500,json.dumps({'error':'Could not remove image from the database'}))
            # Remove an old pre-migration file if one remains on disk.
            try:
                if target.is_file():
                    target.unlink(); removed=True
            except OSError:
                return self.send(500,json.dumps({'error':'Could not remove legacy image file'}))
            if not removed: return self.send(404,json.dumps({'error':'Image not found'}))
            return self.send(200,'{"ok":true}')
        if u.path!='/api/storage': return self.send(404,'{}')
        if not valid_session(self.headers): return self.send(403,json.dumps({'error':'Sign-in required'}))
        from urllib.parse import parse_qs
        key=parse_qs(u.query).get('key',[''])[0]
        with self.db() as c:
            if key=='__all__': c.execute('DELETE FROM kv')
            elif key and not key.startswith('__'): c.execute('DELETE FROM kv WHERE key=?',(key,))
        return self.send(200,'{"ok":true}')
    def log_message(self, fmt, *args): print('%s - %s' % (self.address_string(), fmt%args))

def lan_ips():
    """Prefer the route-selected LAN address; ignore loopback and virtual interfaces."""
    found=[]
    try:
        out=subprocess.check_output(['ip','-4','route','get','1.1.1.1'], text=True, stderr=subprocess.DEVNULL, timeout=2)
        m=re.search(r'\bsrc\s+(\d+\.\d+\.\d+\.\d+)', out)
        if m and not m.group(1).startswith('127.'):
            found.append(m.group(1))
    except Exception:
        pass
    try:
        out=subprocess.check_output(['ip','-o','-4','addr','show','scope','global'], text=True, stderr=subprocess.DEVNULL, timeout=2)
        for line in out.splitlines():
            # Ignore common container/virtual bridge interfaces when a physical LAN IP is available.
            parts=line.split()
            iface=parts[1] if len(parts)>1 else ''
            m=re.search(r'\binet\s+(\d+\.\d+\.\d+\.\d+)/',line)
            if m and not any(x in iface.lower() for x in ('docker','br-','veth','virbr','tun','tailscale','wg')):
                found.append(m.group(1))
    except Exception:
        pass
    if not found:
        try:
            for info in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET):
                ip=info[4][0]
                if not ip.startswith('127.') and not ip.startswith('169.254.'):
                    found.append(ip)
        except OSError:
            pass
    return list(dict.fromkeys(found))

if __name__=='__main__':
    with sqlite3.connect(DB) as c: c.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    inventory_mvp.init_db(DB)
    print('\nStock Manager — inventory workspace (offline; SQLite database)')
    print(f'Stock Manager:     http://127.0.0.1:{PORT}/mvp')
    for ip in lan_ips(): print(f'LAN access:     http://{ip}:{PORT}/mvp  (Stock Manager workspace)')
    print('If LAN access fails: make sure devices use the same Wi-Fi (not guest Wi-Fi), allow TCP port '+str(PORT)+' in the Linux firewall, and check router client isolation.\n')
    print('Keep this terminal open. Press Ctrl+C to stop.\n')
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()

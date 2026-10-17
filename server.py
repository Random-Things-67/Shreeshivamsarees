#!/usr/bin/env python3
"""Offline LAN web server for Saree Stock. Python standard library only."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import sqlite3, json, os, socket, subprocess, re, hmac, hashlib, time, mimetypes, uuid
from email.parser import BytesParser
from email.policy import default as email_policy

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

class Handler(BaseHTTPRequestHandler):
    server_version = 'SareeLocal/1.0'
    def db(self):
        c=sqlite3.connect(DB, timeout=10)
        c.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        return c
    def send(self, status, body, ctype='application/json; charset=utf-8'):
        data=body if isinstance(body,bytes) else body.encode('utf-8')
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        u=urlparse(self.path)
        # Every admin URL alias must require the signed session cookie, including /admin.html.
        if u.path in ('/admin','/admin/','/admin.html') and not valid_session(self.headers):
            target=STATIC/'login.html'; return self.send(200,target.read_bytes(),'text/html; charset=utf-8')
        # Legacy fallback: older cached admin pages navigated to this URL with GET.
        if u.path=='/api/logout':
            self.send_response(303); self.send_header('Location','/admin'); self.send_header('Set-Cookie','saree_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length','0'); self.end_headers(); return
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
        path='/admin.html' if u.path in ('/admin','/admin/') else (u.path if u.path!='/' else '/index.html')
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
        if u.path=='/api/login':
            length=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(min(length,10000)).decode('utf-8','replace')
            from urllib.parse import parse_qs
            password=parse_qs(raw).get('password',[''])[0]
            if not hmac.compare_digest(password,ADMIN_PASSWORD):
                return self.send(401,(STATIC/'login.html').read_text().replace('LOGIN_ERROR','Incorrect password. Try again.'),'text/html; charset=utf-8')
            expiry=int(time.time())+SESSION_SECONDS
            self.send_response(303); self.send_header('Location','/admin'); self.send_header('Set-Cookie',f'saree_admin={session_token(expiry)}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}'); self.send_header('Content-Length','0'); self.end_headers(); return
        if u.path=='/api/logout':
            self.send_response(303); self.send_header('Location','/admin'); self.send_header('Set-Cookie','saree_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); self.send_header('Content-Length','0'); self.end_headers(); return
        if u.path=='/api/home-images/upload':
            if not valid_session(self.headers): return self.send(403,json.dumps({'error':'Admin login required'}))
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
            upload_dir=STATIC/'uploads'
            upload_dir.mkdir(parents=True,exist_ok=True)
            saved=[]; total=0
            signatures=(
                ('.png', lambda b: b.startswith(b'\x89PNG\r\n\x1a\n')),
                ('.jpg', lambda b: b.startswith(b'\xff\xd8\xff')),
                ('.gif', lambda b: b.startswith((b'GIF87a',b'GIF89a'))),
                ('.webp', lambda b: len(b)>=12 and b.startswith(b'RIFF') and b[8:12]==b'WEBP'),
            )
            try:
                for part in parts:
                    data=part.get_payload(decode=True) or b''
                    total+=len(data)
                    if not data: raise ValueError('An uploaded file is empty')
                    if len(data)>8_000_000: raise ValueError('Each image must be 8 MB or smaller')
                    if total>20_000_000: raise ValueError('Total upload must be 20 MB or smaller')
                    match=next(((ext,check) for ext,check in signatures if check(data)),None)
                    if not match: raise ValueError('Use JPG, PNG, WEBP or GIF image files only')
                    ext=match[0]; filename='home-'+uuid.uuid4().hex+ext
                    dest=upload_dir/filename; tmp=upload_dir/(filename+'.tmp')
                    tmp.write_bytes(data); tmp.replace(dest)
                    original=Path(part.get_filename() or 'image'+ext).name
                    saved.append({'name':original[:120],'url':'/uploads/'+filename,'filename':filename,'size':len(data)})
            except ValueError as e:
                for item in saved:
                    try: (upload_dir/item['filename']).unlink(missing_ok=True)
                    except OSError: pass
                return self.send(400,json.dumps({'error':str(e)}))
            except Exception:
                for item in saved:
                    try: (upload_dir/item['filename']).unlink(missing_ok=True)
                    except OSError: pass
                return self.send(500,json.dumps({'error':'Could not save the uploaded images'}))
            return self.send(201,json.dumps({'ok':True,'images':saved}))
        return self.send(404,'{}')

    def do_PUT(self):
        u=urlparse(self.path)
        if u.path!='/api/storage': return self.send(404,'{}')
        if not valid_session(self.headers): return self.send(403,json.dumps({'error':'Admin login required'}))
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
        if u.path=='/api/home-images':
            if not valid_session(self.headers): return self.send(403,json.dumps({'error':'Admin login required'}))
            from urllib.parse import parse_qs
            name=parse_qs(u.query).get('name',[''])[0]
            if not re.fullmatch(r'home-[0-9a-f]{32}\.(?:jpg|png|webp|gif)',name):
                return self.send(400,json.dumps({'error':'Invalid image name'}))
            target=STATIC/'uploads'/name
            try: target.unlink()
            except FileNotFoundError: return self.send(404,json.dumps({'error':'Image not found'}))
            except OSError: return self.send(500,json.dumps({'error':'Could not remove image'}))
            return self.send(200,'{"ok":true}')
        if u.path!='/api/storage': return self.send(404,'{}')
        if not valid_session(self.headers): return self.send(403,json.dumps({'error':'Admin login required'}))
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
    print('\nSaree Stock local server (offline; SQLite database)')
    print(f'Admin panel:    http://127.0.0.1:{PORT}/admin')
    print(f'Customer store: http://127.0.0.1:{PORT}/')
    for ip in lan_ips(): print(f'LAN access:     http://{ip}:{PORT}/  (customers) | http://{ip}:{PORT}/admin  (admin)')
    print('If LAN access fails: make sure devices use the same Wi-Fi (not guest Wi-Fi), allow TCP port '+str(PORT)+' in the Linux firewall, and check router client isolation.\n')
    print('Keep this terminal open. Press Ctrl+C to stop.\n')
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()

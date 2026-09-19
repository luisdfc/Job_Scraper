from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import __version__
from .common import atomic_json, now_iso, read_json
from .storage import Store


def root_key(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()


def make_handler(root: Path, token: str):
    store=Store(root)
    class Handler(BaseHTTPRequestHandler):
        server_version='LocalJobSearch/2'
        def log_message(self, format, *args):
            # No notes, candidate information or request bodies in server logs.
            pass
        @property
        def origin(self):
            return 'http://127.0.0.1:'+str(self.server.server_port)
        def trusted(self):
            host=self.headers.get('Host','')
            origin=self.headers.get('Origin')
            return (host=='127.0.0.1:'+str(self.server.server_port)
                    and (not origin or origin==self.origin)
                    and self.headers.get('Sec-Fetch-Site') not in ('cross-site',))
        def respond(self, status: int, payload: bytes, content_type='application/json; charset=utf-8', filename=''):
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Cross-Origin-Resource-Policy','same-origin')
            if filename:
                self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
            self.end_headers()
            self.wfile.write(payload)
        def json(self, payload, status=200):
            self.respond(status,json.dumps(payload,ensure_ascii=False).encode())
        def do_GET(self):
            if not self.trusted():
                self.json({'error':'Origen no permitido'},403)
                return
            path=urlsplit(self.path).path
            if path=='/api/health':
                self.json({'app':'job-search-local','version':__version__,'root_key':root_key(root)})
                return
            if path=='/api/states':
                self.json({'states':store.states(),'token':token})
                return
            if path=='/api/export-states':
                self.json({'schema_version':1,'exported_at':now_iso(),'states':store.states()})
                return
            relative=unquote(path).lstrip('/') or 'index.html'
            if relative!='index.html' and not re.fullmatch(r'runs/[A-Za-z0-9_-]+/(?:shortlist\.html|ranked_jobs\.json|manifest\.json|queries\.json|outside_default_view\.json)',relative):
                self.json({'error':'Recurso no disponible'},404)
                return
            target=(root/relative).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                self.json({'error':'No existe el archivo'},404)
                return
            content_type='text/html; charset=utf-8' if target.suffix=='.html' else 'application/json; charset=utf-8'
            self.respond(200,target.read_bytes(),content_type)
        def read_body(self):
            if not self.trusted() or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),token):
                self.json({'error':'Origen o token de guardado no válido'},403)
                return None
            if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
                self.json({'error':'Se requiere JSON'},415)
                return None
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536:
                    raise ValueError('Tamaño de petición no permitido')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError('Se requiere un objeto JSON')
                return data
            except (ValueError,TypeError) as e:
                self.json({'error':str(e)},400)
                return None
        def do_PUT(self):
            if urlsplit(self.path).path!='/api/state':
                self.json({'error':'No existe ese recurso'},404)
                return
            data=self.read_body()
            if data is None:
                return
            try:
                ident=data.get('job_id')
                if not isinstance(ident,str) or not re.fullmatch(r'job_[a-f0-9]{24}',ident):
                    raise ValueError('Identificador de oferta no válido')
                revision=data.get('revision')
                if not isinstance(revision,int) or revision<0:
                    raise ValueError('Se requiere la revisión actual del estado')
                result=store.set_state(ident,data.get('status'),data.get('notes',''),revision)
                self.json(result)
            except RuntimeError as e:
                self.json({'error':str(e)},409)
            except (ValueError,TypeError) as e:
                self.json({'error':str(e)},400)
        def do_POST(self):
            if urlsplit(self.path).path!='/api/stop':
                self.json({'error':'No existe ese recurso'},404)
                return
            if self.read_body() is None:
                return
            self.json({'status':'stopping'})
            threading.Thread(target=self.server.shutdown,daemon=True).start()
    return Handler


def serve(root: Path, port=8765):
    token=secrets.token_urlsafe(32)
    handler=make_handler(root,token)
    try:
        server=ThreadingHTTPServer(('127.0.0.1',port),handler)
    except OSError:
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    control=root/'private/server.json'
    atomic_json(control,{'port':server.server_port,'pid':os.getpid(),'root_key':root_key(root),'version':__version__})
    print('Informes locales: http://127.0.0.1:'+str(server.server_port),flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
        saved=read_json(control,{})
        if saved.get('pid')==os.getpid():
            control.unlink(missing_ok=True)


def request_json(port: int, path: str, *, method='GET', data=None, token=''):
    origin='http://127.0.0.1:'+str(port)
    headers={'Origin':origin}
    payload=None
    if data is not None:
        headers.update({'Content-Type':'application/json','X-CSRF-Token':token})
        payload=json.dumps(data).encode()
    req=urllib.request.Request(origin+path,data=payload,headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=2) as response:
        return json.load(response)


def running(root: Path) -> int | None:
    control=read_json(root/'private/server.json',{})
    port=control.get('port')
    if not isinstance(port,int) or not 0<port<65536:
        return None
    try:
        health=request_json(port,'/api/health')
        if health.get('root_key')==root_key(root) and health.get('version')==__version__:
            return port
    except (OSError,ValueError,TimeoutError):
        pass
    return None


def ensure_server(root: Path, port=8765) -> int:
    existing=running(root)
    if existing:
        return existing
    cmd=[sys.executable,str(root/'job_search.py'),'serve','--port',str(port)]
    kwargs={'cwd':str(root),'stdin':subprocess.DEVNULL}
    if os.name=='nt':
        kwargs['creationflags']=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs['start_new_session']=True
    log=root/'private/server.log'
    with log.open('ab') as stream:
        proc=subprocess.Popen(cmd,stdout=stream,stderr=stream,**kwargs)
    for _ in range(50):
        existing=running(root)
        if existing:
            return existing
        if proc.poll() is not None:
            break
        time.sleep(.1)
    raise RuntimeError('No se pudo iniciar el servidor local. Consulta private/server.log o ejecuta job_search.py serve.')


def open_report(root: Path, report: Path, *, port=8765) -> str:
    actual=ensure_server(root,port)
    relative=report.resolve().relative_to(root.resolve()).as_posix()
    url=f'http://127.0.0.1:{actual}/{relative}'
    webbrowser.open(url)
    return url


def stop(root: Path) -> bool:
    port=running(root)
    if not port:
        return False
    token=request_json(port,'/api/states')['token']
    request_json(port,'/api/stop',method='POST',data={'stop':True},token=token)
    return True

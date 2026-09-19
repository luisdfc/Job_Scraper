"""Local-only HTTP API contract and security checks."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from local_search.server import make_handler,request_json
from local_search.storage import Store

@pytest.fixture
def api(tmp_path):
    (tmp_path/'index.html').write_text('test index',encoding='utf-8')
    store=Store(tmp_path)
    ident=store.register([{'url':'https://example.org/jobs/1','title':'Analyst'}],'test')[0]['job_id']
    srv=ThreadingHTTPServer(('127.0.0.1',0),make_handler(tmp_path,'test-token'))
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    yield srv.server_port,store,ident
    srv.shutdown();srv.server_close();thread.join(timeout=2)


def test_save_read_and_conflict(api):
    port,store,ident=api
    payload={'job_id':ident,'status':'applied','notes':'Private note','revision':0}
    saved=request_json(port,'/api/state',method='PUT',data=payload,token='test-token')
    assert saved['status']=='applied' and saved['revision']==1
    current=request_json(port,'/api/states')
    assert current['states'][ident]['notes']=='Private note'
    with pytest.raises(urllib.error.HTTPError) as exc:
        request_json(port,'/api/state',method='PUT',data=payload,token='test-token')
    assert exc.value.code==409


def test_no_write_without_csrf_token(api):
    port,store,ident=api
    with pytest.raises(urllib.error.HTTPError) as exc:
        request_json(port,'/api/state',method='PUT',data={'job_id':ident,'status':'discarded','notes':'','revision':0})
    assert exc.value.code==403 and not store.states()

@pytest.mark.parametrize('path',['/private/job_search.sqlite3','/config.json','/../config.json','/%2e%2e/config.json'])
def test_private_files_not_served(api,path):
    port,_,_=api
    with pytest.raises(urllib.error.HTTPError) as exc:
        request_json(port,path)
    assert exc.value.code==404


def test_other_origin_rejected(api):
    port,_,_=api
    req=urllib.request.Request(f'http://127.0.0.1:{port}/api/states',headers={'Origin':'https://other.example'})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code==403


def test_export_includes_persisted_states(api):
    port,store,ident=api
    store.set_state(ident,'saved','note',0)
    data=request_json(port,'/api/export-states')
    assert data['states'][ident]['status']=='saved'
    assert data['schema_version']==1

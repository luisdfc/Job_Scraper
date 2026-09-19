"""Búsquedas locales por fecha, scoring explicable y seguimiento de candidaturas.

weekly/daily: siete días, instantánea nueva; open: solo abre, nunca busca ni rankea.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

from local_search.common import atomic_json, read_json, run_lock, safe_url
from local_search.scoring import evaluate, salary_signal
from local_search.reports import write_report, build_index
from local_search.pipeline import (load_settings, list_runs, migrate_legacy, run_search,
                                   select_run, rerank, recover)
from local_search.storage import Store

ROOT=Path(__file__).resolve().parent


def rank(selector=None, *, make_latest=False):
    config,rules=load_settings(ROOT)
    return rerank(ROOT,config,rules,selector,make_latest=make_latest)


def main(argv=None) -> int:
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8',errors='replace',line_buffering=True)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['weekly','daily','initial','rank','check','open','runs','migrate','probe','recover','serve','stop','export-state'])
    parser.add_argument('--source',choices=['indeed','linkedin','both'],default='both')
    parser.add_argument('--run',help='Identificador completo o fecha sin ambigüedad; latest abre la última búsqueda')
    parser.add_argument('--history',action='store_true',help='Abrir el índice de informes')
    parser.add_argument('--make-latest',action='store_true',help='Con rank, abrir esta reclasificación por defecto; conserva la fecha de captura')
    parser.add_argument('--days',type=int,help='Ventana de publicación; weekly/daily usan 7, initial usa 30')
    parser.add_argument('--no-verify',action='store_true',help='Omitir comprobación de vigencia/descripciones; queda explícito en el informe')
    parser.add_argument('--port',type=int,default=None,help='Puerto local preferido (por defecto 8765)')
    parser.add_argument('--max-pages',type=int,help='Salvaguarda por consulta; 0 = sin límite. Alcanzarla marca resultado parcial')
    args=parser.parse_args(argv)
    try:
        if args.make_latest and args.action!='rank':
            raise ValueError('--make-latest solo se utiliza con rank')
        if args.days is not None and args.days<1:
            raise ValueError('--days debe ser positivo')
        if args.max_pages is not None and args.max_pages<0:
            raise ValueError('--max-pages debe ser cero o positivo')
        if args.port is not None and not 0<=args.port<65536:
            raise ValueError('Puerto no válido')
        # Opening/serving remains possible even if someone is editing scoring/config.
        if args.action in ('open','serve','stop','runs','export-state'):
            from local_search import server
            try:
                config=read_json(ROOT/'config.json',{})
            except (ValueError,OSError):
                config={}  # Existing snapshots must remain accessible during a config edit.
            port=args.port if args.port is not None else config.get('local_ui',{}).get('port',8765)
            if args.action=='serve':
                server.serve(ROOT,port)
            elif args.action=='stop':
                print('Servidor local detenido.' if server.stop(ROOT) else 'No hay un servidor local activo para este proyecto.')
            elif args.action=='runs':
                for m in list_runs(ROOT):
                    print(f'{m["id"]} | {m.get("status")} | {m.get("job_count",0)} ofertas | {m.get("kind")}')
            elif args.action=='export-state':
                path=ROOT/'private/candidaturas_export.json'
                atomic_json(path,{'schema_version':1,'states':Store(ROOT).states()})
                print(path)
            else:
                report=ROOT/'index.html' if args.history else select_run(ROOT,args.run)/'shortlist.html'
                if not report.is_file():
                    raise ValueError('No hay un informe guardado. Ejecuta migrate o weekly primero.')
                print(server.open_report(ROOT,report,port=port))
            return 0
        config,rules=load_settings(ROOT)
        if args.action=='check':
            print('Configuración JSON y patrones válidos. Sin filtro de título ni recorte por score en el motor local.')
            print('Ventana semanal/diaria: 168 horas. Páginas de informe:',config.get('local_ui',{}).get('page_size',50))
            print('Consultas: LinkedIn',len(config.get('search_terms',{}).get('linkedin',[])),'· Indeed',len(config.get('search_terms',{}).get('indeed',[])))
            print('Páginas por consulta:',config.get('collection',{}).get('max_pages_per_query',0),'(0 = sin límite); tope de tiempo:',config.get('collection',{}).get('query_timeout_seconds',240),'s por consulta')
            print('JobSpy disponible:',bool(importlib.util.find_spec('jobspy')))
            print('Validación local, no prueba de conectividad. Para comprobar las fuentes: job_search.py probe')
            return 0
        with run_lock(ROOT):
            if args.action=='migrate':
                report=migrate_legacy(ROOT,config,rules)
                print(report or 'No hay datos anteriores para importar.')
            elif args.action=='rank':
                rank(args.run,make_latest=args.make_latest)
            elif args.action=='recover':
                if not args.run:
                    raise ValueError('recover requiere --run con la ejecución incompleta')
                recover(ROOT,config,rules,args.run)
            else:
                active=copy.deepcopy(config)
                if args.max_pages is not None:
                    active.setdefault('collection',{})['max_pages_per_query']=args.max_pages
                days=args.days if args.days is not None else config.get('run',{}).get('backfill_days',30) if args.action=='initial' else 7
                for name in ('linkedin','indeed'):
                    if args.source in ('both',name) and (not active.get('search_terms',{}).get(name) or not active.get('locations',{}).get(name)):
                        raise ValueError(f'Faltan consultas o ubicaciones para {name}; no se usará config.example.json como sustituto.')
                run_search(ROOT,active,rules,days=days,source=args.source,
                           verify=not args.no_verify and config.get('verification',{}).get('enabled',True),probe=args.action=='probe')
        return 0
    except (OSError,ValueError,RuntimeError,KeyError,json.JSONDecodeError) as e:
        print(f'ERROR: {e}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())

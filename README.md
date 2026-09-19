# Job Scraper local

Búsqueda semanal en LinkedIn e Indeed, con una captura independiente por ejecución, ranking explicable y seguimiento privado de candidaturas.

## Uso en Windows

Desde la carpeta `Job_Scraper`:

```powershell
.\.venv\Scripts\python.exe job_search.py weekly
.\.venv\Scripts\python.exe job_search.py open
```

`weekly` consulta los últimos siete días. La duración depende de las consultas, pausas y comprobaciones de las fuentes. No mezcla resultados de búsquedas anteriores ni garantiza recuperar todas las vacantes del mercado.

```powershell
# Elegir una captura anterior
.\.venv\Scripts\python.exe job_search.py open --history

# Aplicar el ranking actualizado a lo descargado y abrirlo por defecto
.\.venv\Scripts\python.exe job_search.py rank --make-latest
.\.venv\Scripts\python.exe job_search.py open

# Revisar configuración; una prueba corta de conexión es opcional
.\.venv\Scripts\python.exe job_search.py check
.\.venv\Scripts\python.exe job_search.py probe
```

Una reclasificación conserva la fecha de captura y sus limitaciones: no comprueba de nuevo las vacantes. Sin `--make-latest`, crea un informe consultable en el histórico sin cambiar el predeterminado.

## Organización

| Ruta | Uso |
|---|---|
| `job_search.py` | Entrada de la aplicación local |
| `local_search/` | Captación, interpretación de anuncios, ranking, datos, informes y servidor local |
| `scrape_jobs.py` | Adaptadores y parsers heredados usados por el motor y sus pruebas |
| `scripts/` | Comprobaciones de privacidad y calibración sin red |
| `tests/` | Pruebas reproducibles; `tests/local/` necesita configuración privada |
| `docs/` | Documentación vigente del ranking |
| `config.json` | Consultas y preferencias locales; ignorado por Git |
| `private/` | Perfil maestro, ranking personal, candidaturas, calibración y respaldos; ignorado por Git |
| `runs/` | Capturas e informes por fecha; ignorado por Git |
| `output/` | Compatibilidad con importaciones anteriores; no se usa para rellenar capturas nuevas |

El perfil maestro dentro de `private/` es la referencia factual. `private/ranking.json` recoge la calibración y el hash del perfil: si cambias el perfil, hay que revisar esa calibración. El motor no presupone que nuevas capacidades aparezcan automáticamente por editar el texto.

## Qué significa la prioridad

La afinidad refleja las funciones y preferencias; la prioridad añade ajustes por requisitos y responsabilidad. Un requisito deseable no resta. Una diferencia pequeña de experiencia pesa poco. Una función central de otra profesión o una responsabilidad directiva puede limitar la prioridad aunque el sector interese.

Cada tarjeta muestra motivos, advertencias y evidencia. La puntuación es una heurística para ordenar la lectura, no una probabilidad de contratación. No elimina ofertas por puntuación. Los filtros y la búsqueda actúan sobre todo el conjunto, con 50 tarjetas por página. «Mostrar absolutamente todas» recupera también cierres confirmados, fechas antiguas y ubicaciones fuera del ámbito.

Consulta [el diseño y sus límites](docs/ranking.md). La calibración con anuncios descargados queda únicamente en `private/calibration/`.

## Datos privados

Los perfiles, preferencias, resultados, estados y notas quedan fuera de Git. El servidor escucha únicamente en `127.0.0.1`. El flujo local no publica informes, no envía candidaturas ni utiliza servicios externos para puntuar. Las únicas peticiones normales son a las fuentes de ofertas.

```powershell
# Comprobar exactamente el contenido preparado para Git
.\.venv\Scripts\python.exe scripts/check_privacy.py

# Instalar la protección antes de cada commit en una copia nueva
 git config core.hooksPath .githooks
```

La protección revisa el índice de Git y también se ejecuta en CI. No publiques carpetas privadas ni fuerces su inclusión con `git add -f`. No puede proteger una copia manual fuera del proyecto ni deshacer una publicación anterior.

Los estados y notas se guardan en `private/job_search.sqlite3`; no dependen del navegador. Los respaldos previos a una actualización están en `private/backups/`. No borres esa base ni `runs/` si quieres conservar tus candidaturas y búsquedas.

## Instalación y pruebas

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r tests/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

En una instalación nueva, copia `config.example.json` a `config.json` y `ranking.example.json` a `private/ranking.json`, y calibra las preferencias con tu propio perfil. Las plantillas no contienen datos personales.

Las pruebas no hacen una búsqueda real en portales. Para la calibración local, etiqueta ejemplos y comparaciones en `private/calibration/labels.json` y ejecuta `python scripts/calibrate_ranking.py ID_DE_CAPTURA`. No compartas ese directorio: contiene anuncios y decisiones personales.

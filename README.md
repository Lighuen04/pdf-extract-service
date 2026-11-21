# PDF Extract Service

Microservicio independiente derivado de `services/extract` de [pdf-extractext](https://github.com/Gianluca-gitea/pdf-extractext/tree/0123464/services/extract). Extrae texto y Markdown con PyMuPDF, infiere encabezados por tamaño de fuente y ofrece OCR opcional con Tesseract.

El sistema tiene cuatro repositorios. **Documents** almacena y recupera PDFs. **ExtractServices** procesa el PDF recibido y devuelve su texto. El **orquestador** coordina los servicios y Traefik. La **GUI** presenta los resultados.

## Ejecutar

Desde la raíz del repositorio:

```sh
docker build -t pdf-extract-service .
docker run --rm --init --name pdf-extract -p 8000:8000 pdf-extract-service
```

Para personalizar el contenedor, agregar `--env-file .env` antes del nombre de la imagen. El Dockerfile instala los idiomas inglés y español, ejecuta sin privilegios y comprueba `/health/live` con la biblioteca estándar. El Compose de despliegue y los límites de infraestructura pertenecen al orquestador. Si cambia `PORT`, adaptar el mapeo del puerto del contenedor.

Con Python 3.14 y uv:

```sh
uv sync --locked
uv run --locked python -m app.server
uv run --locked python -m pytest --cov=app --cov-fail-under=90
```

El OCR local requiere Tesseract y los datos de los idiomas elegidos. `.env.example` documenta las variables de tamaño, capacidad, timeouts, OCR y logging. El OCR se desactiva por defecto y los idiomas admiten comas o `+`. Los logs salen por stdout en texto o JSON.

## API

| Ruta | Comportamiento |
| --- | --- |
| `POST /extract` | Markdown con los encabezados y saltos de línea de los párrafos. |
| `POST /extract/text` | Texto plano en orden de lectura. |
| `GET /health` y `GET /health/live` | Devuelven `{"status":"ok"}`. |
| `GET /health/ready` | Informa capacidad y cierre. Devuelve 503 cuando no acepta nuevos trabajos. |

Los POST aceptan un cuerpo `application/pdf` o un formulario multipart con campo `file`. Devuelven `{"content":"…","page_count":1}`.

```sh
curl -fsS -H 'Content-Type: application/pdf' --data-binary '@documento.pdf' http://localhost:8000/extract/text
curl -fsS -F 'file=@documento.pdf' http://localhost:8000/extract
```

En PowerShell, usar `curl.exe`. Un archivo vacío, ilegible o corrupto devuelve 400. El límite por PDF devuelve 413 y se aplica durante la lectura, incluso sin `Content-Length`. Multipart usa archivos temporales con spool y admite hasta 1 MiB adicional para el cuerpo completo. Si falla el OCR y queda texto recuperado, ese texto se devuelve. Si el fallo impide recuperar texto, el servicio responde 500.

## Organización y resiliencia

`main.py`, `pdf_service.py`, `settings.py` y `logging_config.py` recuperan la organización del servicio original. `uploads.py` encapsula la lectura HTTP acotada, `extraction_pool.py` administra el trabajo y `server.py` configura Uvicorn.

- **Concurrencia y saturaci?n.** Las dos rutas comparten un pool que FastAPI abre y cierra en el lifespan. El límite cuenta cargas en curso, solicitudes en cola y extracciones activas. La saturación devuelve 503 con `Retry-After: 1` antes de recibir otro PDF.
- **Timeout y aislamiento.** El 504 vence la espera HTTP. Un trabajo en cola se retira. Uno activo conserva su cupo hasta terminar. Una extracción fallida deja el pool disponible para las siguientes, salvo que el executor de procesos quede inutilizable. Se usa un hilo dedicado por defecto y procesos al habilitar varios workers, porque PyMuPDF no admite uso concurrente desde varios hilos.
- **Apagado y health checks.** Uvicorn drena las solicitudes. El lifespan rechaza nuevas extracciones, despierta las que esperan y aguarda las activas hasta el plazo configurado. Un hilo bloqueado puede sobrevivir al plazo del lifespan, por lo que el supervisor debe aplicar un límite externo. Liveness sigue respondiendo durante la saturación y readiness indica que se agotó la capacidad.
- **Gateway y orquestador.** Gestionan rate limiting, límites y plazos de subida, reintentos con backoff, circuit breaker y coordinación distribuida. Un 504 puede dejar un trabajo en ejecución y debe tenerse en cuenta antes de reintentar.

La división entre HTTP, procesamiento y ciclo de vida del pool aplica responsabilidad única y mantiene explícitas las dependencias. Texto y Markdown comparten apertura, validación y recorrido de bloques. Un único pool concreto, funciones reutilizadas y módulos planos evitan abstracciones que no aportan al servicio. Los límites de bytes controlan la carga comprimida. Dimensionar tamaño, cupos y workers en función de la RAM, porque un PDF puede expandirse al procesarlo.

## Twelve-Factor App

| Factor | Aplicación |
| --- | --- |
| I. Código base | Un repositorio Git para construir el servicio. |
| II. Dependencias | Paquetes Python fijados por `uv.lock` y dependencias del sistema declaradas en el Dockerfile. |
| III. Configuración | Variables de entorno y `.env.example` para desarrollo. |
| IV. Servicios de respaldo | La extracción solo requiere bibliotecas y Tesseract local. |
| V. Build, release, run | La imagen se construye una vez y su configuración llega al ejecutarla. |
| VI. Procesos | Capacidad y archivos temporales por instancia, sin persistencia de documentos. |
| VII. Puerto | HTTP en `PORT`, por defecto 8000. |
| VIII. Concurrencia | Cupos por proceso y escalado horizontal desde el orquestador. |
| IX. Desechabilidad | Drenaje con plazo y terminación externa para trabajos nativos bloqueados. |
| X. Paridad dev/prod | Mismo lock y comando de inicio. CI prueba la imagen final. |
| XI. Logs | Eventos en stdout para su recolección. |
| XII. Tareas administrativas | Pruebas y diagnósticos como comandos separados. El servicio carece de migraciones. |

## Verificación

En Windows, 64 pruebas pasaron con 95,32 % de cobertura. Se omitió la prueba de OCR bilingüe porque la instalación local carecía de español. mypy y Flake8 pasaron. Una imagen construida y ejecutada con Docker quedó healthy y ambas rutas de extracción devolvieron texto de un PDF de imagen usando OCR con inglés y español.
- **Pruebas.** La l?nea base pas? 64 pruebas con 95,32 % de cobertura. Con el lock actualizado pasaron 65 con 96,01 % de cobertura. mypy y Flake8 pasaron. La prueba local de OCR biling?e se omiti? porque Windows carec?a de espa?ol. En Docker, el usuario sin privilegios, las tres rutas de salud y las dos rutas de extracci?n se verificaron con dos workers y un PDF de imagen de dos p?ginas con OCR `eng+spa`.

### Imagen auditada el 20 de noviembre de 2026

- **Correcciones.** El Dockerfile instala el `zlib1g` corregido de Debian y retira `pip` y `ensurepip` de la etapa final. La imagen anterior reportaba dos avisos de zlib y ocho hallazgos en Python relacionados con herramientas de instalaci?n. El reescaneo con Trivy 0.76.0 report? cero avisos Python, secretos detectados o hallazgos de configuraci?n. [Aviso de Debian para zlib](https://security-tracker.debian.org/tracker/CVE-2026-85091)
- **Pendientes.** Trivy report? 506 hallazgos de paquetes Debian, incluidos 133 altos y 1 cr?tico, sin versi?n corregida indicada para esa imagen. El cr?tico es `CVE-2026-6653` en `libxml2`. Debian mantiene Trixie como vulnerable y clasifica el caso como menor, sin actualizaci?n de seguridad para esa versi?n. El servicio conserva la base estable. [Debian Security Tracker](https://security-tracker.debian.org/tracker/CVE-2026-6653)

# PDF Extract Service

Microservicio independiente derivado de [`services/extract` de pdf-extractext](https://github.com/Gianluca-gitea/pdf-extractext/tree/0123464/services/extract). Extrae texto plano o Markdown de PDFs con PyMuPDF y ofrece OCR opcional con Tesseract. En el sistema Polyrepo, Documents almacena los PDF, el orquestador coordina los servicios, ExtractServices procesa los documentos y la GUI presenta los resultados. El despliegue centralizado y Traefik pertenecen al orquestador.

## Ejecución

Desde la raíz de este repositorio:

```sh
docker build -t pdf-extract-service .
docker run --rm --init --name pdf-extract -p 8000:8000 pdf-extract-service
```

Para desarrollo, con Python 3.14 y uv:

```sh
uv sync --locked
uv run --locked python -m app.server
```

El OCR está desactivado por defecto. `.env.example` describe las variables disponibles; si cambia `PORT`, hay que adaptar el puerto publicado. El contenedor instala Tesseract en inglés y español, corre sin privilegios y registra en stdout.

La licencia del extractor es AGPL-3.0-only, conforme a la licencia del proyecto fuente; el texto está en [`LICENSE`](./LICENSE).

## API

| Ruta | Resultado |
| --- | --- |
| `POST /extract` | Markdown con detección de encabezados. |
| `POST /extract/text` | Texto plano en orden de lectura. |
| `GET /health` | Compatibilidad: `{"status":"ok"}`. |
| `GET /health/live` | Liveness. |
| `GET /health/ready` | Capacidad y cierre; responde 503 si no admite trabajos. |

Las rutas POST aceptan `application/pdf` binario o multipart con el campo `file`; ambas devuelven `{"content":"…","page_count":1}`. El límite por PDF es 10 MiB. Por ejemplo:

```sh
curl -fsS -H 'Content-Type: application/pdf' --data-binary '@documento.pdf' http://localhost:8000/extract/text
curl -fsS -F 'file=@documento.pdf' http://localhost:8000/extract
```

Markdown infiere los encabezados de cada bloque comparando su mayor tamaño de fuente con el tamaño predominante del documento. Separa los bloques como párrafos y conserva los saltos de línea del cuerpo. Las fuentes mixtas pueden producir encabezados imprecisos y la conversión no reconstruye tablas ni diseños complejos.

Con OCR habilitado, un fallo en un bloque de imagen se registra y se omite ese bloque. Si se obtuvo texto nativo o texto de otras imágenes, la API devuelve HTTP 200 con el contenido disponible, que puede ser parcial. Si hubo un fallo de OCR y no se obtuvo texto, devuelve HTTP 500 con un mensaje genérico. Un documento sin texto ni fallos devuelve contenido vacío.

## Resiliencia e integración

Los cuatro patrones del proyecto se asignan fuera del extractor:

| Patrón | Responsable |
| --- | --- |
| Retry | Orquestador |
| Circuit Breaker | Orquestador |
| Rate Limit | Traefik |
| Cache | Orquestador |

El extractor devuelve HTTP 503 al saturarse y ofrece health checks y apagado controlado. `APP_MAX_PENDING_EXTRACTIONS` vale 100 por defecto y limita, por proceso de API, la suma de las solicitudes en recepción, las que esperan un worker y las extracciones activas. `APP_EXTRACT_WORKERS` vale 1 por defecto y limita cuántas extracciones pueden ejecutarse a la vez.

`APP_UPLOAD_TIMEOUT_SECONDS` limita la recepción del cuerpo a 30 segundos por defecto. Si vence, la API devuelve HTTP 408 y libera el cupo. `APP_EXTRACTION_TIMEOUT_SECONDS` vale 30 segundos por defecto y empieza después de la recepción, incluyendo la espera por un worker. Si vence, la API devuelve HTTP 504. Un trabajo nativo que ya empezó conserva su cupo hasta que termina.

## Diseño y seguridad

La aplicación mantiene módulos planos, estado efímero, configuración por entorno y logs en stdout. Valida la firma y apertura del PDF, rechaza documentos cifrados o inválidos, limita el tamaño de lectura incluso sin `Content-Length` y restringe el multipart. Los workers y el OCR tienen límites de espera; los logs no incluyen el contenido del PDF. Docker ejecuta como usuario no-root; CI usa permisos mínimos, dependencias Python bloqueadas y escanea la imagen.

Los límites de bytes no garantizan un límite equivalente de memoria o CPU al procesar un PDF; dimensionar el tamaño y los workers según la memoria disponible. La autenticación y los límites globales pertenecen a la infraestructura/orquestador.

En la verificación local del 20 de noviembre de 2026, `pip-audit` no encontró vulnerabilidades conocidas en los paquetes Python bloqueados. Trivy 0.76.0 encontró 134 vulnerabilidades HIGH/CRITICAL en paquetes Debian de la imagen (133 HIGH y 1 CRITICAL), todas sin versión corregida indicada en ese escaneo; no se detectaron vulnerabilidades corregibles de esas severidades. El hallazgo crítico `CVE-2026-6653` afecta a `libxml2`, que está enlazado por Tesseract; el extractor entrega imágenes extraídas del PDF al OCR y no se identificó una ruta de entrada XML específica. Esto no demuestra que los hallazgos sean inalcanzables: se mantienen como riesgo abierto del sistema base. CI conserva el informe completo de Trivy como artefacto y falla por hallazgos HIGH/CRITICAL corregibles.

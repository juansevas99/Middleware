# Lista de Tareas Pendientes - Middleware ICG / Facturación Electrónica

Este documento centraliza el roadmap, las tareas pendientes y el progreso del proyecto de acuerdo con la nueva arquitectura *Stateless* (con ICGFRONT como única fuente de verdad).

---

## Estado General del Proyecto

- [x] **Fase 1: Transición a Arquitectura Stateless (Eliminación de SQLite)**
- [x] **Fase 2: Implementación de Write-Back en ICG (`icg_db.py`)**
- [x] **Fase 3: Canal 1 - Emisión en Tiempo Real (Endpoint para Evento de Caja)**
- [x] **Fase 4: Canal 2 - Ciclo Periódico Unificado (Poller + Heartbeat Ligero)**
- [x] **Fase 5: Definición del Contrato de API y Mock Server con el Core Fiscal**
- [x] **Fase 6: Telemetría, Logs y Robustez Operativa**
- [ ] **Fase 7: Pruebas de Integración y Despliegue como Servicio Windows**
- [ ] **Fase 8: Asistente de Instalación y Diagnóstico (Setup Wizard)**

---

## 1. Transición a Arquitectura Stateless
> Retirar la base de datos local SQLite y basar toda la persistencia exclusivamente en ICGFRONT.

- [x] Desacoplar `local_db.py` y remover dependencias de SQLite.
- [x] Simplificar la consulta de facturas pendientes en `icg_db.py` basándose en `FE_CUFE IS NULL` y la serie fija de la caja.
- [x] Mover la estandarización tributaria al CORE (Middleware envía datos crudos de ICG, prescindiendo de `mapeo_dian.yaml` local).
- [x] Eliminar archivos y carpetas obsoletas (`cola_facturacion.db`, `local_db.py`, `mapper.py`, `mapeo_dian.yaml`, `src/models/`, `poller.py`).

---

## 2. Write-Back en ICG (`icg_db.py`)
> Implementar los métodos de escritura directa en `FACTURASVENTACAMPOSLIBRES` según los scripts SQL de referencia.

- [x] Implementar método `escribir_cufe_icg(serie, numfactura, n, cufe, qr, estado=30)`:
  - Lógica UPSERT (insertar si no existe, actualizar si ya existe).
  - Asentar `FE_CUFE`, `FE_QR`, `FE_ESTADO = 30`, `FE_PROC = 5` y fechas de estado y validación.
- [x] Implementar método `escribir_error_icg(serie, numfactura, n, error_mensaje, estado=40)`:
  - Lógica UPSERT para registrar rechazo o error en `FE_ESTADO = 40`, `FE_PROC = 2` y `FE_RESPONSEERROR`.

---

## 3. Canal 1: Emisión en Tiempo Real (Evento de Caja)
> Permitir que Front Retail dispare la emisión inmediata y reciba el CUDE antes de imprimir el tiquete.

- [x] Crear servidor HTTP local multihilo en `src/api/server.py`.
- [x] Endpoint `POST /api/v1/eventos/venta` (recibe `{ numserie, numfactura, n }`).
- [x] Flujo síncrono:
  - Extracción inmediata en memoria desde ICG (`extraer_factura_completa`).
  - Envío al Core con timeout estricto de caja (`timeout_caja_segundos: 2.5`).
  - Si responde OK: Write-Back a ICG y responder `ELECTRONICA` a Front Retail con CUDE y QR.
- [x] Contingencia en caliente:
  - Si el Core tarda o falla: responder `CONTINGENCIA` y `TIQUETE_SIMPLE` a Front Retail.
  - La factura queda en ICG sin CUFE para ser rescatada por el ciclo de fondo.
- [x] Health check en `GET /health`.

---

## 4. Canal 2: Ciclo Periódico Unificado (Poller + Heartbeat)
> Sondeo de fondo que barre facturas sin CUFE y reconcilia con el Core.

- [x] Implementar `SyncWorker` en `src/services/sync_worker.py`:
  - **Paso 4.1 (Heartbeat Ligero):** Consulta al Core por llaves (`POST /v1/reconciliacion`).
    - *Aprobada:* Write-Back en ICG con CUFE/QR.
    - *En trámite DIAN:* Se mantiene a la espera sin acción.
    - *Rechazada:* Asienta error en ICG (`FE_ESTADO = 40`).
  - **Paso 4.2 (Push Contingencias):** Para facturas reportadas como `NO_EXISTE` en el Core:
    - Extrae detalle completo de ICG.
    - Emite documento al Core (`POST /v1/emision`).
    - Aplica Write-Back del resultado.
- [x] Orquestador unificado en `src/main.py` corriendo el Worker en hilo demonio.

---

## 5. Contrato de API y Mock Server con el Core Fiscal
> Formalizar los endpoints y crear un simulador local para pruebas sin internet.

- [x] Crear un Mock Server local en Python (`mock_core/mock_core_server.py`) para simular el Core Fiscal:
  - Simular respuesta inmediata con CUDE (Happy Path).
  - Simular latencia / timeout (`modo: TIMEOUT`).
  - Simular rechazo DIAN (`modo: RECHAZO`, HTTP 400).
  - Simular endpoint de reconciliación (`POST /v1/reconciliacion`).
- [x] Apuntar `config/config.yaml` al simulador local (`http://127.0.0.1:8090/v1`).
- [ ] Documentar especificación OpenAPI/JSON Schema del payload que espera el Core.

---

## 6. Logs, Rendimiento y Mantenimiento
- [x] Configurar `RotatingFileHandler` en `src/main.py` (máx 10MB con 5 backups).
- [x] Limpiar `config/config.yaml` agrupando identidad, API local, BD ICG, Core y Ciclo.

---

## 7. Pruebas y Despliegue
- [x] Banco de pruebas automatizadas (`tests/test_01_db.py`, `test_02_api.py`, `test_03_worker.py`, `test_04_estructural.py` y `run_all_tests.py` - 20/20 casos aprobados).
- [ ] Script de empaquetado (`PyInstaller`) para compilar a ejecutable Windows.
- [ ] Guía/Script para instalar como Servicio de Windows con NSSM.

---

## 8. Asistente de Instalación y Diagnóstico (Setup Wizard)
> Herramienta interactiva de despliegue para técnicos en tienda, asegurando que cada caja quede 100% operativa antes de iniciar el servicio.

- [ ] **Módulo CLI Interactivo / GUI ligera:**
  - Preguntar parámetros básicos si no existen (tienda, caja, serie, servidor SQL, credenciales, API key del Core).
- [ ] **Checklist de Verificación Automática (Pre-Flight Checks):**
  - **Check BD SQL Server:** Valida autenticación y que la base de datos indicada (`ICGFRONT` o `RAMSAS`) exista y responda.
  - **Check Estructura de Tablas:** Valida que existan las tablas indispensables (`FACTURASVENTA`, `FACTURASVENTACAMPOSLIBRES`, `SERIESRESOLUCION`, `CLIENTES`, `ALBVENTALIN`, `ALBVENTAPAG`).
  - **Check Serie y Resolución:** Comprueba que la serie configurada (`serie_caja`) exista en `SERIESRESOLUCION` y tenga `ACTIVO = 1`.
  - **Check Identidad y Ubicación:** Valida que `id_tienda` e `id_terminal` estén diligenciados y sean consistentes.
  - **Check Conexión Core Fiscal:** Realiza un ping HTTP al Core Fiscal validando que la `api_key` sea aceptada.
  - **Check Puerto Local:** Verifica que el puerto `8080` esté libre para el API de Front Retail.
- [ ] **Generación y Guardado de Configuración:**
  - Genera o actualiza `config/config.yaml` solo cuando todos los checks pasen en verde.
- [ ] **Instalación como Servicio de Windows:**
  - Opción de registrar y levantar el servicio en Windows automáticamente al finalizar el asistente.

---

## 9. Documento Soporte en Adquisiciones a No Obligados (DS)
> Soporte para compras y gastos realizados a proveedores que no tienen obligación de facturar electrónicamente.
> **Diseño:** Solo polling (sin evento en tiempo real). Ver [diagrama_secuencia_documento_soporte.md](sources/diagrama_secuencia_documento_soporte.md).

- [x] Definir flujo y diagrama de secuencia (polling + Heartbeat + Push, igual al ciclo de ventas).
- [x] Verificar tablas en ICG: `FACTURASCOMPRA`, `FACTURASCOMPRACAMPOSLIBRES` (sin `FE_PROC`), `FACTURASCOMPRASERIESRESOL`, `FACTURASCOMPRATOT`, `FACTURASCOMPRARET`, `ALBCOMPRACAB/LIN`.
- [x] Agregar sección `ciclos_sincronizacion.documento_soporte` en `config.yaml` (habilitado, `prefijos_fiscales`, `series_icg`, intervalo, ventana, límite).
- [x] **Mapeo y Extracción en ICG (`icg_db.py`):**
  - DS identificado por **prefijo fiscal** (`FACTURASCOMPRASERIESRESOL.SERIEFISCAL1`: `DEQ1`, `DEQ2`, `DEC`, `GFDA`, `GFDS`), no por `NUMSERIE`.
  - `obtener_documentos_soporte_pendientes()` y `extraer_documento_soporte_completo()` (cabecera, proveedor, líneas, impuestos, retenciones) en `dict`/`list` nativos.
- [x] **Envío al Core Fiscal (`client_core.py`):**
  - `enviar_documento_soporte()` → `POST /v1/documento-soporte` y `reconciliar_llaves_ds()` → `POST /v1/documento-soporte/reconciliacion`.
  - Numeración autorizada (prefijo, resolución, número fiscal) incluida en la cabecera; la clave técnica la gestiona el Core.
- [x] **Write-Back de CUDS:** `escribir_cuds_icg()` y `escribir_error_ds_icg()` en `FACTURASCOMPRACAMPOSLIBRES` (sin `FE_PROC`).
- [x] **Worker independiente:** `src/services/doc_soporte_worker.py` en hilo propio (`threading.Event`), orquestado desde `main.py`.
- [x] **Pruebas:** `tests/test_05_doc_soporte.py` (9 casos) — suite total 29/29.
- [ ] Validar con contabilidad la lista definitiva de prefijos fiscales DS y el plazo normativo de transmisión.

---

## 10. Recepción de Facturas de Compra y Eventos RADIAN (Estampado Ligero de CUFE)
> Consulta de facturas de proveedores y eventos RADIAN gestionados en el CORE, y estampado ligero del CUFE en ICG para habilitar su contabilización nativa.

- [ ] **En el CORE Fiscal (Cerebro Central y Visualizador):**
  - Consulta y descarga automática de facturas emitidas por proveedores al NIT de la empresa ante la DIAN.
  - Visualizador Web para que el usuario consulte facturas recibidas y dispare los 3 eventos RADIAN (Acuse de recibo, Recibo de bienes/servicios y Aceptación expresa).
  - Poner a disposición del Middleware las facturas con eventos completos (`GET /v1/compras/cufes-aprobados`).
- [ ] **En el Middleware (Pulling Ligero - Dumb Middleware):**
  - Subciclo periódico que consulta al Core la lista de CUFEs aprobados por proveedor/factura.
  - **Cero cálculos y cero contabilizaciones:** No crea albaranes ni altera inventarios.
  - Método en `icg_db.py`: `escribir_cufe_compra_icg(nit_proveedor, num_factura, cufe, estado_radian)`.
  - Actualiza el `CUFE` y estado en los campos libres de compras en ICG (`COMPRAS` / `FACTURASCOMPRA`), permitiendo que el ERP habilite su contabilización legal.
  - Notifica al Core que el CUFE fue estampado exitosamente para cerrar el ciclo.

---
*Última actualización: 2026-11-09*

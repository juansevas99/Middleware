# Registro y Evidencia de Casos de Prueba - Middleware ICG

Este documento mantiene el registro histórico, la especificación y el estado de ejecución de cada caso de prueba (unitario, funcional y estructural) del Middleware.

---

## 1. Tablero de Control de Pruebas

| Métrica | Valor |
| :--- | :--- |
| **Total Casos Definidos** | 20 |
| **✅ Aprobados (PASSED)** | **20 (100%)** |
| **⏳ Pendientes (PENDING)** | 0 |
| **❌ Fallidos (FAILED)** | 0 |
| **Última Ejecución** | 2026-11-06 10:25:56 |
| **Comando Maestro** | `.\.venv\Scripts\python.exe run_all_tests.py` |
| **Entorno de Pruebas** | Windows - Python 3.15.2 (.venv) + Mock Core Fiscal (`127.0.0.1:8090`) |
| **Motor de Base de Datos** | Microsoft SQL Server 2025 (RTM) - 17.0.1000.7 |
| **Instancia / Base de Datos** | `SVRPREMIUMLAB\ICGMNGER` / `RAMSAS` (Serie activa: `BC3S`) |

---

## 2. Bloque 1: Base de Datos y Persistencia (`tests/test_01_db.py`)

| ID | Caso de Prueba | Tiempo | Estado | Evidencia |
| :--- | :--- | :---: | :---: | :--- |
| **UT-DB-01** | Conectividad y Handshake ODBC | `2.1ms` | ✅ **PASSED** | Conexión física establecida contra `Microsoft SQL Server 2025 (RTM)`. |
| **UT-DB-02** | Verificación de 8 Tablas Indispensables | `5.8ms` | ✅ **PASSED** | Las 8 tablas existen en `[RAMSAS].dbo`. |
| **UT-DB-03** | Parametrización Dinámica de `db_name` | `3.9ms` | ✅ **PASSED** | Cero ocurrencias de `ICGFRONT.dbo.` quemadas en `icg_db.py`. |
| **UT-DB-04** | Validación de Serie y Resolución Activa | `1.2ms` | ✅ **PASSED** | Serie `BC3S` encontrada en `SERIESRESOLUCION` con `ACTIVO = 1`. |
| **UT-DB-05** | Extracción Completa de Factura en Memoria | `266.6ms` | ✅ **PASSED** | `extraer_factura_completa` retorna `identidad`, `cabecera`, `cliente`, `lineas`, `impuestos` y `pagos`. |
| **UT-DB-06** | Write-Back de Éxito (UPSERT: INSERT + UPDATE) | `52.8ms` | ✅ **PASSED** | Inserta cuando no existe y actualiza sin duplicar fila (`FE_ESTADO = 30`, `FE_PROC = 5`). |
| **UT-DB-07** | Write-Back de Error y Truncado Seguro | `9.5ms` | ✅ **PASSED** | Guarda `FE_ESTADO = 40`, `FE_PROC = 2` y trunca mensajes > 4000 chars a 3900 sin error 8152. |

---

## 3. Bloque 2: API REST Local de Caja (`tests/test_02_api.py`)

| ID | Caso de Prueba | Tiempo | Estado | Evidencia |
| :--- | :--- | :---: | :---: | :--- |
| **UT-API-01** | Health Check del Servicio (`GET /health`) | `3.4ms` | ✅ **PASSED** | Responde `HTTP 200` con `{"status": "UP", "caja": "CAJA_01"}`. |
| **UT-API-02** | Validación de Payload de Entrada | `12.4ms` | ✅ **PASSED** | `POST /api/v1/eventos/venta` con `{}` responde `HTTP 400` (`ERROR_DATOS`). |
| **UT-API-03** | Emisión en Tiempo Real Exitosa (Happy Path) | `16.7ms` | ✅ **PASSED** | Responde `OK`, `accion_impresion: "ELECTRONICA"`, CUDE SHA-384 (96 hex) y Write-Back en ICG. |
| **UT-API-04** | Contingencia en Caliente por Timeout (> 2.5s) | `2528.1ms` | ✅ **PASSED** | Ante Core con retardo de 4.0s, corta a los 2.5s y responde `CONTINGENCIA` / `TIQUETE_SIMPLE`. |
| **UT-API-05** | Contingencia por Sin Conexión (`ConnectionRefused`) | `2018.6ms` | ✅ **PASSED** | Ante Core inalcanzable, responde `CONTINGENCIA` sin caer el servidor API. |

---

## 4. Bloque 3: Ciclo Periódico Unificado (`tests/test_03_worker.py`)

| ID | Caso de Prueba | Tiempo | Estado | Evidencia |
| :--- | :--- | :---: | :---: | :--- |
| **UT-WK-01** | Sondeo en Bandeja Limpia | `0.1ms` | ✅ **PASSED** | Con 0 pendientes en ICG, hace 0 llamadas HTTP al Core. |
| **UT-WK-02** | Reconciliación Heartbeat - Subcaso A (`APROBADA`) | `17.6ms` | ✅ **PASSED** | Recupera CUFE por Heartbeat y aplica Write-Back con 0 llamadas a `/v1/emision`. |
| **UT-WK-03** | Reconciliación Heartbeat - Subcaso B (`EN_PROCESO`) | `25.2ms` | ✅ **PASSED** | No toca ICG ni reenvía factura mientras sigue en trámite DIAN. |
| **UT-WK-04** | Reconciliación Heartbeat - Subcaso C (`RECHAZADA`) | `25.9ms` | ✅ **PASSED** | Registra `FE_ESTADO = 40` y mensaje de regla DIAN en `FE_RESPONSEERROR`. |
| **UT-WK-05** | Push de Contingencia - Subcaso D (`NO_EXISTE`) | `17.2ms` | ✅ **PASSED** | Extrae factura completa, la emite en `/v1/emision` y asienta el CUDE en ICG. |

---

## 5. Bloque 4: Pruebas Estructurales y Resiliencia (`tests/test_04_estructural.py`)

| ID | Caso de Prueba | Tiempo | Estado | Evidencia |
| :--- | :--- | :---: | :---: | :--- |
| **TC-EST-01** | Concurrencia Caja vs Worker | `247.1ms` | ✅ **PASSED** | 5 cobros simultáneos en Caja respondieron en < 250ms mientras el Worker procesaba 15 facturas en lote; las 20 quedaron con CUFE. |
| **TC-EST-02** | Idempotencia en Write-Back y Core | `59.0ms` | ✅ **PASSED** | 3 disparos repetidos para la misma factura generaron 1 sola emisión DIAN, el mismo CUDE y 1 sola fila en ICG. |
| **TC-EST-03** | Ciclo Completo de Vida de Contingencia | `2036.0ms` | ✅ **PASSED** | Venta con Core caído -> `TIQUETE_SIMPLE` -> Regresa Core -> Worker recupera y asienta CUFE automáticamente. |

---

## 6. Bitácora de Ejecuciones

| Fecha / Hora | Módulo Evaluado | Script / Comando | Resultado | Notas |
| :--- | :--- | :--- | :--- | :--- |
| **2026-11-05 23:05** | Base de Datos | `python test_db_connection.py` | ❌ FAILED | Error 18456 (Login fallido para 'Middleware' por contraseña vacía). |
| **2026-11-05 23:13** | Base de Datos | `python test_db_connection.py` | ✅ PASSED | Conexión exitosa a `SVRPREMIUMLAB\ICGMNGER` (`RAMSAS`). 8/8 tablas verificadas. |
| **2026-11-06 10:25** | Suite Completa (4 Bloques) | `.\.venv\Scripts\python.exe run_all_tests.py` | ✅ **PASSED (20/20)** | Todos los casos unitarios, funcionales y estructurales aprobados en 16.62s. |

---
*Documento administrado activamente por el equipo de ingeniería.*

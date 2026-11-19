# Diagrama de Secuencia: Emisión desde Caja y Sincronización

Este documento describe la interacción entre la **Caja (Front Retail + ICG Front DB)**, el **Middleware (Stateless)** y el **Core Fiscal**, cubriendo tanto la emisión sincrónica por evento como el ciclo unificado de contingencias y sincronización (Poller + Heartbeat).

```mermaid
sequenceDiagram
    autonumber

    box rgb(225, 238, 255) CAJA (PUESTO POS)
        participant FR as Front Retail
        participant ICGDB as ICG Front DB (SQL Server)
    end
    box rgb(225, 250, 240) MIDDLEWARE LOCAL (STATELESS)
        participant APP as App Middleware
    end
    box rgb(240, 230, 255) CORE FISCAL & DIAN
        participant CORE as Core Fiscal (Nube)
        participant DIAN as DIAN
    end

    %% =========================================================================
    %% FLUJO 1: EMISIÓN EN TIEMPO REAL (POR EVENTO DE CAJA)
    %% =========================================================================
    rect rgb(245, 248, 255)
    Note over FR, DIAN: FLUJO 1: Emisión en Tiempo Real (Evento de Venta en Caja)
    FR->>FR: Clic de venta / Cobro
    FR->>ICGDB: Registra la venta nativa en FACTURASVENTA
    FR->>APP: Dispara Evento de Venta (ID: Serie + Factura)

    APP->>ICGDB: Extraer datos crudos (Cabecera, Cliente, Líneas, Pagos, Impuestos)
    ICGDB-->>APP: Retorna tuplas de venta
    APP->>APP: Mapea a DTO en memoria
    APP->>CORE: POST /v1/emision (Payload Venta)

    alt Caso 1.1: Éxito en Línea (Sin Contingencia)
        CORE->>DIAN: Transmisión UBL 2.1 firmada
        DIAN-->>CORE: Aprobación DIAN (CUDE generado)
        CORE-->>APP: HTTP 200 (CUDE + QR + Estado Aprobado)
        APP->>ICGDB: Write-Back a FACTURASVENTACAMPOSLIBRES (FE_CUFE, FE_QR, FE_ESTADO = 30)
        APP-->>FR: Respuesta OK (Listo para impresión)
        FR->>ICGDB: Lee CUDE y QR
        FR->>FR: Imprime tiquete electrónico completo (con CUDE y QR)

    else Caso 1.2: Contingencia en Caliente (Timeout o Sin Internet)
        CORE--xAPP: Timeout de red / Conexión rechazada
        APP-->>FR: Aviso de contingencia / Sin respuesta
        FR->>FR: Imprime tiquete tradicional (Contingencia sin CUDE ni QR)
        Note over ICGDB: La factura permanece en ICGFRONT con FE_CUFE NULL
    end
    end

    %% =========================================================================
    %% FLUJO 2: CICLO UNIFICADO DE CONTINGENCIAS Y HEARTBEAT (PERIÓDICO)
    %% =========================================================================
    rect rgb(240, 255, 245)
    Note over APP, DIAN: FLUJO 2: Ciclo Periódico Unificado (Poller + Heartbeat cada N segundos)
    
    APP->>ICGDB: Consulta facturas sin CUFE (FE_CUFE IS NULL)
    ICGDB-->>APP: Lista de facturas pendientes

    opt Hay facturas sin CUFE en ICG
        Note over APP, CORE: Paso 2.1: Heartbeat Ligero (Consulta por Llaves)
        APP->>CORE: POST /v1/reconciliacion { llaves: ["SETT-101", "SETT-102", ...] }
        CORE-->>APP: Mapa de estados { "SETT-101": APROBADA, "SETT-102": EN_PROCESO, "SETT-103": NO_EXISTE }

        opt Subcaso A: La factura ya fue aprobada en CORE (Respuesta pendiente)
            Note over APP, ICGDB: Factura SETT-101: CORE ya tenía el CUDE
            APP->>ICGDB: Write-Back (FE_CUFE, FE_QR, FE_ESTADO = 30)
        end

        opt Subcaso B: La factura está en trámite en la DIAN
            Note over APP, CORE: Factura SETT-102: Sigue en proceso en la DIAN
            APP->>APP: No realiza acción (espera al siguiente ciclo)
        end

        opt Subcaso C: Rechazo definitivo por parte de la DIAN
            Note over APP, ICGDB: Si CORE reporta RECHAZO
            APP->>ICGDB: Write-Back de Error (FE_ESTADO = 40, FE_RESPONSEERROR = detalle)
        end

        opt Subcaso D: La factura nunca llegó al CORE (Contingencia Offline)
            Note over APP, CORE: Factura SETT-103: El CORE no la conoce (NO_EXISTE)
            APP->>ICGDB: Extraer detalle completo de la factura
            ICGDB-->>APP: Datos de cabecera, líneas, impuestos y pagos
            APP->>CORE: POST /v1/emision (Envío diferido del documento)
            CORE->>DIAN: Emisión UBL 2.1 ante la DIAN
            DIAN-->>CORE: Aprobación DIAN
            CORE-->>APP: HTTP 200 (CUDE + QR)
            APP->>ICGDB: Write-Back (FE_CUFE, FE_QR, FE_ESTADO = 30)
        end
    end
    end
```

---

## Actores y Responsabilidades

| Actor | Rol en el Ecosistema | Responsabilidad Principal |
| :--- | :--- | :--- |
| **Front Retail (FR)** | Punto de Venta (Caja) | Ejecuta el cobro, dispara el evento hacia el Middleware e imprime el tiquete (con o sin CUDE según la respuesta inmediata). |
| **ICG Front DB (`ICGFRONT`)** | Base de Datos Local | **Única fuente de verdad local**. Almacena transacciones de venta y recibe el Write-Back en `FACTURASVENTACAMPOSLIBRES` (`FE_CUFE`, `FE_QR`, `FE_ESTADO`). |
| **App Middleware** | Agente Local (*Stateless*) | Procesa el evento en línea, mapea a DTO en memoria, y ejecuta el ciclo periódico unificado (Heartbeat ligero + Push de contingencias). No posee base de datos intermedia. |
| **Core Fiscal (Nube)** | Motor Fiscal y Certificador | Centraliza la lógica de negocio, validaciones DIAN, firma digital, XML UBL 2.1, generación de CUDE/QR e idempotencia de documentos. |
| **DIAN** | Autoridad Tributaria | Valida y aprueba o rechaza los documentos electrónicos transmitidos por el Core Fiscal. |

---

## Escenarios Cubiertos

1. **Venta normal en línea (Happy Path):** Cobro -> Emisión sincrónica -> CUDE devuelto en < 2 segundos -> Tiquete impreso con QR y CUDE.
2. **Contingencia en el cobro:** Falla de enlace o lentitud -> La caja no se frena, imprime tiquete simple -> La factura queda en ICG para sincronización posterior.
3. **Pérdida de respuesta (ACK perdido):** El Core aprobó pero la caja no recibió el CUDE -> El Heartbeat ligero lo recupera en el siguiente ciclo sin reemitir.
4. **DIAN asíncrona / lenta:** El documento queda en trámite -> El Heartbeat consulta periódicamente sin reenviar payloads pesados hasta obtener la respuesta.
5. **Rechazo DIAN:** El Core reporta el error y el Middleware lo asienta directamente en `FE_RESPONSEERROR` para gestión en tienda.
6. **Recuperación tras corte total de internet:** Una vez restablecido el enlace, el ciclo unificado detecta las facturas como `NO_EXISTE` en el Core y las transmite secuencialmente.

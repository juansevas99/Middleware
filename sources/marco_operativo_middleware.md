# Marco Operativo y Gestión de Contingencias - Middleware ICG

Este documento define el marco de reglas operativas, responsabilidades y el comportamiento esperado del Middleware ante los escenarios de fallo más comunes en un entorno de retail (Cajas POS). 

El objetivo principal de este marco es garantizar que la operación de venta al público en la tienda **nunca se detenga** ni se vea afectada por los procesos de facturación electrónica.

---

## 1. Idempotencia y Reintentos (Protección contra duplicidad)

**Escenario:** El Middleware envía el JSON de la factura al Core Fiscal. El Core lo procesa, la DIAN lo aprueba y el Core responde con el CUFE. Sin embargo, justo en ese milisegundo, la red de la tienda falla y el Middleware nunca recibe la respuesta (Timeout). 
**Comportamiento esperado:**
*   **Middleware:** Al no recibir respuesta, asume que el envío falló. La factura permanece en la cola local (`Cola_Facturacion`) con estado `PENDIENTE` y se reintentará en el siguiente ciclo.
*   **Core Fiscal (Responsable de Idempotencia):** Cuando el Middleware reintente enviar la misma factura, el Core **NO debe** volver a enviarla a la DIAN. El Core tiene la responsabilidad de revisar su propia base de datos buscando la llave `NUMSERIE + NUMFACTURA`. Al ver que ya existe y ya tiene CUFE, el Core simplemente responderá inmediatamente devolviendo el CUFE generado la primera vez (HTTP 200 OK).

## 2. Pérdida de Conexión con el Core Fiscal (Internet caído)

**Escenario:** Se cae el enlace de internet del supermercado o los servidores del Core Fiscal en la nube están en mantenimiento.
**Comportamiento esperado:**
*   **Middleware:** Intentará enviar el paquete y recibirá un error de conexión (`ConnectionRefused` o `DNS Resolution Error`). El Middleware captura la excepción, registra un error temporal en su log local y deja las facturas en estado `PENDIENTE` en la base SQLite.
*   **Caja (ICG):** Sigue imprimiendo tiquetes físicos en "Contingencia".
*   **Recuperación:** En el próximo ciclo de sondeo, el Middleware intentará de nuevo. Una vez vuelva el internet, el Middleware enviará todas las facturas represadas en ráfaga (respetando los límites de carga).

## 3. Pérdida de Conexión o Autorización con la Base de Datos Local (ICGFRONT)

**Escenario:** El servicio de SQL Server de la caja se detiene, cambia la contraseña del usuario `sa`, o el motor rechaza la conexión.
**Comportamiento esperado:**
*   **Middleware:** Al intentar ejecutar el ciclo de Polling y fallar la conexión ODBC (`pyodbc.Error`), el Middleware **no debe cerrarse ni detener su servicio de Windows**.
*   Debe atrapar el error de base de datos, escribir un log local de nivel **CRÍTICO** (ej. "Error de autenticación con SQL Server") y abortar el ciclo de extracción actual.
*   Dormirá durante el tiempo definido (ej. 30 segundos) y volverá a intentarlo en el siguiente ciclo. Esto garantiza que si SQL Server se reinicia, el Middleware se reconecte automáticamente sin intervención humana.

## 4. Timeout sin respuesta del Core Fiscal (Latencia)

**Escenario:** El Middleware envía el documento, pero el Core Fiscal tarda demasiado en responder (ej. la DIAN está muy lenta y el Core se queda esperando). El cliente HTTP del Middleware alcanza su límite de espera (ej. 15 segundos).
**Comportamiento esperado:**
*   **Middleware:** Corta la conexión (`TimeoutException`), marca el intento como fallido y deja la factura `PENDIENTE` en la cola para el próximo ciclo.
*   *Nota:* Este escenario se resuelve de forma segura gracias a la regla de **Idempotencia** (Punto 1).

## 5. Datos corruptos y Sanitización (Límite de Responsabilidades)

**Escenario:** Un producto en ICG tiene emojis (🍔), caracteres invisibles de tabulación, o un nombre de cliente con símbolos XML inválidos (`< > &`). 
**Comportamiento esperado:**
*   **Middleware (SIN RESPONSABILIDAD):** El Middleware es un "Router Ciego". Su responsabilidad es extraer el texto crudo de la base de datos exactamente como está en ICG y armar el JSON. **No debe** tener lógica compleja de limpieza de strings, expresiones regulares o validaciones de longitud.
*   **Core Fiscal (RESPONSABLE ABSOLUTO):** Toda la carga de limpieza, validación de esquema, validación de datos (ej. verificar que un NIT no tenga letras) y sanitización contra inyección XML es responsabilidad del Core en la nube.
*   **Resolución de fallos:** Si el Core determina que los datos rompen el XML o son inválidos para la DIAN, devolverá al Middleware una respuesta `400 Bad Request` indicando el error (ej. `"Caracteres inválidos en la descripción del ítem 3"`). El Middleware tomará ese error, actualizará su cola a `ERROR_CORE` y hará el *Write-Back* hacia ICG para que el administrador de la tienda lo lea en su pantalla y lo corrija.

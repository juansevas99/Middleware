-- ==============================================================================
-- SCRIPTS DE EXTRACCION Y WRITE-BACK PARA MIDDLEWARE ICGFRONT
-- Adaptado para Base de Datos de Caja (ICGFRONT)
-- ==============================================================================
-- IMPORTANTE: Todas las consultas de extraccion usan WITH (NOLOCK) 
-- para evitar bloqueos (deadlocks) en la caja registradora durante la operacion.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. DETECCION DE DOCUMENTOS PENDIENTES (POLLING)
-- Busca facturas sin CUFE o en estado pendiente/error inicial (0, 2).
-- ------------------------------------------------------------------------------
DECLARE @desde date = '2026-01-01';

SELECT TOP 100 
    f.NUMSERIE, f.NUMFACTURA, f.N, f.FECHA, f.TOTALNETO,
    f.TOTALIMPUESTOS, f.TIPODOC,
    c.FE_ESTADO, c.FE_PROC, c.FE_CUFE
FROM ICGFRONT.dbo.FACTURASVENTA f WITH (NOLOCK)
LEFT JOIN ICGFRONT.dbo.FACTURASVENTACAMPOSLIBRES c WITH (NOLOCK)
    ON f.NUMSERIE = c.NUMSERIE
    AND f.NUMFACTURA = c.NUMFACTURA
    AND f.N = c.N
INNER JOIN ICGFRONT.dbo.SERIESRESOLUCION sr WITH (NOLOCK)
    ON sr.SERIERESOL = f.NUMSERIE
    AND sr.ACTIVO = 1
WHERE (c.FE_CUFE IS NULL OR LTRIM(RTRIM(c.FE_CUFE)) = '')
    AND (c.FE_ESTADO IS NULL OR c.FE_ESTADO IN (0, 2))
    AND f.FECHA >= @desde
ORDER BY f.FECHA ASC;

-- ==============================================================================
-- 2. EXTRACCION DE DATOS DE LA FACTURA (PAYLOAD)
-- Para las siguientes consultas, el Middleware debe inyectar las variables
-- @serie, @numfac y @n identificadas en el paso anterior.
-- ==============================================================================
DECLARE @serie varchar(10) = 'SETT';
DECLARE @numfac int = 980001;
DECLARE @n char(1) = 'V';

-- ------------------------------------------------------------------------------
-- 2.1 Cabecera de la factura
-- ------------------------------------------------------------------------------
SELECT 
    NUMSERIE, NUMFACTURA, N, CODCLIENTE, FECHA, HORA,
    TOTALBRUTO, TOTALIMPUESTOS, TOTALNETO, TOTALRETENCION,
    TIPODOC, CODMONEDA, FACTORMONEDA, CODVENDEDOR
FROM ICGFRONT.dbo.FACTURASVENTA WITH (NOLOCK)
WHERE NUMSERIE = @serie
  AND NUMFACTURA = @numfac
  AND N = @n;

-- ------------------------------------------------------------------------------
-- 2.2 Datos del Adquiriente (Cliente)
-- Requiere inyectar el @codcliente obtenido en la cabecera (2.1)
-- ------------------------------------------------------------------------------
DECLARE @codcliente int = 1;

SELECT 
    CODCLIENTE, NIF20, CIF, NOMBRECLIENTE, NOMBRECOMERCIAL,
    DIRECCION1, CODPOSTAL, POBLACION, PROVINCIA, PAIS,
    E_MAIL, TELEFONO1, REGIMFACT, PERSONAJURIDICA, TIPODOCIDENT
FROM ICGFRONT.dbo.CLIENTES WITH (NOLOCK)
WHERE CODCLIENTE = @codcliente;

-- ------------------------------------------------------------------------------
-- 2.3 Líneas de detalle (Productos vía Albarán)
-- ------------------------------------------------------------------------------
SELECT 
    l.NUMLIN, l.REFERENCIA, l.DESCRIPCION, l.UNIDADESTOTAL,
    l.PRECIO, l.DTO, l.TOTAL, l.TIPOIMPUESTO, l.IVA, l.REQ,
    l.TIPORETENCION, l.PORCRETENCION, l.CODARTICULO
FROM ICGFRONT.dbo.ALBVENTACAB a WITH (NOLOCK)
INNER JOIN ICGFRONT.dbo.ALBVENTALIN l WITH (NOLOCK)
    ON a.NUMSERIE = l.NUMSERIE
    AND a.NUMALBARAN = l.NUMALBARAN
    AND a.N = l.N
WHERE a.NUMSERIEFAC = @serie
  AND a.NUMFAC = @numfac
  AND a.NFAC = @n
ORDER BY l.NUMLIN;

-- ------------------------------------------------------------------------------
-- 2.4 Totales de Impuestos (Agrupados)
-- ------------------------------------------------------------------------------
SELECT 
    NUMLINEA, TIPOIVA, BASEIMPONIBLE, IVA, TOTIVA,
    REQ, TOTREQ, TOTAL
FROM ICGFRONT.dbo.FACTURASVENTATOT WITH (NOLOCK)
WHERE SERIE = @serie
  AND NUMERO = @numfac
  AND N = @n
ORDER BY NUMLINEA;

-- ------------------------------------------------------------------------------
-- 2.5 Formas de Pago
-- ------------------------------------------------------------------------------
SELECT 
    p.NUMLINEA, p.CODFORMAPAGO, p.CODMONEDA, p.IMPORTE
FROM ICGFRONT.dbo.ALBVENTACAB a WITH (NOLOCK)
INNER JOIN ICGFRONT.dbo.ALBVENTAPAG p WITH (NOLOCK)
    ON a.NUMSERIE = p.NUMSERIE
    AND a.NUMALBARAN = p.NUMALBARAN
    AND a.N = p.N
WHERE a.NUMSERIEFAC = @serie
  AND a.NUMFAC = @numfac
  AND a.NFAC = @n
ORDER BY p.NUMLINEA;

-- ------------------------------------------------------------------------------
-- 2.6 Datos de la Resolución DIAN (Prefijos y Rangos)
-- ------------------------------------------------------------------------------
SELECT 
    fs.NUMSERIE, fs.NUMFACTURA, fs.N,
    fs.SERIEFISCAL1, fs.SERIEFISCAL2, fs.NUMEROFISCAL
FROM ICGFRONT.dbo.FACTURASVENTASERIESRESOL fs WITH (NOLOCK)
WHERE fs.NUMSERIE = @serie
  AND fs.NUMFACTURA = @numfac
  AND fs.N = @n;

-- ==============================================================================
-- 3. WRITE-BACK (ACTUALIZACIÓN DE ESTADOS HACIA ICG)
-- Usa lógica UPSERT (MERGE) porque la tabla CAMPOSLIBRES podría no tener
-- la fila creada nativamente por ICG.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 3.1 Write-Back Exitoso (Guardar CUFE y QR)
-- ------------------------------------------------------------------------------
DECLARE @cufe nvarchar(100) = 'HASH_CUFE_DEVUELTO_POR_CORE';
DECLARE @qr nvarchar(500) = 'URL_DEL_QR_DEVUELTA_POR_CORE';

IF EXISTS (SELECT 1 FROM ICGFRONT.dbo.FACTURASVENTACAMPOSLIBRES WHERE NUMSERIE = @serie AND NUMFACTURA = @numfac AND N = @n)
BEGIN
    -- Si existe, actualizamos
    UPDATE ICGFRONT.dbo.FACTURASVENTACAMPOSLIBRES
    SET FE_CUFE = @cufe,
        FE_QR = @qr,
        FE_ESTADO = 30,
        FE_PROC = 5,
        FE_FECHAESTADO = GETDATE(),
        FE_FECHAVALIDACION = GETDATE(),
        FE_RESPONSEERROR = NULL
    WHERE NUMSERIE = @serie
      AND NUMFACTURA = @numfac
      AND N = @n;
END
ELSE
BEGIN
    -- Si no existe, la insertamos
    INSERT INTO ICGFRONT.dbo.FACTURASVENTACAMPOSLIBRES
        (NUMSERIE, NUMFACTURA, N, FE_CUFE, FE_QR, FE_ESTADO, FE_PROC, FE_FECHAESTADO, FE_RESPONSEERROR)
    VALUES
        (@serie, @numfac, @n, @cufe, @qr, 30, 5, GETDATE(), NULL);
END

-- ------------------------------------------------------------------------------
-- 3.2 Registrar Error (Rechazo DIAN o CORE)
-- ------------------------------------------------------------------------------
DECLARE @error_dian nvarchar(4000) = 'Rechazo: Mensaje de error de la DIAN o CORE';

IF EXISTS (SELECT 1 FROM ICGFRONT.dbo.FACTURASVENTACAMPOSLIBRES WHERE NUMSERIE = @serie AND NUMFACTURA = @numfac AND N = @n)
BEGIN
    -- Si existe, actualizamos estado a Error (40)
    UPDATE ICGFRONT.dbo.FACTURASVENTACAMPOSLIBRES
    SET FE_ESTADO = 40,
        FE_PROC = 2,
        FE_RESPONSEERROR = @error_dian,
        FE_FECHAESTADO = GETDATE()
    WHERE NUMSERIE = @serie
      AND NUMFACTURA = @numfac
      AND N = @n;
END
ELSE
BEGIN
    -- Si no existe, la insertamos con estado de Error (40)
    INSERT INTO ICGFRONT.dbo.FACTURASVENTACAMPOSLIBRES
        (NUMSERIE, NUMFACTURA, N, FE_ESTADO, FE_PROC, FE_FECHAESTADO, FE_RESPONSEERROR)
    VALUES
        (@serie, @numfac, @n, 40, 2, GETDATE(), @error_dian);
END

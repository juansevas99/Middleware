import logging
import pyodbc
from typing import List, Dict, Optional
from src.config_loader import settings

logger = logging.getLogger(__name__)

class ICGDatabase:
    def __init__(self):
        """
        Inicializa la conexión a SQL Server leyendo las credenciales desde config.yaml.
        Soporta bases de datos de Caja (ICGFRONT) o Centrales (RAMSAS/ICGManager).
        """
        db_conf = settings.get("database", {})
        self.db_name = db_conf.get("db_name", "ICGFRONT")
        trusted = str(db_conf.get("trusted_connection", "no")).lower()
        
        if trusted in ["yes", "true", "1"]:
            self.dsn = (
                f"DRIVER={db_conf.get('driver')};"
                f"SERVER={db_conf.get('server')};"
                f"DATABASE={self.db_name};"
                f"Trusted_Connection=yes;"
            )
        else:
            self.dsn = (
                f"DRIVER={db_conf.get('driver')};"
                f"SERVER={db_conf.get('server')};"
                f"DATABASE={self.db_name};"
                f"UID={db_conf.get('uid')};"
                f"PWD={db_conf.get('pwd')};"
            )
        
    def _get_connection(self):
        return pyodbc.connect(self.dsn, autocommit=True)

    def _dictfetchall(self, cursor) -> List[Dict]:
        """Convierte los resultados del cursor en una lista de diccionarios."""
        if not cursor.description:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def obtener_facturas_pendientes(self) -> List[Dict]:
        """
        Consulta en la base configurada las facturas sin CUFE aplicando los filtros
        de serie de la caja y rango de días definidos en config.yaml.
        """
        ciclo_conf = settings.get("ciclo_sincronizacion", {})
        dias_atras = ciclo_conf.get("dias_hacia_atras", 3)
        limite = ciclo_conf.get("limite_facturas_por_ciclo", 50)
        
        # Serie fija de esta terminal (si aplica)
        serie_caja = settings.get("identidad", {}).get("serie_caja")
        filtro_serie = ""
        if serie_caja:
            filtro_serie = f"AND f.NUMSERIE = '{serie_caja}'"

        query = f"""
        SELECT TOP {limite}
            f.NUMSERIE, f.NUMFACTURA, f.N, f.FECHA, f.TOTALNETO,
            f.TOTALIMPUESTOS, f.TIPODOC,
            c.FE_ESTADO, c.FE_PROC, c.FE_CUFE
        FROM [{self.db_name}].dbo.FACTURASVENTA f WITH (NOLOCK)
        LEFT JOIN [{self.db_name}].dbo.FACTURASVENTACAMPOSLIBRES c WITH (NOLOCK)
            ON f.NUMSERIE = c.NUMSERIE
            AND f.NUMFACTURA = c.NUMFACTURA
            AND f.N = c.N
        INNER JOIN [{self.db_name}].dbo.SERIESRESOLUCION sr WITH (NOLOCK)
            ON sr.SERIERESOL = f.NUMSERIE
            AND sr.ACTIVO = 1
        WHERE (c.FE_CUFE IS NULL OR LTRIM(RTRIM(c.FE_CUFE)) = '')
            AND (c.FE_ESTADO IS NULL OR c.FE_ESTADO IN (0, 2))
            AND f.FECHA >= DATEADD(day, -{dias_atras}, CAST(GETDATE() AS DATE))
            {filtro_serie}
        ORDER BY f.FECHA ASC;
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            return self._dictfetchall(cursor)

    def extraer_cabecera(self, serie: str, numfac: int, n: str) -> Dict:
        query = f"""
        SELECT 
            f.NUMSERIE, f.NUMFACTURA, f.N, f.CODCLIENTE, f.FECHA, f.HORA,
            f.TOTALBRUTO, f.TOTALIMPUESTOS, f.TOTALNETO, f.TOTALRETENCION,
            f.TIPODOC, f.CODMONEDA, f.FACTORMONEDA, f.CODVENDEDOR,
            sr.NUMRESOL
        FROM [{self.db_name}].dbo.FACTURASVENTA f WITH (NOLOCK)
        LEFT JOIN [{self.db_name}].dbo.SERIESRESOLUCION sr WITH (NOLOCK)
            ON sr.SERIERESOL = f.NUMSERIE AND sr.ACTIVO = 1
        WHERE f.NUMSERIE = ? AND f.NUMFACTURA = ? AND f.N = ?;
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (serie, numfac, n))
            rows = self._dictfetchall(cursor)
            return rows[0] if rows else {}

    def extraer_cliente(self, cod_cliente: int) -> Dict:
        query = f"""
        SELECT 
            CODCLIENTE, NIF20, CIF, NOMBRECLIENTE, NOMBRECOMERCIAL,
            DIRECCION1, CODPOSTAL, POBLACION, PROVINCIA, PAIS,
            E_MAIL, TELEFONO1, REGIMFACT, PERSONAJURIDICA, TIPODOCIDENT
        FROM [{self.db_name}].dbo.CLIENTES WITH (NOLOCK)
        WHERE CODCLIENTE = ?;
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (cod_cliente,))
            rows = self._dictfetchall(cursor)
            return rows[0] if rows else {}

    def extraer_lineas(self, serie: str, numfac: int, n: str) -> List[Dict]:
        query = f"""
        SELECT 
            l.NUMLIN, l.REFERENCIA, l.DESCRIPCION, l.UNIDADESTOTAL,
            l.PRECIO, l.DTO, l.TOTAL, l.TIPOIMPUESTO, l.IVA, l.REQ,
            l.TIPORETENCION, l.PORCRETENCION, l.CODARTICULO
        FROM [{self.db_name}].dbo.ALBVENTACAB a WITH (NOLOCK)
        INNER JOIN [{self.db_name}].dbo.ALBVENTALIN l WITH (NOLOCK)
            ON a.NUMSERIE = l.NUMSERIE
            AND a.NUMALBARAN = l.NUMALBARAN
            AND a.N = l.N
        WHERE a.NUMSERIEFAC = ? AND a.NUMFAC = ? AND a.NFAC = ?
        ORDER BY l.NUMLIN;
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (serie, numfac, n))
            return self._dictfetchall(cursor)

    def extraer_impuestos(self, serie: str, numfac: int, n: str) -> List[Dict]:
        query = f"""
        SELECT 
            NUMLINEA, TIPOIVA, BASEIMPONIBLE, IVA, TOTIVA,
            REQ, TOTREQ, TOTAL
        FROM [{self.db_name}].dbo.FACTURASVENTATOT WITH (NOLOCK)
        WHERE SERIE = ? AND NUMERO = ? AND N = ?
        ORDER BY NUMLINEA;
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (serie, numfac, n))
            return self._dictfetchall(cursor)

    def extraer_pagos(self, serie: str, numfac: int, n: str) -> List[Dict]:
        query = f"""
        SELECT 
            p.NUMLINEA, p.CODFORMAPAGO, p.CODMONEDA, p.IMPORTE
        FROM [{self.db_name}].dbo.ALBVENTACAB a WITH (NOLOCK)
        INNER JOIN [{self.db_name}].dbo.ALBVENTAPAG p WITH (NOLOCK)
            ON a.NUMSERIE = p.NUMSERIE
            AND a.NUMALBARAN = p.NUMALBARAN
            AND a.N = p.N
        WHERE a.NUMSERIEFAC = ? AND a.NUMFAC = ? AND a.NFAC = ?
        ORDER BY p.NUMLINEA;
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (serie, numfac, n))
            return self._dictfetchall(cursor)

    def extraer_factura_completa(self, serie: str, numfac: int, n: str) -> Optional[Dict]:
        """
        Extrae y agrupa todos los datos de la factura en una estructura lista para enviar al Core.
        """
        cabecera = self.extraer_cabecera(serie, numfac, n)
        if not cabecera:
            return None
            
        cod_cliente = cabecera.get("CODCLIENTE")
        cliente = self.extraer_cliente(cod_cliente) if cod_cliente else {}
        lineas = self.extraer_lineas(serie, numfac, n)
        impuestos = self.extraer_impuestos(serie, numfac, n)
        pagos = self.extraer_pagos(serie, numfac, n)
        
        # Identidad de la terminal
        identidad = settings.get("identidad", {})
        
        return {
            "identidad": {
                "id_tienda": identidad.get("id_tienda"),
                "id_terminal": identidad.get("id_terminal"),
            },
            "cabecera": cabecera,
            "cliente": cliente,
            "lineas": lineas,
            "impuestos": impuestos,
            "pagos": pagos
        }

    # =========================================================================
    # WRITE-BACK (ESCRITURA DIRECTA A ICGFRONT / RAMSAS)
    # =========================================================================

    def escribir_cufe_icg(self, serie: str, numfac: int, n: str, cufe: str, qr_url: str = "", estado: int = 30) -> bool:
        """
        Registra el CUFE, QR y estado Aprobado (30) en FACTURASVENTACAMPOSLIBRES usando UPSERT dinámico.
        """
        query = f"""
        IF EXISTS (SELECT 1 FROM [{self.db_name}].dbo.FACTURASVENTACAMPOSLIBRES WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?)
        BEGIN
            UPDATE [{self.db_name}].dbo.FACTURASVENTACAMPOSLIBRES
            SET FE_CUFE = ?,
                FE_QR = ?,
                FE_ESTADO = ?,
                FE_PROC = 5,
                FE_FECHAESTADO = GETDATE(),
                FE_FECHAVALIDACION = GETDATE(),
                FE_RESPONSEERROR = NULL
            WHERE NUMSERIE = ?
              AND NUMFACTURA = ?
              AND N = ?;
        END
        ELSE
        BEGIN
            INSERT INTO [{self.db_name}].dbo.FACTURASVENTACAMPOSLIBRES
                (NUMSERIE, NUMFACTURA, N, FE_CUFE, FE_QR, FE_ESTADO, FE_PROC, FE_FECHAESTADO, FE_FECHAVALIDACION, FE_RESPONSEERROR)
            VALUES
                (?, ?, ?, ?, ?, ?, 5, GETDATE(), GETDATE(), NULL);
        END
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (
                    # Parámetros para IF EXISTS
                    serie, numfac, n,
                    # Parámetros para UPDATE
                    cufe, qr_url, estado, serie, numfac, n,
                    # Parámetros para INSERT
                    serie, numfac, n, cufe, qr_url, estado
                ))
                logger.info(f"[{serie}-{numfac}-{n}] Write-Back exitoso en {self.db_name} (CUFE registrado).")
                return True
        except Exception as e:
            logger.error(f"[{serie}-{numfac}-{n}] Error en Write-Back de CUFE en {self.db_name}: {str(e)}", exc_info=True)
            return False

    def escribir_error_icg(self, serie: str, numfac: int, n: str, error_mensaje: str, estado: int = 40) -> bool:
        """
        Registra el mensaje de error / rechazo en FACTURASVENTACAMPOSLIBRES (FE_ESTADO = 40).
        """
        query = f"""
        IF EXISTS (SELECT 1 FROM [{self.db_name}].dbo.FACTURASVENTACAMPOSLIBRES WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?)
        BEGIN
            UPDATE [{self.db_name}].dbo.FACTURASVENTACAMPOSLIBRES
            SET FE_ESTADO = ?,
                FE_PROC = 2,
                FE_RESPONSEERROR = ?,
                FE_FECHAESTADO = GETDATE()
            WHERE NUMSERIE = ?
              AND NUMFACTURA = ?
              AND N = ?;
        END
        ELSE
        BEGIN
            INSERT INTO [{self.db_name}].dbo.FACTURASVENTACAMPOSLIBRES
                (NUMSERIE, NUMFACTURA, N, FE_ESTADO, FE_PROC, FE_FECHAESTADO, FE_RESPONSEERROR)
            VALUES
                (?, ?, ?, ?, 2, GETDATE(), ?);
        END
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (
                    # Parámetros para IF EXISTS
                    serie, numfac, n,
                    # Parámetros para UPDATE
                    estado, error_mensaje[:3900], serie, numfac, n,
                    # Parámetros para INSERT
                    serie, numfac, n, estado, error_mensaje[:3900]
                ))
                logger.warning(f"[{serie}-{numfac}-{n}] Error registrado en {self.db_name}: {error_mensaje[:100]}")
                return True
        except Exception as e:
            logger.error(f"[{serie}-{numfac}-{n}] Error en Write-Back de Error en {self.db_name}: {str(e)}", exc_info=True)
            return False

    # =========================================================================
    # DOCUMENTO SOPORTE (COMPRAS A NO OBLIGADOS A FACTURAR) - FASE 9
    # -------------------------------------------------------------------------
    # En ICG el DS se identifica por el prefijo fiscal de la resolución DIAN
    # (FACTURASCOMPRASERIESRESOL.SERIEFISCAL1), no por la serie interna NUMSERIE.
    # FACTURASCOMPRACAMPOSLIBRES NO tiene la columna FE_PROC.
    # =========================================================================

    def obtener_documentos_soporte_pendientes(self, dias_hacia_atras: Optional[int] = None,
                                              limite: Optional[int] = None) -> List[Dict]:
        """
        Lista las compras con prefijo fiscal de Documento Soporte que aún no tienen CUDS.
        Los parámetros opcionales sobrescriben los valores de config.yaml (útil en pruebas).
        """
        ds_conf = settings.get("ciclos_sincronizacion", {}).get("documento_soporte", {})
        prefijos = [str(p) for p in (ds_conf.get("prefijos_fiscales") or [])]
        series_icg = [str(s) for s in (ds_conf.get("series_icg") or [])]
        dias = int(dias_hacia_atras if dias_hacia_atras is not None else ds_conf.get("dias_hacia_atras", 15))
        top = int(limite if limite is not None else ds_conf.get("limite_por_ciclo", 30))

        if not prefijos:
            logger.warning("Documento Soporte: no hay 'prefijos_fiscales' configurados. Nada que consultar.")
            return []

        filtro_series = ""
        if series_icg:
            filtro_series = f"AND f.NUMSERIE IN ({', '.join('?' for _ in series_icg)})"

        query = f"""
        SELECT TOP (?)
            f.NUMSERIE, f.NUMFACTURA, f.N, f.FECHA, f.CODPROVEEDOR, f.SUFACTURA, f.TOTALNETO,
            r.SERIEFISCAL1, r.SERIEFISCAL2, r.NUMEROFISCAL,
            cl.FE_ESTADO, cl.FE_CUFE
        FROM [{self.db_name}].dbo.FACTURASCOMPRASERIESRESOL r WITH (NOLOCK)
        INNER JOIN [{self.db_name}].dbo.FACTURASCOMPRA f WITH (NOLOCK)
            ON f.NUMSERIE = r.NUMSERIE AND f.NUMFACTURA = r.NUMFACTURA AND f.N = r.N
        LEFT JOIN [{self.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES cl WITH (NOLOCK)
            ON cl.NUMSERIE = f.NUMSERIE AND cl.NUMFACTURA = f.NUMFACTURA AND cl.N = f.N
        WHERE r.SERIEFISCAL1 IN ({', '.join('?' for _ in prefijos)})
            AND (cl.FE_CUFE IS NULL OR LTRIM(RTRIM(cl.FE_CUFE)) = '')
            AND (cl.FE_ESTADO IS NULL OR cl.FE_ESTADO IN (0, 2))
            AND f.FECHA >= DATEADD(day, -?, CAST(GETDATE() AS DATE))
            {filtro_series}
        ORDER BY f.FECHA ASC, f.NUMFACTURA ASC;
        """
        params = [top, *prefijos, dias, *series_icg]
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return self._dictfetchall(cursor)

    def extraer_documento_soporte_completo(self, serie: str, numfac: int, n: str) -> Optional[Dict]:
        """
        Extrae la compra en estructuras nativas (dict/list) lista para json.dumps().
        Sin clases de dominio ni cálculos: el Core arma el XML UBL tipo 05 y calcula el CUDS.
        """
        q_cabecera = f"""
        SELECT
            f.NUMSERIE, f.NUMFACTURA, f.N, f.SUFACTURA, f.CODPROVEEDOR, f.FECHA, f.HORA,
            f.FECHASUFACTURA, f.TOTALBRUTO, f.TOTALIMPUESTOS, f.TOTALNETO, f.TOTALRETENCION,
            f.DTOCOMERCIAL, f.TOTDTOCOMERCIAL, f.DTOPP, f.TOTDTOPP, f.TOTALCARGOSDTOS,
            f.CODMONEDA, f.FACTORMONEDA, f.IVAINCLUIDO, f.TIPODOC, f.REGIMFACT, f.ESSERVICIO,
            r.SERIEFISCAL1 AS PREFIJO_FISCAL, r.SERIEFISCAL2 AS NUMRESOL, r.NUMEROFISCAL,
            cl.CONCEPTO_GASTO
        FROM [{self.db_name}].dbo.FACTURASCOMPRA f WITH (NOLOCK)
        LEFT JOIN [{self.db_name}].dbo.FACTURASCOMPRASERIESRESOL r WITH (NOLOCK)
            ON r.NUMSERIE = f.NUMSERIE AND r.NUMFACTURA = f.NUMFACTURA AND r.N = f.N
        LEFT JOIN [{self.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES cl WITH (NOLOCK)
            ON cl.NUMSERIE = f.NUMSERIE AND cl.NUMFACTURA = f.NUMFACTURA AND cl.N = f.N
        WHERE f.NUMSERIE = ? AND f.NUMFACTURA = ? AND f.N = ?;
        """
        q_proveedor = f"""
        SELECT
            CODPROVEEDOR, NIF20, CIF, NOMPROVEEDOR, NOMCOMERCIAL, DIRECCION1, CODPOSTAL,
            POBLACION, PROVINCIA, PAIS, CODPAIS, E_MAIL, TELEFONO1, REGIMFACT,
            PERSONAJURIDICA, TIPODOCIDENT, REGIMRET, TIPORET, REGIMRETICA
        FROM [{self.db_name}].dbo.PROVEEDORES WITH (NOLOCK)
        WHERE CODPROVEEDOR = ?;
        """
        q_lineas = f"""
        SELECT
            l.NUMLIN, l.CODARTICULO, l.REFERENCIA, l.DESCRIPCION, l.UNIDADESTOTAL,
            l.PRECIO, l.DTO, l.TOTAL, l.TIPOIMPUESTO, l.IVA, l.REQ,
            l.TIPORETENCION, l.PORCRETENCION, l.TIPORETENCIONICA, l.PORCRETENCIONICA
        FROM [{self.db_name}].dbo.ALBCOMPRACAB a WITH (NOLOCK)
        INNER JOIN [{self.db_name}].dbo.ALBCOMPRALIN l WITH (NOLOCK)
            ON a.NUMSERIE = l.NUMSERIE AND a.NUMALBARAN = l.NUMALBARAN AND a.N = l.N
        WHERE a.NUMSERIEFAC = ? AND a.NUMFAC = ? AND a.NFAC = ?
        ORDER BY a.NUMALBARAN, l.NUMLIN;
        """
        q_impuestos = f"""
        SELECT NUMLINEA, TIPOIVA, BRUTO, BASEIMPONIBLE, IVA, TOTIVA, REQ, TOTREQ, TOTAL, ESGASTO
        FROM [{self.db_name}].dbo.FACTURASCOMPRATOT WITH (NOLOCK)
        WHERE SERIE = ? AND NUMERO = ? AND N = ?
        ORDER BY NUMLINEA;
        """
        q_retenciones = f"""
        SELECT NUMLIN, TIPORET, REGIMRET, BASE, PORCRET, IMPORTE
        FROM [{self.db_name}].dbo.FACTURASCOMPRARET WITH (NOLOCK)
        WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?
        ORDER BY NUMLIN;
        """
        llave = (serie, numfac, n)
        # Una sola conexión para las 5 lecturas del mismo documento
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(q_cabecera, llave)
            filas = self._dictfetchall(cursor)
            if not filas:
                return None
            cabecera = filas[0]

            proveedor = {}
            if cabecera.get("CODPROVEEDOR") is not None:
                cursor.execute(q_proveedor, (cabecera["CODPROVEEDOR"],))
                prov = self._dictfetchall(cursor)
                proveedor = prov[0] if prov else {}

            cursor.execute(q_lineas, llave)
            lineas = self._dictfetchall(cursor)
            cursor.execute(q_impuestos, llave)
            impuestos = self._dictfetchall(cursor)
            cursor.execute(q_retenciones, llave)
            retenciones = self._dictfetchall(cursor)

        identidad = settings.get("identidad", {})
        return {
            "tipo_documento": "DOCUMENTO_SOPORTE",
            "identidad": {
                "id_tienda": identidad.get("id_tienda"),
                "id_terminal": identidad.get("id_terminal"),
            },
            "cabecera": cabecera,
            "proveedor": proveedor,
            "lineas": lineas,
            "impuestos": impuestos,
            "retenciones": retenciones,
        }

    def escribir_cuds_icg(self, serie: str, numfac: int, n: str, cuds: str,
                          qr_url: str = "", estado: int = 30) -> bool:
        """
        Registra CUDS, QR y estado Aprobado (30) en FACTURASCOMPRACAMPOSLIBRES (UPSERT, sin FE_PROC).
        """
        query = f"""
        IF EXISTS (SELECT 1 FROM [{self.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?)
        BEGIN
            UPDATE [{self.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES
            SET FE_CUFE = ?,
                FE_QR = ?,
                FE_ESTADO = ?,
                FE_FECHAESTADO = GETDATE(),
                FE_FECHAVALIDACION = GETDATE(),
                FE_RESPONSEERROR = NULL
            WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?;
        END
        ELSE
        BEGIN
            INSERT INTO [{self.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES
                (NUMSERIE, NUMFACTURA, N, FE_CUFE, FE_QR, FE_ESTADO, FE_FECHAESTADO, FE_FECHAVALIDACION, FE_RESPONSEERROR)
            VALUES
                (?, ?, ?, ?, ?, ?, GETDATE(), GETDATE(), NULL);
        END
        """
        cuds = (cuds or "")[:100]       # FE_CUFE nvarchar(100)
        qr_url = (qr_url or "")[:500]   # FE_QR nvarchar(500)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (
                    serie, numfac, n,
                    cuds, qr_url, estado, serie, numfac, n,
                    serie, numfac, n, cuds, qr_url, estado
                ))
                logger.info(f"[DS {serie}-{numfac}-{n}] Write-Back exitoso en {self.db_name} (CUDS registrado).")
                return True
        except Exception as e:
            logger.error(f"[DS {serie}-{numfac}-{n}] Error en Write-Back de CUDS en {self.db_name}: {str(e)}", exc_info=True)
            return False

    def escribir_error_ds_icg(self, serie: str, numfac: int, n: str, error_mensaje: str,
                              estado: int = 40) -> bool:
        """
        Registra el rechazo del Documento Soporte (FE_ESTADO = 40) en FACTURASCOMPRACAMPOSLIBRES.
        Trunca el mensaje a 3900 caracteres (FE_RESPONSEERROR nvarchar(4000)).
        """
        query = f"""
        IF EXISTS (SELECT 1 FROM [{self.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?)
        BEGIN
            UPDATE [{self.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES
            SET FE_ESTADO = ?,
                FE_RESPONSEERROR = ?,
                FE_FECHAESTADO = GETDATE()
            WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?;
        END
        ELSE
        BEGIN
            INSERT INTO [{self.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES
                (NUMSERIE, NUMFACTURA, N, FE_ESTADO, FE_FECHAESTADO, FE_RESPONSEERROR)
            VALUES
                (?, ?, ?, ?, GETDATE(), ?);
        END
        """
        mensaje = (error_mensaje or "")[:3900]
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (
                    serie, numfac, n,
                    estado, mensaje, serie, numfac, n,
                    serie, numfac, n, estado, mensaje
                ))
                logger.warning(f"[DS {serie}-{numfac}-{n}] Error registrado en {self.db_name}: {mensaje[:100]}")
                return True
        except Exception as e:
            logger.error(f"[DS {serie}-{numfac}-{n}] Error en Write-Back de error DS en {self.db_name}: {str(e)}", exc_info=True)
            return False

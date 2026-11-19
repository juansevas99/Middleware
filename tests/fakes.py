"""
Dobles de prueba (Fakes) para aislar la base de datos en las pruebas de
API, Worker y Estructurales sin alterar registros reales de RAMSAS/ICGFRONT.
"""
import threading
from datetime import date, datetime
from typing import Dict, List, Optional


class FakeICGDatabase:
    """
    Simula en memoria el comportamiento exacto de ICGDatabase
    (FACTURASVENTA + FACTURASVENTACAMPOSLIBRES).
    """

    def __init__(self, serie_caja: str = "BC3S"):
        self.lock = threading.RLock()
        self.db_name = "FAKE_ICG"
        self.serie_caja = serie_caja
        # llave ("SERIE-NUM-N") -> dict con datos de la factura
        self.facturas: Dict[str, Dict] = {}
        # llave ("SERIE-NUM-N") -> dict con fila de FACTURASVENTACAMPOSLIBRES
        self.campos_libres: Dict[str, Dict] = {}

    def sembrar_factura(self, serie: str = "BC3S", numfac: int = 1001, n: str = "B",
                        total_neto: float = 59500.0, con_cufe: Optional[str] = None,
                        fe_estado: Optional[int] = None):
        llave = f"{serie}-{numfac}-{n}"
        with self.lock:
            self.facturas[llave] = {
                "NUMSERIE": serie,
                "NUMFACTURA": numfac,
                "N": n,
                "FECHA": date.today(),
                "HORA": datetime.now().strftime("%H:%M:%S"),
                "CODCLIENTE": 1,
                "TOTALBRUTO": round(total_neto / 1.19, 2),
                "TOTALIMPUESTOS": round(total_neto - (total_neto / 1.19), 2),
                "TOTALNETO": total_neto,
                "TOTALRETENCION": 0.0,
                "TIPODOC": 24,
                "CODMONEDA": 1,
                "FACTORMONEDA": 1.0,
                "CODVENDEDOR": 10,
                "NUMRESOL": "18764091496327",
            }
            if con_cufe is not None or fe_estado is not None:
                self.campos_libres[llave] = {
                    "NUMSERIE": serie,
                    "NUMFACTURA": numfac,
                    "N": n,
                    "FE_CUFE": con_cufe,
                    "FE_QR": "",
                    "FE_ESTADO": fe_estado if fe_estado is not None else (30 if con_cufe else 0),
                    "FE_PROC": 5 if con_cufe else 0,
                    "FE_RESPONSEERROR": None,
                }
            elif llave in self.campos_libres:
                del self.campos_libres[llave]

    def obtener_facturas_pendientes(self) -> List[Dict]:
        with self.lock:
            resultado = []
            for llave, fac in self.facturas.items():
                if self.serie_caja and fac["NUMSERIE"] != self.serie_caja:
                    continue
                cl = self.campos_libres.get(llave, {})
                cufe = (cl.get("FE_CUFE") or "").strip()
                estado = cl.get("FE_ESTADO")
                if cufe == "" and (estado is None or estado in (0, 2)):
                    resultado.append({
                        "NUMSERIE": fac["NUMSERIE"],
                        "NUMFACTURA": fac["NUMFACTURA"],
                        "N": fac["N"],
                        "FECHA": fac["FECHA"],
                        "TOTALNETO": fac["TOTALNETO"],
                        "TOTALIMPUESTOS": fac["TOTALIMPUESTOS"],
                        "TIPODOC": fac["TIPODOC"],
                        "FE_ESTADO": estado,
                        "FE_PROC": cl.get("FE_PROC"),
                        "FE_CUFE": cl.get("FE_CUFE"),
                    })
            return resultado

    def extraer_factura_completa(self, serie: str, numfac: int, n: str) -> Optional[Dict]:
        llave = f"{serie}-{numfac}-{n}"
        with self.lock:
            cab = self.facturas.get(llave)
            if not cab:
                return None
            return {
                "identidad": {"id_tienda": "SEDE_PRINCIPAL", "id_terminal": "CAJA_01"},
                "cabecera": dict(cab),
                "cliente": {
                    "CODCLIENTE": 1,
                    "NIF20": "222222222222",
                    "NOMBRECLIENTE": "CONSUMIDOR FINAL",
                    "E_MAIL": "consumidor@ejemplo.com",
                },
                "lineas": [
                    {
                        "NUMLIN": 1,
                        "REFERENCIA": "ART-001",
                        "DESCRIPCION": "PRODUCTO DE PRUEBA",
                        "UNIDADESTOTAL": 1.0,
                        "PRECIO": cab["TOTALBRUTO"],
                        "DTO": 0.0,
                        "TOTAL": cab["TOTALBRUTO"],
                        "TIPOIMPUESTO": 1,
                        "IVA": 19.0,
                        "REQ": 0.0,
                        "CODARTICULO": 100,
                    }
                ],
                "impuestos": [
                    {
                        "NUMLINEA": 1,
                        "TIPOIVA": 1,
                        "BASEIMPONIBLE": cab["TOTALBRUTO"],
                        "IVA": 19.0,
                        "TOTIVA": cab["TOTALIMPUESTOS"],
                        "REQ": 0.0,
                        "TOTREQ": 0.0,
                        "TOTAL": cab["TOTALNETO"],
                    }
                ],
                "pagos": [
                    {
                        "NUMLINEA": 1,
                        "CODFORMAPAGO": 10,
                        "CODMONEDA": 1,
                        "IMPORTE": cab["TOTALNETO"],
                    }
                ],
            }

    def escribir_cufe_icg(self, serie: str, numfac: int, n: str, cufe: str,
                          qr_url: str = "", estado: int = 30) -> bool:
        llave = f"{serie}-{numfac}-{n}"
        with self.lock:
            # Comportamiento UPSERT idéntico a FACTURASVENTACAMPOSLIBRES
            self.campos_libres[llave] = {
                "NUMSERIE": serie,
                "NUMFACTURA": numfac,
                "N": n,
                "FE_CUFE": cufe,
                "FE_QR": qr_url,
                "FE_ESTADO": estado,
                "FE_PROC": 5,
                "FE_RESPONSEERROR": None,
            }
        return True

    def escribir_error_icg(self, serie: str, numfac: int, n: str,
                           error_mensaje: str, estado: int = 40) -> bool:
        llave = f"{serie}-{numfac}-{n}"
        with self.lock:
            existente = self.campos_libres.get(llave, {})
            self.campos_libres[llave] = {
                "NUMSERIE": serie,
                "NUMFACTURA": numfac,
                "N": n,
                "FE_CUFE": existente.get("FE_CUFE"),
                "FE_QR": existente.get("FE_QR"),
                "FE_ESTADO": estado,
                "FE_PROC": 2,
                "FE_RESPONSEERROR": error_mensaje[:3900],
            }
        return True

    # ------------------------------------------------------------------ Documento Soporte
    # Simula FACTURASCOMPRA + FACTURASCOMPRASERIESRESOL + FACTURASCOMPRACAMPOSLIBRES (sin FE_PROC)

    def _asegurar_ds(self):
        if not hasattr(self, "compras"):
            self.compras: Dict[str, Dict] = {}
            self.campos_libres_compra: Dict[str, Dict] = {}

    def sembrar_documento_soporte(self, serie: str = "GFD", numfac: int = 9001, n: str = "B",
                                  prefijo: str = "DEQ1", total_neto: float = 3208625.0):
        self._asegurar_ds()
        llave = f"{serie}-{numfac}-{n}"
        with self.lock:
            self.compras[llave] = {
                "NUMSERIE": serie, "NUMFACTURA": numfac, "N": n,
                "FECHA": date.today(), "CODPROVEEDOR": 704, "SUFACTURA": f"CC-{numfac}",
                "TOTALBRUTO": total_neto, "TOTALNETO": total_neto, "TOTALRETENCION": 0.0,
                "PREFIJO_FISCAL": prefijo, "NUMRESOL": "18764044693065", "NUMEROFISCAL": numfac,
            }
            self.campos_libres_compra.pop(llave, None)

    def obtener_documentos_soporte_pendientes(self, dias_hacia_atras=None, limite=None) -> List[Dict]:
        self._asegurar_ds()
        with self.lock:
            res = []
            for llave, c in self.compras.items():
                cl = self.campos_libres_compra.get(llave, {})
                if (cl.get("FE_CUFE") or "").strip() == "" and cl.get("FE_ESTADO") in (None, 0, 2):
                    res.append({"NUMSERIE": c["NUMSERIE"], "NUMFACTURA": c["NUMFACTURA"], "N": c["N"],
                                "SERIEFISCAL1": c["PREFIJO_FISCAL"], "FE_ESTADO": cl.get("FE_ESTADO"),
                                "FE_CUFE": cl.get("FE_CUFE")})
            return res[:limite] if limite else res

    def extraer_documento_soporte_completo(self, serie: str, numfac: int, n: str) -> Optional[Dict]:
        self._asegurar_ds()
        with self.lock:
            cab = self.compras.get(f"{serie}-{numfac}-{n}")
            if not cab:
                return None
            return {
                "tipo_documento": "DOCUMENTO_SOPORTE",
                "identidad": {"id_tienda": "SEDE_PRINCIPAL", "id_terminal": "CAJA_01"},
                "cabecera": dict(cab),
                "proveedor": {"CODPROVEEDOR": 704, "NIF20": "20983067", "NOMPROVEEDOR": "PROVEEDOR NO OBLIGADO"},
                "lineas": [{"NUMLIN": 1, "DESCRIPCION": "ARRENDAMIENTO", "UNIDADESTOTAL": 1.0,
                            "PRECIO": cab["TOTALBRUTO"], "TOTAL": cab["TOTALBRUTO"], "IVA": 0.0}],
                "impuestos": [],
                "retenciones": [],
            }

    def escribir_cuds_icg(self, serie: str, numfac: int, n: str, cuds: str,
                          qr_url: str = "", estado: int = 30) -> bool:
        self._asegurar_ds()
        with self.lock:
            self.campos_libres_compra[f"{serie}-{numfac}-{n}"] = {
                "FE_CUFE": cuds, "FE_QR": qr_url, "FE_ESTADO": estado, "FE_RESPONSEERROR": None,
            }
        return True

    def escribir_error_ds_icg(self, serie: str, numfac: int, n: str, error_mensaje: str,
                              estado: int = 40) -> bool:
        self._asegurar_ds()
        llave = f"{serie}-{numfac}-{n}"
        with self.lock:
            prev = self.campos_libres_compra.get(llave, {})
            self.campos_libres_compra[llave] = {
                "FE_CUFE": prev.get("FE_CUFE"), "FE_QR": prev.get("FE_QR"),
                "FE_ESTADO": estado, "FE_RESPONSEERROR": error_mensaje[:3900],
            }
        return True


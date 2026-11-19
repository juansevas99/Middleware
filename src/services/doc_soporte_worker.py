"""
WORKER DE DOCUMENTO SOPORTE (FASE 9)
====================================
Hilo independiente que ejecuta el ciclo periódico unificado para compras a
proveedores no obligados a facturar. Es un calco del Canal 2 de ventas:

    1. Poller: lee en ICG las compras con prefijo fiscal DS y sin CUDS.
    2. Heartbeat ligero: envía solo las llaves al Core.
    3. Según el veredicto por llave:
         APROBADA   -> Write-Back de CUDS/QR (sin reenviar el documento)
         EN_PROCESO -> sin acción, se reconsulta en el siguiente ciclo
         RECHAZADA  -> Write-Back de error (FE_ESTADO = 40)
         NO_EXISTE  -> extrae el payload crudo y lo envía al Core (Push)

No hay Canal 1 (evento en tiempo real): nadie espera un tiquete impreso.
"""
import logging
import threading
from typing import Dict, Optional

from src.config_loader import settings
from src.database.icg_db import ICGDatabase
from src.services.client_core import CoreFiscalClient

logger = logging.getLogger(__name__)


class DocSoporteWorker:
    def __init__(self, icg_db: Optional[ICGDatabase] = None,
                 client_core: Optional[CoreFiscalClient] = None,
                 intervalo: Optional[float] = None):
        # Instancias propias: este hilo nunca comparte conexión ODBC con el de ventas
        self.icg_db = icg_db or ICGDatabase()
        self.client_core = client_core or CoreFiscalClient()
        conf = settings.get("ciclos_sincronizacion", {}).get("documento_soporte", {})
        self.intervalo = float(intervalo if intervalo is not None else conf.get("intervalo_segundos", 300))
        self._detener = threading.Event()

    # ------------------------------------------------------------------ ciclo
    def ejecutar_ciclo(self) -> Dict[str, int]:
        """Ejecuta una iteración completa. Retorna un resumen por resultado."""
        resumen = {"pendientes": 0, "aprobados": 0, "en_proceso": 0, "rechazados": 0,
                   "enviados": 0, "fallos_red": 0}

        pendientes = self.icg_db.obtener_documentos_soporte_pendientes()
        if not pendientes:
            logger.info("[DS] Sin Documentos Soporte pendientes de CUDS.")
            return resumen
        resumen["pendientes"] = len(pendientes)

        llaves_map = {f"{d['NUMSERIE']}-{d['NUMFACTURA']}-{d['N']}": (d["NUMSERIE"], d["NUMFACTURA"], d["N"])
                      for d in pendientes}
        logger.info(f"[DS] {len(llaves_map)} pendientes. Heartbeat ligero al Core...")

        ok, msg, mapa = self.client_core.reconciliar_llaves_ds(list(llaves_map.keys()))
        if not ok:
            logger.warning(f"[DS] No fue posible reconciliar: {msg}. Se reintentará en el siguiente ciclo.")
            resumen["fallos_red"] = len(llaves_map)
            return resumen

        estados = mapa.get("documentos", {})
        for llave, (serie, num, n) in llaves_map.items():
            if self._detener.is_set():
                break
            info = estados.get(llave, {}) or {}
            estado = str(info.get("estado", "NO_EXISTE")).upper()

            if estado in ("APROBADA", "APROBADO"):
                cuds = info.get("cuds") or info.get("cufe", "")
                logger.info(f"[DS {llave}] Ya aprobado en el Core. Write-Back de CUDS.")
                self.icg_db.escribir_cuds_icg(serie, num, n, cuds, info.get("qr_url", ""), estado=30)
                resumen["aprobados"] += 1

            elif estado == "EN_PROCESO":
                logger.info(f"[DS {llave}] En trámite en la DIAN. Se reconsulta en el siguiente ciclo.")
                resumen["en_proceso"] += 1

            elif estado in ("RECHAZADA", "RECHAZADO"):
                error = info.get("mensaje_error", "Rechazo DIAN no especificado")
                logger.warning(f"[DS {llave}] Rechazado: {error}")
                self.icg_db.escribir_error_ds_icg(serie, num, n, error, estado=40)
                resumen["rechazados"] += 1

            else:  # NO_EXISTE -> Push del documento crudo
                self._push_documento(llave, serie, num, n, resumen)

        logger.info(f"[DS] Ciclo terminado: {resumen}")
        return resumen

    def _push_documento(self, llave: str, serie: str, num: int, n: str, resumen: Dict[str, int]):
        documento = self.icg_db.extraer_documento_soporte_completo(serie, num, n)
        if not documento:
            logger.error(f"[DS {llave}] No se pudo extraer el documento de ICG. Se omite.")
            return

        ok, msg, data = self.client_core.enviar_documento_soporte(documento)
        if ok:
            cuds = data.get("cuds") or data.get("cufe", "")
            logger.info(f"[DS {llave}] Emitido. Write-Back de CUDS.")
            self.icg_db.escribir_cuds_icg(serie, num, n, cuds, data.get("qr_url", ""), estado=30)
            resumen["enviados"] += 1
        elif "Error de validación" in msg:
            # Rechazo definitivo (4xx): se asienta para que contabilidad corrija
            logger.error(f"[DS {llave}] Rechazo definitivo del Core: {msg}")
            self.icg_db.escribir_error_ds_icg(serie, num, n, msg, estado=40)
            resumen["rechazados"] += 1
        else:
            # Red/timeout/5xx: no se toca ICG; el siguiente Heartbeat decidirá
            logger.warning(f"[DS {llave}] Falla transitoria: {msg}")
            resumen["fallos_red"] += 1

    # ------------------------------------------------------------------ hilo
    def iniciar(self):
        """Bucle del hilo. Un error en un ciclo nunca mata el hilo."""
        logger.info(f"DocSoporteWorker iniciado. Frecuencia: cada {self.intervalo} segundos.")
        while not self._detener.is_set():
            try:
                self.ejecutar_ciclo()
            except Exception as e:
                logger.error(f"[DS] Error inesperado en el ciclo: {e}", exc_info=True)
            self._detener.wait(self.intervalo)   # Espera interrumpible
        logger.info("DocSoporteWorker detenido.")

    def detener(self):
        self._detener.set()


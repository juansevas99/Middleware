import time
import logging
import threading
from typing import List, Dict
from src.database.icg_db import ICGDatabase
from src.services.client_core import CoreFiscalClient
from src.config_loader import settings

logger = logging.getLogger(__name__)

class SyncWorker:
    """
    Trabajador en segundo plano que ejecuta el ciclo periódico unificado:
    1. Poller de facturas sin CUFE en ICG.
    2. Heartbeat ligero por llaves hacia el Core.
    3. Push de contingencias para las facturas que el Core no conoce.
    """
    def __init__(self, icg_db: ICGDatabase = None, client_core: CoreFiscalClient = None,
                 intervalo: float = None):
        self.icg_db = icg_db or ICGDatabase()
        self.client_core = client_core or CoreFiscalClient()
        self.ciclo_conf = settings.get("ciclo_sincronizacion", {})
        # Prioridad: parámetro > ciclos_sincronizacion.ventas > ciclo_sincronizacion (legado)
        conf_ventas = settings.get("ciclos_sincronizacion", {}).get("ventas", {})
        self.intervalo = float(
            intervalo if intervalo is not None
            else conf_ventas.get("intervalo_segundos", self.ciclo_conf.get("intervalo_segundos", 45))
        )
        self.activo = False
        self._detener = threading.Event()

    def ejecutar_ciclo(self):
        """Ejecuta una iteración completa del ciclo periódico."""
        logger.info("=== Iniciando ciclo de sincronización periódica (Poller + Heartbeat) ===")
        try:
            pendientes = self.icg_db.obtener_facturas_pendientes()
            if not pendientes:
                logger.info("No se encontraron facturas sin CUFE en ICG. Todo al día.")
                return

            logger.info(f"Se encontraron {len(pendientes)} facturas pendientes en ICG.")
            
            # 1. Armar lista de llaves
            llaves_map = {}
            for f in pendientes:
                serie = f["NUMSERIE"]
                num = f["NUMFACTURA"]
                n = f["N"]
                llave = f"{serie}-{num}-{n}"
                llaves_map[llave] = (serie, num, n)
                
            lista_llaves = list(llaves_map.keys())

            # 2. Paso 2.1: Heartbeat Ligero hacia el Core
            logger.info(f"Consultando estado en Core para {len(lista_llaves)} llaves...")
            exito_recon, msg_recon, mapa_estados = self.client_core.reconciliar_llaves(lista_llaves)

            if not exito_recon:
                logger.warning(f"No fue posible reconciliar con el Core: {msg_recon}. Se reintentará en el siguiente ciclo.")
                return

            # 3. Procesar respuestas por cada factura
            estados_doc = mapa_estados.get("documentos", {})
            
            for llave, (serie, num, n) in llaves_map.items():
                estado_info = estados_doc.get(llave, {})
                estado = estado_info.get("estado", "NO_EXISTE").upper()

                if estado == "APROBADA":
                    cufe = estado_info.get("cufe", "")
                    qr_url = estado_info.get("qr_url", "")
                    logger.info(f"[{llave}] Reconciliación: Factura APROBADA en Core. Aplicando Write-Back...")
                    self.icg_db.escribir_cufe_icg(serie, num, n, cufe, qr_url, estado=30)

                elif estado == "EN_PROCESO":
                    logger.info(f"[{llave}] Reconciliación: En trámite en la DIAN. Se esperará al siguiente ciclo.")

                elif estado == "RECHAZADA":
                    error_dian = estado_info.get("mensaje_error", "Rechazo DIAN no especificado")
                    logger.warning(f"[{llave}] Reconciliación: Factura RECHAZADA. Registrando en ICG: {error_dian}")
                    self.icg_db.escribir_error_icg(serie, num, n, error_dian, estado=40)

                elif estado == "NO_EXISTE":
                    # Paso 2.2: Contingencia Offline (El Core nunca vio esta factura)
                    logger.info(f"[{llave}] No existe en el Core. Extrayendo datos completos para emisión de contingencia...")
                    factura_completa = self.icg_db.extraer_factura_completa(serie, num, n)
                    
                    if not factura_completa:
                        logger.error(f"[{llave}] Error al extraer detalle de ICG. Saltando.")
                        continue
                        
                    exito_envio, msg_envio, data_envio = self.client_core.enviar_factura(
                        factura_completa, timeout=self.client_core.timeout_bulk
                    )
                    
                    if exito_envio:
                        cufe = data_envio.get("cufe", "")
                        qr_url = data_envio.get("qr_url", "")
                        logger.info(f"[{llave}] Emisión de contingencia EXITOSA. Guardando CUFE en ICG...")
                        self.icg_db.escribir_cufe_icg(serie, num, n, cufe, qr_url, estado=30)
                    else:
                        if "Error de validación" in msg_envio:
                            logger.error(f"[{llave}] Error de validación definitivo devuelto por el Core: {msg_envio}")
                            self.icg_db.escribir_error_icg(serie, num, n, msg_envio, estado=40)
                        else:
                            logger.warning(f"[{llave}] Falló envío de contingencia (Red/Timeout): {msg_envio}")

        except Exception as e:
            logger.error(f"Error crítico en ciclo de sincronización: {str(e)}", exc_info=True)

    def iniciar(self):
        """Inicia el bucle continuo del worker (espera interrumpible con threading.Event)."""
        self.activo = True
        self._detener.clear()
        logger.info(f"SyncWorker iniciado. Frecuencia: cada {self.intervalo} segundos.")
        while not self._detener.is_set():
            self.ejecutar_ciclo()   # ejecutar_ciclo ya captura sus propias excepciones
            self._detener.wait(self.intervalo)
        self.activo = False
        logger.info("SyncWorker detenido.")

    def detener(self):
        self.activo = False
        self._detener.set()


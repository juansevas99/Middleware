import os
import sys
import threading
import logging
from logging.handlers import RotatingFileHandler

# Asegurar que la ruta base esté en el PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config_loader import settings
from src.database.icg_db import ICGDatabase
from src.services.client_core import CoreFiscalClient
from src.services.sync_worker import SyncWorker
from src.services.doc_soporte_worker import DocSoporteWorker
from src.api.server import crear_servidor_api

def configurar_logging():
    """Configura el logging con rotación de archivos para evitar sobrellenar el disco."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_file = os.path.join(base_dir, "middleware.log")
    
    # Rota a los 10MB, conservando hasta 5 respaldos
    handler_rotativo = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    handler_consola = logging.StreamHandler(sys.stdout)
    
    formato = logging.Formatter("%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s")
    handler_rotativo.setFormatter(formato)
    handler_consola.setFormatter(formato)
    
    logger_raiz = logging.getLogger()
    logger_raiz.setLevel(logging.INFO)
    logger_raiz.addHandler(handler_rotativo)
    logger_raiz.addHandler(handler_consola)

def main():
    configurar_logging()
    logger = logging.getLogger(__name__)
    
    identidad = settings.get("identidad", {})
    id_tienda = identidad.get("id_tienda", "TIENDA_DEFAULT")
    id_caja = identidad.get("id_terminal", "CAJA_DEFAULT")
    serie = identidad.get("serie_caja", "N/A")
    
    logger.info("=================================================================")
    logger.info("    INICIANDO MIDDLEWARE STATELESS - ICG FRONT RETAIL           ")
    logger.info(f"    Sede: {id_tienda} | Terminal: {id_caja} | Serie: {serie}    ")
    logger.info("=================================================================")
    
    # 1. Instanciar componentes compartidos (usados por la API local de caja)
    icg_db = ICGDatabase()
    client_core = CoreFiscalClient()
    
    # 2. Iniciar los ciclos periódicos, cada uno en su propio hilo demonio y con su propia
    #    conexión a SQL Server (pyodbc no comparte cursores de forma segura entre hilos).
    ciclos = settings.get("ciclos_sincronizacion", {})
    workers = []

    # Hilo 1: Ventas (ciclos_sincronizacion.ventas con respaldo en la sección legada)
    conf_ventas = ciclos.get("ventas") or settings.get("ciclo_sincronizacion", {})
    if conf_ventas.get("habilitado", True):
        worker_ventas = SyncWorker(icg_db=ICGDatabase(), client_core=CoreFiscalClient())
        workers.append((worker_ventas, "WorkerVentas"))

    # Hilo 2: Documento Soporte (solo en la instalación del servidor central)
    conf_ds = ciclos.get("documento_soporte", {})
    if conf_ds.get("habilitado", False):
        worker_ds = DocSoporteWorker(icg_db=ICGDatabase(), client_core=CoreFiscalClient())
        workers.append((worker_ds, "WorkerDocSoporte"))

    hilos = []
    for worker, nombre in workers:
        hilo = threading.Thread(target=worker.iniciar, name=nombre, daemon=True)
        hilo.start()
        hilos.append(hilo)
        logger.info(f"Hilo '{nombre}' iniciado (cada {worker.intervalo:.0f}s).")
    if not workers:
        logger.warning("No hay ciclos periódicos habilitados en config.yaml.")
    
    # 3. Iniciar Servidor HTTP Local para Front Retail
    api_conf = settings.get("api_local", {})
    host = api_conf.get("host", "127.0.0.1")
    puerto = int(api_conf.get("puerto", 8080))
    
    servidor_api = crear_servidor_api(host, puerto, icg_db=icg_db, client_core=client_core)
    logger.info(f"Servidor API local escuchando en http://{host}:{puerto}/api/v1/eventos/venta")
    
    try:
        servidor_api.serve_forever()
    except KeyboardInterrupt:
        logger.info("Deteniendo el Middleware de forma segura...")
        for worker, _ in workers:
            worker.detener()
        servidor_api.shutdown()
        servidor_api.server_close()
        for hilo in hilos:
            hilo.join(timeout=5)
        logger.info("Middleware detenido.")

if __name__ == "__main__":
    main()


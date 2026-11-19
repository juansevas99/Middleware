import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Dict, Any

from src.database.icg_db import ICGDatabase
from src.services.client_core import CoreFiscalClient
from src.config_loader import settings

logger = logging.getLogger(__name__)

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Servidor HTTP multihilo para atender peticiones concurrentes de caja."""
    daemon_threads = True

class CajaApiHandler(BaseHTTPRequestHandler):
    icg_db: ICGDatabase = None
    client_core: CoreFiscalClient = None

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _responder_json(self, codigo_http: int, data: Dict[str, Any]):
        cuerpo = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo_http)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        """Health-check para verificar que el servicio esté vivo."""
        if self.path == "/health":
            self._responder_json(200, {
                "status": "UP",
                "servicio": "Middleware POS ICG",
                "caja": settings.get("identidad", {}).get("id_terminal")
            })
        else:
            self._responder_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self):
        """Atiende eventos de venta desde Front Retail."""
        if self.path != "/api/v1/eventos/venta":
            self._responder_json(404, {"error": "Endpoint no encontrado"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))
            
            serie = payload.get("numserie")
            numfac = payload.get("numfactura")
            n = payload.get("n", "V")
            
            if not serie or numfac is None:
                self._responder_json(400, {
                    "resultado": "ERROR_DATOS",
                    "mensaje": "Los campos 'numserie' y 'numfactura' son requeridos."
                })
                return

            numfac = int(numfac)
            llave = f"{serie}-{numfac}-{n}"
            logger.info(f"[{llave}] Evento de venta recibido desde Front Retail.")

            # 1. Extraer factura en memoria desde ICG
            factura = self.icg_db.extraer_factura_completa(serie, numfac, n)
            if not factura:
                logger.error(f"[{llave}] Factura no encontrada en ICGFRONT.")
                self._responder_json(404, {
                    "resultado": "NO_ENCONTRADA",
                    "mensaje": f"La factura {llave} no existe en ICGFRONT."
                })
                return

            # 2. Enviar inmediatamente al Core Fiscal (con timeout estricto para caja)
            timeout_caja = float(settings.get("api_local", {}).get("timeout_caja_segundos", 2.5))
            exito, mensaje, data_core = self.client_core.enviar_factura(factura, timeout=timeout_caja)

            if exito:
                cufe = data_core.get("cufe", "")
                qr_url = data_core.get("qr_url", "")
                
                # Write-Back inmediato en ICG
                self.icg_db.escribir_cufe_icg(serie, numfac, n, cufe, qr_url, estado=30)
                logger.info(f"[{llave}] Emisión Online OK. Notificando a Front Retail para impresión electrónica.")
                
                self._responder_json(200, {
                    "resultado": "OK",
                    "accion_impresion": "ELECTRONICA",
                    "cude": cufe,
                    "qr_url": qr_url,
                    "mensaje": "Factura aprobada por la DIAN"
                })
            else:
                # Contingencia en Caliente (Timeout, Caída de red o Error)
                logger.warning(f"[{llave}] Contingencia activada ({mensaje}). Notificando a Front Retail para impresión simple.")
                
                self._responder_json(200, {
                    "resultado": "CONTINGENCIA",
                    "accion_impresion": "TIQUETE_SIMPLE",
                    "cude": None,
                    "qr_url": None,
                    "mensaje": f"Venta en contingencia: {mensaje}"
                })

        except Exception as e:
            logger.error(f"Error procesando evento de venta: {str(e)}", exc_info=True)
            self._responder_json(500, {
                "resultado": "CONTINGENCIA",
                "accion_impresion": "TIQUETE_SIMPLE",
                "cude": None,
                "qr_url": None,
                "mensaje": f"Error interno: {str(e)}"
            })

def crear_servidor_api(host: str = "127.0.0.1", puerto: int = 8080, 
                       icg_db: ICGDatabase = None, 
                       client_core: CoreFiscalClient = None) -> ThreadedHTTPServer:
    """Instancia el servidor HTTP vinculándole los componentes compartidos."""
    CajaApiHandler.icg_db = icg_db or ICGDatabase()
    CajaApiHandler.client_core = client_core or CoreFiscalClient()
    servidor = ThreadedHTTPServer((host, puerto), CajaApiHandler)
    return servidor


"""
SIMULADOR DEL CORE FISCAL (MOCK SERVER)
=======================================
Reemplaza al Core Fiscal real mientras este no exista. Implementa el mismo
contrato HTTP que consume el Middleware:

    POST /v1/emision          -> Emite una factura (devuelve CUFE + QR)
    POST /v1/reconciliacion   -> Heartbeat ligero por llaves
    POST /v1/documento-soporte                -> Emite un Documento Soporte (devuelve CUDS + QR)
    POST /v1/documento-soporte/reconciliacion -> Heartbeat ligero de Documento Soporte
    GET  /health              -> Health check

Además expone endpoints de CONTROL para que las pruebas fuercen escenarios:

    POST /_control/reset      -> Limpia todo el estado del simulador
    POST /_control/config     -> {"modo": "OK|TIMEOUT|RECHAZO|ERROR_500",
                                  "retardo_timeout": 5, "retardo_ok": 0,
                                  "mensaje_rechazo": "..."}
    POST /_control/documento  -> Fuerza el estado de una llave en el Core:
                                 {"llave": "BC3S-1-V", "estado": "APROBADA|EN_PROCESO|RECHAZADA",
                                  "cufe": "...", "mensaje_error": "..."}
    GET  /_control/stats      -> Contadores de peticiones y documentos

Comportamiento del modo TIMEOUT: el simulador duerme 'retardo_timeout' segundos
y DESPUÉS aprueba la factura. Así se reproduce el caso real de "ACK perdido":
la DIAN aprobó, pero la respuesta llegó cuando la caja ya había cortado.

Uso standalone:
    python mock_core/mock_core_server.py --port 8090
"""
import argparse
import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

logger = logging.getLogger("mock_core")

MODOS_VALIDOS = {"OK", "TIMEOUT", "RECHAZO", "ERROR_500"}


def generar_cufe(llave: str) -> str:
    """CUFE sintético con el mismo formato del real (SHA-384, 96 caracteres hex)."""
    return hashlib.sha384(f"MOCK-CORE-{llave}".encode("utf-8")).hexdigest()


def generar_qr(cufe: str) -> str:
    return f"https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey={cufe}"


class EstadoMock:
    """Estado en memoria del simulador (equivale a la BD del Core)."""

    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self.modo = "OK"
            self.retardo_timeout = 5.0
            self.retardo_ok = 0.0
            self.mensaje_rechazo = "Rechazo DIAN simulado: Regla FAD06 - NIT del adquiriente inválido"
            self.documentos = {}            # llave -> {estado, cufe, qr_url, mensaje_error}
            self.peticiones_emision = {}    # llave -> nº de POST /emision recibidos
            self.emisiones_dian = {}        # llave -> nº de veces que "se transmitió a la DIAN"
            self.peticiones_reconciliacion = 0
            # Documento Soporte (almacén independiente: las llaves de compra no chocan con las de venta)
            self.documentos_ds = {}
            self.peticiones_emision_ds = {}
            self.emisiones_dian_ds = {}
            self.peticiones_reconciliacion_ds = 0

    def stats(self) -> dict:
        with self.lock:
            return {
                "modo": self.modo,
                "documentos": dict(self.documentos),
                "peticiones_emision": dict(self.peticiones_emision),
                "emisiones_dian": dict(self.emisiones_dian),
                "peticiones_reconciliacion": self.peticiones_reconciliacion,
                "documentos_ds": dict(self.documentos_ds),
                "peticiones_emision_ds": dict(self.peticiones_emision_ds),
                "emisiones_dian_ds": dict(self.emisiones_dian_ds),
                "peticiones_reconciliacion_ds": self.peticiones_reconciliacion_ds,
            }


ESTADO = EstadoMock()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class MockCoreHandler(BaseHTTPRequestHandler):

    # ------------------------------------------------------------------ utilidades
    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _leer_json(self) -> dict:
        largo = int(self.headers.get("Content-Length", 0))
        if largo == 0:
            return {}
        return json.loads(self.rfile.read(largo).decode("utf-8"))

    def _responder(self, codigo: int, data: dict):
        try:
            cuerpo = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # El cliente ya cortó (caso TIMEOUT). Es lo esperado.
            logger.info("Cliente desconectado antes de recibir la respuesta.")

    def _autorizado(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and len(auth) > len("Bearer ")

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        if self.path == "/health":
            self._responder(200, {"status": "UP", "servicio": "Mock Core Fiscal"})
        elif self.path == "/_control/stats":
            self._responder(200, ESTADO.stats())
        else:
            self._responder(404, {"error": "Ruta no encontrada"})

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        try:
            body = self._leer_json()
        except json.JSONDecodeError:
            self._responder(400, {"error": "JSON inválido"})
            return

        rutas = {
            "/v1/emision": self._emision,
            "/v1/reconciliacion": self._reconciliacion,
            "/v1/documento-soporte": self._documento_soporte,
            "/v1/documento-soporte/reconciliacion": self._reconciliacion_ds,
            "/_control/reset": self._control_reset,
            "/_control/config": self._control_config,
            "/_control/documento": self._control_documento,
            "/_control/documento-soporte": self._control_documento_ds,
        }
        accion = rutas.get(self.path)
        if not accion:
            self._responder(404, {"error": "Ruta no encontrada"})
            return

        if self.path.startswith("/v1/") and not self._autorizado():
            self._responder(401, {"error": "Token Bearer ausente o inválido"})
            return

        accion(body)

    # ------------------------------------------------------------------ /v1/emision
    def _emision(self, payload: dict):
        cab = payload.get("cabecera") or {}
        if not cab.get("NUMSERIE") or cab.get("NUMFACTURA") is None:
            self._responder(400, {"error": "Payload sin cabecera NUMSERIE/NUMFACTURA"})
            return

        llave = f"{cab.get('NUMSERIE')}-{cab.get('NUMFACTURA')}-{cab.get('N')}"

        with ESTADO.lock:
            ESTADO.peticiones_emision[llave] = ESTADO.peticiones_emision.get(llave, 0) + 1
            existente = ESTADO.documentos.get(llave)
            modo = ESTADO.modo
            retardo_timeout = ESTADO.retardo_timeout
            retardo_ok = ESTADO.retardo_ok
            mensaje_rechazo = ESTADO.mensaje_rechazo

        # Idempotencia: si ya está aprobada, devuelve el mismo CUFE sin re-transmitir a la DIAN
        if existente and existente.get("estado") == "APROBADA":
            self._responder(200, {**existente, "llave": llave, "idempotente": True})
            return

        if modo == "ERROR_500":
            self._responder(500, {"error": "Error interno simulado del Core"})
            return

        if modo == "RECHAZO":
            with ESTADO.lock:
                ESTADO.documentos[llave] = {"estado": "RECHAZADA", "mensaje_error": mensaje_rechazo}
            self._responder(400, {"estado": "RECHAZADA", "llave": llave, "mensaje_error": mensaje_rechazo})
            return

        if modo == "TIMEOUT":
            time.sleep(retardo_timeout)   # La caja corta antes; el Core igual termina aprobando
        elif retardo_ok > 0:
            time.sleep(retardo_ok)

        cufe = generar_cufe(llave)
        doc = {
            "estado": "APROBADA",
            "cufe": cufe,
            "qr_url": generar_qr(cufe),
            "fecha_validacion": datetime.now().isoformat(timespec="seconds"),
        }
        with ESTADO.lock:
            ESTADO.documentos[llave] = doc
            ESTADO.emisiones_dian[llave] = ESTADO.emisiones_dian.get(llave, 0) + 1

        self._responder(200, {**doc, "llave": llave})

    # ------------------------------------------------------------------ /v1/reconciliacion
    def _reconciliacion(self, body: dict):
        llaves = body.get("llaves") or []
        with ESTADO.lock:
            ESTADO.peticiones_reconciliacion += 1
            documentos = {
                llave: ESTADO.documentos.get(llave, {"estado": "NO_EXISTE"})
                for llave in llaves
            }
        self._responder(200, {"documentos": documentos})

    # ------------------------------------------------------------------ /v1/documento-soporte
    def _documento_soporte(self, payload: dict):
        cab = payload.get("cabecera") or {}
        if not cab.get("NUMSERIE") or cab.get("NUMFACTURA") is None:
            self._responder(400, {"error": "Payload DS sin cabecera NUMSERIE/NUMFACTURA"})
            return
        if not (payload.get("proveedor") or {}).get("NIF20") and not (payload.get("proveedor") or {}).get("CIF"):
            self._responder(400, {"estado": "RECHAZADA", "mensaje_error": "DS sin identificación del proveedor"})
            return

        llave = f"{cab.get('NUMSERIE')}-{cab.get('NUMFACTURA')}-{cab.get('N')}"
        with ESTADO.lock:
            ESTADO.peticiones_emision_ds[llave] = ESTADO.peticiones_emision_ds.get(llave, 0) + 1
            existente = ESTADO.documentos_ds.get(llave)
            modo, retardo_timeout = ESTADO.modo, ESTADO.retardo_timeout
            retardo_ok, mensaje_rechazo = ESTADO.retardo_ok, ESTADO.mensaje_rechazo

        if existente and existente.get("estado") == "APROBADA":
            self._responder(200, {**existente, "llave": llave, "idempotente": True})
            return
        if modo == "ERROR_500":
            self._responder(500, {"error": "Error interno simulado del Core"})
            return
        if modo == "RECHAZO":
            with ESTADO.lock:
                ESTADO.documentos_ds[llave] = {"estado": "RECHAZADA", "mensaje_error": mensaje_rechazo}
            self._responder(400, {"estado": "RECHAZADA", "llave": llave, "mensaje_error": mensaje_rechazo})
            return
        if modo == "TIMEOUT":
            time.sleep(retardo_timeout)
        elif retardo_ok > 0:
            time.sleep(retardo_ok)

        cuds = generar_cufe(f"DS-{llave}")
        doc = {
            "estado": "APROBADA",
            "cuds": cuds,
            "qr_url": generar_qr(cuds),
            "fecha_validacion": datetime.now().isoformat(timespec="seconds"),
        }
        with ESTADO.lock:
            ESTADO.documentos_ds[llave] = doc
            ESTADO.emisiones_dian_ds[llave] = ESTADO.emisiones_dian_ds.get(llave, 0) + 1
        self._responder(200, {**doc, "llave": llave})

    def _reconciliacion_ds(self, body: dict):
        llaves = body.get("llaves") or []
        with ESTADO.lock:
            ESTADO.peticiones_reconciliacion_ds += 1
            documentos = {llave: ESTADO.documentos_ds.get(llave, {"estado": "NO_EXISTE"}) for llave in llaves}
        self._responder(200, {"documentos": documentos})

    # ------------------------------------------------------------------ control
    def _control_reset(self, _body):
        ESTADO.reset()
        self._responder(200, {"ok": True})

    def _control_config(self, body: dict):
        with ESTADO.lock:
            if "modo" in body:
                modo = str(body["modo"]).upper()
                if modo not in MODOS_VALIDOS:
                    self._responder(400, {"error": f"Modo inválido. Use {sorted(MODOS_VALIDOS)}"})
                    return
                ESTADO.modo = modo
            if "retardo_timeout" in body:
                ESTADO.retardo_timeout = float(body["retardo_timeout"])
            if "retardo_ok" in body:
                ESTADO.retardo_ok = float(body["retardo_ok"])
            if "mensaje_rechazo" in body:
                ESTADO.mensaje_rechazo = str(body["mensaje_rechazo"])
        self._responder(200, {"ok": True, "modo": ESTADO.modo})

    def _control_documento(self, body: dict):
        llave = body.get("llave")
        estado = str(body.get("estado", "")).upper()
        if not llave or estado not in {"APROBADA", "EN_PROCESO", "RECHAZADA"}:
            self._responder(400, {"error": "Se requiere 'llave' y 'estado' (APROBADA|EN_PROCESO|RECHAZADA)"})
            return

        doc = {"estado": estado}
        if estado == "APROBADA":
            cufe = body.get("cufe") or generar_cufe(llave)
            doc.update({"cufe": cufe, "qr_url": body.get("qr_url") or generar_qr(cufe)})
        elif estado == "RECHAZADA":
            doc["mensaje_error"] = body.get("mensaje_error") or ESTADO.mensaje_rechazo

        with ESTADO.lock:
            ESTADO.documentos[llave] = doc
        self._responder(200, {"ok": True, "llave": llave, "documento": doc})

    def _control_documento_ds(self, body: dict):
        llave = body.get("llave")
        estado = str(body.get("estado", "")).upper()
        if not llave or estado not in {"APROBADA", "EN_PROCESO", "RECHAZADA"}:
            self._responder(400, {"error": "Se requiere 'llave' y 'estado' (APROBADA|EN_PROCESO|RECHAZADA)"})
            return
        doc = {"estado": estado}
        if estado == "APROBADA":
            cuds = body.get("cuds") or generar_cufe(f"DS-{llave}")
            doc.update({"cuds": cuds, "qr_url": body.get("qr_url") or generar_qr(cuds)})
        elif estado == "RECHAZADA":
            doc["mensaje_error"] = body.get("mensaje_error") or ESTADO.mensaje_rechazo
        with ESTADO.lock:
            ESTADO.documentos_ds[llave] = doc
        self._responder(200, {"ok": True, "llave": llave, "documento": doc})


def crear_servidor(host: str = "127.0.0.1", puerto: int = 8090) -> ThreadedHTTPServer:
    return ThreadedHTTPServer((host, puerto), MockCoreHandler)


def iniciar_en_hilo(host: str = "127.0.0.1", puerto: int = 8090) -> ThreadedHTTPServer:
    """Levanta el simulador en un hilo demonio (usado por la suite de pruebas)."""
    servidor = crear_servidor(host, puerto)
    hilo = threading.Thread(target=servidor.serve_forever, name="MockCore", daemon=True)
    hilo.start()
    return servidor


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulador local del Core Fiscal")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [MOCK-CORE] %(message)s")
    srv = crear_servidor(args.host, args.port)
    logger.info(f"Mock Core Fiscal escuchando en http://{args.host}:{args.port}  (Ctrl+C para detener)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
        srv.server_close()


"""
BLOQUE 4: Pruebas Estructurales y de Resiliencia (TC-EST-01 a TC-EST-03)
Mide concurrencia entre Caja y Worker, idempotencia de extremo a extremo y el
ciclo completo de vida de una contingencia con auto-recuperación.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import sys
import threading
import time
import unittest
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock_core.mock_core_server import iniciar_en_hilo as iniciar_mock_core, ESTADO as ESTADO_MOCK
from src.api.server import crear_servidor_api
from src.services.client_core import CoreFiscalClient
from src.services.sync_worker import SyncWorker
from tests.fakes import FakeICGDatabase


class TestBloque04Estructural(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            requests.get("http://127.0.0.1:8090/health", timeout=0.5)
            cls._mock_propio = None
        except Exception:
            cls._mock_propio = iniciar_mock_core("127.0.0.1", 8090)

        cls.fake_db = FakeICGDatabase(serie_caja="BC3S")
        cls.client_core = CoreFiscalClient()
        cls.worker = SyncWorker(icg_db=cls.fake_db, client_core=cls.client_core)
        cls.api_server = crear_servidor_api(
            "127.0.0.1", 18081, icg_db=cls.fake_db, client_core=cls.client_core
        )
        cls.api_thread = threading.Thread(target=cls.api_server.serve_forever, daemon=True)
        cls.api_thread.start()
        cls.url_api = "http://127.0.0.1:18081"

    @classmethod
    def tearDownClass(cls):
        cls.api_server.shutdown()
        cls.api_server.server_close()
        if cls._mock_propio:
            cls._mock_propio.shutdown()
            cls._mock_propio.server_close()

    def setUp(self):
        ESTADO_MOCK.reset()
        self.fake_db.facturas.clear()
        self.fake_db.campos_libres.clear()

    def test_TC_EST_01_concurrencia_caja_vs_worker(self):
        """TC-EST-01: Cobro concurrente de 5 ventas en Caja mientras el Worker procesa un lote de 15."""
        # 15 facturas represadas para el Worker + 5 nuevas de Caja
        for num in range(7001, 7016):
            self.fake_db.sembrar_factura("BC3S", num, "B")
        for num in range(8001, 8006):
            self.fake_db.sembrar_factura("BC3S", num, "B")

        hilo_worker = threading.Thread(target=self.worker.ejecutar_ciclo)
        hilo_worker.start()

        def cobrar_en_caja(numfac: int):
            t0 = time.perf_counter()
            r = requests.post(
                f"{self.url_api}/api/v1/eventos/venta",
                json={"numserie": "BC3S", "numfactura": numfac, "n": "B"},
                timeout=5,
            )
            return r.json(), (time.perf_counter() - t0)

        with ThreadPoolExecutor(max_workers=5) as pool:
            resultados = list(pool.map(cobrar_en_caja, range(8001, 8006)))

        hilo_worker.join(timeout=10)

        for body, duracion in resultados:
            self.assertEqual(body["resultado"], "OK")
            self.assertLess(duracion, 1.5, "La caja no debe bloquearse por el Worker")

        # Las 20 facturas deben haber quedado con su CUFE en ICG
        self.assertEqual(len(self.fake_db.obtener_facturas_pendientes()), 0)

    def test_TC_EST_02_idempotencia_y_cero_duplicidad(self):
        """TC-EST-02: 3 disparos repetidos para la misma factura generan 1 sola emisión DIAN y 1 fila."""
        self.fake_db.sembrar_factura("BC3S", 9001, "B")

        cufes = []
        for _ in range(3):
            r = requests.post(
                f"{self.url_api}/api/v1/eventos/venta",
                json={"numserie": "BC3S", "numfactura": 9001, "n": "B"},
                timeout=5,
            )
            self.assertEqual(r.status_code, 200)
            cufes.append(r.json()["cude"])

        # El mismo CUFE en los 3 intentos
        self.assertEqual(len(set(cufes)), 1)
        # El Core solo transmitió 1 vez a la DIAN gracias a la idempotencia
        stats = ESTADO_MOCK.stats()
        self.assertEqual(stats["emisiones_dian"].get("BC3S-9001-B"), 1)
        # En ICG solo hay 1 registro para esa llave
        self.assertIn("BC3S-9001-B", self.fake_db.campos_libres)

    def test_TC_EST_03_ciclo_completo_contingencia_y_autorecuperacion(self):
        """TC-EST-03: Venta en contingencia -> Impresión simple -> Regresa Core -> Worker recupera CUFE."""
        self.fake_db.sembrar_factura("BC3S", 9500, "B")

        # Paso 1: Enlace con el Core caído en el momento de la venta
        url_real = self.client_core.url_base
        self.client_core.url_base = "http://127.0.0.1:19999/v1"
        try:
            r_caja = requests.post(
                f"{self.url_api}/api/v1/eventos/venta",
                json={"numserie": "BC3S", "numfactura": 9500, "n": "B"},
                timeout=5,
            )
        finally:
            # Paso 2: Regresa el enlace al Core
            self.client_core.url_base = url_real

        self.assertEqual(r_caja.json()["resultado"], "CONTINGENCIA")
        self.assertEqual(r_caja.json()["accion_impresion"], "TIQUETE_SIMPLE")
        self.assertEqual(len(self.fake_db.obtener_facturas_pendientes()), 1)

        # Paso 3: El SyncWorker despierta y recupera automáticamente la factura
        self.worker.ejecutar_ciclo()

        self.assertEqual(len(self.fake_db.obtener_facturas_pendientes()), 0)
        cl = self.fake_db.campos_libres.get("BC3S-9500-B")
        self.assertIsNotNone(cl)
        self.assertEqual(cl["FE_ESTADO"], 30)
        self.assertTrue(cl["FE_CUFE"] and len(cl["FE_CUFE"]) == 96)


if __name__ == "__main__":
    unittest.main(verbosity=2)


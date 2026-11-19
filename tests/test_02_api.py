"""
BLOQUE 2: Pruebas del Servidor API Local de Caja (UT-API-01 a UT-API-05)
Levanta el servidor API del Middleware y el Simulador del Core Fiscal en hilos
locales para validar el comportamiento del Canal 1 (Evento de Caja).
"""
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
from tests.fakes import FakeICGDatabase


class TestBloque02ApiLocal(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 1. Asegurar Mock Core arriba en puerto 8090
        try:
            requests.get("http://127.0.0.1:8090/health", timeout=0.5)
            cls._mock_propio = None
        except Exception:
            cls._mock_propio = iniciar_mock_core("127.0.0.1", 8090)

        # 2. Levantar API del Middleware en puerto de prueba 18080
        cls.fake_db = FakeICGDatabase(serie_caja="BC3S")
        cls.client_core = CoreFiscalClient()
        cls.api_server = crear_servidor_api(
            "127.0.0.1", 18080, icg_db=cls.fake_db, client_core=cls.client_core
        )
        cls.api_thread = threading.Thread(target=cls.api_server.serve_forever, daemon=True)
        cls.api_thread.start()
        cls.url_api = "http://127.0.0.1:18080"

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

    def test_UT_API_01_health_check(self):
        """UT-API-01: GET /health responde HTTP 200 con estado UP e id de caja."""
        r = requests.get(f"{self.url_api}/health", timeout=2)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "UP")
        self.assertEqual(data["caja"], "CAJA_01")

    def test_UT_API_02_validacion_payload_entrada(self):
        """UT-API-02: POST /api/v1/eventos/venta con payload incompleto responde HTTP 400."""
        r = requests.post(f"{self.url_api}/api/v1/eventos/venta", json={}, timeout=2)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["resultado"], "ERROR_DATOS")

    def test_UT_API_03_emision_tiempo_real_exitosa_happy_path(self):
        """UT-API-03: Emisión síncrona exitosa devuelve ELECTRONICA + CUDE y hace Write-Back."""
        self.fake_db.sembrar_factura("BC3S", 5001, "B", total_neto=119000.0)

        t0 = time.perf_counter()
        r = requests.post(
            f"{self.url_api}/api/v1/eventos/venta",
            json={"numserie": "BC3S", "numfactura": 5001, "n": "B"},
            timeout=5,
        )
        duracion = time.perf_counter() - t0

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["resultado"], "OK")
        self.assertEqual(body["accion_impresion"], "ELECTRONICA")
        self.assertTrue(body["cude"] and len(body["cude"]) == 96)
        self.assertLess(duracion, 1.5)

        # Verificar Write-Back en ICG
        cl = self.fake_db.campos_libres.get("BC3S-5001-B")
        self.assertIsNotNone(cl)
        self.assertEqual(cl["FE_CUFE"], body["cude"])
        self.assertEqual(cl["FE_ESTADO"], 30)

    def test_UT_API_04_contingencia_en_caliente_por_timeout(self):
        """UT-API-04: Si el Core tarda más de 2.5s, corta a tiempo y responde CONTINGENCIA."""
        self.fake_db.sembrar_factura("BC3S", 5002, "B")
        requests.post(
            "http://127.0.0.1:8090/_control/config",
            json={"modo": "TIMEOUT", "retardo_timeout": 4.0},
            timeout=2,
        )

        t0 = time.perf_counter()
        r = requests.post(
            f"{self.url_api}/api/v1/eventos/venta",
            json={"numserie": "BC3S", "numfactura": 5002, "n": "B"},
            timeout=6,
        )
        duracion = time.perf_counter() - t0

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["resultado"], "CONTINGENCIA")
        self.assertEqual(body["accion_impresion"], "TIQUETE_SIMPLE")
        self.assertIsNone(body["cude"])
        # Cortó alrededor de 2.5s (no esperó los 4.0s completos)
        self.assertLess(duracion, 3.3)
        # En ICG la factura sigue sin CUFE para que la recoja el Worker
        self.assertNotIn("BC3S-5002-B", self.fake_db.campos_libres)

    def test_UT_API_05_contingencia_por_sin_conexion(self):
        """UT-API-05: Si el Core no está disponible (ConnectionRefused), responde CONTINGENCIA inmediato."""
        self.fake_db.sembrar_factura("BC3S", 5003, "B")
        # Apuntar temporalmente a un puerto cerrado
        url_original = self.client_core.url_base
        self.client_core.url_base = "http://127.0.0.1:19999/v1"
        try:
            r = requests.post(
                f"{self.url_api}/api/v1/eventos/venta",
                json={"numserie": "BC3S", "numfactura": 5003, "n": "B"},
                timeout=5,
            )
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["resultado"], "CONTINGENCIA")
            self.assertEqual(body["accion_impresion"], "TIQUETE_SIMPLE")
        finally:
            self.client_core.url_base = url_original


if __name__ == "__main__":
    unittest.main(verbosity=2)


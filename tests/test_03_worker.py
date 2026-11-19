"""
BLOQUE 3: Pruebas del Ciclo Periódico Unificado - SyncWorker (UT-WK-01 a UT-WK-05)
Valida el comportamiento del Poller + Heartbeat ligero + Push de contingencias.
"""
import os
import sys
import unittest
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock_core.mock_core_server import iniciar_en_hilo as iniciar_mock_core, ESTADO as ESTADO_MOCK
from src.services.client_core import CoreFiscalClient
from src.services.sync_worker import SyncWorker
from tests.fakes import FakeICGDatabase


class TestBloque03SyncWorker(unittest.TestCase):

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

    @classmethod
    def tearDownClass(cls):
        if cls._mock_propio:
            cls._mock_propio.shutdown()
            cls._mock_propio.server_close()

    def setUp(self):
        ESTADO_MOCK.reset()
        self.fake_db.facturas.clear()
        self.fake_db.campos_libres.clear()

    def test_UT_WK_01_sondeo_bandeja_limpia(self):
        """UT-WK-01: Sin facturas pendientes en ICG, el worker no realiza llamadas al Core."""
        self.worker.ejecutar_ciclo()
        stats = ESTADO_MOCK.stats()
        self.assertEqual(stats["peticiones_reconciliacion"], 0)
        self.assertEqual(len(stats["peticiones_emision"]), 0)

    def test_UT_WK_02_reconciliacion_subcaso_A_aprobada(self):
        """UT-WK-02: Si el Core ya tenía la factura APROBADA, hace Write-Back sin reenviar JSON."""
        self.fake_db.sembrar_factura("BC3S", 6001, "B")
        requests.post(
            "http://127.0.0.1:8090/_control/documento",
            json={
                "llave": "BC3S-6001-B",
                "estado": "APROBADA",
                "cufe": "CUFE_RECUPERADO_POR_HEARTBEAT_999",
            },
            timeout=2,
        )

        self.worker.ejecutar_ciclo()

        cl = self.fake_db.campos_libres.get("BC3S-6001-B")
        self.assertIsNotNone(cl)
        self.assertEqual(cl["FE_CUFE"], "CUFE_RECUPERADO_POR_HEARTBEAT_999")
        self.assertEqual(cl["FE_ESTADO"], 30)
        # No debió llamarse a /v1/emision porque se recuperó en el Heartbeat ligero
        stats = ESTADO_MOCK.stats()
        self.assertEqual(stats["peticiones_emision"].get("BC3S-6001-B", 0), 0)

    def test_UT_WK_03_reconciliacion_subcaso_B_en_proceso(self):
        """UT-WK-03: Si el Core reporta EN_PROCESO, el worker no toca ICG ni reenvía."""
        self.fake_db.sembrar_factura("BC3S", 6002, "B")
        requests.post(
            "http://127.0.0.1:8090/_control/documento",
            json={"llave": "BC3S-6002-B", "estado": "EN_PROCESO"},
            timeout=2,
        )

        self.worker.ejecutar_ciclo()

        self.assertNotIn("BC3S-6002-B", self.fake_db.campos_libres)
        stats = ESTADO_MOCK.stats()
        self.assertEqual(stats["peticiones_emision"].get("BC3S-6002-B", 0), 0)

    def test_UT_WK_04_reconciliacion_subcaso_C_rechazada(self):
        """UT-WK-04: Si el Core reporta RECHAZADA, el worker registra FE_ESTADO=40 y error en ICG."""
        self.fake_db.sembrar_factura("BC3S", 6003, "B")
        requests.post(
            "http://127.0.0.1:8090/_control/documento",
            json={
                "llave": "BC3S-6003-B",
                "estado": "RECHAZADA",
                "mensaje_error": "Regla DIAN FAJ43b: NIT inválido",
            },
            timeout=2,
        )

        self.worker.ejecutar_ciclo()

        cl = self.fake_db.campos_libres.get("BC3S-6003-B")
        self.assertIsNotNone(cl)
        self.assertEqual(cl["FE_ESTADO"], 40)
        self.assertIn("FAJ43b", cl["FE_RESPONSEERROR"])

    def test_UT_WK_05_push_contingencia_subcaso_D_no_existe(self):
        """UT-WK-05: Si el Core responde NO_EXISTE, el worker extrae la factura completa y la emite."""
        self.fake_db.sembrar_factura("BC3S", 6004, "B")

        self.worker.ejecutar_ciclo()

        cl = self.fake_db.campos_libres.get("BC3S-6004-B")
        self.assertIsNotNone(cl)
        self.assertEqual(cl["FE_ESTADO"], 30)
        self.assertTrue(cl["FE_CUFE"] and len(cl["FE_CUFE"]) == 96)
        stats = ESTADO_MOCK.stats()
        self.assertEqual(stats["peticiones_emision"].get("BC3S-6004-B", 0), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


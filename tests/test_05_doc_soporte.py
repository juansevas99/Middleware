"""
BLOQUE 5: Documento Soporte y Arquitectura Multihilo (UT-DS-01 a UT-DS-07, TC-DS-01 y TC-DS-02)
Valida las consultas reales sobre FACTURASCOMPRA (RAMSAS), el Write-Back en
FACTURASCOMPRACAMPOSLIBRES (restaurando la fila original al terminar), el ciclo
unificado del DocSoporteWorker contra el Mock Core y la convivencia de hilos.
"""
import json
import os
import sys
import threading
import time
import unittest

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock_core.mock_core_server import iniciar_en_hilo as iniciar_mock_core, ESTADO as ESTADO_MOCK
from src.config_loader import settings
from src.database.icg_db import ICGDatabase
from src.services.client_core import CoreFiscalClient, JSONEncoderCustom
from src.services.doc_soporte_worker import DocSoporteWorker
from src.services.sync_worker import SyncWorker
from tests.fakes import FakeICGDatabase

MOCK = "http://127.0.0.1:8090"
CAMPOS_CL = ["FE_CUFE", "FE_QR", "FE_ADJUNTOS", "FE_ESTADO", "FE_RESPONSEERROR",
             "FE_FECHAESTADO", "FE_FECHAVALIDACION", "CONCEPTO_GASTO"]


class TestBloque05DocumentoSoporte(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            requests.get(f"{MOCK}/health", timeout=0.5)
            cls._mock_propio = None
        except Exception:
            cls._mock_propio = iniciar_mock_core("127.0.0.1", 8090)

        cls.db = ICGDatabase()
        cls.prefijos = settings["ciclos_sincronizacion"]["documento_soporte"]["prefijos_fiscales"]
        # Ventana amplia solo para pruebas: garantiza encontrar DS reales en RAMSAS
        muestra = cls.db.obtener_documentos_soporte_pendientes(dias_hacia_atras=4000, limite=1)
        cls.ds_real = muestra[0] if muestra else None

    @classmethod
    def tearDownClass(cls):
        if cls._mock_propio:
            cls._mock_propio.shutdown()
            cls._mock_propio.server_close()

    def setUp(self):
        ESTADO_MOCK.reset()
        self.fake = FakeICGDatabase()
        self.worker = DocSoporteWorker(icg_db=self.fake, client_core=CoreFiscalClient(), intervalo=0.2)

    # ------------------------------------------------------------------ utilidades BD real
    def _llave_real(self):
        if not self.ds_real:
            self.skipTest("No hay Documentos Soporte pendientes en la base configurada")
        return self.ds_real["NUMSERIE"], self.ds_real["NUMFACTURA"], self.ds_real["N"]

    def _snapshot(self, llave):
        with self.db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT {', '.join(CAMPOS_CL)} FROM [{self.db.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES "
                        f"WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?", llave)
            fila = cur.fetchone()
        return list(fila) if fila else None

    def _restaurar(self, llave, original):
        with self.db._get_connection() as conn:
            cur = conn.cursor()
            if original is None:
                cur.execute(f"DELETE FROM [{self.db.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES "
                            f"WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?", llave)
            else:
                sets = ", ".join(f"{c} = ?" for c in CAMPOS_CL)
                cur.execute(f"UPDATE [{self.db.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES SET {sets} "
                            f"WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?", (*original, *llave))

    # ------------------------------------------------------------------ BD real
    def test_UT_DS_01_consulta_pendientes_por_prefijo_fiscal(self):
        """UT-DS-01: Lista DS sin CUDS filtrando por prefijo fiscal (SERIEFISCAL1) en la BD real."""
        filas = self.db.obtener_documentos_soporte_pendientes(dias_hacia_atras=4000, limite=10)
        self.assertGreater(len(filas), 0, "Se esperaban DS pendientes en la base de pruebas")
        self.assertLessEqual(len(filas), 10)
        for f in filas:
            self.assertIn(f["SERIEFISCAL1"], self.prefijos)
            self.assertTrue(not (f["FE_CUFE"] or "").strip())
            self.assertIn(f["FE_ESTADO"], (None, 0, 2))

    def test_UT_DS_02_extraccion_completa_en_estructuras_nativas(self):
        """UT-DS-02: Extrae cabecera, proveedor, líneas, impuestos y retenciones como dict/list serializable."""
        serie, num, n = self._llave_real()
        doc = self.db.extraer_documento_soporte_completo(serie, num, n)
        self.assertIsNotNone(doc)
        for clave in ("tipo_documento", "identidad", "cabecera", "proveedor", "lineas", "impuestos", "retenciones"):
            self.assertIn(clave, doc)
        self.assertEqual(doc["tipo_documento"], "DOCUMENTO_SOPORTE")
        self.assertEqual(doc["cabecera"]["NUMFACTURA"], num)
        self.assertIn(doc["cabecera"]["PREFIJO_FISCAL"], self.prefijos)
        self.assertIsInstance(doc["lineas"], list)
        self.assertTrue(doc["proveedor"].get("NIF20") or doc["proveedor"].get("CIF"))
        json.dumps(doc, cls=JSONEncoderCustom)   # Debe serializar sin clases intermedias

    def test_UT_DS_03_write_back_cuds_upsert_sin_fe_proc(self):
        """UT-DS-03: escribir_cuds_icg graba CUDS/QR/FE_ESTADO=30 en COMPRACAMPOSLIBRES sin duplicar filas."""
        llave = self._llave_real()
        original = self._snapshot(llave)
        try:
            self.assertTrue(self.db.escribir_cuds_icg(*llave, cuds="CUDS_TEST_1", qr_url="https://qr/1"))
            self.assertTrue(self.db.escribir_cuds_icg(*llave, cuds="CUDS_TEST_2", qr_url="https://qr/2"))
            with self.db._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT FE_CUFE, FE_QR, FE_ESTADO FROM [{self.db.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES "
                            f"WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?", llave)
                filas = cur.fetchall()
            self.assertEqual(len(filas), 1)
            self.assertEqual(filas[0][0], "CUDS_TEST_2")
            self.assertEqual(filas[0][2], 30)
        finally:
            self._restaurar(llave, original)
        self.assertEqual(self._snapshot(llave), original, "La fila original debe quedar restaurada")

    def test_UT_DS_04_write_back_error_truncado(self):
        """UT-DS-04: escribir_error_ds_icg guarda FE_ESTADO=40 y trunca el mensaje a 3900 caracteres."""
        llave = self._llave_real()
        original = self._snapshot(llave)
        try:
            self.assertTrue(self.db.escribir_error_ds_icg(*llave, "ERROR_DS_" + "X" * 4500))
            with self.db._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT FE_ESTADO, LEN(FE_RESPONSEERROR) FROM [{self.db.db_name}].dbo.FACTURASCOMPRACAMPOSLIBRES "
                            f"WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?", llave)
                fila = cur.fetchone()
            self.assertEqual(fila[0], 40)
            self.assertLessEqual(fila[1], 3900)
        finally:
            self._restaurar(llave, original)

    # ------------------------------------------------------------------ Worker + Mock Core
    def test_UT_DS_05_heartbeat_aprobada_en_proceso_rechazada(self):
        """UT-DS-05: Heartbeat DS: APROBADA hace Write-Back, EN_PROCESO no actúa, RECHAZADA graba error."""
        for num in (9101, 9102, 9103):
            self.fake.sembrar_documento_soporte("GFD", num, "B")
        requests.post(f"{MOCK}/_control/documento-soporte",
                      json={"llave": "GFD-9101-B", "estado": "APROBADA", "cuds": "CUDS_RECUPERADO"}, timeout=2)
        requests.post(f"{MOCK}/_control/documento-soporte",
                      json={"llave": "GFD-9102-B", "estado": "EN_PROCESO"}, timeout=2)
        requests.post(f"{MOCK}/_control/documento-soporte",
                      json={"llave": "GFD-9103-B", "estado": "RECHAZADA", "mensaje_error": "DSAJ01 NIT inválido"}, timeout=2)

        resumen = self.worker.ejecutar_ciclo()

        cl = self.fake.campos_libres_compra
        self.assertEqual(cl["GFD-9101-B"]["FE_CUFE"], "CUDS_RECUPERADO")
        self.assertEqual(cl["GFD-9101-B"]["FE_ESTADO"], 30)
        self.assertNotIn("GFD-9102-B", cl)
        self.assertEqual(cl["GFD-9103-B"]["FE_ESTADO"], 40)
        self.assertIn("DSAJ01", cl["GFD-9103-B"]["FE_RESPONSEERROR"])
        self.assertEqual((resumen["aprobados"], resumen["en_proceso"], resumen["rechazados"]), (1, 1, 1))
        self.assertEqual(len(ESTADO_MOCK.stats()["peticiones_emision_ds"]), 0, "Ninguna debía reenviarse")

    def test_UT_DS_06_push_no_existe_y_rechazo_definitivo(self):
        """UT-DS-06: NO_EXISTE envía el payload crudo y graba CUDS; un 4xx del Core graba FE_ESTADO=40."""
        self.fake.sembrar_documento_soporte("GFD", 9201, "B")
        self.worker.ejecutar_ciclo()
        cl = self.fake.campos_libres_compra["GFD-9201-B"]
        self.assertEqual(cl["FE_ESTADO"], 30)
        self.assertEqual(len(cl["FE_CUFE"]), 96)
        stats = ESTADO_MOCK.stats()
        self.assertEqual(stats["emisiones_dian_ds"].get("GFD-9201-B"), 1)
        self.assertEqual(stats["documentos"], {}, "El DS no debe contaminar el almacén de ventas")

        self.fake.sembrar_documento_soporte("GFD", 9202, "B")
        requests.post(f"{MOCK}/_control/config", json={"modo": "RECHAZO"}, timeout=2)
        self.worker.ejecutar_ciclo()
        self.assertEqual(self.fake.campos_libres_compra["GFD-9202-B"]["FE_ESTADO"], 40)

    def test_UT_DS_07_core_caido_no_toca_icg(self):
        """UT-DS-07: Sin conexión al Core el ciclo no escribe en ICG y reintenta en el siguiente ciclo."""
        self.fake.sembrar_documento_soporte("GFD", 9301, "B")
        cliente_caido = CoreFiscalClient()
        cliente_caido.url_base = "http://127.0.0.1:8099/v1"   # Puerto sin servicio
        cliente_caido.timeout_bulk = 1.0
        worker = DocSoporteWorker(icg_db=self.fake, client_core=cliente_caido, intervalo=0.2)

        resumen = worker.ejecutar_ciclo()

        self.assertEqual(resumen["fallos_red"], 1)
        self.assertNotIn("GFD-9301-B", self.fake.campos_libres_compra)
        self.assertEqual(len(self.fake.obtener_documentos_soporte_pendientes()), 1, "Debe seguir pendiente")

    # ------------------------------------------------------------------ Concurrencia
    def test_TC_DS_01_lecturas_odbc_concurrentes_sin_colision(self):
        """TC-DS-01: Ventas y DS consultan RAMSAS en paralelo (conexión por hilo) sin errores ODBC."""
        errores = []

        def leer(fn, veces=3):
            db = ICGDatabase()   # Una instancia/conexión por hilo
            try:
                for _ in range(veces):
                    fn(db)
            except Exception as e:
                errores.append(repr(e))

        hilos = [
            threading.Thread(target=leer, args=(lambda d: d.obtener_facturas_pendientes(),)),
            threading.Thread(target=leer, args=(lambda d: d.obtener_documentos_soporte_pendientes(
                dias_hacia_atras=4000, limite=5),)),
        ]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=60)
        self.assertEqual(errores, [])
        self.assertTrue(all(not h.is_alive() for h in hilos))

    def test_TC_DS_02_workers_ventas_y_ds_en_hilos_independientes(self):
        """TC-DS-02: WorkerVentas y WorkerDocSoporte corren en paralelo, procesan lo suyo y se detienen limpio."""
        self.fake.sembrar_factura("BC3S", 9401, "B")
        self.fake.sembrar_documento_soporte("GFD", 9402, "B")
        w_ventas = SyncWorker(icg_db=self.fake, client_core=CoreFiscalClient(), intervalo=0.2)
        w_ds = DocSoporteWorker(icg_db=self.fake, client_core=CoreFiscalClient(), intervalo=0.2)
        hilos = [threading.Thread(target=w_ventas.iniciar, name="WorkerVentas", daemon=True),
                 threading.Thread(target=w_ds.iniciar, name="WorkerDocSoporte", daemon=True)]
        for h in hilos:
            h.start()

        limite = time.time() + 5
        while time.time() < limite:
            if "BC3S-9401-B" in self.fake.campos_libres and "GFD-9402-B" in self.fake.campos_libres_compra:
                break
            time.sleep(0.05)

        w_ventas.detener()
        w_ds.detener()
        for h in hilos:
            h.join(timeout=2)

        self.assertEqual(self.fake.campos_libres["BC3S-9401-B"]["FE_ESTADO"], 30)
        self.assertEqual(self.fake.campos_libres_compra["GFD-9402-B"]["FE_ESTADO"], 30)
        self.assertTrue(all(not h.is_alive() for h in hilos), "threading.Event debe detener los hilos de inmediato")
        stats = ESTADO_MOCK.stats()
        self.assertEqual(stats["emisiones_dian"].get("BC3S-9401-B"), 1)
        self.assertEqual(stats["emisiones_dian_ds"].get("GFD-9402-B"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


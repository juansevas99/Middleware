"""
BLOQUE 1: Pruebas de Base de Datos y Persistencia (UT-DB-01 a UT-DB-07)
Ejecuta pruebas reales contra SQL Server (según config.yaml) limpiando cualquier
registro sintético temporal al finalizar.
"""
import inspect
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config_loader import settings
from src.database.icg_db import ICGDatabase


class TestBloque01BaseDatos(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = ICGDatabase()
        # Buscar una factura real en FACTURASVENTA que NO tenga fila en FACTURASVENTACAMPOSLIBRES
        # para satisfacer la FK FACTURASVENTACAMPOSLIBRES_FK y limpiar al terminar.
        with cls.db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT TOP 1 f.NUMSERIE, f.NUMFACTURA, f.N "
                f"FROM [{cls.db.db_name}].dbo.FACTURASVENTA f WITH (NOLOCK) "
                f"LEFT JOIN [{cls.db.db_name}].dbo.FACTURASVENTACAMPOSLIBRES c WITH (NOLOCK) "
                f"  ON f.NUMSERIE = c.NUMSERIE AND f.NUMFACTURA = c.NUMFACTURA AND f.N = c.N "
                f"WHERE c.NUMFACTURA IS NULL"
            )
            fila = cur.fetchone()
        cls.serie_test, cls.numfac_test, cls.n_test = fila[0], fila[1], fila[2]

    def _limpiar_fila_test(self):
        with self.db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM [{self.db.db_name}].dbo.FACTURASVENTACAMPOSLIBRES "
                f"WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?",
                (self.serie_test, self.numfac_test, self.n_test),
            )

    def test_UT_DB_01_conectividad_handshake_odbc(self):
        """UT-DB-01: Apertura de conexión física contra SQL Server y lectura de versión."""
        with self.db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT @@VERSION")
            version = cur.fetchone()[0]
        self.assertIn("Microsoft SQL Server", version)

    def test_UT_DB_02_tablas_indispensables(self):
        """UT-DB-02: Existencia de las 8 tablas críticas en la base configurada."""
        tablas = [
            "FACTURASVENTA",
            "FACTURASVENTACAMPOSLIBRES",
            "SERIESRESOLUCION",
            "CLIENTES",
            "ALBVENTACAB",
            "ALBVENTALIN",
            "ALBVENTAPAG",
            "FACTURASVENTATOT",
        ]
        with self.db._get_connection() as conn:
            cur = conn.cursor()
            for t in tablas:
                cur.execute(
                    f"SELECT COUNT(*) FROM [{self.db.db_name}].INFORMATION_SCHEMA.TABLES "
                    f"WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = ?",
                    (t,),
                )
                self.assertEqual(cur.fetchone()[0], 1, f"Falta la tabla dbo.{t}")

    def test_UT_DB_03_parametrizacion_dinamica_db_name(self):
        """UT-DB-03: Ninguna consulta en icg_db.py tiene quemado 'ICGFRONT.dbo.'."""
        codigo = inspect.getsource(ICGDatabase)
        self.assertNotIn("ICGFRONT.dbo.", codigo)
        self.assertEqual(self.db.db_name, settings["database"]["db_name"])

    def test_UT_DB_04_validacion_serie_activa_en_resolucion(self):
        """UT-DB-04: La serie configurada en config.yaml existe y está activa en SERIESRESOLUCION."""
        serie_caja = settings["identidad"]["serie_caja"]
        with self.db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT SERIERESOL, NUMRESOL, ACTIVO FROM [{self.db.db_name}].dbo.SERIESRESOLUCION "
                f"WHERE SERIERESOL = ? AND ACTIVO = 1",
                (serie_caja,),
            )
            fila = cur.fetchone()
        self.assertIsNotNone(fila, f"La serie '{serie_caja}' no existe o no está activa en SERIESRESOLUCION")
        self.assertEqual(fila[2], 1)

    def test_UT_DB_05_extraccion_completa_factura_en_memoria(self):
        """UT-DB-05: extraer_factura_completa arma estructura completa desde una factura real."""
        serie_caja = settings["identidad"]["serie_caja"]
        with self.db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT TOP 1 NUMSERIE, NUMFACTURA, N FROM [{self.db.db_name}].dbo.FACTURASVENTA WITH (NOLOCK) "
                f"WHERE NUMSERIE = ?",
                (serie_caja,),
            )
            muestra = cur.fetchone()

        self.assertIsNotNone(muestra, f"No hay facturas para la serie {serie_caja}")
        factura = self.db.extraer_factura_completa(muestra[0], muestra[1], muestra[2])
        self.assertIsNotNone(factura)
        for clave in ("identidad", "cabecera", "cliente", "lineas", "impuestos", "pagos"):
            self.assertIn(clave, factura)
        self.assertEqual(factura["cabecera"]["NUMFACTURA"], muestra[1])

    def test_UT_DB_06_write_back_exito_upsert_insert_y_update(self):
        """UT-DB-06: escribir_cufe_icg inserta si no existe y actualiza sin duplicar si ya existe."""
        self._limpiar_fila_test()
        try:
            # 1) INSERT (no existía)
            ok1 = self.db.escribir_cufe_icg(
                self.serie_test, self.numfac_test, self.n_test,
                cufe="CUFE_INICIAL_123", qr_url="https://dian.gov.co/qr1", estado=30
            )
            self.assertTrue(ok1)

            # 2) UPDATE (ya existía)
            ok2 = self.db.escribir_cufe_icg(
                self.serie_test, self.numfac_test, self.n_test,
                cufe="CUFE_ACTUALIZADO_456", qr_url="https://dian.gov.co/qr2", estado=30
            )
            self.assertTrue(ok2)

            with self.db._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT FE_CUFE, FE_QR, FE_ESTADO, FE_PROC FROM [{self.db.db_name}].dbo.FACTURASVENTACAMPOSLIBRES "
                    f"WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?",
                    (self.serie_test, self.numfac_test, self.n_test),
                )
                filas = cur.fetchall()

            self.assertEqual(len(filas), 1, "No debe duplicar filas en CAMPOSLIBRES")
            self.assertEqual(filas[0][0], "CUFE_ACTUALIZADO_456")
            self.assertEqual(filas[0][2], 30)
            self.assertEqual(filas[0][3], 5)
        finally:
            self._limpiar_fila_test()

    def test_UT_DB_07_write_back_error_truncado_seguro(self):
        """UT-DB-07: escribir_error_icg guarda FE_ESTADO=40 y trunca mensajes largos sin fallar."""
        self._limpiar_fila_test()
        try:
            mensaje_largo = "ERROR_DIAN_" + ("X" * 4500)
            ok = self.db.escribir_error_icg(
                self.serie_test, self.numfac_test, self.n_test,
                error_mensaje=mensaje_largo, estado=40
            )
            self.assertTrue(ok)

            with self.db._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT FE_ESTADO, FE_PROC, LEN(FE_RESPONSEERROR) "
                    f"FROM [{self.db.db_name}].dbo.FACTURASVENTACAMPOSLIBRES "
                    f"WHERE NUMSERIE = ? AND NUMFACTURA = ? AND N = ?",
                    (self.serie_test, self.numfac_test, self.n_test),
                )
                fila = cur.fetchone()

            self.assertIsNotNone(fila)
            self.assertEqual(fila[0], 40)
            self.assertEqual(fila[1], 2)
            self.assertLessEqual(fila[2], 3900)
        finally:
            self._limpiar_fila_test()


if __name__ == "__main__":
    unittest.main(verbosity=2)

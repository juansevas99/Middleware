r"""
EJECUTOR MAESTRO DE LA SUITE DE PRUEBAS (18 CASOS)
==================================================
Levanta automáticamente el Simulador del Core Fiscal en memoria, ejecuta los
4 bloques de pruebas (Base de Datos, API Local, SyncWorker y Estructurales)
y presenta un reporte consolidado.

Uso:
    .\.venv\Scripts\python.exe run_all_tests.py
"""
import logging
import os
import sys
import time
import unittest

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Silenciar logs verbosos durante la corrida de tests para que la tabla sea limpia
logging.basicConfig(level=logging.CRITICAL)

from mock_core.mock_core_server import iniciar_en_hilo as iniciar_mock_core


class ReporteDetalladoResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.registros = []
        self._t0 = 0.0

    def startTest(self, test):
        self._t0 = time.perf_counter()
        super().startTest(test)

    def addSuccess(self, test):
        dur = (time.perf_counter() - self._t0) * 1000
        self.registros.append((test, "PASSED", dur, ""))
        super().addSuccess(test)

    def addFailure(self, test, err):
        dur = (time.perf_counter() - self._t0) * 1000
        self.registros.append((test, "FAILED", dur, self._exc_info_to_string(err, test)))
        super().addFailure(test, err)

    def addError(self, test, err):
        dur = (time.perf_counter() - self._t0) * 1000
        self.registros.append((test, "ERROR", dur, self._exc_info_to_string(err, test)))
        super().addError(test, err)


def main() -> int:
    print("=" * 88)
    print(" SUITE COMPLETA DE PRUEBAS AISLADAS - MIDDLEWARE ICG + MOCK CORE FISCAL")
    print("=" * 88)

    mock_srv = iniciar_mock_core("127.0.0.1", 8090)
    try:
        loader = unittest.TestLoader()
        suite = loader.discover("tests", pattern="test_*.py")
        runner = unittest.TextTestRunner(
            verbosity=0, resultclass=ReporteDetalladoResult, stream=open(os.devnull, "w")
        )
        t_inicio = time.perf_counter()
        result: ReporteDetalladoResult = runner.run(suite)
        t_total = time.perf_counter() - t_inicio

        print(f"{'CASO':<38} | {'ESTADO':<8} | {'TIEMPO':>9} | DESCRIPCIÓN")
        print("-" * 88)
        for test, estado, dur_ms, _ in result.registros:
            nombre = test._testMethodName.replace("test_", "")
            doc = (test.shortDescription() or "").split(":", 1)[-1].strip()
            icono = "✅" if estado == "PASSED" else "❌"
            print(f"{nombre:<38} | {icono} {estado:<5} | {dur_ms:>7.1f}ms | {doc[:42]}")

        print("=" * 88)
        aprobados = sum(1 for _, e, _, _ in result.registros if e == "PASSED")
        total = len(result.registros)
        print(f" RESUMEN: {aprobados}/{total} casos aprobados en {t_total:.2f}s")
        print("=" * 88)

        for test, estado, _, detalle in result.registros:
            if estado != "PASSED":
                print(f"\n[DETALLE DE FALLO] {test.id()}:\n{detalle}")

        return 0 if result.wasSuccessful() else 1
    finally:
        mock_srv.shutdown()
        mock_srv.server_close()


if __name__ == "__main__":
    sys.exit(main())

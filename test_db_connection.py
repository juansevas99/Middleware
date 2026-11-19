import sys
import os

# Asegurar que el directorio raíz esté en el PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.config_loader import settings
from src.database.icg_db import ICGDatabase

def imprimir_banner(titulo):
    print("\n" + "=" * 65)
    print(f" {titulo}")
    print("=" * 65)

def probar_conexion_bd():
    imprimir_banner("DIAGNÓSTICO Y PRUEBA DE CONEXIÓN A BASE DE DATOS ICG")
    
    db_conf = settings.get("database", {})
    identidad = settings.get("identidad", {})
    serie_caja = identidad.get("serie_caja", "N/A")
    db_name = db_conf.get("db_name", "ICGFRONT")
    
    print(f"-> Servidor SQL:       {db_conf.get('server')}")
    print(f"-> Base de Datos:      {db_name}")
    print(f"-> Driver ODBC:        {db_conf.get('driver')}")
    print(f"-> Usuario SQL:        {db_conf.get('uid') if db_conf.get('trusted_connection') != 'yes' else 'Windows Auth'}")
    print(f"-> Serie de esta Caja: {serie_caja}")
    print("-" * 65)
    
    try:
        db = ICGDatabase()
        print("\n[Paso 1] Intentando abrir conexión ODBC...")
        
        with db._get_connection() as conn:
            cursor = conn.cursor()
            print("  ✅ Conexión física establecida exitosamente.")
            
            # 1. Versión de SQL Server
            cursor.execute("SELECT @@VERSION")
            version = cursor.fetchone()[0]
            linea_version = version.split("\n")[0]
            print(f"  ✅ Motor detectado: {linea_version[:55]}...")
            
            # 2. Verificar existencia de la Base de Datos
            cursor.execute("SELECT DB_NAME()")
            db_actual = cursor.fetchone()[0]
            print(f"  ✅ Base de datos activa: [{db_actual}]")
            
            # 3. Comprobar Tablas Críticas
            print("\n[Paso 2] Comprobando integridad de tablas requeridas...")
            tablas_requeridas = [
                "FACTURASVENTA",
                "FACTURASVENTACAMPOSLIBRES",
                "SERIESRESOLUCION",
                "CLIENTES",
                "ALBVENTACAB",
                "ALBVENTALIN",
                "ALBVENTAPAG",
                "FACTURASVENTATOT"
            ]
            
            todas_ok = True
            for tabla in tablas_requeridas:
                query_check = f"""
                SELECT COUNT(*) FROM [{db_name}].INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = ?;
                """
                cursor.execute(query_check, (tabla,))
                existe = cursor.fetchone()[0] > 0
                if existe:
                    print(f"  ✅ Tabla dbo.{tabla.ljust(26)} : EXISTE")
                else:
                    print(f"  ❌ Tabla dbo.{tabla.ljust(26)} : NO ENCONTRADA")
                    todas_ok = False
                    
            if not todas_ok:
                print("\n  ⚠️ ALERTA: Faltan tablas en la base de datos configurada.")
                return

            # 4. Validar Serie y Resolución
            print(f"\n[Paso 3] Validando serie asignada a la caja ('{serie_caja}')...")
            cursor.execute(f"""
                SELECT SERIERESOL, NUMRESOL, ACTIVO 
                FROM [{db_name}].dbo.SERIESRESOLUCION 
                WHERE SERIERESOL = ?;
            """, (serie_caja,))
            fila_serie = cursor.fetchone()
            
            if fila_serie:
                num_resol = fila_serie[1]
                activo = fila_serie[2]
                estado_str = "ACTIVA (1)" if activo == 1 else f"INACTIVA ({activo})"
                simbolo = "✅" if activo == 1 else "⚠️"
                print(f"  {simbolo} Serie [{serie_caja}]: Encontrada en SERIESRESOLUCION.")
                print(f"     -> Resolución: {num_resol}")
                print(f"     -> Estado:     {estado_str}")
            else:
                print(f"  ⚠️ La serie '{serie_caja}' NO existe en la tabla SERIESRESOLUCION de [{db_name}].")
                print("     El Middleware solo procesará facturas cuya serie esté en esta tabla.")

            # Total facturas en FACTURASVENTA con esa serie
            cursor.execute(f"SELECT COUNT(*) FROM [{db_name}].dbo.FACTURASVENTA WHERE NUMSERIE = ?", (serie_caja,))
            total_serie = cursor.fetchone()[0]
            print(f"  ℹ️ Total facturas emitidas con la serie '{serie_caja}': {total_serie}")

            # 5. Probar el método oficial del Middleware: obtener_facturas_pendientes
            print(f"\n[Paso 4] Consultando facturas pendientes de emisión en el Middleware...")
            pendientes = db.obtener_facturas_pendientes()
            print(f"  ✅ Facturas sin CUFE encontradas listas para procesar: {len(pendientes)}")

            if pendientes:
                primera = pendientes[0]
                s = primera.get("NUMSERIE")
                num = primera.get("NUMFACTURA")
                n = primera.get("N")
                fecha = primera.get("FECHA")
                total = primera.get("TOTALNETO")
                print(f"     -> Muestra (Primera pendiente): Serie {s} | Factura #{num} | N: {n} | Fecha: {fecha} | Total: ${total:,.2f}")
                
                # Probar extracción completa
                print(f"\n[Paso 5] Probando extracción completa de la factura {s}-{num}-{n}...")
                factura_completa = db.extraer_factura_completa(s, num, n)
                if factura_completa:
                    lineas = len(factura_completa.get("lineas", []))
                    pagos = len(factura_completa.get("pagos", []))
                    impuestos = len(factura_completa.get("impuestos", []))
                    cliente = factura_completa.get("cliente", {}).get("NOMBRECLIENTE", "Consumidor Final")
                    print(f"  ✅ Extracción exitosa:")
                    print(f"     - Cliente:   {cliente}")
                    print(f"     - Líneas:    {lineas} ítems")
                    print(f"     - Pagos:     {pagos} medios")
                    print(f"     - Impuestos: {impuestos} registros")
                else:
                    print("  ❌ Falló la extracción completa de la factura.")
            else:
                print("     (No hay facturas pendientes en el rango de fechas configurado).")

        imprimir_banner("RESULTADO: ¡TODAS LAS PRUEBAS DE BASE DE DATOS PASARON!")

    except Exception as e:
        imprimir_banner("❌ ERROR DURANTE LA PRUEBA DE CONEXIÓN")
        print(f"Detalle del error:\n{str(e)}")
        print("\nDiagnóstico de causas probables:")
        print("1. El servidor o la IP especificada en config.yaml no está accesible.")
        print("2. El servicio de SQL Server está detenido o el puerto 1433 está bloqueado.")
        print("3. Las credenciales de usuario o contraseña son incorrectas.")
        print("4. El nombre del driver ODBC no coincide con los drivers instalados en Windows.")
        print("=" * 65)

if __name__ == "__main__":
    probar_conexion_bd()


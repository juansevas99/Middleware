import json
import logging
from typing import Dict, List, Tuple, Optional
import requests
from datetime import date, datetime

from src.config_loader import settings

logger = logging.getLogger(__name__)

class JSONEncoderCustom(json.JSONEncoder):
    """Maneja tipos especiales de fecha y números en la serialización."""
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)

class CoreFiscalClient:
    def __init__(self):
        self.config = settings.get("core_fiscal", {})
        self.url_base = self.config.get("url_base", "https://api.tucorefiscal.com/v1").rstrip("/")
        self.endpoint_emision = self.config.get("endpoint_emision", "/emision")
        self.endpoint_reconciliacion = self.config.get("endpoint_reconciliacion", "/reconciliacion")
        self.endpoint_documento_soporte = self.config.get("endpoint_documento_soporte", "/documento-soporte")
        self.endpoint_reconciliacion_ds = self.config.get("endpoint_reconciliacion_ds", "/documento-soporte/reconciliacion")
        self.api_key = self.config.get("api_key", "")
        self.timeout_envio = float(self.config.get("timeout_envio_segundos", 2.0))
        self.timeout_bulk = float(self.config.get("timeout_bulk_segundos", 15.0))
        
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

    def enviar_factura(self, payload: dict, timeout: Optional[float] = None) -> Tuple[bool, str, Dict]:
        """
        Envía una factura al Core Fiscal para emisión formal ante la DIAN.
        Retorna: (exito: bool, mensaje: str, data: dict)
        """
        url = f"{self.url_base}{self.endpoint_emision}"
        t = timeout if timeout is not None else self.timeout_envio
        
        try:
            payload_str = json.dumps(payload, cls=JSONEncoderCustom, ensure_ascii=False)
            response = requests.post(url, data=payload_str, headers=self._get_headers(), timeout=t)
            
            if response.status_code == 200:
                data = response.json()
                return True, "Aprobada", data
                
            elif 400 <= response.status_code < 500:
                return False, f"Error de validación (HTTP {response.status_code}): {response.text}", {}
            else:
                return False, f"Error en servidor Core (HTTP {response.status_code}): {response.text}", {}
                
        except requests.exceptions.Timeout:
            return False, f"Timeout ({t}s) esperando al Core Fiscal", {}
        except requests.exceptions.ConnectionError:
            return False, "Error de conexión (sin internet o servidor caído)", {}
        except Exception as e:
            return False, f"Error inesperado en cliente HTTP: {str(e)}", {}

    def reconciliar_llaves(self, llaves: List[str]) -> Tuple[bool, str, Dict]:
        """
        Heartbeat ligero: envía una lista de llaves ["SETT-101-V", ...] al Core
        para consultar el estado de tramitación de cada una.
        Retorna: (exito: bool, mensaje: str, mapa_estados: dict)
        """
        url = f"{self.url_base}{self.endpoint_reconciliacion}"
        identidad = settings.get("identidad", {})
        
        body = {
            "id_tienda": identidad.get("id_tienda"),
            "id_terminal": identidad.get("id_terminal"),
            "llaves": llaves
        }
        
        try:
            response = requests.post(
                url,
                json=body,
                headers=self._get_headers(),
                timeout=self.timeout_bulk
            )
            
            if response.status_code == 200:
                return True, "Reconciliación exitosa", response.json()
            else:
                return False, f"Error en reconciliación (HTTP {response.status_code}): {response.text}", {}
                
        except requests.exceptions.Timeout:
            return False, "Timeout en reconciliación con el Core", {}
        except requests.exceptions.ConnectionError:
            return False, "Sin conexión para reconciliación", {}
        except Exception as e:
            return False, f"Error en reconciliación: {str(e)}", {}

    # =========================================================================
    # DOCUMENTO SOPORTE (FASE 9)
    # =========================================================================

    def enviar_documento_soporte(self, payload: dict, timeout: Optional[float] = None) -> Tuple[bool, str, Dict]:
        """
        Envía el Documento Soporte crudo al Core (POST /documento-soporte).
        Usa el timeout holgado del ciclo periódico: no hay un usuario esperando.
        Retorna: (exito: bool, mensaje: str, data: dict)
        """
        url = f"{self.url_base}{self.endpoint_documento_soporte}"
        t = timeout if timeout is not None else self.timeout_bulk
        try:
            payload_str = json.dumps(payload, cls=JSONEncoderCustom, ensure_ascii=False)
            response = requests.post(url, data=payload_str.encode("utf-8"),
                                     headers=self._get_headers(), timeout=t)
            if response.status_code == 200:
                return True, "Aprobado", response.json()
            elif 400 <= response.status_code < 500:
                return False, f"Error de validación (HTTP {response.status_code}): {response.text}", {}
            else:
                return False, f"Error en servidor Core (HTTP {response.status_code}): {response.text}", {}
        except requests.exceptions.Timeout:
            return False, f"Timeout ({t}s) esperando al Core Fiscal", {}
        except requests.exceptions.ConnectionError:
            return False, "Error de conexión (sin internet o servidor caído)", {}
        except Exception as e:
            return False, f"Error inesperado en cliente HTTP: {str(e)}", {}

    def reconciliar_llaves_ds(self, llaves: List[str]) -> Tuple[bool, str, Dict]:
        """
        Heartbeat ligero de Documento Soporte: consulta el estado de las llaves de compra.
        Retorna: (exito: bool, mensaje: str, mapa_estados: dict)
        """
        url = f"{self.url_base}{self.endpoint_reconciliacion_ds}"
        identidad = settings.get("identidad", {})
        body = {
            "id_tienda": identidad.get("id_tienda"),
            "id_terminal": identidad.get("id_terminal"),
            "tipo_documento": "DOCUMENTO_SOPORTE",
            "llaves": llaves,
        }
        try:
            response = requests.post(url, json=body, headers=self._get_headers(), timeout=self.timeout_bulk)
            if response.status_code == 200:
                return True, "Reconciliación DS exitosa", response.json()
            return False, f"Error en reconciliación DS (HTTP {response.status_code}): {response.text}", {}
        except requests.exceptions.Timeout:
            return False, "Timeout en reconciliación DS con el Core", {}
        except requests.exceptions.ConnectionError:
            return False, "Sin conexión para reconciliación DS", {}
        except Exception as e:
            return False, f"Error en reconciliación DS: {str(e)}", {}

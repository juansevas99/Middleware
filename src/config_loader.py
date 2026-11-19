import yaml
from pathlib import Path

def get_config(config_file="config.yaml") -> dict:
    """
    Carga y retorna el archivo de configuración YAML como un diccionario.
    Busca automáticamente en la carpeta 'config' en la raíz del proyecto Middleware.
    """
    # Construir la ruta absoluta (subiendo desde src/ a Middleware/config/)
    base_dir = Path(__file__).resolve().parent.parent
    config_path = base_dir / "config" / config_file
    
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# Objeto global para importar directamente en otros módulos
settings = get_config()

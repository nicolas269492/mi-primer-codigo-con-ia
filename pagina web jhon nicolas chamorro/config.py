"""Configuración de la aplicación.

Flask lee estos valores con `app.config.from_object(...)` (ver src/__init__.py).
Cada valor se puede sobrescribir con una variable de entorno del mismo nombre,
así no hace falta editar el código para cambiar, por ejemplo, la contraseña de MySQL:

    set DB_PASSWORD=miclave      (Windows CMD)
    $env:DB_PASSWORD="miclave"   (PowerShell)
"""
import os


class Config:
    """Configuración normal (desarrollo / uso diario)."""

    # Clave con la que Flask firma la cookie de sesión. Si alguien la conoce,
    # podría falsificar sesiones: cámbiala por una cadena larga y aleatoria en producción.
    SECRET_KEY = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")

    # Datos de conexión a MySQL. Los valores por defecto son los de XAMPP
    # (usuario root sin contraseña en localhost:3306).
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_USER = os.environ.get("DB_USER", "root")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_NAME = os.environ.get("DB_NAME", "almacen")

    # Carpeta donde se guardan las imágenes de productos que sube el admin.
    # En la base de datos solo se guarda el nombre del archivo, no la ruta.
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "img", "productos")

    # Tamaño máximo de una petición (imágenes incluidas). Si se supera, Flask responde 413.
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB

    TESTING = False


class TestConfig(Config):
    """Configuración para las pruebas automáticas (pytest).

    Usa una base de datos distinta ('almacen_test') para que las pruebas,
    que borran y recrean las tablas, nunca toquen los datos reales.
    """

    TESTING = True
    DB_NAME = os.environ.get("DB_NAME_TEST", "almacen_test")
    WTF_CSRF_ENABLED = False

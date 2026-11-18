"""Fixtures de pytest (piezas reutilizables que reciben las pruebas como parámetros).

Usa la base 'almacen_test', que se borra y se recrea antes de CADA prueba,
así cada prueba empieza con los mismos datos de ejemplo y no depende de las demás.

Ejecutar desde la raíz del proyecto:   python -m pytest
"""
import os
import sys

import pytest

# Añade la raíz del proyecto al path para poder importar config y src.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import TestConfig  # noqa: E402
from src import create_app  # noqa: E402
from src.init_db import crear_base  # noqa: E402

# Ids de los usuarios de ejemplo que crea init_db.
ADMIN, TRABAJADOR, CLIENTE = 1, 2, 3


class ConfigPruebas(TestConfig):
    """Configuración de pruebas sin CSRF, para poder enviar formularios sin token."""
    TESTING_SIN_CSRF = True


@pytest.fixture
def app():
    """Aplicación con la base de pruebas recién creada (datos de ejemplo)."""
    app = create_app(ConfigPruebas)
    crear_base(app)
    return app


@pytest.fixture
def ctx(app):
    """Contexto de aplicación activo: permite llamar directamente a src.servicios."""
    with app.app_context():
        yield app


@pytest.fixture
def client(app):
    """Navegador simulado de Flask para hacer peticiones GET/POST sin servidor real."""
    return app.test_client()


def entrar_como(client, usuario_id):
    """Simula que `usuario_id` inició sesión (escribe su id en la sesión del cliente de prueba)."""
    with client.session_transaction() as s:
        s["usuario_id"] = usuario_id
    return client


@pytest.fixture
def como_admin(client):
    """Cliente de prueba con sesión de administrador."""
    return entrar_como(client, ADMIN)


@pytest.fixture
def como_trabajador(client):
    """Cliente de prueba con sesión de trabajador."""
    return entrar_como(client, TRABAJADOR)


@pytest.fixture
def como_cliente(client):
    """Cliente de prueba con sesión de cliente."""
    return entrar_como(client, CLIENTE)

"""Login, registro, roles y CSRF."""
import pytest

from config import TestConfig
from src import create_app
from tests.conftest import entrar_como, ADMIN, TRABAJADOR, CLIENTE


def test_login_correcto_redirige_segun_rol(client):
    r = client.post("/login", data={"email": "trabajador@almacen.com", "password": "trabajador123"})
    assert r.status_code == 302 and r.location.endswith("/trabajador/")


def test_login_incorrecto(client):
    r = client.post("/login", data={"email": "admin@almacen.com", "password": "mala"})
    assert r.status_code == 200
    assert "incorrectos" in r.get_data(as_text=True)


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/admin/")
    assert r.status_code == 302 and "/login" in r.location


def test_registro_crea_siempre_cliente(client, ctx):
    from src.basedatos import consultar_uno
    client.post("/registro", data={"nombre": "Ana", "email": "ana@x.com", "password": "secreta1",
                                   "rol": "admin"})
    assert consultar_uno("SELECT rol FROM usuarios WHERE email='ana@x.com'")["rol"] == "cliente"


@pytest.mark.parametrize("usuario, url, esperado", [
    (CLIENTE, "/admin/", 403),
    (CLIENTE, "/trabajador/", 403),
    (TRABAJADOR, "/admin/ventas/", 403),
    (TRABAJADOR, "/cliente/", 403),
    (ADMIN, "/trabajador/", 200),     # el admin también puede ver la interfaz de pedidos
])
def test_permisos_por_rol(client, usuario, url, esperado):
    entrar_como(client, usuario)
    assert client.get(url).status_code == esperado


def test_post_sin_csrf_es_rechazado():
    app = create_app(TestConfig)  # CSRF activo
    c = app.test_client()
    assert c.post("/login", data={"email": "a", "password": "b"}).status_code == 400

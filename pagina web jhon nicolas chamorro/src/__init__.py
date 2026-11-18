"""Fábrica de la aplicación Flask del sistema de almacén.

Patrón "application factory": en lugar de crear la app como variable global,
la función `create_app()` la construye. Así se pueden crear varias apps con
configuraciones distintas (por ejemplo, una para pruebas con otra base de datos).
"""
import os
from decimal import Decimal

from flask import Flask, render_template

from config import Config

# Carpeta raíz del proyecto (un nivel arriba de src/). Se usa para que Flask
# encuentre las carpetas templates/ y static/, que están fuera de src/.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _formato_moneda(valor):
    """Filtro de plantillas `moneda`: convierte un número en precio con formato colombiano.

    Ejemplo en una plantilla:  {{ producto.precio|moneda }}
        1234567.5  ->  "$1.234.568"   (punto como separador de miles, sin decimales)

    Si el valor no es un número válido, lo devuelve tal cual para no romper la página.
    """
    try:
        n = Decimal(valor or 0)
    except Exception:
        return valor
    # Python formatea con comas (1,234,568); se cambian por puntos.
    return "$" + f"{n:,.0f}".replace(",", ".")


def create_app(config_obj=Config):
    """Crea y configura la aplicación Flask.

    Pasos:
      1. Crea la app indicando dónde están templates/ y static/.
      2. Carga la configuración (config.py) y crea la carpeta de imágenes si falta.
      3. Activa la base de datos y la autenticación (login, roles, CSRF).
      4. Registra los "blueprints": cada módulo (admin, trabajador, cliente...)
         es un blueprint con sus propias rutas.
      5. Registra el filtro `moneda` y las páginas de error personalizadas.

    Parámetro:
        config_obj: clase de configuración a usar (Config o TestConfig).
    Devuelve:
        la aplicación Flask lista para ejecutarse.
    """
    app = Flask(
        __name__,
        template_folder=os.path.join(RAIZ, "templates"),
        static_folder=os.path.join(RAIZ, "static"),
    )
    app.config.from_object(config_obj)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Base de datos (cierra la conexión al terminar cada petición) y autenticación.
    from src import auth, basedatos
    basedatos.init_app(app)
    auth.init_app(app)
    app.register_blueprint(auth.bp)  # rutas /, /login, /registro, /logout

    # Módulos de la aplicación. Se importan aquí dentro (no arriba) para evitar
    # importaciones circulares, porque esos módulos a su vez importan src.auth.
    from src.rutas import (admin_inventario, admin_pedidos, admin_usuarios, admin_ventas,
                           cliente, trabajador)
    app.register_blueprint(admin_inventario.bp)  # /admin            panel, productos, movimientos, estancados
    app.register_blueprint(admin_ventas.bp)      # /admin/ventas     registro de ventas
    app.register_blueprint(admin_pedidos.bp)     # /admin/pedidos    CRUD de pedidos
    app.register_blueprint(admin_usuarios.bp)    # /admin/usuarios   gestión de usuarios
    app.register_blueprint(trabajador.bp)        # /trabajador       tablero de pedidos y disponibilidad
    app.register_blueprint(cliente.bp)           # /cliente          catálogo, carrito, mis pedidos

    # Hace disponible {{ valor|moneda }} en todas las plantillas.
    app.jinja_env.filters["moneda"] = _formato_moneda

    # Páginas de error amigables (usan templates/error.html) en lugar de las de Flask.
    @app.errorhandler(403)
    def _prohibido(_e):
        """El usuario tiene sesión pero su rol no puede ver esa página."""
        return render_template("error.html", codigo=403,
                               mensaje="No tienes permiso para ver esta página."), 403

    @app.errorhandler(404)
    def _no_encontrado(_e):
        """La URL o el registro (producto, pedido...) no existe."""
        return render_template("error.html", codigo=404, mensaje="Página no encontrada."), 404

    @app.errorhandler(400)
    def _mala_peticion(e):
        """Petición inválida, por ejemplo un formulario sin token CSRF."""
        return render_template("error.html", codigo=400, mensaje=e.description), 400

    return app

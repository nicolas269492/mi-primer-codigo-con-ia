"""Autenticación: login, registro de clientes, control de roles y protección CSRF.

Roles del sistema:
    admin       -> todo el panel de administración
    trabajador  -> tablero de pedidos y disponibilidad
    cliente     -> catálogo, carrito y sus pedidos

Decoradores para proteger rutas (se ponen encima de cada función de vista):
    @login_requerido                       # basta con haber iniciado sesión
    @rol_requerido("admin")                # solo ese rol
    @rol_requerido("trabajador", "admin")  # cualquiera de esos roles

Usuario actual: `g.usuario` (dict con id, nombre, email, rol) o None si no hay sesión.
En las plantillas: `usuario_actual` y la función `csrf_token()`.

CSRF: todo POST debe enviar el token, como campo de formulario `csrf_token`
o como cabecera `X-CSRFToken` (para peticiones fetch/JSON desde JavaScript).
"""
import secrets
from functools import wraps

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from src.basedatos import consultar_uno, ejecutar

# Blueprint sin prefijo: sus rutas cuelgan directamente de la raíz (/login, /registro...).
bp = Blueprint("auth", __name__)

# Página a la que se envía a cada rol después de iniciar sesión.
INICIO_POR_ROL = {
    "admin": "admin_inv.dashboard",
    "trabajador": "trabajador.pedidos",
    "cliente": "cliente.catalogo",
}


# ---------------------------------------------------------------- decoradores
def login_requerido(vista):
    """Decorador: exige que el usuario haya iniciado sesión.

    - Página normal sin sesión -> redirige a /login (y guarda en ?next= la página
      que quería ver, para volver a ella tras entrar).
    - Petición de API (JSON) sin sesión -> responde 401 en vez de redirigir,
      porque el JavaScript no puede seguir una redirección a una página HTML.
    """
    @wraps(vista)  # conserva el nombre de la función original (Flask lo necesita)
    def envoltura(*args, **kwargs):
        if g.usuario is None:
            if request.is_json or request.path.startswith("/api") or "/api/" in request.path:
                abort(401)
            return redirect(url_for("auth.login", next=request.path))
        return vista(*args, **kwargs)
    return envoltura


def rol_requerido(*roles):
    """Decorador con parámetros: exige sesión Y que el rol esté entre `roles`.

    Si el usuario tiene sesión pero otro rol, responde 403 (prohibido).
    Ejemplo:
        @bp.route("/ventas")
        @rol_requerido("admin")
        def ventas(): ...
    """
    def decorador(vista):
        @wraps(vista)
        @login_requerido  # primero comprueba la sesión
        def envoltura(*args, **kwargs):
            if g.usuario["rol"] not in roles:
                abort(403)
            return vista(*args, **kwargs)
        return envoltura
    return decorador


# ---------------------------------------------------------------- csrf
def csrf_token():
    """Devuelve el token CSRF de la sesión (lo crea la primera vez).

    CSRF = ataque en el que otra web envía un formulario a nuestra app usando la
    sesión abierta del usuario. Para evitarlo, cada formulario lleva un token
    secreto aleatorio que solo nuestras páginas conocen; si no coincide, se rechaza.
    """
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def _verificar_csrf():
    """Rechaza (400) cualquier POST/PUT/PATCH/DELETE que no traiga el token correcto."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        enviado = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        if not enviado or enviado != session.get("_csrf"):
            abort(400, description="Token CSRF inválido. Recarga la página e inténtalo de nuevo.")


def _cargar_usuario():
    """Lee el id guardado en la sesión y carga ese usuario en `g.usuario`.

    Si el usuario fue desactivado o borrado mientras tenía la sesión abierta,
    se limpia la sesión (queda deslogueado en la siguiente petición).
    """
    uid = session.get("usuario_id")
    g.usuario = None
    if uid:
        u = consultar_uno(
            "SELECT id, nombre, email, rol FROM usuarios WHERE id = %s AND activo = 1", (uid,)
        )
        if u:
            g.usuario = u
        else:
            session.clear()


def init_app(app):
    """Registra en la app las funciones que se ejecutan en TODAS las peticiones."""

    @app.before_request
    def _antes():
        """Antes de cada petición: carga el usuario y verifica el token CSRF."""
        if request.endpoint == "static":  # archivos CSS/JS/imágenes: no hace falta
            return
        _cargar_usuario()
        # En las pruebas automáticas se puede desactivar el CSRF con TESTING_SIN_CSRF.
        if not app.config.get("TESTING_SIN_CSRF"):
            _verificar_csrf()

    @app.context_processor
    def _contexto():
        """Variables disponibles en todas las plantillas sin pasarlas a mano."""
        return {"usuario_actual": g.get("usuario"), "csrf_token": csrf_token}


# ---------------------------------------------------------------- rutas
@bp.route("/")
def inicio():
    """Página raíz: sin sesión -> login; con sesión -> página de inicio de su rol."""
    if g.usuario is None:
        return redirect(url_for("auth.login"))
    return redirect(url_for(INICIO_POR_ROL[g.usuario["rol"]]))


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Inicio de sesión.

    GET  -> muestra el formulario.
    POST -> busca el usuario activo por correo y compara la contraseña con el hash
            guardado. Si es correcta, guarda su id en la sesión y lo redirige.
    """
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        clave = request.form.get("password", "")
        u = consultar_uno("SELECT * FROM usuarios WHERE email = %s AND activo = 1", (email,))
        if u and check_password_hash(u["password_hash"], clave):
            session.clear()  # sesión nueva: evita reutilizar una sesión anterior
            session["usuario_id"] = u["id"]
            # Volver a la página que quería ver, solo si es una ruta interna
            # (empieza por "/" pero no por "//", que sería otro dominio).
            destino = request.args.get("next")
            if destino and destino.startswith("/") and not destino.startswith("//"):
                return redirect(destino)
            return redirect(url_for(INICIO_POR_ROL[u["rol"]]))
        # Mismo mensaje si falla el correo o la contraseña: no revela cuál era.
        flash("Correo o contraseña incorrectos.", "error")
    return render_template("login.html")


@bp.route("/registro", methods=["GET", "POST"])
def registro():
    """Registro público de clientes.

    Siempre crea usuarios con rol 'cliente', aunque alguien envíe otro rol en el
    formulario. Los admin y trabajadores solo los crea un admin (módulo usuarios).
    La contraseña se guarda como hash (nunca en texto plano).
    """
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        email = request.form.get("email", "").strip().lower()
        clave = request.form.get("password", "")
        if not nombre or not email or len(clave) < 6:
            flash("Completa todos los campos (contraseña mínimo 6 caracteres).", "error")
        elif consultar_uno("SELECT id FROM usuarios WHERE email = %s", (email,)):
            flash("Ya existe una cuenta con ese correo.", "error")
        else:
            uid = ejecutar(
                "INSERT INTO usuarios (nombre, email, password_hash, rol) VALUES (%s,%s,%s,'cliente')",
                (nombre, email, generate_password_hash(clave)),
            )
            # Inicia sesión automáticamente con la cuenta recién creada.
            session.clear()
            session["usuario_id"] = uid
            flash("¡Cuenta creada! Bienvenido.", "ok")
            return redirect(url_for("cliente.catalogo"))
    return render_template("registro.html")


@bp.route("/logout", methods=["POST"])
def logout():
    """Cierra la sesión. Es POST (no GET) para que un enlace externo no pueda desloguearte."""
    session.clear()
    return redirect(url_for("auth.login"))

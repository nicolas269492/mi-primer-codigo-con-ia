"""Admin – Gestión de usuarios (crear, editar, activar/desactivar).

Rutas (solo rol 'admin'):
    /admin/usuarios/                 listado con filtros por rol, estado y texto
    /admin/usuarios/nuevo            crear usuario de cualquier rol
    /admin/usuarios/<id>/editar      editar datos, rol y (opcional) contraseña
    /admin/usuarios/<id>/activo      (POST) activar/desactivar

Los usuarios no se borran: se desactivan, para conservar su historial de
pedidos y ventas. Un usuario desactivado no puede iniciar sesión.
"""
import re

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   url_for)
from mysql.connector import IntegrityError
from werkzeug.security import generate_password_hash

from src.auth import rol_requerido
from src.basedatos import consultar, consultar_uno, ejecutar

bp = Blueprint("admin_usuarios", __name__, url_prefix="/admin/usuarios")

ROLES = ("admin", "trabajador", "cliente")
ETIQUETAS_ROL = {"admin": "Administrador", "trabajador": "Trabajador", "cliente": "Cliente"}
# Validación sencilla de correo: algo@algo.algo, sin espacios.
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MSG_EMAIL_DUPLICADO = "Ya existe un usuario con el correo «{}». Usa otro correo."


@bp.context_processor
def _contexto():
    """Pone ROLES y ETIQUETAS_ROL a disposición de las plantillas de este módulo."""
    return {"ROLES": ROLES, "ETIQUETAS_ROL": ETIQUETAS_ROL}


def _usuario_o_404(usuario_id):
    """Devuelve el usuario (sin el hash de la contraseña) o responde 404."""
    u = consultar_uno(
        "SELECT id, nombre, email, rol, activo, creado_en FROM usuarios WHERE id = %s",
        (usuario_id,),
    )
    if not u:
        abort(404)
    return u


def _validar(datos, usuario_id=None, clave_obligatoria=True):
    """Valida los datos del formulario. Devuelve una lista de errores (vacía = todo bien).

    Parámetros:
        datos:             dict leído con _leer_form().
        usuario_id:        al editar, el id del propio usuario (para que su correo
                           no cuente como duplicado).
        clave_obligatoria: True al crear; al editar es False y la contraseña solo
                           se valida si se escribió una nueva.
    """
    errores = []
    if not datos["nombre"]:
        errores.append("El nombre es obligatorio.")
    elif len(datos["nombre"]) > 120:
        errores.append("El nombre no puede superar 120 caracteres.")
    if not _RE_EMAIL.match(datos["email"]) or len(datos["email"]) > 120:
        errores.append("Escribe un correo electrónico válido.")
    if datos["rol"] not in ROLES:
        errores.append("Rol inválido.")
    if clave_obligatoria or datos["password"]:
        if len(datos["password"]) < 6:
            errores.append("La contraseña debe tener al menos 6 caracteres.")
    # Solo se consulta la base si lo demás está bien.
    if not errores:
        sql, params = "SELECT id FROM usuarios WHERE email = %s", [datos["email"]]
        if usuario_id:
            sql += " AND id <> %s"
            params.append(usuario_id)
        if consultar_uno(sql, tuple(params)):
            errores.append(MSG_EMAIL_DUPLICADO.format(datos["email"]))
    return errores


def _leer_form():
    """Lee los campos del formulario de usuario (correo en minúsculas, sin espacios)."""
    return {
        "nombre": (request.form.get("nombre") or "").strip(),
        "email": (request.form.get("email") or "").strip().lower(),
        "rol": request.form.get("rol") or "",
        "password": request.form.get("password") or "",
    }


@bp.route("/")
@rol_requerido("admin")
def usuarios():
    """Listado de usuarios con filtros ?rol=, ?buscar= (nombre/correo) y ?estado=activo|inactivo.

    También cuenta cuántos usuarios activos hay de cada rol y cuántos pedidos
    tiene cada usuario.
    """
    rol = request.args.get("rol") or ""
    if rol not in ROLES:
        rol = ""
    buscar = (request.args.get("buscar") or "").strip()
    estado = request.args.get("estado") or ""
    sql = (
        "SELECT u.id, u.nombre, u.email, u.rol, u.activo, u.creado_en, "
        "(SELECT COUNT(*) FROM pedidos p WHERE p.cliente_id = u.id) AS num_pedidos "
        "FROM usuarios u WHERE 1=1"
    )
    params = []
    if rol:
        sql += " AND u.rol = %s"
        params.append(rol)
    if buscar:
        sql += " AND (u.nombre LIKE %s OR u.email LIKE %s)"
        params += [f"%{buscar}%"] * 2
    if estado in ("activo", "inactivo"):
        sql += " AND u.activo = %s"
        params.append(1 if estado == "activo" else 0)
    sql += " ORDER BY u.activo DESC, u.rol, u.nombre"
    lista = consultar(sql, tuple(params))
    conteo = {r: 0 for r in ROLES}
    for f in consultar("SELECT rol, COUNT(*) AS n FROM usuarios WHERE activo = 1 GROUP BY rol"):
        conteo[f["rol"]] = int(f["n"])
    return render_template("admin/usuarios.html", usuarios=lista, rol=rol, buscar=buscar,
                           estado=estado, conteo=conteo)


@bp.route("/nuevo", methods=["GET", "POST"])
@rol_requerido("admin")
def nuevo():
    """Crear usuario (admin, trabajador o cliente). La contraseña se guarda como hash."""
    datos = {"nombre": "", "email": "", "rol": "cliente", "password": ""}
    if request.method == "POST":
        datos = _leer_form()
        errores = _validar(datos)
        if not errores:
            try:
                uid = ejecutar(
                    "INSERT INTO usuarios (nombre, email, password_hash, rol) VALUES (%s,%s,%s,%s)",
                    (datos["nombre"], datos["email"], generate_password_hash(datos["password"]),
                     datos["rol"]),
                )
                flash(f"Usuario «{datos['nombre']}» creado (#{uid}).", "ok")
                return redirect(url_for(".usuarios"))
            except IntegrityError:
                # Por si otro admin creó el mismo correo justo al mismo tiempo.
                errores.append(MSG_EMAIL_DUPLICADO.format(datos["email"]))
        for e in errores:
            flash(e, "error")
    return render_template("admin/usuario_form.html", usuario=None, datos=datos)


@bp.route("/<int:usuario_id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def editar(usuario_id):
    """Editar nombre, correo, rol y, opcionalmente, la contraseña de un usuario.

    - Si el campo contraseña se deja vacío, se conserva la actual.
    - Un admin no puede quitarse a sí mismo el rol de administrador (evita que
      el sistema se quede sin nadie que lo administre).
    """
    usuario = _usuario_o_404(usuario_id)
    es_yo = usuario_id == g.usuario["id"]
    datos = {"nombre": usuario["nombre"], "email": usuario["email"], "rol": usuario["rol"],
             "password": ""}
    if request.method == "POST":
        datos = _leer_form()
        errores = _validar(datos, usuario_id=usuario_id, clave_obligatoria=False)
        if es_yo and datos["rol"] != "admin":
            errores.append("No puedes quitarte el rol de administrador a ti mismo.")
            datos["rol"] = "admin"
        if not errores:
            try:
                if datos["password"]:
                    ejecutar(
                        "UPDATE usuarios SET nombre=%s, email=%s, rol=%s, password_hash=%s WHERE id=%s",
                        (datos["nombre"], datos["email"], datos["rol"],
                         generate_password_hash(datos["password"]), usuario_id),
                    )
                else:
                    ejecutar(
                        "UPDATE usuarios SET nombre=%s, email=%s, rol=%s WHERE id=%s",
                        (datos["nombre"], datos["email"], datos["rol"], usuario_id),
                    )
                extra = " Contraseña cambiada." if datos["password"] else ""
                flash(f"Usuario «{datos['nombre']}» actualizado.{extra}", "ok")
                return redirect(url_for(".usuarios"))
            except IntegrityError:
                errores.append(MSG_EMAIL_DUPLICADO.format(datos["email"]))
        for e in errores:
            flash(e, "error")
    datos["password"] = ""  # nunca se devuelve la contraseña escrita al formulario
    return render_template("admin/usuario_form.html", usuario=usuario, datos=datos, es_yo=es_yo)


@bp.route("/<int:usuario_id>/activo", methods=["POST"])
@rol_requerido("admin")
def cambiar_activo(usuario_id):
    """Activa o desactiva un usuario. Un admin no puede desactivar su propia cuenta."""
    usuario = _usuario_o_404(usuario_id)
    if usuario_id == g.usuario["id"]:
        flash("No puedes desactivar tu propia cuenta.", "error")
        return redirect(url_for(".usuarios"))
    nuevo = 0 if usuario["activo"] else 1
    ejecutar("UPDATE usuarios SET activo = %s WHERE id = %s", (nuevo, usuario_id))
    flash(f"Usuario «{usuario['nombre']}» {'activado' if nuevo else 'desactivado'}.", "ok")
    return redirect(url_for(".usuarios"))

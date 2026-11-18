"""Módulo cliente: catálogo con disponibilidad en vivo, carrito en sesión y pedidos.

Solo lo puede usar el rol 'cliente'.

Páginas:
    /cliente/                          catálogo
    /cliente/carrito                   carrito y envío del pedido
    /cliente/mis_pedidos               historial de pedidos del cliente
    /cliente/pedidos/<id>              detalle con línea de tiempo
API JSON (la usa static/js/cliente.js):
    GET  /cliente/api/catalogo         productos y disponibilidad (cada 15 s)
    GET  /cliente/api/carrito          resumen del carrito
    POST /cliente/api/carrito          agregar o fijar cantidad
    DELETE /cliente/api/carrito/<id>   quitar producto
    POST /cliente/api/carrito/vaciar   vaciar carrito

El carrito vive en session["carrito"] = {"<producto_id>": cantidad}.
(La sesión de Flask es una cookie firmada; las claves deben ser texto, por eso el id va como string.)
Al cliente solo se le muestra `disponible` (stock - reservado), nunca el stock interno.
"""
from datetime import datetime

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)

from src import servicios
from src.auth import rol_requerido

bp = Blueprint("cliente", __name__, url_prefix="/cliente")

MAX_POR_LINEA = 999  # tope de unidades de un mismo producto en el carrito


# ---------------------------------------------------------------- utilidades
def _imagen_url(nombre):
    """URL pública de la imagen de un producto, o None si no tiene imagen."""
    if not nombre:
        return None
    return url_for("static", filename="img/productos/" + nombre)


def _producto_publico(p):
    """Datos de un producto aptos para el cliente (sin stock interno ni código).

    Es lo que viaja al navegador, tanto en la página como en la API.
    """
    return {
        "id": p["id"],
        "nombre": p["nombre"],
        "categoria": p["categoria"],
        "descripcion": p.get("descripcion") or "",
        "precio": float(p["precio"]),
        "disponible": int(p["disponible"]),
        "stock_minimo": int(p["stock_minimo"]),  # para avisar "¡Últimas unidades!"
        "imagen_url": _imagen_url(p.get("imagen")),
    }


def _carrito():
    """Copia del carrito guardado en la sesión ({} si está vacío)."""
    return dict(session.get("carrito") or {})


def _guardar_carrito(carrito):
    """Guarda el carrito en la sesión, quitando las líneas con cantidad 0 o negativa.

    session.modified = True le indica a Flask que debe reenviar la cookie.
    """
    session["carrito"] = {k: int(v) for k, v in carrito.items() if int(v) > 0}
    session.modified = True


def _a_entero(valor, defecto=None):
    """Convierte a entero; si no se puede, devuelve `defecto`."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return defecto


def _resumen_carrito(ajustar=True):
    """Líneas del carrito con precio y disponibilidad ACTUALES.

    Como otros clientes pueden comprar mientras tanto, cada vez que se consulta
    el carrito se revalida contra la base de datos:
      - productos desactivados, inexistentes o agotados se quitan;
      - si una cantidad supera lo disponible, se baja a lo disponible.
    Cada ajuste genera un aviso para mostrar al cliente.

    Devuelve: {"lineas": [...], "total": float, "articulos": int, "avisos": [str]}
    Si `ajustar` es True, los cambios se guardan en la sesión.
    """
    carrito = _carrito()
    lineas, avisos, total, articulos = [], [], 0.0, 0
    cambiado = False
    for pid, cant in list(carrito.items()):  # list(): se modifica el dict dentro del bucle
        p = servicios.obtener_producto(_a_entero(pid, 0))
        if not p or not p["activo"]:
            avisos.append("Un producto de tu carrito ya no está disponible y se quitó.")
            carrito.pop(pid)
            cambiado = True
            continue
        disponible = int(p["disponible"])
        if disponible <= 0:
            avisos.append(f"'{p['nombre']}' se agotó y se quitó del carrito.")
            carrito.pop(pid)
            cambiado = True
            continue
        if cant > disponible:
            avisos.append(
                f"Solo quedan {disponible} unidades de '{p['nombre']}'; ajustamos tu cantidad "
                f"de {cant} a {disponible}."
            )
            cant = disponible
            carrito[pid] = cant
            cambiado = True
        datos = _producto_publico(p)
        subtotal = datos["precio"] * cant
        datos.update(cantidad=cant, subtotal=subtotal)
        lineas.append(datos)
        total += subtotal
        articulos += cant
    if cambiado and ajustar:
        _guardar_carrito(carrito)
    lineas.sort(key=lambda l: l["nombre"].lower())
    return {"lineas": lineas, "total": total, "articulos": articulos, "avisos": avisos}


# ---------------------------------------------------------------- catálogo
@bp.route("/")
@rol_requerido("cliente")
def catalogo():
    """Página del catálogo. Se envían los productos iniciales y luego el JS los actualiza."""
    productos = [_producto_publico(p) for p in servicios.listar_productos(solo_activos=True)]
    return render_template(
        "cliente/catalogo.html",
        productos=productos,
        categorias=servicios.categorias(),
        carrito=_carrito(),
    )


@bp.route("/api/catalogo")
@rol_requerido("cliente")
def api_catalogo():
    """JSON con los productos activos y su disponibilidad actual.

    El catálogo lo consulta cada 15 s para mostrar agotados, nuevos productos y
    cambios de precio sin recargar la página.
    """
    productos = [_producto_publico(p) for p in servicios.listar_productos(solo_activos=True)]
    return jsonify(
        productos=productos,
        carrito=_carrito(),
        generado=datetime.now().isoformat(timespec="seconds"),
    )


# ---------------------------------------------------------------- carrito (API)
@bp.route("/api/carrito", methods=["GET"])
@rol_requerido("cliente")
def api_carrito_resumen():
    """JSON con el resumen del carrito (también actualiza el contador del menú)."""
    return jsonify(ok=True, **_resumen_carrito())


@bp.route("/api/carrito", methods=["POST"])
@rol_requerido("cliente")
def api_carrito_agregar():
    """Agrega un producto al carrito o cambia su cantidad.

    Recibe JSON {producto_id, cantidad, modo}:
        modo 'agregar' (por defecto) -> suma `cantidad` a lo que ya había
        modo 'fijar'                 -> reemplaza la cantidad (0 = quitar)
    La cantidad se limita a lo disponible (con aviso). Respuestas:
        400 datos inválidos, 404 producto inexistente, 409 producto agotado.
    """
    datos = request.get_json(silent=True) or {}
    pid = _a_entero(datos.get("producto_id"))
    cantidad = _a_entero(datos.get("cantidad"), 1)
    modo = datos.get("modo", "agregar")
    if pid is None or cantidad is None:
        return jsonify(ok=False, error="Datos inválidos."), 400

    p = servicios.obtener_producto(pid)
    if not p or not p["activo"]:
        return jsonify(ok=False, error="El producto no existe o ya no está disponible."), 404

    carrito = _carrito()
    clave = str(pid)
    actual = carrito.get(clave, 0)
    nueva = cantidad if modo == "fijar" else actual + cantidad
    aviso = None

    if nueva <= 0:
        carrito.pop(clave, None)
    else:
        disponible = int(p["disponible"])
        if disponible <= 0:
            carrito.pop(clave, None)
            _guardar_carrito(carrito)
            return jsonify(ok=False, error=f"'{p['nombre']}' está agotado.",
                           **_resumen_carrito()), 409
        tope = min(disponible, MAX_POR_LINEA)
        if nueva > tope:
            aviso = f"Solo hay {disponible} unidades disponibles de '{p['nombre']}'; " \
                    f"tu carrito quedó con {tope}."
            nueva = tope
        carrito[clave] = nueva
    _guardar_carrito(carrito)
    resumen = _resumen_carrito()
    if aviso:
        resumen["avisos"].insert(0, aviso)
    return jsonify(ok=True, mensaje=f"'{p['nombre']}' en el carrito: {carrito.get(clave, 0)}.",
                   **resumen)


# Dos URLs para la misma función: DELETE (forma REST) o POST .../quitar (alternativa).
@bp.route("/api/carrito/<int:producto_id>", methods=["DELETE"])
@bp.route("/api/carrito/<int:producto_id>/quitar", methods=["POST"])
@rol_requerido("cliente")
def api_carrito_quitar(producto_id):
    """Quita un producto del carrito y devuelve el resumen actualizado."""
    carrito = _carrito()
    carrito.pop(str(producto_id), None)
    _guardar_carrito(carrito)
    return jsonify(ok=True, **_resumen_carrito())


@bp.route("/api/carrito/vaciar", methods=["POST"])
@rol_requerido("cliente")
def api_carrito_vaciar():
    """Vacía el carrito por completo."""
    _guardar_carrito({})
    return jsonify(ok=True, **_resumen_carrito())


# ---------------------------------------------------------------- carrito (página)
@bp.route("/carrito")
@rol_requerido("cliente")
def carrito():
    """Página del carrito. Si un envío anterior falló, recupera las notas que había escrito."""
    resumen = _resumen_carrito()
    return render_template("cliente/carrito.html", resumen=resumen,
                           notas=session.pop("notas_pedido", ""))


@bp.route("/carrito/enviar", methods=["POST"])
@rol_requerido("cliente")
def enviar_pedido():
    """Convierte el carrito en un pedido (servicios.crear_pedido).

    - Éxito: vacía el carrito y lleva al detalle del pedido nuevo.
    - Error (p. ej. alguien compró antes y ya no alcanza): conserva el carrito
      y las notas, y vuelve al carrito mostrando el motivo.
    """
    notas = request.form.get("notas", "").strip()[:500]
    carrito = _carrito()
    if not carrito:
        flash("Tu carrito está vacío.", "error")
        return redirect(url_for("cliente.carrito"))
    items = {int(pid): int(cant) for pid, cant in carrito.items()}
    try:
        pedido_id = servicios.crear_pedido(g.usuario["id"], items, notas)
    except servicios.ErrorInventario as e:
        session["notas_pedido"] = notas
        flash(f"No se pudo enviar el pedido: {e}", "error")
        return redirect(url_for("cliente.carrito"))
    _guardar_carrito({})
    flash(f"¡Pedido #{pedido_id} enviado! Te avisaremos cuando cambie de estado.", "ok")
    return redirect(url_for("cliente.pedido_detalle", pedido_id=pedido_id))


# ---------------------------------------------------------------- pedidos
@bp.route("/mis_pedidos")
@rol_requerido("cliente")
def mis_pedidos():
    """Lista de pedidos del cliente que inició sesión (nunca los de otros)."""
    pedidos = servicios.listar_pedidos(cliente_id=g.usuario["id"])
    return render_template("cliente/mis_pedidos.html", pedidos=pedidos)


def _pedido_propio(pedido_id):
    """Devuelve el pedido solo si pertenece al cliente actual.

    Si no existe o es de otro cliente responde 404 (no 403), para no revelar
    que ese número de pedido existe.
    """
    pedido = servicios.obtener_pedido(pedido_id)
    if not pedido or pedido["cliente_id"] != g.usuario["id"]:
        abort(404)
    return pedido


def _linea_tiempo(pedido):
    """Pasos para la línea de tiempo del detalle del pedido.

    Devuelve [{nombre, etiqueta, estado: hecho|actual|futuro, fecha}]:
        normal:    Recibido -> En preparación -> Despachado
        cancelado: Recibido -> Cancelado
    """
    etiquetas = {
        "pendiente": "Recibido",
        "preparacion": "En preparación",
        "despachado": "Despachado",
        "cancelado": "Cancelado",
    }
    estado = pedido["estado"]
    if estado == "cancelado":
        orden = ["pendiente", "cancelado"]
    else:
        orden = ["pendiente", "preparacion", "despachado"]
    idx = orden.index(estado)  # posición del estado actual
    pasos = []
    for i, nombre in enumerate(orden):
        if i < idx:                      # pasos ya superados
            marca, fecha = "hecho", pedido["creado_en"] if i == 0 else None
        elif i == idx:                   # paso actual
            marca = "actual"
            fecha = pedido["creado_en"] if i == 0 else pedido["actualizado_en"]
        else:                            # pasos por venir
            marca, fecha = "futuro", None
        pasos.append({"nombre": nombre, "etiqueta": etiquetas[nombre], "estado": marca,
                      "fecha": fecha})
    return pasos


@bp.route("/pedidos/<int:pedido_id>")
@rol_requerido("cliente")
def pedido_detalle(pedido_id):
    """Detalle de un pedido propio: productos, total y línea de tiempo del estado."""
    pedido = _pedido_propio(pedido_id)
    return render_template("cliente/pedido_detalle.html", pedido=pedido,
                           pasos=_linea_tiempo(pedido))


@bp.route("/pedidos/<int:pedido_id>/cancelar", methods=["POST"])
@rol_requerido("cliente")
def cancelar_pedido(pedido_id):
    """El cliente cancela su pedido, SOLO si sigue 'pendiente' (aún no lo están preparando).

    Al cancelarlo se liberan las unidades reservadas y vuelven al catálogo.
    """
    pedido = _pedido_propio(pedido_id)
    if pedido["estado"] != "pendiente":
        flash("Solo puedes cancelar pedidos que aún estén pendientes.", "error")
    else:
        try:
            servicios.cambiar_estado_pedido(pedido_id, "cancelado", g.usuario["id"])
            flash(f"Pedido #{pedido_id} cancelado.", "ok")
        except servicios.ErrorInventario as e:
            flash(str(e), "error")
    return redirect(url_for("cliente.pedido_detalle", pedido_id=pedido_id))

"""Admin – CRUD de pedidos (crear, ver, editar, cambiar estado, eliminar).

Rutas (solo rol 'admin'):
    /admin/pedidos/                   listado con filtro por estado y cliente
    /admin/pedidos/nuevo              crear un pedido a nombre de un cliente
    /admin/pedidos/<id>               detalle con botones de cambio de estado
    /admin/pedidos/<id>/editar        cambiar líneas, notas o cliente
    /admin/pedidos/<id>/estado        (POST) cambiar estado
    /admin/pedidos/<id>/eliminar      (POST) borrar pedido no despachado

Toda modificación pasa por src.servicios (crear_pedido, actualizar_pedido,
cambiar_estado_pedido, eliminar_pedido), que valida stock y reglas de negocio.
"""
from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   url_for)

from src import servicios
from src.auth import rol_requerido
from src.basedatos import consultar, consultar_uno
from src.servicios import ESTADOS_PEDIDO, TRANSICIONES, ErrorInventario

bp = Blueprint("admin_pedidos", __name__, url_prefix="/admin/pedidos")

# Nombre "bonito" de cada estado para mostrar en pantalla.
ETIQUETAS_ESTADO = {
    "pendiente": "Pendiente",
    "preparacion": "En preparación",
    "despachado": "Despachado",
    "cancelado": "Cancelado",
}
# Texto y estilo (clase CSS) del botón para pasar AL estado indicado
ACCION_ESTADO = {
    "pendiente": ("Volver a pendiente", "btn-sec"),
    "preparacion": ("Pasar a preparación", "btn-primario"),
    "despachado": ("Despachar (genera venta)", "btn-ok"),
    "cancelado": ("Cancelar pedido", "btn-peligro"),
}


@bp.context_processor
def _contexto():
    """Pone estas constantes a disposición de todas las plantillas de este módulo."""
    return {"ETIQUETAS_ESTADO": ETIQUETAS_ESTADO, "ACCION_ESTADO": ACCION_ESTADO,
            "TRANSICIONES": TRANSICIONES}


# ---------------------------------------------------------------- utilidades
def _clientes(solo_activos=True, incluir_id=None):
    """Usuarios con rol 'cliente' para los selectores.

    Parámetros:
        solo_activos: si es True, solo clientes activos.
        incluir_id:   al editar, incluye también al cliente actual del pedido
                      aunque esté inactivo (para no perderlo del selector).
    """
    sql = "SELECT id, nombre, email, activo FROM usuarios WHERE rol = 'cliente'"
    if solo_activos:
        sql += " AND (activo = 1" + (" OR id = %s)" if incluir_id else ")")
    sql += " ORDER BY nombre"
    return consultar(sql, (incluir_id,) if solo_activos and incluir_id else ())


def _lineas_formulario():
    """Líneas tal como llegaron en el formulario (para volver a pintarlas si hay error).

    El formulario envía dos listas paralelas: producto_id[] y cantidad[]; zip()
    las empareja línea a línea. Se ignoran las líneas totalmente vacías.
    """
    lineas = []
    for pid, cant in zip(request.form.getlist("producto_id[]"), request.form.getlist("cantidad[]")):
        pid, cant = (pid or "").strip(), (cant or "").strip()
        if pid or cant:
            lineas.append({"producto_id": pid, "cantidad": cant})
    return lineas


def _leer_lineas():
    """Convierte las líneas del formulario en el dict {producto_id: cantidad} que usa servicios.

    Si un producto aparece en varias líneas, suma sus cantidades.
    Lanza ErrorInventario si falta un producto, una cantidad no es entera o
    positiva, o el pedido no tiene ninguna línea.
    """
    items = {}
    for linea in _lineas_formulario():
        pid, cant = linea["producto_id"], linea["cantidad"]
        if not pid:
            raise ErrorInventario("Selecciona el producto en todas las líneas.")
        try:
            p, c = int(pid), int(cant)
        except ValueError:
            raise ErrorInventario("Las cantidades deben ser números enteros.")
        if c <= 0:
            raise ErrorInventario("Las cantidades deben ser mayores que cero.")
        items[p] = items.get(p, 0) + c
    if not items:
        raise ErrorInventario("Agrega al menos un producto al pedido.")
    return items


def _productos_para_form(pedido=None):
    """Productos activos con su 'disponible', para el selector de líneas del formulario.

    Al editar un pedido abierto, sus propias unidades reservadas se suman al
    disponible (se liberan al reemplazar las líneas). Ejemplo: si hay 10 en
    stock y este mismo pedido reserva 4, al editarlo se pueden pedir hasta 10.
    """
    productos = servicios.listar_productos(solo_activos=True)
    if pedido and pedido["estado"] in ("pendiente", "preparacion"):
        propias = {}
        for it in pedido["items"]:
            propias[it["producto_id"]] = propias.get(it["producto_id"], 0) + int(it["cantidad"])
        for p in productos:
            p["disponible"] += propias.get(p["id"], 0)
    return productos


def _leer_cliente():
    """Lee el cliente_id del formulario y devuelve ese cliente (id, activo) o None.

    Solo devuelve usuarios con rol 'cliente' (no se puede hacer un pedido a nombre
    de un admin o trabajador).
    """
    try:
        cid = int(request.form.get("cliente_id") or 0)
    except ValueError:
        cid = 0
    cliente = consultar_uno(
        "SELECT id, activo FROM usuarios WHERE id = %s AND rol = 'cliente'", (cid,))
    return cliente


def _pedido_o_404(pedido_id):
    """Devuelve el pedido completo (con items) o responde 404 si no existe."""
    pedido = servicios.obtener_pedido(pedido_id)
    if not pedido:
        abort(404)
    return pedido


# ---------------------------------------------------------------- listado / detalle
@bp.route("/")
@rol_requerido("admin")
def pedidos():
    """Listado de pedidos con filtros ?estado= y ?cliente_id= y contador por estado."""
    estado = request.args.get("estado") or ""
    if estado not in ESTADOS_PEDIDO:  # un estado desconocido = sin filtro
        estado = ""
    try:
        cliente_id = int(request.args.get("cliente_id") or 0) or None
    except ValueError:
        cliente_id = None
    lista = servicios.listar_pedidos(estado=estado or None, cliente_id=cliente_id)
    # Cuántos pedidos hay en cada estado (para las pestañas/filtros).
    conteo = {e: 0 for e in ESTADOS_PEDIDO}
    for f in consultar("SELECT estado, COUNT(*) AS n FROM pedidos GROUP BY estado"):
        conteo[f["estado"]] = int(f["n"])
    return render_template(
        "admin/pedidos.html", pedidos=lista, estado=estado, cliente_id=cliente_id,
        clientes=_clientes(solo_activos=False), conteo=conteo, estados=ESTADOS_PEDIDO,
    )


@bp.route("/<int:pedido_id>")
@rol_requerido("admin")
def detalle(pedido_id):
    """Detalle de un pedido: líneas, venta asociada (si se despachó) y botones de estado.

    Solo se muestran los botones de las transiciones permitidas desde el estado actual.
    """
    pedido = _pedido_o_404(pedido_id)
    venta = consultar_uno("SELECT id, fecha, total FROM ventas WHERE pedido_id = %s", (pedido_id,))
    transiciones = [e for e in ESTADOS_PEDIDO if e in TRANSICIONES[pedido["estado"]]]
    return render_template("admin/pedido_detalle.html", pedido=pedido, venta=venta,
                           transiciones=transiciones)


# ---------------------------------------------------------------- crear / editar
@bp.route("/nuevo", methods=["GET", "POST"])
@rol_requerido("admin")
def nuevo():
    """Crear un pedido a nombre de un cliente.

    GET  -> formulario con una línea vacía.
    POST -> valida cliente y líneas y llama a servicios.crear_pedido (que
            comprueba la disponibilidad). Si hay error, vuelve a mostrar el
            formulario con lo que el admin había escrito.
    """
    lineas, cliente_id, notas = [], "", ""
    if request.method == "POST":
        notas = (request.form.get("notas") or "").strip()
        cliente_id = request.form.get("cliente_id") or ""
        lineas = _lineas_formulario()
        try:
            cliente = _leer_cliente()
            if not cliente or not cliente["activo"]:
                raise ErrorInventario("Selecciona un cliente activo.")
            pid = servicios.crear_pedido(cliente["id"], _leer_lineas(), notas)
            flash(f"Pedido #{pid} creado.", "ok")
            return redirect(url_for(".detalle", pedido_id=pid))
        except ErrorInventario as e:
            flash(str(e), "error")
    return render_template(
        "admin/pedido_form.html", pedido=None, clientes=_clientes(),
        productos=_productos_para_form(), lineas=lineas or [{"producto_id": "", "cantidad": "1"}],
        cliente_id=str(cliente_id), notas=notas,
    )


@bp.route("/<int:pedido_id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def editar(pedido_id):
    """Editar un pedido pendiente o en preparación (líneas, notas y cliente).

    Los despachados o cancelados no se pueden editar: redirige al detalle con aviso.
    """
    pedido = _pedido_o_404(pedido_id)
    if pedido["estado"] not in ("pendiente", "preparacion"):
        flash("Solo se pueden editar pedidos pendientes o en preparación.", "error")
        return redirect(url_for(".detalle", pedido_id=pedido_id))

    # Valores iniciales del formulario = los del pedido actual.
    lineas = [{"producto_id": str(it["producto_id"]), "cantidad": str(it["cantidad"])}
              for it in pedido["items"]]
    cliente_id, notas = str(pedido["cliente_id"]), pedido["notas"]
    if request.method == "POST":
        notas = (request.form.get("notas") or "").strip()
        cliente_id = request.form.get("cliente_id") or ""
        lineas = _lineas_formulario()
        try:
            cliente = _leer_cliente()
            # Se permite mantener el cliente actual aunque esté inactivo,
            # pero no cambiar a otro cliente inactivo.
            if not cliente or (not cliente["activo"] and cliente["id"] != pedido["cliente_id"]):
                raise ErrorInventario("Selecciona un cliente activo.")
            servicios.actualizar_pedido(pedido_id, _leer_lineas(), notas=notas,
                                        cliente_id=cliente["id"])
            flash(f"Pedido #{pedido_id} actualizado.", "ok")
            return redirect(url_for(".detalle", pedido_id=pedido_id))
        except ErrorInventario as e:
            flash(str(e), "error")
    return render_template(
        "admin/pedido_form.html", pedido=pedido,
        clientes=_clientes(incluir_id=pedido["cliente_id"]),
        productos=_productos_para_form(pedido), lineas=lineas or [{"producto_id": "", "cantidad": "1"}],
        cliente_id=str(cliente_id), notas=notas,
    )


# ---------------------------------------------------------------- estado / eliminar
@bp.route("/<int:pedido_id>/estado", methods=["POST"])
@rol_requerido("admin")
def cambiar_estado(pedido_id):
    """Cambia el estado del pedido (campo `estado` del formulario).

    Si el nuevo estado es 'despachado', servicios descuenta el stock y crea la
    venta; aquí solo se informa el resultado. El campo opcional `volver=lista`
    hace regresar al listado en lugar de al detalle.
    """
    nuevo_estado = request.form.get("estado", "")
    try:
        venta_id = servicios.cambiar_estado_pedido(pedido_id, nuevo_estado, g.usuario["id"])
        if venta_id:
            flash(f"Pedido #{pedido_id} despachado. Se registró la venta #{venta_id} "
                  "y se descontó el stock.", "ok")
        else:
            flash(f"Pedido #{pedido_id} ahora está «{ETIQUETAS_ESTADO.get(nuevo_estado, nuevo_estado)}».",
                  "ok")
    except ErrorInventario as e:
        flash(str(e), "error")
    destino = request.form.get("volver")
    if destino == "lista":
        return redirect(url_for(".pedidos"))
    return redirect(url_for(".detalle", pedido_id=pedido_id))


@bp.route("/<int:pedido_id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def eliminar(pedido_id):
    """Elimina un pedido que no esté despachado (libera su reserva de stock).

    Si no se puede eliminar, muestra el motivo y vuelve al detalle (o al listado
    si el pedido ya no existe).
    """
    try:
        servicios.eliminar_pedido(pedido_id)
        flash(f"Pedido #{pedido_id} eliminado.", "ok")
        return redirect(url_for(".pedidos"))
    except ErrorInventario as e:
        flash(str(e), "error")
        if consultar_uno("SELECT id FROM pedidos WHERE id = %s", (pedido_id,)):
            return redirect(url_for(".detalle", pedido_id=pedido_id))
        return redirect(url_for(".pedidos"))

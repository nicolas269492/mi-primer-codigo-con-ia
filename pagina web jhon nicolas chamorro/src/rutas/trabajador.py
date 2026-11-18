"""Módulo del trabajador: tablero de pedidos y disponibilidad de productos.

Lo pueden usar los roles 'trabajador' y 'admin'.

Páginas:
    trabajador.pedidos          GET  /trabajador/
    trabajador.pedido           GET  /trabajador/pedido/<id>
    trabajador.cambiar_estado   POST /trabajador/pedido/<id>/estado   (respaldo sin JS)
    trabajador.disponibilidad   GET  /trabajador/disponibilidad
API JSON (la usa static/js/trabajador.js para actualizar sin recargar la página):
    trabajador.api_pedidos         GET  /trabajador/api/pedidos
    trabajador.api_estado          POST /trabajador/api/pedidos/<id>/estado  {estado}
    trabajador.api_disponibilidad  GET  /trabajador/api/disponibilidad?buscar=&categoria=&estado=
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
                   url_for)

from src import servicios
from src.auth import rol_requerido
from src.basedatos import consultar
from src.servicios import ErrorInventario

bp = Blueprint("trabajador", __name__, url_prefix="/trabajador")

# Acciones disponibles por estado: (estado destino, texto del botón, clase, confirmación)
# Si "confirmación" no está vacía, se pide confirmar antes de hacer el cambio.
ACCIONES = {
    "pendiente": [
        ("preparacion", "Empezar a preparar", "btn-primario", ""),
        ("cancelado", "Cancelar", "btn-sec", "¿Cancelar este pedido? Esta acción no se puede deshacer."),
    ],
    "preparacion": [
        ("despachado", "Marcar despachado", "btn-ok", "¿Confirmas el despacho? Se descontará el stock."),
        ("pendiente", "Devolver a pendiente", "btn-sec", ""),
        ("cancelado", "Cancelar", "btn-sec", "¿Cancelar este pedido? Esta acción no se puede deshacer."),
    ],
    "despachado": [],   # estados finales: sin acciones
    "cancelado": [],
}

# Nombre de cada estado para mostrar en pantalla.
NOMBRES_ESTADO = {
    "pendiente": "Pendiente",
    "preparacion": "En preparación",
    "despachado": "Despachado",
    "cancelado": "Cancelado",
}

# Mensaje de éxito según el estado al que se pasó ({id} se reemplaza por el nº de pedido).
MENSAJES_OK = {
    "preparacion": "Pedido #{id} en preparación.",
    "despachado": "Pedido #{id} despachado. Stock descontado y venta registrada.",
    "pendiente": "Pedido #{id} devuelto a pendiente.",
    "cancelado": "Pedido #{id} cancelado.",
}


# ---------------------------------------------------------------- utilidades
def _hace(fecha):
    """Antigüedad legible de una fecha: 'hace 25 min', 'hace 3 h', 'hace 2 días'.

    Sirve para que el trabajador vea de un vistazo qué pedidos llevan más tiempo esperando.
    """
    if not fecha:
        return ""
    seg = max(0, int((datetime.now() - fecha).total_seconds()))
    if seg < 60:
        return "hace un momento"
    minutos = seg // 60
    if minutos < 60:
        return f"hace {minutos} min"
    horas = minutos // 60
    if horas < 24:
        return f"hace {horas} h {minutos % 60} min" if minutos % 60 else f"hace {horas} h"
    dias = horas // 24
    return "hace 1 día" if dias == 1 else f"hace {dias} días"


def _pedidos_sin_stock():
    """Ids de pedidos abiertos con algún ítem cuya cantidad supera el stock actual.

    Esos pedidos no se podrán despachar hasta que entre mercancía; el tablero
    los marca en rojo para que el trabajador lo sepa antes de intentarlo.
    """
    filas = consultar(
        "SELECT DISTINCT d.pedido_id FROM pedido_detalle d "
        "JOIN pedidos pe ON pe.id = d.pedido_id JOIN productos p ON p.id = d.producto_id "
        "WHERE pe.estado IN ('pendiente', 'preparacion') AND d.cantidad > p.stock"
    )
    return {f["pedido_id"] for f in filas}


def _tablero():
    """Pedidos agrupados por columna del tablero.

    Devuelve un dict {estado: [tarjetas]} con:
        pendiente y preparacion -> todos (el más antiguo primero = orden de atención)
        despachado y cancelado  -> solo los de HOY (para no llenar el tablero)
    Cada tarjeta es un dict listo para JSON con id, cliente, fechas, antigüedad,
    unidades, total, notas, aviso de falta de stock y botones de acción.
    """
    sin_stock = _pedidos_sin_stock()
    hoy = date.today()
    grupos = {"pendiente": [], "preparacion": [], "despachado": [], "cancelado": []}
    for p in servicios.listar_pedidos():
        estado = p["estado"]
        if estado in ("despachado", "cancelado"):
            # Se usa la fecha de última actualización como "fecha de despacho/cancelación".
            if not p["actualizado_en"] or p["actualizado_en"].date() != hoy:
                continue
        grupos[estado].append({
            "id": p["id"],
            "cliente": p["cliente_nombre"],
            "estado": estado,
            "fecha": p["creado_en"].strftime("%d/%m/%Y %H:%M"),
            "hora": p["creado_en"].strftime("%H:%M"),
            "hace": _hace(p["creado_en"]),
            "actualizado": p["actualizado_en"].strftime("%H:%M") if p["actualizado_en"] else "",
            "unidades": int(p["unidades"] or 0),
            "total": float(p["total"] or 0),
            "notas": p["notas"] or "",
            "sin_stock": p["id"] in sin_stock,
            "acciones": [
                {"estado": e, "texto": t, "clase": c, "confirmar": conf}
                for e, t, c, conf in ACCIONES[estado]
            ],
        })
    # Abiertos: el más antiguo primero (orden de atención). Cerrados: el más reciente primero.
    grupos["pendiente"].sort(key=lambda x: x["id"])
    grupos["preparacion"].sort(key=lambda x: x["id"])
    return grupos


def _estado_producto(p):
    """Clasifica un producto: 'agotado' (disponible 0), 'bajo' (stock <= mínimo) u 'ok'."""
    if p["disponible"] <= 0:
        return "agotado"
    if p["bajo_minimo"]:
        return "bajo"
    return "ok"


def _disponibilidad(buscar="", categoria="", estado=""):
    """Filas de la tabla de disponibilidad (productos activos), con filtros opcionales.

    estado: '' (todos), 'agotado', 'bajo' u 'ok'.
    """
    filas = []
    for p in servicios.listar_productos(solo_activos=True, buscar=buscar, categoria=categoria):
        est = _estado_producto(p)
        if estado and est != estado:
            continue
        filas.append({
            "id": p["id"],
            "codigo": p["codigo"],
            "nombre": p["nombre"],
            "categoria": p["categoria"],
            "stock": int(p["stock"]),
            "reservado": p["reservado"],
            "disponible": p["disponible"],
            "minimo": int(p["stock_minimo"]),
            "estado": est,
        })
    return filas


def _resumen(filas):
    """Totales para los indicadores de arriba: nº de productos, agotados y bajo mínimo."""
    return {
        "total": len(filas),
        "agotados": sum(1 for p in filas if p["estado"] == "agotado"),
        "bajo": sum(1 for p in filas if p["estado"] == "bajo"),
    }


def _cambiar(pedido_id, estado):
    """Aplica el cambio de estado con servicios. Devuelve (ok, mensaje).

    La comparten la versión con JavaScript (API) y la de formulario normal.
    """
    try:
        servicios.cambiar_estado_pedido(pedido_id, estado, g.usuario["id"])
    except ErrorInventario as e:
        return False, str(e)
    return True, MENSAJES_OK.get(estado, "Estado actualizado.").format(id=pedido_id)


# ---------------------------------------------------------------- páginas
@bp.route("/")
@rol_requerido("trabajador", "admin")
def pedidos():
    """Tablero de pedidos. Se dibuja una vez aquí y luego el JS lo refresca cada 15 s."""
    return render_template("trabajador/pedidos.html", grupos=_tablero(),
                           nombres_estado=NOMBRES_ESTADO)


@bp.route("/pedido/<int:pedido_id>")
@rol_requerido("trabajador", "admin")
def pedido(pedido_id):
    """Detalle de un pedido para alistarlo: productos, cantidades y stock actual.

    `faltantes` = líneas cuya cantidad supera el stock (impiden despachar).
    """
    ped = servicios.obtener_pedido(pedido_id)
    if not ped:
        abort(404)
    faltantes = [it for it in ped["items"] if it["cantidad"] > it["stock"]]
    return render_template(
        "trabajador/pedido.html", pedido=ped, faltantes=faltantes,
        acciones=ACCIONES[ped["estado"]], hace=_hace(ped["creado_en"]),
        nombres_estado=NOMBRES_ESTADO,
        unidades=sum(it["cantidad"] for it in ped["items"]),
    )


@bp.route("/pedido/<int:pedido_id>/estado", methods=["POST"])
@rol_requerido("trabajador", "admin")
def cambiar_estado(pedido_id):
    """Cambio de estado por formulario normal (respaldo si el navegador no tiene JavaScript).

    Vuelve al detalle si el formulario trae volver=detalle; si no, al tablero.
    """
    ok, msg = _cambiar(pedido_id, request.form.get("estado", ""))
    flash(msg, "ok" if ok else "error")
    destino = request.form.get("volver")
    if destino == "detalle":
        return redirect(url_for("trabajador.pedido", pedido_id=pedido_id))
    return redirect(url_for("trabajador.pedidos"))


@bp.route("/disponibilidad")
@rol_requerido("trabajador", "admin")
def disponibilidad():
    """Tabla de disponibilidad (solo lectura) con búsqueda y filtros por categoría y estado."""
    buscar = request.args.get("buscar", "").strip()
    categoria = request.args.get("categoria", "").strip()
    estado = request.args.get("estado", "").strip()
    if estado not in ("", "agotado", "bajo", "ok"):
        estado = ""
    return render_template(
        "trabajador/disponibilidad.html",
        productos=_disponibilidad(buscar, categoria, estado),
        categorias=servicios.categorias(),
        buscar=buscar, categoria=categoria, estado=estado,
        resumen=_resumen(_disponibilidad()),  # los totales siempre sobre todos los productos
    )


# ---------------------------------------------------------------- API JSON
@bp.route("/api/pedidos")
@rol_requerido("trabajador", "admin")
def api_pedidos():
    """JSON con los pedidos del tablero y cuántos hay en cada columna.

    El JS lo consulta cada 15 s; si aparece un id nuevo en 'pendiente', avisa
    de que llegó un pedido.
    """
    grupos = _tablero()
    return jsonify({
        "ok": True,
        "generado": datetime.now().strftime("%H:%M:%S"),
        "pedidos": grupos,
        "conteo": {k: len(v) for k, v in grupos.items()},
    })


@bp.route("/api/pedidos/<int:pedido_id>/estado", methods=["POST"])
@rol_requerido("trabajador", "admin")
def api_estado(pedido_id):
    """Cambia el estado desde JavaScript. Recibe JSON {"estado": "..."}.

    Responde {ok: true, mensaje} o, si no se puede (p. ej. falta stock),
    {ok: false, mensaje} con código 400.
    """
    datos = request.get_json(silent=True) or {}  # silent: si no es JSON válido, devuelve None
    estado = str(datos.get("estado", ""))
    ok, msg = _cambiar(pedido_id, estado)
    if not ok:
        return jsonify({"ok": False, "mensaje": msg}), 400
    return jsonify({"ok": True, "mensaje": msg, "estado": estado})


@bp.route("/api/disponibilidad")
@rol_requerido("trabajador", "admin")
def api_disponibilidad():
    """JSON de disponibilidad (mismos filtros que la página). El JS lo consulta cada 30 s."""
    buscar = request.args.get("buscar", "").strip()
    categoria = request.args.get("categoria", "").strip()
    estado = request.args.get("estado", "").strip()
    if estado not in ("", "agotado", "bajo", "ok"):
        estado = ""
    productos = _disponibilidad(buscar, categoria, estado)
    # El resumen se calcula sobre todos los productos; si no hay filtro se reutiliza la lista.
    todos = productos if not (buscar or categoria or estado) else _disponibilidad()
    return jsonify({
        "ok": True,
        "generado": datetime.now().strftime("%H:%M:%S"),
        "productos": productos,
        "resumen": _resumen(todos),
    })

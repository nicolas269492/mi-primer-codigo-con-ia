"""Admin – Registro de ventas.

Las ventas NO se crean a mano: se generan automáticamente al despachar un pedido
(servicios.cambiar_estado_pedido). Aquí solo se consultan, se resumen y se exportan.
"""
import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, Response, abort, flash, render_template, request

from src.auth import rol_requerido
from src.basedatos import consultar, consultar_uno

# Rutas (solo rol 'admin'):
#   /admin/ventas/               listado con filtros, indicadores y más vendidos
#   /admin/ventas/<id>           detalle de una venta
#   /admin/ventas/exportar.csv   descarga del listado filtrado para Excel
bp = Blueprint("admin_ventas", __name__, url_prefix="/admin/ventas")


# ---------------------------------------------------------------- utilidades
def _leer_fecha(nombre):
    """Lee una fecha de la URL (?desde=AAAA-MM-DD) y la convierte a `date`.

    Devuelve (fecha, texto_original). Si el texto está vacío o mal escrito,
    devuelve (None, "") y, si estaba mal escrito, avisa con un mensaje.
    """
    texto = (request.args.get(nombre) or "").strip()
    if not texto:
        return None, ""
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date(), texto
    except ValueError:
        flash(f"Fecha '{texto}' inválida; se ignoró el filtro.", "error")
        return None, ""


def _filtros():
    """Lee los filtros de la petición y devuelve (dict_filtros, sql_where, params).

    Filtros: ?desde, ?hasta (rango de fechas, "hasta" incluye todo ese día) y
    ?cliente (texto que se busca en el nombre o correo del cliente).
    Si el rango viene al revés (desde > hasta), se intercambian las fechas.
    El mismo filtro sirve para el listado, el top de productos y el CSV.
    """
    desde, desde_txt = _leer_fecha("desde")
    hasta, hasta_txt = _leer_fecha("hasta")
    if desde and hasta and desde > hasta:
        flash("La fecha inicial es posterior a la final; se intercambiaron.", "info")
        desde, hasta = hasta, desde
        desde_txt, hasta_txt = hasta_txt, desde_txt
    cliente = (request.args.get("cliente") or "").strip()

    where, params = ["1=1"], []
    if desde:
        where.append("v.fecha >= %s")
        params.append(datetime.combine(desde, datetime.min.time()))
    if hasta:
        where.append("v.fecha < %s")
        params.append(datetime.combine(hasta + timedelta(days=1), datetime.min.time()))
    if cliente:
        where.append("(u.nombre LIKE %s OR u.email LIKE %s)")
        like = f"%{cliente}%"
        params += [like, like]
    filtros = {"desde": desde_txt, "hasta": hasta_txt, "cliente": cliente}
    return filtros, " AND ".join(where), params


def _listar(where, params):
    """Ventas que cumplen el filtro (más recientes primero).

    Cada venta incluye nombre y correo del cliente, quién la registró
    (el trabajador que despachó el pedido) y el total de unidades vendidas.
    """
    filas = consultar(
        "SELECT v.*, u.nombre AS cliente_nombre, u.email AS cliente_email, "
        "us.nombre AS usuario_nombre, "
        "(SELECT COALESCE(SUM(d.cantidad), 0) FROM venta_detalle d WHERE d.venta_id = v.id) AS unidades "
        "FROM ventas v JOIN usuarios u ON u.id = v.cliente_id "
        "LEFT JOIN usuarios us ON us.id = v.usuario_id "
        f"WHERE {where} ORDER BY v.fecha DESC, v.id DESC",
        tuple(params),
    )
    for f in filas:
        f["unidades"] = int(f["unidades"])
    return filas


def _kpis(ventas):
    """Indicadores del listado: nº de ventas, total vendido, ticket promedio y unidades.

    Ticket promedio = total / nº de ventas (0 si no hay ventas, para no dividir por cero).
    """
    n = len(ventas)
    total = sum((Decimal(v["total"]) for v in ventas), Decimal("0"))
    return {
        "num_ventas": n,
        "total": total,
        "ticket": (total / n) if n else Decimal("0"),
        "unidades": sum(v["unidades"] for v in ventas),
    }


def _mas_vendidos(where, params, limite=10):
    """Top de productos más vendidos en el período filtrado.

    Agrupa las líneas de venta por producto y suma unidades e importe
    (cantidad × precio de venta). Ordena por unidades y devuelve `limite` filas.
    """
    filas = consultar(
        "SELECT p.id, p.codigo, p.nombre, p.categoria, SUM(d.cantidad) AS unidades, "
        "SUM(d.cantidad * d.precio_unitario) AS importe, COUNT(DISTINCT v.id) AS num_ventas "
        "FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id "
        "JOIN usuarios u ON u.id = v.cliente_id JOIN productos p ON p.id = d.producto_id "
        f"WHERE {where} GROUP BY p.id, p.codigo, p.nombre, p.categoria "
        "ORDER BY unidades DESC, importe DESC LIMIT %s",
        tuple(params) + (limite,),
    )
    for f in filas:
        f["unidades"] = int(f["unidades"])
    return filas


# ---------------------------------------------------------------- rutas
@bp.route("/")
@rol_requerido("admin")
def ventas():
    """Página principal de ventas: filtros, indicadores, listado y top 10 de productos.

    Los "atajos" son rangos de fechas predefinidos (hoy, últimos 7 días, este
    mes, últimos 30 días) que la plantilla convierte en botones.
    """
    filtros, where, params = _filtros()
    lista = _listar(where, params)
    hoy = date.today()
    atajos = {
        "hoy": (hoy, hoy),
        "7dias": (hoy - timedelta(days=6), hoy),
        "mes": (hoy.replace(day=1), hoy),
        "30dias": (hoy - timedelta(days=29), hoy),
    }
    return render_template(
        "admin/ventas.html",
        ventas=lista,
        kpis=_kpis(lista),
        top=_mas_vendidos(where, params),
        filtros=filtros,
        atajos={k: (a.isoformat(), b.isoformat()) for k, (a, b) in atajos.items()},
    )


@bp.route("/<int:venta_id>")
@rol_requerido("admin")
def detalle(venta_id):
    """Detalle de una venta: cliente, quién la registró, pedido de origen y sus líneas.

    Responde 404 si la venta no existe.
    """
    venta =consultar_uno(
        "SELECT v.*, u.nombre AS cliente_nombre, u.email AS cliente_email, "
        "us.nombre AS usuario_nombre, pe.estado AS pedido_estado, pe.notas AS pedido_notas "
        "FROM ventas v JOIN usuarios u ON u.id = v.cliente_id "
        "LEFT JOIN usuarios us ON us.id = v.usuario_id "
        "LEFT JOIN pedidos pe ON pe.id = v.pedido_id WHERE v.id = %s",
        (venta_id,),
    )
    if not venta:
        abort(404)
    lineas = consultar(
        "SELECT d.*, p.codigo, p.nombre, (d.cantidad * d.precio_unitario) AS subtotal "
        "FROM venta_detalle d JOIN productos p ON p.id = d.producto_id "
        "WHERE d.venta_id = %s ORDER BY d.id",
        (venta_id,),
    )
    unidades = sum(int(l["cantidad"]) for l in lineas)
    return render_template("admin/ventas_detalle.html", venta=venta, lineas=lineas,
                           unidades=unidades)


@bp.route("/exportar.csv")
@rol_requerido("admin")
def exportar_csv():
    """Descarga el listado filtrado como archivo CSV para abrir en Excel.

    - UTF-8 con BOM (marca invisible al inicio) para que Excel muestre bien las tildes.
    - Separador ';' y decimales con coma, como espera Excel configurado en español.
    - Añade al final una fila con el TOTAL de unidades y dinero.
    """
    _filtros_, where, params = _filtros()
    lista = _listar(where, params)

    def numero(valor):
        """1234.5 -> "1234,50" (coma decimal para Excel en español)."""
        return f"{Decimal(valor):.2f}".replace(".", ",")

    # Se escribe el CSV en memoria (StringIO) en lugar de en un archivo del disco.
    salida = io.StringIO()
    salida.write("﻿")
    w = csv.writer(salida, delimiter=";", lineterminator="\r\n")
    w.writerow(["Venta", "Fecha", "Pedido", "Cliente", "Email cliente", "Registrada por",
                "Unidades", "Total"])
    for v in lista:
        w.writerow([
            v["id"],
            v["fecha"].strftime("%Y-%m-%d %H:%M"),
            v["pedido_id"] or "",
            v["cliente_nombre"],
            v["cliente_email"],
            v["usuario_nombre"] or "",
            v["unidades"],
            numero(v["total"]),
        ])
    k = _kpis(lista)
    w.writerow([])
    w.writerow(["", "", "", "", "", "TOTAL", k["unidades"], numero(k["total"])])

    nombre = f"ventas_{date.today():%Y%m%d}.csv"
    return Response(
        salida.getvalue().encode("utf-8"),
        mimetype="text/csv",
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{nombre}"',
        },
    )

"""Módulo ADMIN – INVENTARIO: panel, productos (CRUD), entradas/salidas y estancados.

Todas las rutas empiezan por /admin y solo las puede usar el rol 'admin'.

    /admin/                          panel con indicadores (dashboard)
    /admin/productos                 listado, crear, editar, activar/desactivar, eliminar
    /admin/movimientos               registrar entradas/salidas e historial
    /admin/estancados                productos sin movimiento y umbral de días

Regla: el stock solo cambia con servicios.registrar_movimiento.
"""
import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import mysql.connector
from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, url_for)
from werkzeug.utils import secure_filename

from src import servicios
from src.auth import rol_requerido
from src.basedatos import consultar, consultar_uno, ejecutar, guardar_config, transaccion
from src.servicios import ErrorInventario

# Blueprint = grupo de rutas. "admin_inv" es el nombre usado en url_for("admin_inv.xxx").
bp = Blueprint("admin_inv", __name__, url_prefix="/admin")

EXTENSIONES_IMG = {"png", "jpg", "jpeg", "webp"}  # formatos de imagen permitidos
POR_PAGINA = 50                                    # filas por página en el historial


# ---------------------------------------------------------------- utilidades
def _entero(valor, por_defecto=None):
    """Convierte un texto del formulario a entero; si no se puede, devuelve `por_defecto`.

    Ejemplo: _entero(" 12 ") -> 12 ;  _entero("abc", 0) -> 0
    """
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return por_defecto


def _fecha(valor):
    """'YYYY-MM-DD' -> date o None."""
    try:
        return datetime.strptime((valor or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _guardar_imagen(archivo):
    """Guarda la imagen subida y devuelve el nombre de archivo (o None si no se subió).

    - secure_filename limpia el nombre (quita rutas como "../" y caracteres raros).
    - Se añade un prefijo aleatorio (uuid) para que dos imágenes con el mismo
      nombre no se sobrescriban.
    - Lanza ErrorInventario si la extensión no es una imagen permitida.
    """
    if not archivo or not archivo.filename:
        return None
    nombre = secure_filename(archivo.filename)
    ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    if ext not in EXTENSIONES_IMG:
        raise ErrorInventario("La imagen debe ser PNG, JPG, JPEG o WEBP.")
    base = nombre.rsplit(".", 1)[0][:60] or "imagen"
    final = f"{uuid.uuid4().hex[:12]}_{base}.{ext}"
    archivo.save(os.path.join(current_app.config["UPLOAD_FOLDER"], final))
    return final


def _borrar_imagen(nombre):
    """Borra del disco la imagen de un producto (si existe). Nunca lanza error.

    os.path.basename evita que un nombre manipulado borre archivos fuera de la carpeta.
    """
    if not nombre:
        return
    ruta = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(nombre))
    try:
        if os.path.isfile(ruta):
            os.remove(ruta)
    except OSError:
        pass


def _leer_form_producto():
    """Lee y valida los campos del formulario de producto (crear y editar).

    Devuelve:
        datos:   dict con los valores limpios (código en mayúsculas, precio como
                 Decimal con 2 decimales, stock_minimo como entero...).
        errores: lista de mensajes; si está vacía, el formulario es válido.
    El stock NO está aquí: solo cambia con entradas/salidas.
    """
    f = request.form
    datos = {
        "codigo": f.get("codigo", "").strip().upper(),
        "nombre": f.get("nombre", "").strip(),
        "descripcion": f.get("descripcion", "").strip(),
        "categoria": f.get("categoria", "").strip() or "General",
        "precio": f.get("precio", "").strip(),
        "stock_minimo": f.get("stock_minimo", "0").strip(),
    }
    errores = []
    if not datos["codigo"]:
        errores.append("El código es obligatorio.")
    elif len(datos["codigo"]) > 40:
        errores.append("El código admite máximo 40 caracteres.")
    if not datos["nombre"]:
        errores.append("El nombre es obligatorio.")
    elif len(datos["nombre"]) > 150:
        errores.append("El nombre admite máximo 150 caracteres.")
    if len(datos["categoria"]) > 80:
        errores.append("La categoría admite máximo 80 caracteres.")
    try:
        precio = Decimal(datos["precio"].replace(",", "."))
        if precio < 0 or precio >= Decimal("10000000000"):
            raise InvalidOperation
        datos["precio"] = precio.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        errores.append("El precio debe ser un número mayor o igual a cero.")
    minimo = _entero(datos["stock_minimo"])
    if minimo is None or minimo < 0:
        errores.append("El stock mínimo debe ser un entero mayor o igual a cero.")
    else:
        datos["stock_minimo"] = minimo
    return datos, errores


def _codigo_duplicado(codigo, excluir_id=None):
    """True si ya existe otro producto con ese código.

    Al editar se pasa `excluir_id` para que el propio producto no cuente como duplicado.
    """
    sql, params = "SELECT id FROM productos WHERE codigo = %s", [codigo]
    if excluir_id:
        sql += " AND id <> %s"
        params.append(excluir_id)
    return consultar_uno(sql, tuple(params)) is not None


def _producto_o_404(producto_id):
    """Devuelve el producto o corta la petición con un error 404 si no existe."""
    p =servicios.obtener_producto(producto_id)
    if not p:
        abort(404)
    return p


# ---------------------------------------------------------------- panel
@bp.route("/")
@rol_requerido("admin")
def dashboard():
    """Panel principal del admin.

    Calcula los indicadores (KPIs): productos activos, unidades en stock, valor del
    inventario (stock × precio), productos bajo mínimo, estancados, pedidos
    pendientes y ventas de hoy y del mes. Además muestra la lista de productos
    bajo su stock mínimo (los más urgentes primero) y los últimos 10 movimientos.
    """
    # Totales del inventario. SUM(stock <= stock_minimo) cuenta los que cumplen
    # la condición, porque en MySQL una comparación vale 1 (verdadero) o 0.
    k = consultar_uno(
        "SELECT COUNT(*) AS activos, COALESCE(SUM(stock), 0) AS unidades, "
        "COALESCE(SUM(stock * precio), 0) AS valor, "
        "COALESCE(SUM(stock <= stock_minimo), 0) AS bajo_minimo "
        "FROM productos WHERE activo = 1"
    )
    pend = consultar_uno("SELECT COUNT(*) AS n FROM pedidos WHERE estado = 'pendiente'")
    # Ventas desde el día 1 del mes actual; dentro se separan las de hoy.
    v = consultar_uno(
        "SELECT COALESCE(SUM(CASE WHEN DATE(fecha) = CURDATE() THEN total END), 0) AS hoy, "
        "COUNT(CASE WHEN DATE(fecha) = CURDATE() THEN 1 END) AS n_hoy, "
        "COALESCE(SUM(total), 0) AS mes, COUNT(*) AS n_mes "
        "FROM ventas WHERE fecha >= CURDATE() - INTERVAL (DAYOFMONTH(CURDATE()) - 1) DAY"
    )
    estancados = servicios.productos_estancados()
    kpis = {
        "activos": int(k["activos"]),
        "unidades": int(k["unidades"]),
        "valor": k["valor"],
        "bajo_minimo": int(k["bajo_minimo"]),
        "estancados": len(estancados),
        "pendientes": int(pend["n"]),
        "ventas_hoy": v["hoy"], "n_ventas_hoy": int(v["n_hoy"]),
        "ventas_mes": v["mes"], "n_ventas_mes": int(v["n_mes"]),
    }
    # Productos bajo mínimo, ordenados por cuánto les falta (los más urgentes arriba).
    bajo = [p for p in servicios.listar_productos() if p["bajo_minimo"]]
    bajo.sort(key=lambda p: (p["stock"] - p["stock_minimo"], p["nombre"]))
    ultimos = consultar(
        "SELECT m.*, p.nombre AS producto, p.codigo, u.nombre AS usuario "
        "FROM movimientos m JOIN productos p ON p.id = m.producto_id "
        "LEFT JOIN usuarios u ON u.id = m.usuario_id ORDER BY m.fecha DESC, m.id DESC LIMIT 10"
    )
    return render_template("admin/dashboard.html", kpis=kpis, bajo=bajo[:15],
                           total_bajo=len(bajo), ultimos=ultimos,
                           dias_umbral=servicios.dias_estancado())


# ---------------------------------------------------------------- productos
@bp.route("/productos")
@rol_requerido("admin")
def productos():
    """Listado de productos con filtros por texto, categoría y estado (activos/inactivos/todos).

    Muestra para cada producto su stock, lo reservado por pedidos y lo disponible.
    """
    buscar =request.args.get("buscar", "").strip()
    categoria = request.args.get("categoria", "").strip()
    estado = request.args.get("estado", "activos")
    if estado not in ("activos", "inactivos", "todos"):
        estado = "activos"
    lista = servicios.listar_productos(solo_activos=(estado == "activos"),
                                       buscar=buscar, categoria=categoria)
    if estado == "inactivos":
        lista = [p for p in lista if not p["activo"]]
    cats = [f["categoria"] for f in consultar(
        "SELECT DISTINCT categoria FROM productos ORDER BY categoria")]
    return render_template("admin/productos.html", productos=lista, categorias=cats,
                           buscar=buscar, categoria=categoria, estado=estado)


@bp.route("/productos/nuevo", methods=["GET", "POST"])
@rol_requerido("admin")
def producto_nuevo():
    """Crear producto.

    GET  -> formulario vacío.
    POST -> valida, guarda la imagen y crea el producto con stock 0; si se indicó
            un stock inicial, lo registra como movimiento de ENTRADA "Inventario
            inicial" en la misma transacción. Si algo falla, borra la imagen
            subida y vuelve a mostrar el formulario con los errores.
    """
    if request.method == "POST":
        datos, errores = _leer_form_producto()
        stock_inicial = _entero(request.form.get("stock_inicial", "0") or "0")
        if stock_inicial is None or stock_inicial < 0:
            errores.append("El stock inicial debe ser un entero mayor o igual a cero.")
        if not errores and _codigo_duplicado(datos["codigo"]):
            errores.append(f"Ya existe un producto con el código '{datos['codigo']}'.")
        imagen = None
        if not errores:
            try:
                imagen = _guardar_imagen(request.files.get("imagen"))
            except ErrorInventario as e:
                errores.append(str(e))
        if not errores:
            try:
                with transaccion() as c:
                    c.execute(
                        "INSERT INTO productos (codigo, nombre, descripcion, categoria, precio, "
                        "stock, stock_minimo, imagen) VALUES (%s,%s,%s,%s,%s,0,%s,%s)",
                        (datos["codigo"], datos["nombre"], datos["descripcion"],
                         datos["categoria"], datos["precio"], datos["stock_minimo"], imagen),
                    )
                    nuevo_id = c.lastrowid
                    if stock_inicial > 0:
                        servicios.registrar_movimiento(nuevo_id, "entrada", stock_inicial,
                                                       "Inventario inicial", g.usuario["id"],
                                                       cur=c)
                flash(f"Producto '{datos['nombre']}' creado.", "ok")
                return redirect(url_for("admin_inv.productos"))
            except ErrorInventario as e:
                _borrar_imagen(imagen)
                errores.append(str(e))
            except mysql.connector.IntegrityError:
                _borrar_imagen(imagen)
                errores.append(f"Ya existe un producto con el código '{datos['codigo']}'.")
        for e in errores:
            flash(e, "error")
        return render_template("admin/producto_form.html", producto=None,
                               form=request.form, categorias=servicios.categorias())
    return render_template("admin/producto_form.html", producto=None, form={},
                           categorias=servicios.categorias())


@bp.route("/productos/<int:producto_id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def producto_editar(producto_id):
    """Editar producto (código, nombre, descripción, categoría, precio, mínimo, imagen).

    El stock NO se edita aquí: para cambiarlo se registra una entrada o salida.
    Si se sube una imagen nueva o se marca "quitar imagen", el archivo anterior
    se borra del disco después de guardar.
    """
    producto =_producto_o_404(producto_id)
    if request.method == "POST":
        datos, errores = _leer_form_producto()
        if not errores and _codigo_duplicado(datos["codigo"], excluir_id=producto_id):
            errores.append(f"Ya existe otro producto con el código '{datos['codigo']}'.")
        nueva_img = None
        if not errores:
            try:
                nueva_img = _guardar_imagen(request.files.get("imagen"))
            except ErrorInventario as e:
                errores.append(str(e))
        if not errores:
            imagen = producto["imagen"]
            quitar = request.form.get("quitar_imagen") == "1"
            if nueva_img:
                imagen = nueva_img
            elif quitar:
                imagen = None
            try:
                ejecutar(
                    "UPDATE productos SET codigo=%s, nombre=%s, descripcion=%s, categoria=%s, "
                    "precio=%s, stock_minimo=%s, imagen=%s WHERE id=%s",
                    (datos["codigo"], datos["nombre"], datos["descripcion"], datos["categoria"],
                     datos["precio"], datos["stock_minimo"], imagen, producto_id),
                )
            except mysql.connector.IntegrityError:
                _borrar_imagen(nueva_img)
                flash(f"Ya existe otro producto con el código '{datos['codigo']}'.", "error")
            else:
                if imagen != producto["imagen"]:
                    _borrar_imagen(producto["imagen"])
                flash(f"Producto '{datos['nombre']}' actualizado.", "ok")
                return redirect(url_for("admin_inv.productos"))
        for e in errores:
            flash(e, "error")
        return render_template("admin/producto_form.html", producto=producto,
                               form=request.form, categorias=servicios.categorias())
    return render_template("admin/producto_form.html", producto=producto, form=producto,
                           categorias=servicios.categorias())


@bp.route("/productos/<int:producto_id>/estado", methods=["POST"])
@rol_requerido("admin")
def producto_estado(producto_id):
    """Activa o desactiva un producto (lo invierte).

    Un producto desactivado desaparece del catálogo del cliente, pero conserva
    su historial de movimientos, pedidos y ventas.
    """
    producto =_producto_o_404(producto_id)
    nuevo = 0 if producto["activo"] else 1
    ejecutar("UPDATE productos SET activo = %s WHERE id = %s", (nuevo, producto_id))
    flash(f"Producto '{producto['nombre']}' {'activado' if nuevo else 'desactivado'}.", "ok")
    return redirect(request.referrer or url_for("admin_inv.productos"))


@bp.route("/productos/<int:producto_id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def producto_eliminar(producto_id):
    """Borra un producto definitivamente, SOLO si nunca tuvo movimientos, pedidos ni ventas.

    Si tiene historial, borrarlo dejaría registros huérfanos, así que se pide
    desactivarlo en su lugar.
    """
    producto =_producto_o_404(producto_id)
    uso = consultar_uno(
        "SELECT (SELECT COUNT(*) FROM movimientos WHERE producto_id = %s) AS movs, "
        "(SELECT COUNT(*) FROM pedido_detalle WHERE producto_id = %s) AS peds, "
        "(SELECT COUNT(*) FROM venta_detalle WHERE producto_id = %s) AS vents",
        (producto_id, producto_id, producto_id),
    )
    if uso["movs"] or uso["peds"] or uso["vents"]:
        flash(f"'{producto['nombre']}' tiene movimientos, pedidos o ventas registrados y no se "
              "puede eliminar. Desactívalo en su lugar.", "error")
        return redirect(url_for("admin_inv.productos"))
    try:
        ejecutar("DELETE FROM productos WHERE id = %s", (producto_id,))
    except mysql.connector.IntegrityError:
        flash(f"'{producto['nombre']}' tiene registros asociados y no se puede eliminar.", "error")
        return redirect(url_for("admin_inv.productos"))
    _borrar_imagen(producto["imagen"])
    flash(f"Producto '{producto['nombre']}' eliminado.", "ok")
    return redirect(url_for("admin_inv.productos"))


# ---------------------------------------------------------------- movimientos
def _filtros_movimientos():
    """Lee los filtros del historial (?producto_id, ?tipo, ?desde, ?hasta).

    Devuelve (filtros, where, params): los valores leídos, el texto del WHERE
    para SQL y la lista de parámetros. "hasta" incluye todo ese día (se compara
    con el día siguiente a las 00:00).
    """
    a = request.args
    filtros = {
        "producto_id": _entero(a.get("producto_id"), None),
        "tipo": a.get("tipo", "") if a.get("tipo") in ("entrada", "salida") else "",
        "desde": _fecha(a.get("desde")),
        "hasta": _fecha(a.get("hasta")),
    }
    where, params = ["1=1"], []
    if filtros["producto_id"]:
        where.append("m.producto_id = %s")
        params.append(filtros["producto_id"])
    if filtros["tipo"]:
        where.append("m.tipo = %s")
        params.append(filtros["tipo"])
    if filtros["desde"]:
        where.append("m.fecha >= %s")
        params.append(filtros["desde"])
    if filtros["hasta"]:
        where.append("m.fecha < %s")
        params.append(filtros["hasta"] + timedelta(days=1))
    return filtros, " AND ".join(where), params


@bp.route("/movimientos")
@rol_requerido("admin")
def movimientos():
    """Pantalla de Entradas/Salidas: formulario para registrar y historial filtrado.

    Muestra los totales de entradas y salidas según el filtro y pagina el
    historial de 50 en 50 (?pagina=N).
    """
    filtros, where, params = _filtros_movimientos()
    pagina = max(1, _entero(request.args.get("pagina"), 1) or 1)

    tot = consultar_uno(
        "SELECT COUNT(*) AS n, "
        "COALESCE(SUM(CASE WHEN m.tipo = 'entrada' THEN m.cantidad END), 0) AS entradas, "
        "COALESCE(SUM(CASE WHEN m.tipo = 'salida' THEN m.cantidad END), 0) AS salidas "
        f"FROM movimientos m WHERE {where}",
        tuple(params),
    )
    total_filas = int(tot["n"])
    # -(-a // b) es la división redondeando hacia arriba (p. ej. 101 filas -> 3 páginas).
    paginas = max(1, -(-total_filas // POR_PAGINA))
    pagina = min(pagina, paginas)
    filas = consultar(
        "SELECT m.*, p.nombre AS producto, p.codigo, u.nombre AS usuario "
        "FROM movimientos m JOIN productos p ON p.id = m.producto_id "
        f"LEFT JOIN usuarios u ON u.id = m.usuario_id WHERE {where} "
        "ORDER BY m.fecha DESC, m.id DESC LIMIT %s OFFSET %s",
        tuple(params) + (POR_PAGINA, (pagina - 1) * POR_PAGINA),
    )
    # Filtros actuales sin "pagina", para construir los enlaces Anterior/Siguiente.
    args_base = {k: v for k, v in request.args.items() if k != "pagina" and v}
    return render_template(
        "admin/movimientos.html",
        movimientos=filas, filtros=filtros,
        totales={"entradas": int(tot["entradas"]), "salidas": int(tot["salidas"]),
                 "n": total_filas},
        pagina=pagina, paginas=paginas, args_base=args_base,
        productos_activos=servicios.listar_productos(),
        todos_productos=consultar("SELECT id, codigo, nombre, activo FROM productos ORDER BY nombre"),
        preseleccion=_entero(request.args.get("nuevo_producto"), None),
    )


@bp.route("/movimientos/registrar", methods=["POST"])
@rol_requerido("admin")
def movimiento_registrar():
    """Registra una entrada o salida enviada desde el formulario.

    Llama a servicios.registrar_movimiento (que valida la cantidad y el stock) y
    vuelve a la página de origen (campo oculto "volver") con un mensaje.
    Solo se acepta un destino interno (que empiece por "/") por seguridad.
    """
    f = request.form
    producto_id = _entero(f.get("producto_id"))
    tipo = f.get("tipo", "")
    motivo = f.get("motivo", "").strip()[:255]
    destino = request.form.get("volver") or url_for("admin_inv.movimientos")
    if not destino.startswith("/") or destino.startswith("//"):
        destino = url_for("admin_inv.movimientos")
    if not producto_id:
        flash("Selecciona un producto.", "error")
        return redirect(destino)
    prod = consultar_uno("SELECT nombre, activo FROM productos WHERE id = %s", (producto_id,))
    if not prod:
        flash("El producto no existe.", "error")
        return redirect(destino)
    if not motivo:
        motivo = "Entrada manual" if tipo == "entrada" else "Salida manual"
    try:
        servicios.registrar_movimiento(producto_id, tipo, f.get("cantidad"), motivo,
                                       g.usuario["id"])
        flash(f"{'Entrada' if tipo == 'entrada' else 'Salida'} de {int(f.get('cantidad'))} "
              f"unidad(es) de '{prod['nombre']}' registrada.", "ok")
    except ErrorInventario as e:
        flash(str(e), "error")
    return redirect(destino)


# ---------------------------------------------------------------- estancados
@bp.route("/estancados")
@rol_requerido("admin")
def estancados():
    """Reporte de productos estancados en bodega.

    Usa el umbral guardado, o uno de prueba con ?dias=N (sin guardarlo). Muestra
    días sin movimiento, valor inmovilizado de cada producto y los totales.
    """
    guardado =servicios.dias_estancado()
    probado = _entero(request.args.get("dias"))
    if probado is not None and not (1 <= probado <= 3650):
        flash("El umbral debe ser un entero entre 1 y 3650 días.", "error")
        probado = None
    dias = probado or guardado
    lista = servicios.productos_estancados(dias)
    total = sum((Decimal(p["valor_inmovilizado"] or 0) for p in lista), Decimal("0"))
    unidades = sum(int(p["stock"]) for p in lista)
    return render_template("admin/estancados.html", productos=lista, dias=dias,
                           guardado=guardado, probando=(probado is not None and probado != guardado),
                           total=total, unidades=unidades)


@bp.route("/estancados/umbral", methods=["POST"])
@rol_requerido("admin")
def estancados_umbral():
    """Guarda el número de días (1–3650) a partir del cual un producto se considera estancado."""
    dias =_entero(request.form.get("dias"))
    if dias is None or not (1 <= dias <= 3650):
        flash("El umbral debe ser un entero entre 1 y 3650 días.", "error")
    else:
        guardar_config("dias_estancado", dias)
        flash(f"Umbral de estancamiento guardado: {dias} días.", "ok")
    return redirect(url_for("admin_inv.estancados"))

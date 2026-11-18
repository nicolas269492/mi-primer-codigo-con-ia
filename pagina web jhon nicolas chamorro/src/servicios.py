"""Lógica de negocio compartida por todos los módulos (el "corazón" del sistema).

Las rutas (src/rutas/*.py) solo reciben datos del navegador y llaman a estas
funciones; las reglas del inventario viven aquí, en un único lugar.

REGLAS:
- El stock SOLO cambia a través de `registrar_movimiento` (entrada/salida),
  que deja rastro en la tabla `movimientos` y actualiza `ultimo_movimiento`.
- disponible = stock - reservado, donde reservado = unidades en pedidos
  'pendiente' o 'preparacion'. El catálogo y la vista del trabajador usan `disponible`.
- Al pasar un pedido a 'despachado' se descuenta stock (salidas) y se crea la venta.
"""
from decimal import Decimal

from src.basedatos import consultar, consultar_uno, obtener_config, transaccion

# Estados posibles de un pedido.
ESTADOS_PEDIDO = ("pendiente", "preparacion", "despachado", "cancelado")

# A qué estados se puede pasar desde cada estado (máquina de estados del pedido):
#   pendiente  -> preparación o cancelado
#   preparación -> volver a pendiente, despachado o cancelado
#   despachado y cancelado son finales (no se pueden cambiar).
TRANSICIONES = {
    "pendiente": {"preparacion", "cancelado"},
    "preparacion": {"pendiente", "despachado", "cancelado"},
    "despachado": set(),
    "cancelado": set(),
}

# Subconsulta SQL reutilizable: suma las unidades del producto `p` que están en
# pedidos abiertos (pendiente o en preparación). Es lo que está "reservado".
_SQL_RESERVADO = (
    "(SELECT COALESCE(SUM(d.cantidad), 0) FROM pedido_detalle d "
    " JOIN pedidos pe ON pe.id = d.pedido_id "
    " WHERE d.producto_id = p.id AND pe.estado IN ('pendiente', 'preparacion'))"
)


class ErrorInventario(Exception):
    """Error de negocio (stock insuficiente, transición inválida...).

    Su mensaje está pensado para mostrarse tal cual al usuario. Las rutas lo
    capturan con `except ErrorInventario as e:` y hacen `flash(str(e), "error")`.
    """


# ---------------------------------------------------------------- consultas
def listar_productos(solo_activos=True, buscar="", categoria=""):
    """Devuelve la lista de productos con datos de disponibilidad calculados.

    Parámetros:
        solo_activos: si es True, oculta los productos desactivados.
        buscar:       texto a buscar en nombre, código o descripción (opcional).
        categoria:    filtra por una categoría exacta (opcional).
    Devuelve:
        lista de dicts con todas las columnas de `productos` más:
            reservado   -> unidades comprometidas en pedidos abiertos
            disponible  -> stock - reservado (nunca negativo)
            bajo_minimo -> True si stock <= stock_minimo
    """
    sql = (
        f"SELECT p.*, {_SQL_RESERVADO} AS reservado, "
        f"GREATEST(p.stock - {_SQL_RESERVADO}, 0) AS disponible, "
        "(p.stock <= p.stock_minimo) AS bajo_minimo "
        "FROM productos p WHERE 1=1"   # 1=1 permite añadir filtros con " AND ..." sin casos especiales
    )
    params = []
    if solo_activos:
        sql += " AND p.activo = 1"
    if buscar:
        sql += " AND (p.nombre LIKE %s OR p.codigo LIKE %s OR p.descripcion LIKE %s)"
        like = f"%{buscar}%"  # % = "cualquier texto" en LIKE
        params += [like, like, like]
    if categoria:
        sql += " AND p.categoria = %s"
        params.append(categoria)
    sql += " ORDER BY p.nombre"
    filas = consultar(sql, tuple(params))
    # MySQL devuelve las sumas como Decimal y las comparaciones como 0/1:
    # se convierten a int/bool para usarlos cómodamente en Python y JSON.
    for f in filas:
        f["reservado"] = int(f["reservado"])
        f["disponible"] = int(f["disponible"])
        f["bajo_minimo"] = bool(f["bajo_minimo"])
    return filas


def obtener_producto(producto_id):
    """Devuelve un producto (dict con reservado y disponible) o None si no existe."""
    filas = consultar(
        f"SELECT p.*, {_SQL_RESERVADO} AS reservado, "
        f"GREATEST(p.stock - {_SQL_RESERVADO}, 0) AS disponible "
        "FROM productos p WHERE p.id = %s",
        (producto_id,),
    )
    if not filas:
        return None
    f = filas[0]
    f["reservado"] = int(f["reservado"])
    f["disponible"] = int(f["disponible"])
    return f


def categorias():
    """Lista de categorías (sin repetir) de los productos activos, en orden alfabético."""
    return [f["categoria"] for f in consultar(
        "SELECT DISTINCT categoria FROM productos WHERE activo = 1 ORDER BY categoria")]


def dias_estancado():
    """Umbral de días sin movimiento para considerar un producto 'estancado'.

    Se lee de la tabla `configuracion` (el admin lo cambia en la pantalla Estancados).
    Si el valor guardado no es un número válido, usa 30 días.
    """
    try:
        return max(1, int(obtener_config("dias_estancado", "30")))
    except ValueError:
        return 30


def productos_estancados(dias=None):
    """Productos que llevan mucho tiempo en bodega sin moverse.

    Un producto está estancado si: está activo, tiene stock > 0 y no ha tenido
    ninguna entrada ni salida en los últimos `dias` días (si nunca tuvo
    movimientos, se cuenta desde la fecha en que se creó).

    Parámetro:
        dias: umbral a usar; si es None se usa el configurado (dias_estancado()).
    Devuelve:
        lista de productos con dos columnas extra:
            dias_sin_movimiento -> días desde el último movimiento
            valor_inmovilizado  -> stock * precio (dinero "parado" en bodega)
        ordenada del más antiguo al más reciente.
    """
    dias = dias or dias_estancado()
    filas = consultar(
        "SELECT p.*, DATEDIFF(NOW(), COALESCE(p.ultimo_movimiento, p.creado_en)) AS dias_sin_movimiento, "
        "(p.stock * p.precio) AS valor_inmovilizado "
        "FROM productos p WHERE p.activo = 1 AND p.stock > 0 "
        "AND COALESCE(p.ultimo_movimiento, p.creado_en) < NOW() - INTERVAL %s DAY "
        "ORDER BY dias_sin_movimiento DESC",
        (dias,),
    )
    return filas


def obtener_pedido(pedido_id):
    """Devuelve un pedido completo o None si no existe.

    El dict incluye los datos del pedido, el nombre y correo del cliente, el
    nombre de quien lo atendió y la clave `items`: lista de líneas con código,
    nombre, stock actual del producto, cantidad, precio_unitario y subtotal.
    """
    pedido = consultar_uno(
        "SELECT pe.*, u.nombre AS cliente_nombre, u.email AS cliente_email, "
        "t.nombre AS atendido_por_nombre "
        "FROM pedidos pe JOIN usuarios u ON u.id = pe.cliente_id "
        "LEFT JOIN usuarios t ON t.id = pe.atendido_por WHERE pe.id = %s",
        (pedido_id,),
    )
    if pedido:
        pedido["items"] = consultar(
            "SELECT d.*, p.nombre, p.codigo, p.stock, (d.cantidad * d.precio_unitario) AS subtotal "
            "FROM pedido_detalle d JOIN productos p ON p.id = d.producto_id "
            "WHERE d.pedido_id = %s ORDER BY d.id",
            (pedido_id,),
        )
    return pedido


def listar_pedidos(estado=None, cliente_id=None):
    """Lista de pedidos (los más recientes primero), con nombre del cliente y total de unidades.

    Parámetros opcionales para filtrar:
        estado:     'pendiente', 'preparacion', 'despachado' o 'cancelado'.
        cliente_id: solo los pedidos de ese cliente (lo usa "Mis pedidos").
    """
    sql = (
        "SELECT pe.*, u.nombre AS cliente_nombre, "
        "(SELECT COALESCE(SUM(cantidad),0) FROM pedido_detalle WHERE pedido_id = pe.id) AS unidades "
        "FROM pedidos pe JOIN usuarios u ON u.id = pe.cliente_id WHERE 1=1"
    )
    params = []
    if estado:
        sql += " AND pe.estado = %s"
        params.append(estado)
    if cliente_id:
        sql += " AND pe.cliente_id = %s"
        params.append(cliente_id)
    sql += " ORDER BY pe.creado_en DESC, pe.id DESC"
    return consultar(sql, tuple(params))


# ---------------------------------------------------------------- escrituras
def registrar_movimiento(producto_id, tipo, cantidad, motivo, usuario_id, pedido_id=None, cur=None):
    """Registra una ENTRADA o SALIDA de bodega y actualiza el stock del producto.

    Es la ÚNICA función que modifica el stock. Siempre deja un registro en la
    tabla `movimientos` (quién, cuándo, cuánto y por qué).

    Parámetros:
        producto_id: id del producto.
        tipo:        'entrada' (suma al stock) o 'salida' (resta del stock).
        cantidad:    número entero mayor que cero.
        motivo:      texto libre, p. ej. "Compra a proveedor", "Producto dañado".
        usuario_id:  quién hace el movimiento.
        pedido_id:   si la salida se debe a un despacho, el pedido relacionado.
        cur:         cursor de una transacción ya abierta. Si se pasa, el
                     movimiento forma parte de esa transacción (lo usa el despacho
                     para que todas las salidas se guarden juntas o ninguna).
    Devuelve:
        id del movimiento creado.
    Lanza:
        ErrorInventario si los datos no son válidos o no hay stock suficiente.
    """
    # --- Validaciones antes de tocar la base de datos ---
    if tipo not in ("entrada", "salida"):
        raise ErrorInventario("Tipo de movimiento inválido.")
    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        raise ErrorInventario("La cantidad debe ser un número entero.")
    if cantidad <= 0:
        raise ErrorInventario("La cantidad debe ser mayor que cero.")

    def _hacer(c):
        """Hace el movimiento usando el cursor `c`."""
        # FOR UPDATE bloquea la fila del producto hasta terminar la transacción:
        # si dos personas sacan stock a la vez, la segunda espera y ve el stock ya
        # actualizado (evita que el stock quede negativo).
        c.execute("SELECT id, nombre, stock FROM productos WHERE id = %s FOR UPDATE", (producto_id,))
        prod = c.fetchone()
        if not prod:
            raise ErrorInventario("El producto no existe.")
        if tipo == "salida" and cantidad > prod["stock"]:
            raise ErrorInventario(
                f"Stock insuficiente de '{prod['nombre']}': hay {prod['stock']}, se piden {cantidad}."
            )
        delta = cantidad if tipo == "entrada" else -cantidad
        # Actualiza el stock y la fecha del último movimiento (usada por "estancados").
        c.execute(
            "UPDATE productos SET stock = stock + %s, ultimo_movimiento = NOW() WHERE id = %s",
            (delta, producto_id),
        )
        # Deja constancia del movimiento en el historial.
        c.execute(
            "INSERT INTO movimientos (producto_id, tipo, cantidad, motivo, pedido_id, usuario_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (producto_id, tipo, cantidad, motivo or "", pedido_id, usuario_id),
        )
        return c.lastrowid

    # Si nos dieron una transacción, la usamos; si no, abrimos una propia.
    if cur is not None:
        return _hacer(cur)
    with transaccion() as c:
        return _hacer(c)


def _validar_items(c, items, excluir_pedido=None):
    """Revisa las líneas de un pedido antes de guardarlo (uso interno).

    Parámetros:
        c:              cursor de la transacción en curso.
        items:          dict {producto_id: cantidad}. Las cantidades <= 0 se ignoran.
        excluir_pedido: al EDITAR un pedido, su propia reserva no debe contar
                        contra él mismo, así que se excluye de la suma de reservado.
    Devuelve:
        (lineas, total) donde lineas = [(producto_dict, cantidad), ...] y
        total = suma de precio actual * cantidad.
    Lanza:
        ErrorInventario si el pedido está vacío, si un producto no existe o está
        inactivo, o si se pide más de lo disponible.
    """
    # Limpia los datos: convierte a enteros y junta productos repetidos.
    limpios = {}
    for pid, cant in items.items():
        try:
            pid, cant = int(pid), int(cant)
        except (TypeError, ValueError):
            raise ErrorInventario("Cantidades inválidas en el pedido.")
        if cant > 0:
            limpios[pid] = limpios.get(pid, 0) + cant
    if not limpios:
        raise ErrorInventario("El pedido no tiene productos.")

    resultado, total = [], Decimal("0")  # Decimal evita errores de redondeo con dinero
    for pid, cant in limpios.items():
        # Bloquea el producto para que nadie más lo reserve mientras validamos.
        c.execute("SELECT * FROM productos WHERE id = %s FOR UPDATE", (pid,))
        prod = c.fetchone()
        if not prod or not prod["activo"]:
            raise ErrorInventario("Uno de los productos ya no está disponible.")
        # Unidades ya reservadas por otros pedidos abiertos.
        sql_res = (
            "SELECT COALESCE(SUM(d.cantidad),0) AS r FROM pedido_detalle d "
            "JOIN pedidos pe ON pe.id = d.pedido_id "
            "WHERE d.producto_id = %s AND pe.estado IN ('pendiente','preparacion')"
        )
        params = [pid]
        if excluir_pedido:
            sql_res += " AND pe.id <> %s"
            params.append(excluir_pedido)
        c.execute(sql_res, tuple(params))
        disponible = prod["stock"] - int(c.fetchone()["r"])
        if cant > disponible:
            raise ErrorInventario(
                f"Solo hay {max(disponible, 0)} unidades disponibles de '{prod['nombre']}'."
            )
        resultado.append((prod, cant))
        total += Decimal(prod["precio"]) * cant
    return resultado, total


def crear_pedido(cliente_id, items, notas=""):
    """Crea un pedido nuevo en estado 'pendiente'.

    Parámetros:
        cliente_id: id del usuario cliente que hace el pedido.
        items:      dict {producto_id: cantidad}, p. ej. {1: 2, 5: 3}.
        notas:      comentario opcional (máx. 500 caracteres).
    Devuelve:
        id del pedido creado.

    - Valida que haya disponibilidad suficiente de cada producto.
    - Guarda el precio ACTUAL de cada producto en la línea (precio_unitario), así
      si después cambia el precio, el pedido conserva el precio con que se pidió.
    - NO descuenta stock: solo reserva. El stock baja al despachar.
    """
    with transaccion() as c:
        lineas, total = _validar_items(c, items)
        c.execute(
            "INSERT INTO pedidos (cliente_id, total, notas) VALUES (%s, %s, %s)",
            (cliente_id, total, (notas or "")[:500]),
        )
        pedido_id = c.lastrowid
        for prod, cant in lineas:
            c.execute(
                "INSERT INTO pedido_detalle (pedido_id, producto_id, cantidad, precio_unitario) "
                "VALUES (%s, %s, %s, %s)",
                (pedido_id, prod["id"], cant, prod["precio"]),
            )
        return pedido_id


def actualizar_pedido(pedido_id, items, notas=None, cliente_id=None):
    """Modifica un pedido abierto (pendiente o en preparación). Lo usa el CRUD del admin.

    Reemplaza TODAS las líneas por las nuevas `items` ({producto_id: cantidad}),
    recalcula el total con los precios actuales y, si se indican, cambia las
    notas y el cliente. Los pedidos despachados o cancelados no se pueden editar.
    """
    with transaccion() as c:
        c.execute("SELECT * FROM pedidos WHERE id = %s FOR UPDATE", (pedido_id,))
        ped = c.fetchone()
        if not ped:
            raise ErrorInventario("El pedido no existe.")
        if ped["estado"] not in ("pendiente", "preparacion"):
            raise ErrorInventario("Solo se pueden editar pedidos pendientes o en preparación.")
        lineas, total = _validar_items(c, items, excluir_pedido=pedido_id)
        # Borra las líneas viejas y escribe las nuevas.
        c.execute("DELETE FROM pedido_detalle WHERE pedido_id = %s", (pedido_id,))
        for prod, cant in lineas:
            c.execute(
                "INSERT INTO pedido_detalle (pedido_id, producto_id, cantidad, precio_unitario) "
                "VALUES (%s, %s, %s, %s)",
                (pedido_id, prod["id"], cant, prod["precio"]),
            )
        c.execute(
            "UPDATE pedidos SET total = %s, notas = %s, cliente_id = %s WHERE id = %s",
            (
                total,
                ped["notas"] if notas is None else notas[:500],   # None = no cambiar notas
                cliente_id or ped["cliente_id"],                  # None = mismo cliente
                pedido_id,
            ),
        )


def eliminar_pedido(pedido_id):
    """Borra un pedido y sus líneas (al borrarlo se libera su reserva).

    Los pedidos despachados NO se pueden borrar porque ya generaron una venta y
    salidas de stock; borrarlos dejaría el historial incoherente.
    """
    with transaccion() as c:
        c.execute("SELECT estado FROM pedidos WHERE id = %s FOR UPDATE", (pedido_id,))
        ped = c.fetchone()
        if not ped:
            raise ErrorInventario("El pedido no existe.")
        if ped["estado"] == "despachado":
            raise ErrorInventario("Un pedido despachado no se puede eliminar (ya generó una venta).")
        # Las líneas (pedido_detalle) se borran solas por ON DELETE CASCADE.
        c.execute("DELETE FROM pedidos WHERE id = %s", (pedido_id,))


def cambiar_estado_pedido(pedido_id, nuevo_estado, usuario_id):
    """Cambia el estado de un pedido respetando TRANSICIONES.

    Parámetros:
        pedido_id:    pedido a cambiar.
        nuevo_estado: 'pendiente', 'preparacion', 'despachado' o 'cancelado'.
        usuario_id:   quién hace el cambio (se guarda en pedidos.atendido_por).
    Devuelve:
        el id de la venta creada si el nuevo estado es 'despachado'; si no, None.

    Al DESPACHAR, dentro de una sola transacción:
        1. Registra una salida de stock por cada línea del pedido.
        2. Crea la venta (tabla ventas) y sus líneas (venta_detalle).
        3. Cambia el estado del pedido.
    Si falta stock de cualquier producto, se lanza ErrorInventario y se deshace
    TODO (no queda ninguna salida a medias ni venta creada).
    """
    if nuevo_estado not in ESTADOS_PEDIDO:
        raise ErrorInventario("Estado inválido.")
    with transaccion() as c:
        c.execute("SELECT * FROM pedidos WHERE id = %s FOR UPDATE", (pedido_id,))
        ped = c.fetchone()
        if not ped:
            raise ErrorInventario("El pedido no existe.")
        if nuevo_estado not in TRANSICIONES[ped["estado"]]:
            raise ErrorInventario(
                f"No se puede pasar un pedido de '{ped['estado']}' a '{nuevo_estado}'."
            )

        venta_id = None
        if nuevo_estado == "despachado":
            c.execute("SELECT * FROM pedido_detalle WHERE pedido_id = %s", (pedido_id,))
            items = c.fetchall()
            # 1. Salidas de stock (misma transacción: cur=c).
            for it in items:
                registrar_movimiento(
                    it["producto_id"], "salida", it["cantidad"],
                    f"Despacho pedido #{pedido_id}", usuario_id, pedido_id=pedido_id, cur=c,
                )
            # 2. Venta con el total del pedido...
            c.execute(
                "INSERT INTO ventas (pedido_id, cliente_id, usuario_id, total) VALUES (%s, %s, %s, %s)",
                (pedido_id, ped["cliente_id"], usuario_id, ped["total"]),
            )
            venta_id = c.lastrowid
            # ...y sus líneas, copiadas de las del pedido.
            for it in items:
                c.execute(
                    "INSERT INTO venta_detalle (venta_id, producto_id, cantidad, precio_unitario) "
                    "VALUES (%s, %s, %s, %s)",
                    (venta_id, it["producto_id"], it["cantidad"], it["precio_unitario"]),
                )

        # 3. Nuevo estado y quién lo atendió.
        c.execute(
            "UPDATE pedidos SET estado = %s, atendido_por = %s WHERE id = %s",
            (nuevo_estado, usuario_id, pedido_id),
        )
        return venta_id

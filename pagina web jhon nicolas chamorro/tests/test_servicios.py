"""Reglas de negocio del inventario (src/servicios.py)."""
import pytest

from src import servicios as s
from src.basedatos import consultar, consultar_uno, ejecutar
from tests.conftest import ADMIN, CLIENTE, TRABAJADOR

ARROZ = 1      # stock 40
JABON = 4      # stock 0
ESCOBA = 7     # estancado (45 días sin movimiento)


def test_entrada_y_salida_actualizan_stock_y_registran_movimiento(ctx):
    s.registrar_movimiento(ARROZ, "entrada", 10, "Compra", ADMIN)
    s.registrar_movimiento(ARROZ, "salida", 5, "Merma", ADMIN)
    assert s.obtener_producto(ARROZ)["stock"] == 45
    movs = consultar("SELECT tipo, cantidad FROM movimientos WHERE producto_id=%s ORDER BY id", (ARROZ,))
    assert [(m["tipo"], m["cantidad"]) for m in movs][-2:] == [("entrada", 10), ("salida", 5)]


@pytest.mark.parametrize("cantidad", [0, -3, "abc"])
def test_cantidad_invalida(ctx, cantidad):
    with pytest.raises(s.ErrorInventario):
        s.registrar_movimiento(ARROZ, "entrada", cantidad, "", ADMIN)


def test_salida_sin_stock_falla_y_no_cambia_nada(ctx):
    antes = consultar_uno("SELECT COUNT(*) n FROM movimientos")["n"]
    with pytest.raises(s.ErrorInventario, match="Stock insuficiente"):
        s.registrar_movimiento(JABON, "salida", 1, "", ADMIN)
    assert s.obtener_producto(JABON)["stock"] == 0
    assert consultar_uno("SELECT COUNT(*) n FROM movimientos")["n"] == antes


def test_pedido_reserva_disponibilidad_sin_tocar_stock(ctx):
    s.crear_pedido(CLIENTE, {ARROZ: 15})
    p = s.obtener_producto(ARROZ)
    assert (p["stock"], p["reservado"], p["disponible"]) == (40, 15, 25)


def test_pedido_no_puede_exceder_disponible(ctx):
    s.crear_pedido(CLIENTE, {ARROZ: 30})
    with pytest.raises(s.ErrorInventario, match="Solo hay 10"):
        s.crear_pedido(CLIENTE, {ARROZ: 11})


def test_pedido_vacio_o_agotado_falla(ctx):
    with pytest.raises(s.ErrorInventario):
        s.crear_pedido(CLIENTE, {})
    with pytest.raises(s.ErrorInventario):
        s.crear_pedido(CLIENTE, {JABON: 1})


def test_pedido_de_producto_inactivo_falla(ctx):
    ejecutar("UPDATE productos SET activo=0 WHERE id=%s", (ARROZ,))
    with pytest.raises(s.ErrorInventario):
        s.crear_pedido(CLIENTE, {ARROZ: 1})


def test_despacho_descuenta_stock_y_crea_venta(ctx):
    pid = s.crear_pedido(CLIENTE, {ARROZ: 2, 2: 3})
    s.cambiar_estado_pedido(pid, "preparacion", TRABAJADOR)
    venta_id = s.cambiar_estado_pedido(pid, "despachado", TRABAJADOR)

    assert s.obtener_producto(ARROZ)["stock"] == 38
    assert s.obtener_producto(ARROZ)["reservado"] == 0
    venta = consultar_uno("SELECT * FROM ventas WHERE id=%s", (venta_id,))
    assert venta["pedido_id"] == pid
    assert float(venta["total"]) == 2 * 18500 + 3 * 9800
    assert consultar_uno("SELECT COUNT(*) n FROM venta_detalle WHERE venta_id=%s", (venta_id,))["n"] == 2
    salidas = consultar("SELECT * FROM movimientos WHERE pedido_id=%s AND tipo='salida'", (pid,))
    assert len(salidas) == 2


def test_transiciones_invalidas(ctx):
    pid = s.crear_pedido(CLIENTE, {ARROZ: 1})
    with pytest.raises(s.ErrorInventario):
        s.cambiar_estado_pedido(pid, "despachado", TRABAJADOR)  # falta 'preparacion'
    s.cambiar_estado_pedido(pid, "cancelado", TRABAJADOR)
    with pytest.raises(s.ErrorInventario):
        s.cambiar_estado_pedido(pid, "pendiente", TRABAJADOR)
    assert s.obtener_producto(ARROZ)["disponible"] == 40  # cancelar libera la reserva


def test_despacho_sin_stock_suficiente_revierte_todo(ctx):
    pid = s.crear_pedido(CLIENTE, {ARROZ: 2, 2: 25})
    s.cambiar_estado_pedido(pid, "preparacion", TRABAJADOR)
    s.registrar_movimiento(2, "salida", 24, "Daño", ADMIN)  # quedan 1 de aceite
    with pytest.raises(s.ErrorInventario):
        s.cambiar_estado_pedido(pid, "despachado", TRABAJADOR)
    assert s.obtener_producto(ARROZ)["stock"] == 40      # la salida del arroz se revirtió
    assert consultar_uno("SELECT estado FROM pedidos WHERE id=%s", (pid,))["estado"] == "preparacion"
    assert consultar_uno("SELECT COUNT(*) n FROM ventas")["n"] == 0


def test_actualizar_y_eliminar_pedido(ctx):
    pid = s.crear_pedido(CLIENTE, {ARROZ: 5})
    s.actualizar_pedido(pid, {ARROZ: 40}, notas="cambio")  # su propia reserva no cuenta
    assert s.obtener_pedido(pid)["items"][0]["cantidad"] == 40
    s.eliminar_pedido(pid)
    assert s.obtener_pedido(pid) is None


def test_no_se_elimina_pedido_despachado(ctx):
    pid = s.crear_pedido(CLIENTE, {ARROZ: 1})
    s.cambiar_estado_pedido(pid, "preparacion", TRABAJADOR)
    s.cambiar_estado_pedido(pid, "despachado", TRABAJADOR)
    with pytest.raises(s.ErrorInventario):
        s.eliminar_pedido(pid)
    with pytest.raises(s.ErrorInventario):
        s.actualizar_pedido(pid, {ARROZ: 2})


def test_estancados_respeta_umbral(ctx):
    assert [p["id"] for p in s.productos_estancados()] == [ESCOBA]   # umbral 30
    assert s.productos_estancados(dias=60) == []
    s.registrar_movimiento(ESCOBA, "salida", 1, "Venta mostrador", ADMIN)
    assert s.productos_estancados() == []

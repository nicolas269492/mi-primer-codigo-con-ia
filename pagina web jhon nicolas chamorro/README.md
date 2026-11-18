# Control de Almacén

Aplicación web en **Python (Flask) + MySQL (XAMPP)** para controlar un almacén con tres roles.

| Rol | Qué puede hacer |
|---|---|
| **Administrador** | Panel con indicadores, CRUD de productos, entradas y salidas de bodega, productos estancados (umbral configurable), registro de ventas (con exportación CSV), CRUD de pedidos, gestión de usuarios |
| **Trabajador** | Tablero de pedidos (pendiente → en preparación → despachado) que se actualiza solo, detalle para alistar, disponibilidad de productos |
| **Cliente** | Catálogo que se actualiza solo según la disponibilidad, carrito, envío de pedidos, seguimiento y cancelación de sus pedidos |

## Reglas de negocio

- El stock **solo** cambia con movimientos de entrada/salida (queda historial de todo).
- **Disponible = stock − reservado.** Las unidades de pedidos pendientes o en preparación quedan reservadas; el catálogo muestra el disponible.
- Al **despachar** un pedido se descuenta el stock y se crea la **venta** automáticamente. Si falta stock, no se despacha nada.
- Un producto está **estancado** si tiene stock y no ha tenido movimientos en N días (30 por defecto, configurable por el admin).

## Instalación

1. Enciende **MySQL** desde el panel de XAMPP.
2. Instala las dependencias:
   ```
   pip install -r requirements.txt
   ```
3. Crea la base de datos `almacen` con datos de ejemplo (**borra las tablas si ya existen**):
   ```
   python -m src.init_db
   ```
4. Arranca el servidor y abre http://127.0.0.1:5000
   ```
   python run.py
   ```

Usuarios de ejemplo:

| Rol | Correo | Contraseña |
|---|---|---|
| Admin | admin@almacen.com | admin123 |
| Trabajador | trabajador@almacen.com | trabajador123 |
| Cliente | cliente@almacen.com | cliente123 |

Cambia estas contraseñas (y `SECRET_KEY`) antes de usarlo en serio. La conexión se configura con variables de entorno `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `SECRET_KEY` (ver `config.py`).

## Pruebas

```
python -m pytest
```
Usan una base aparte, `almacen_test`, que se recrea en cada prueba.

## Estructura

```
config.py               Configuración (BD, clave secreta, carpeta de imágenes)
run.py                  Arranque del servidor
src/
  __init__.py           create_app(): registra módulos y filtros
  basedatos.py          Conexión MySQL y helpers (consultar, ejecutar, transaccion)
  schema.sql            Tablas
  init_db.py            Crea la BD y datos de ejemplo
  servicios.py          Reglas de negocio: stock, reservas, pedidos, despacho → venta, estancados
  auth.py               Login, registro de clientes, roles, CSRF
  rutas/
    admin_inventario.py Panel, productos, entradas/salidas, estancados
    admin_ventas.py     Registro de ventas
    admin_pedidos.py    CRUD de pedidos
    admin_usuarios.py   Usuarios
    trabajador.py       Tablero de pedidos y disponibilidad
    cliente.py          Catálogo, carrito, mis pedidos
templates/              base.html, login, registro + carpetas admin/, trabajador/, cliente/
static/css, js, img/productos (imágenes subidas)
tests/                  Pruebas con pytest
```

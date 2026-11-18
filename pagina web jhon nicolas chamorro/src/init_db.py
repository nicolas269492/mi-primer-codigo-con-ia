"""Crea la base de datos, las tablas y datos de ejemplo.

Uso (desde la raíz del proyecto):
    python -m src.init_db            # base 'almacen' con datos de ejemplo
    python -m src.init_db --vacia    # sin datos de ejemplo (solo el admin)

¡OJO! Borra y recrea todas las tablas: se pierden los datos que hubiera.
"""
import os
import sys

from werkzeug.security import generate_password_hash

from src.basedatos import _conectar

# Ruta del archivo con las sentencias CREATE TABLE.
SCHEMA = os.path.join(os.path.dirname(__file__), "schema.sql")

# Usuarios iniciales: (nombre, correo, contraseña, rol).
# El primero (admin) se crea siempre; los otros dos solo con datos de ejemplo.
USUARIOS_DEMO = [
    ("Administrador", "admin@almacen.com", "admin123", "admin"),
    ("Trabajador Demo", "trabajador@almacen.com", "trabajador123", "trabajador"),
    ("Cliente Demo", "cliente@almacen.com", "cliente123", "cliente"),
]

# Productos de ejemplo para poder probar la aplicación desde el primer momento.
PRODUCTOS_DEMO = [
    # codigo, nombre, descripcion, categoria, precio, stock, stock_minimo
    ("P-001", "Arroz 5 kg", "Arroz blanco de grano largo", "Abarrotes", 18500, 40, 10),
    ("P-002", "Aceite 1 L", "Aceite vegetal", "Abarrotes", 9800, 25, 8),
    ("P-003", "Detergente 2 kg", "Detergente en polvo", "Aseo", 21000, 12, 5),
    ("P-004", "Jabón de baño x3", "Paquete de 3 jabones", "Aseo", 7500, 0, 5),
    ("P-005", "Café 500 g", "Café molido tostado", "Bebidas", 16000, 30, 10),
    ("P-006", "Gaseosa 1.5 L", "Bebida gaseosa", "Bebidas", 5200, 60, 20),
    ("P-007", "Escoba plástica", "Escoba de cerdas duras", "Hogar", 12000, 8, 3),
]


def crear_base(app, con_demo=True):
    """Crea la base indicada en app.config['DB_NAME'], carga schema.sql e inserta datos.

    Parámetros:
        app:      aplicación Flask (de ella se toman los datos de conexión).
        con_demo: si es True inserta los 3 usuarios y los productos de ejemplo;
                  si es False solo crea el usuario administrador.

    También la usan las pruebas automáticas para dejar una base limpia antes de
    cada prueba.
    """
    with app.app_context():
        nombre = app.config["DB_NAME"]
        # Se conecta al servidor SIN base, porque puede que todavía no exista.
        conn = _conectar(con_base=False)
        cur = conn.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{nombre}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
        )
        cur.execute(f"USE `{nombre}`")

        # mysql.connector ejecuta una sentencia a la vez, así que se separa el
        # archivo por ";" y se quitan las líneas de comentario ("--").
        with open(SCHEMA, encoding="utf-8") as f:
            sql = f.read()
        for sentencia in [s.strip() for s in sql.split(";")]:
            lineas = [l for l in sentencia.splitlines() if not l.strip().startswith("--")]
            sentencia = "\n".join(lineas).strip()
            if sentencia:
                cur.execute(sentencia)

        # Usuarios (la contraseña se guarda como hash, nunca en texto plano).
        usuarios = USUARIOS_DEMO if con_demo else USUARIOS_DEMO[:1]
        for nombre_u, email, clave, rol in usuarios:
            cur.execute(
                "INSERT INTO usuarios (nombre, email, password_hash, rol) VALUES (%s,%s,%s,%s)",
                (nombre_u, email, generate_password_hash(clave), rol),
            )

        if con_demo:
            for p in PRODUCTOS_DEMO:
                cur.execute(
                    "INSERT INTO productos (codigo, nombre, descripcion, categoria, precio, stock, "
                    "stock_minimo, ultimo_movimiento) VALUES (%s,%s,%s,%s,%s,%s,%s, NOW())",
                    p,
                )
                # El stock inicial también queda registrado como movimiento de entrada,
                # para que el historial cuadre con el stock.
                if p[5] > 0:
                    cur.execute(
                        "INSERT INTO movimientos (producto_id, tipo, cantidad, motivo, usuario_id) "
                        "VALUES (LAST_INSERT_ID(), 'entrada', %s, 'Inventario inicial', 1)",
                        (p[5],),
                    )
            # Se "envejece" la escoba 45 días para que aparezca en el reporte de estancados.
            cur.execute(
                "UPDATE productos SET ultimo_movimiento = NOW() - INTERVAL 45 DAY WHERE codigo = 'P-007'"
            )

        conn.commit()
        cur.close()
        conn.close()


# Se ejecuta solo con "python -m src.init_db" (no al importar el módulo).
if __name__ == "__main__":
    from src import create_app

    crear_base(create_app(), con_demo="--vacia" not in sys.argv)
    print("Base de datos creada.")
    for nombre_u, email, clave, rol in USUARIOS_DEMO:
        print(f"  {rol:<11} {email} / {clave}")

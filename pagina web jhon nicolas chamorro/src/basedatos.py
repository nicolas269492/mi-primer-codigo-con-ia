"""Acceso a MySQL (XAMPP) con mysql.connector.

Este módulo esconde los detalles de la conexión. El resto del código solo usa
estas funciones:

    from src.basedatos import consultar, consultar_uno, ejecutar, transaccion

    productos = consultar("SELECT * FROM productos WHERE activo = %s", (1,))   # lista de dicts
    p = consultar_uno("SELECT * FROM productos WHERE id = %s", (5,))          # un dict o None
    nuevo_id = ejecutar("INSERT INTO ... VALUES (%s)", (x,))                   # hace commit

    with transaccion() as cur:          # varias sentencias que deben cumplirse todas o ninguna
        cur.execute(...)
        cur.execute(...)

IMPORTANTE: los valores SIEMPRE se pasan como parámetros (%s + tupla), nunca
pegándolos dentro del texto SQL. Así mysql.connector los escapa y se evita la
inyección SQL.

Todas las filas se devuelven como diccionarios: fila["nombre"], fila["precio"], etc.
"""
from contextlib import contextmanager

import mysql.connector
from flask import current_app, g


def _conectar(con_base=True):
    """Abre una conexión nueva a MySQL con los datos de config.py.

    Parámetro:
        con_base: si es False se conecta al servidor sin elegir base de datos
                  (lo usa init_db.py para poder hacer CREATE DATABASE).
    Devuelve:
        un objeto conexión de mysql.connector.

    autocommit=False significa que los cambios no se guardan hasta llamar a
    commit(); eso permite deshacer (rollback) si algo falla a mitad de camino.
    """
    cfg = current_app.config
    params = dict(
        host=cfg["DB_HOST"],
        port=cfg["DB_PORT"],
        user=cfg["DB_USER"],
        password=cfg["DB_PASSWORD"],
        charset="utf8mb4",              # soporta tildes, ñ y emojis
        collation="utf8mb4_general_ci",
        autocommit=False,
    )
    if con_base:
        params["database"] = cfg["DB_NAME"]
    return mysql.connector.connect(**params)


def get_db():
    """Devuelve la conexión de la petición actual (la crea la primera vez).

    `g` es un objeto de Flask que vive solo durante una petición HTTP. Guardar
    la conexión ahí permite reutilizarla en todas las consultas de esa petición
    en lugar de abrir una conexión nueva por cada consulta.
    """
    if "db" not in g:
        g.db = _conectar()
    return g.db


def cerrar_db(_exc=None):
    """Cierra la conexión al terminar la petición (Flask la llama automáticamente)."""
    db = g.pop("db", None)
    if db is not None and db.is_connected():
        db.close()


def consultar(sql, params=()):
    """Ejecuta un SELECT y devuelve TODAS las filas como lista de diccionarios.

    Ejemplo:
        consultar("SELECT * FROM usuarios WHERE rol = %s", ("cliente",))
        -> [{"id": 3, "nombre": "Cliente Demo", ...}, ...]
    """
    cur = get_db().cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()  # el cursor se cierra siempre, aunque haya error


def consultar_uno(sql, params=()):
    """Igual que consultar(), pero devuelve solo la primera fila (o None si no hay)."""
    filas = consultar(sql, params)
    return filas[0] if filas else None


def ejecutar(sql, params=()):
    """Ejecuta un INSERT, UPDATE o DELETE y guarda el cambio (commit).

    Devuelve:
        lastrowid: el id autoincremental de la fila insertada (útil tras un INSERT).
    Si hay un error, deshace el cambio (rollback) y vuelve a lanzar la excepción.
    """
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(sql, params)
        db.commit()
        return cur.lastrowid
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()


@contextmanager
def transaccion():
    """Agrupa varias sentencias en una transacción: o se guardan todas o ninguna.

    Uso:
        with transaccion() as cur:
            cur.execute("UPDATE productos SET stock = stock - 1 WHERE id = %s", (1,))
            cur.execute("INSERT INTO movimientos ...")

    - Si el bloque `with` termina bien -> commit (se guardan los cambios).
    - Si dentro del bloque se lanza cualquier excepción -> rollback (nada cambia).
    El cursor devuelve filas como diccionarios.
    """
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        yield cur          # aquí se ejecuta el código de dentro del `with`
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()


def obtener_config(clave, por_defecto=None):
    """Lee un valor de la tabla `configuracion` (ajustes editables desde la app).

    Ejemplo: obtener_config("dias_estancado", "30") -> "30"
    Los valores se guardan como texto; quien los usa debe convertirlos (int(), etc.).
    """
    fila = consultar_uno("SELECT valor FROM configuracion WHERE clave = %s", (clave,))
    return fila["valor"] if fila else por_defecto


def guardar_config(clave, valor):
    """Guarda un ajuste en `configuracion`: lo crea si no existe o lo actualiza si ya existe."""
    ejecutar(
        "INSERT INTO configuracion (clave, valor) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE valor = VALUES(valor)",
        (clave, str(valor)),
    )


def init_app(app):
    """Conecta este módulo con la app: cierra la conexión al final de cada petición."""
    app.teardown_appcontext(cerrar_db)

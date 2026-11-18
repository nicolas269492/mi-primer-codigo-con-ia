"""Punto de entrada: arranca el servidor de desarrollo.

    python run.py   ->   abre http://127.0.0.1:5000 en el navegador

`debug=True` recarga el servidor automáticamente al guardar cambios en el código
y muestra los errores detallados en el navegador. NO usar debug en producción.
"""
from src import create_app

# Se construye la aplicación con la configuración por defecto (config.Config).
app = create_app()

if __name__ == "__main__":
    # Solo se ejecuta si lanzas este archivo directamente (no al importarlo).
    app.run(debug=True)

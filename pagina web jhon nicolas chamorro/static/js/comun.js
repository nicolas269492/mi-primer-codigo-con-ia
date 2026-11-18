// Utilidades compartidas por todas las páginas.
// base.html carga este archivo en todas las páginas, antes del JS de cada módulo,
// así que estas funciones se pueden usar desde cliente.js, trabajador.js, etc.

/**
 * Devuelve el token CSRF que base.html pone en <meta name="csrf-token">.
 * El servidor rechaza cualquier POST que no lo incluya (protección contra CSRF).
 */
function csrfToken() {
  const m = document.querySelector('meta[name="csrf-token"]');
  return m ? m.content : '';
}

/**
 * Cabeceras para enviar JSON al servidor con fetch, ya con el token CSRF.
 * Uso: fetch(url, { method: 'POST', headers: cabecerasJSON(), body: JSON.stringify(datos) })
 */
function cabecerasJSON() {
  return { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() };
}

/**
 * Formatea un número como pesos colombianos sin decimales.
 * Ejemplo: moneda(18500) -> "$18.500"
 */
function moneda(n) {
  return '$' + Math.round(Number(n) || 0).toLocaleString('es-CO');
}

/**
 * Escapa los caracteres especiales de HTML (& < > " ').
 * Se usa SIEMPRE antes de meter en innerHTML un texto que viene de la base de datos
 * (nombres, notas...). Así, si alguien escribe "<script>" en un nombre, se muestra
 * como texto en lugar de ejecutarse (protección contra XSS).
 */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

/**
 * Confirmación antes de enviar formularios peligrosos (eliminar, cancelar...).
 * Basta con poner el atributo en el formulario:
 *   <form method="post" data-confirmar="¿Seguro que quieres eliminarlo?">
 * Si el usuario pulsa "Cancelar" en el cuadro de diálogo, el formulario no se envía.
 */
document.addEventListener('submit', e => {
  const msg = e.target.dataset && e.target.dataset.confirmar;
  if (msg && !confirm(msg)) e.preventDefault();
});

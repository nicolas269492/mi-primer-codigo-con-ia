// Módulo del trabajador: tablero de pedidos, alistamiento y disponibilidad.
// Depende de comun.js (csrfToken, cabecerasJSON, moneda, esc).
//
// Este archivo lo cargan tres páginas; cada función iniciarXxx() comprueba si
// su página está presente y, si no, no hace nada:
//   - iniciarTablero()        -> templates/trabajador/pedidos.html
//   - iniciarAlistamiento()   -> templates/trabajador/pedido.html
//   - iniciarDisponibilidad() -> templates/trabajador/disponibilidad.html

// ------------------------------------------------------------------ avisos
/**
 * Muestra un mensaje flotante que desaparece solo.
 * @param texto mensaje a mostrar
 * @param tipo  'info' | 'ok' | 'error' (define el color, clases .alerta-*)
 * @param ms    milisegundos que permanece visible
 */
function avisar(texto, tipo = 'info', ms = 6000) {
  let cont = document.getElementById('avisos');
  if (!cont) {
    cont = document.createElement('div');
    cont.id = 'avisos';
    cont.className = 'avisos';
    document.querySelector('main').prepend(cont);
  }
  const div = document.createElement('div');
  div.className = 'alerta alerta-' + tipo + ' aviso-flotante';
  div.textContent = texto;
  cont.appendChild(div);
  setTimeout(() => div.remove(), ms);
}

// ------------------------------------------------------------------ tablero
// Columnas del tablero (coinciden con los atributos data-lista del HTML).
const COLUMNAS = ['pendiente', 'preparacion', 'despachado', 'cancelado'];

/**
 * Construye el HTML de la tarjeta de un pedido.
 * @param p    pedido tal como lo devuelve /trabajador/api/pedidos
 * @param base URL base del módulo ("/trabajador/")
 * Cada botón de acción es un formulario POST real (funciona sin JS); con JS,
 * el envío se intercepta más abajo y se hace con fetch.
 * Todos los textos pasan por esc() para evitar inyección de HTML.
 */
function tarjetaHTML(p, base) {
  const url = base + 'pedido/' + p.id;
  const cerrado = p.estado === 'despachado' || p.estado === 'cancelado';
  const acciones = (p.acciones || []).map(a => `
    <form class="form-estado" method="post" action="${esc(url)}/estado"
          ${a.confirmar ? `data-confirmar="${esc(a.confirmar)}"` : ''}>
      <input type="hidden" name="csrf_token" value="${esc(csrfToken())}">
      <input type="hidden" name="estado" value="${esc(a.estado)}">
      <button class="btn btn-sm ${esc(a.clase)}" type="submit">${esc(a.texto)}</button>
    </form>`).join('');
  return `
<article class="tp${p.sin_stock ? ' tp-sin-stock' : ''}" data-id="${esc(p.id)}" data-href="${esc(url)}">
  <div class="fila entre">
    <a class="tp-num" href="${esc(url)}">Pedido #${esc(p.id)}</a>
    <span class="nota" title="${esc(p.fecha)}">${esc(cerrado ? p.actualizado : p.hace)}</span>
  </div>
  <div class="tp-cliente">${esc(p.cliente)}</div>
  <div class="fila entre tp-datos">
    <span>${esc(p.unidades)} ${p.unidades === 1 ? 'unidad' : 'unidades'} · ${esc(p.hora)}</span>
    <strong>${esc(moneda(p.total))}</strong>
  </div>
  ${p.sin_stock ? '<div class="tp-aviso">⚠ Stock insuficiente para despachar</div>' : ''}
  ${acciones ? `<div class="tp-acciones">${acciones}</div>` : ''}
</article>`;
}

/**
 * Tablero de pedidos: lo refresca cada 15 s, avisa de pedidos nuevos y
 * cambia estados sin recargar la página.
 */
function iniciarTablero() {
  const tablero = document.getElementById('tablero');
  if (!tablero) return;
  const api = tablero.dataset.api;
  const base = tablero.dataset.base;
  const estadoRefresco = document.getElementById('estado-refresco');
  const tituloOriginal = document.title;

  // Pedidos ya conocidos (los que vinieron renderizados en la página)
  const conocidos = new Set(
    [...document.querySelectorAll('.tp[data-id]')].map(el => Number(el.dataset.id))
  );
  // true mientras se está cambiando un estado: evita que un refresco automático
  // redibuje el tablero a mitad de la operación.
  let ocupado = false;

  /** Redibuja las columnas con los datos de la API, actualiza los contadores
   *  y detecta pedidos pendientes que no se habían visto (= nuevos). */
  function dibujar(datos) {
    const nuevos = [];
    COLUMNAS.forEach(est => {
      const lista = document.querySelector(`[data-lista="${est}"]`);
      const items = datos.pedidos[est] || [];
      if (lista) {
        lista.innerHTML = items.length
          ? items.map(p => tarjetaHTML(p, base)).join('')
          : '<p class="vacio">Sin pedidos</p>';
      }
      document.querySelectorAll(`[data-conteo="${est}"]`).forEach(el => {
        el.textContent = items.length;
      });
      items.forEach(p => {
        if (!conocidos.has(p.id)) {
          conocidos.add(p.id);
          if (est === 'pendiente') nuevos.push(p);
        }
      });
    });
    nuevos.forEach(p => {
      avisar(`🔔 Nuevo pedido #${p.id} de ${p.cliente} (${p.unidades} uds., ${moneda(p.total)})`, 'info', 10000);
      const el = document.querySelector(`.tp[data-id="${p.id}"]`);
      if (el) el.classList.add('tp-nuevo');
    });
    if (nuevos.length) {
      document.title = `(${nuevos.length}) Nuevo pedido · ${tituloOriginal}`;
      setTimeout(() => { document.title = tituloOriginal; }, 10000);
    }
    if (estadoRefresco) estadoRefresco.textContent = 'Actualizado a las ' + datos.generado;
  }

  /** Pide los pedidos al servidor y los dibuja. Si falla, lo indica y
   *  se reintentará en el siguiente ciclo. */
  async function refrescar() {
    if (ocupado) return;
    try {
      const r = await fetch(api, { headers: { 'Accept': 'application/json' } });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      dibujar(await r.json());
    } catch (err) {
      if (estadoRefresco) estadoRefresco.textContent = 'Sin conexión con el servidor; reintentando…';
    }
  }

  // Cambios de estado por fetch (los formularios funcionan igual sin JS)
  document.addEventListener('submit', async e => {
    const form = e.target.closest('.form-estado');
    if (!form || e.defaultPrevented) return;   // comun.js ya canceló si no se confirmó
    e.preventDefault();
    const tarjeta = form.closest('.tp');
    const id = tarjeta.dataset.id;
    const estado = form.querySelector('[name="estado"]').value;
    tarjeta.querySelectorAll('button').forEach(b => { b.disabled = true; });
    ocupado = true;
    try {
      const r = await fetch(`${base}api/pedidos/${encodeURIComponent(id)}/estado`, {
        method: 'POST', headers: cabecerasJSON(), body: JSON.stringify({ estado })
      });
      let datos = {};
      try { datos = await r.json(); } catch (_) { /* respuesta no JSON */ }
      if (r.ok && datos.ok) {
        avisar(datos.mensaje, 'ok');
      } else {
        avisar(datos.mensaje || 'No se pudo cambiar el estado (error ' + r.status + ').', 'error', 9000);
      }
    } catch (err) {
      avisar('Error de conexión al cambiar el estado.', 'error');
    } finally {
      ocupado = false;
      refrescar();
    }
  });

  // Clic en la tarjeta (fuera de botones/enlaces) → detalle
  document.addEventListener('click', e => {
    if (e.target.closest('a, button, form, input')) return;
    const tarjeta = e.target.closest('.tp[data-href]');
    if (tarjeta) window.location.href = tarjeta.dataset.href;
  });

  setInterval(refrescar, 15000);  // refresco automático cada 15 segundos
  // Al volver a la pestaña después de tenerla oculta, refresca de inmediato.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refrescar();
  });
}

// ------------------------------------------------------------------ alistamiento
/**
 * Detalle del pedido: casillas para ir marcando los productos ya alistados.
 * Las marcas se guardan en localStorage (solo en este navegador), con una
 * clave por pedido, para no perderlas si se recarga la página.
 */
function iniciarAlistamiento() {
  const tabla = document.getElementById('lista-alistar');
  if (!tabla) return;
  const clave = 'alistado-pedido-' + tabla.dataset.pedido;
  const progreso = document.getElementById('progreso-alistado');
  const casillas = [...tabla.querySelectorAll('.chk-alistado')];

  let marcados = {};
  try { marcados = JSON.parse(localStorage.getItem(clave) || '{}') || {}; } catch (_) { marcados = {}; }

  /** Tacha las filas marcadas y actualiza el contador "N de M alistados". */
  function actualizar() {
    let n = 0;
    casillas.forEach(c => {
      c.closest('tr').classList.toggle('fila-alistada', c.checked);
      if (c.checked) n++;
    });
    if (progreso) progreso.textContent = n;
  }

  casillas.forEach(c => {
    c.checked = !!marcados[c.dataset.item];
    c.addEventListener('change', () => {
      marcados[c.dataset.item] = c.checked;
      try { localStorage.setItem(clave, JSON.stringify(marcados)); } catch (_) { /* sin almacenamiento */ }
      actualizar();
    });
  });
  actualizar();
}

// ------------------------------------------------------------------ disponibilidad
// Etiqueta de color según el estado de cada producto.
const BADGES_DISP = {
  agotado: '<span class="badge badge-peligro">Agotado</span>',
  bajo: '<span class="badge badge-alerta">Bajo mínimo</span>',
  ok: '<span class="badge badge-ok">OK</span>'
};

/**
 * Tabla de disponibilidad: aplica los filtros sin recargar la página y se
 * refresca sola cada 30 s.
 */
function iniciarDisponibilidad() {
  const form = document.getElementById('filtros-disp');
  const cuerpo = document.getElementById('tabla-disp');
  if (!form || !cuerpo) return;
  const api = form.dataset.api;
  const estadoRefresco = document.getElementById('estado-refresco');
  let temporizador = null;

  /** Filtros del formulario convertidos en "buscar=..&categoria=..&estado=..". */
  function parametros() {
    return new URLSearchParams(new FormData(form)).toString();
  }

  /** Redibuja las filas de la tabla y los tres indicadores de arriba. */
  function dibujar(datos) {
    cuerpo.innerHTML = datos.productos.length
      ? datos.productos.map(p => `
        <tr class="disp-${esc(p.estado)}">
          <td><code>${esc(p.codigo)}</code></td>
          <td>${esc(p.nombre)}</td>
          <td>${esc(p.categoria)}</td>
          <td class="num">${esc(p.stock)}</td>
          <td class="num">${esc(p.reservado)}</td>
          <td class="num"><strong>${esc(p.disponible)}</strong></td>
          <td class="num">${esc(p.minimo)}</td>
          <td>${BADGES_DISP[p.estado] || ''}</td>
        </tr>`).join('')
      : '<tr><td colspan="8" class="vacio">No hay productos que coincidan con el filtro.</td></tr>';
    const r = datos.resumen || {};
    const k = (id, v) => { const el = document.getElementById(id); if (el && v !== undefined) el.textContent = v; };
    k('k-total', r.total); k('k-agotados', r.agotados); k('k-bajo', r.bajo);
    if (estadoRefresco) estadoRefresco.textContent = 'Actualizado a las ' + datos.generado;
  }

  async function refrescar() {
    try {
      const r = await fetch(api + '?' + parametros(), { headers: { 'Accept': 'application/json' } });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      dibujar(await r.json());
    } catch (err) {
      if (estadoRefresco) estadoRefresco.textContent = 'Sin conexión con el servidor; reintentando…';
    }
  }

  /** Pone los filtros en la URL (para que sobrevivan a una recarga) y refresca. */
  function aplicarFiltros() {
    try { history.replaceState(null, '', '?' + parametros()); } catch (_) { /* ignorar */ }
    refrescar();
  }

  form.addEventListener('submit', e => { e.preventDefault(); aplicarFiltros(); });
  form.addEventListener('change', aplicarFiltros);
  // Al escribir en "buscar" se espera 300 ms sin teclear antes de consultar
  // (así no se hace una petición por cada letra).
  form.querySelector('[name="buscar"]').addEventListener('input', () => {
    clearTimeout(temporizador);
    temporizador = setTimeout(aplicarFiltros, 300);
  });

  setInterval(refrescar, 30000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refrescar();
  });
}

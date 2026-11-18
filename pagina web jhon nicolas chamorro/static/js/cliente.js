// Módulo cliente: catálogo en vivo, carrito y contador del menú.
// Depende de comun.js (cabecerasJSON, moneda, esc).
//
// Páginas que usan este archivo:
//   - catálogo (templates/cliente/catalogo.html)  -> iniciarCatalogo()
//   - carrito  (templates/cliente/carrito.html)   -> iniciarCarrito()
//   - todas las del cliente                       -> contador del carrito en el menú
(function () {
  'use strict';

  const API_CARRITO = '/cliente/api/carrito';
  const INTERVALO_MS = 15000;  // cada cuánto se consulta la disponibilidad (15 s)

  // ------------------------------------------------------------ utilidades
  /** Pone el número de artículos en el globo del menú "Carrito" (vacío si es 0). */
  function actualizarContador(articulos) {
    const c = document.getElementById('contador-carrito');
    if (c) c.textContent = articulos > 0 ? String(articulos) : '';
  }

  /** fetch + lectura de JSON. Devuelve los datos aunque el código HTTP sea de
   *  error (p. ej. 409 agotado trae {ok:false, error}); solo lanza excepción si
   *  la respuesta no es JSON. */
  async function pedirJSON(url, opciones) {
    const r = await fetch(url, Object.assign({ credentials: 'same-origin' }, opciones || {}));
    let datos = null;
    try { datos = await r.json(); } catch (_e) { datos = null; }
    if (!datos) throw new Error('Respuesta inválida del servidor (' + r.status + ').');
    return datos;
  }

  /** Agrega (modo 'agregar') o fija (modo 'fijar') la cantidad de un producto en el carrito. */
  function enviarCarrito(productoId, cantidad, modo) {
    return pedirJSON(API_CARRITO, {
      method: 'POST',
      headers: cabecerasJSON(),
      body: JSON.stringify({ producto_id: productoId, cantidad: cantidad, modo: modo || 'agregar' })
    });
  }

  let temporizadorAviso = null;
  /** Muestra un mensaje en la caja #aviso-cliente durante 4 segundos. */
  function avisar(texto, tipo) {
    const caja = document.getElementById('aviso-cliente');
    if (!caja) return;
    caja.className = 'aviso-cliente alerta alerta-' + (tipo || 'ok');
    caja.textContent = texto;
    caja.hidden = false;
    clearTimeout(temporizadorAviso);
    temporizadorAviso = setTimeout(() => { caja.hidden = true; }, 4000);
  }

  // Contador del menú en todas las páginas del cliente
  function refrescarContador() {
    pedirJSON(API_CARRITO).then(d => actualizarContador(d.articulos || 0)).catch(() => {});
  }

  // ------------------------------------------------------------ catálogo
  /**
   * Catálogo en vivo. Cada 15 s pide los productos a la API y actualiza las
   * tarjetas EN SU SITIO (no las vuelve a crear), así no se pierde el scroll,
   * lo escrito en los filtros ni las cantidades.
   * @param cont contenedor #catalogo (su data-api indica la URL de la API)
   */
  function iniciarCatalogo(cont) {
    const urlApi = cont.dataset.api;
    const inBuscar = document.getElementById('filtro-buscar');
    const selCat = document.getElementById('filtro-categoria');
    const chkDisp = document.getElementById('filtro-disponibles');
    const vacio = document.getElementById('catalogo-vacio');
    const indicador = document.getElementById('estado-actualizacion');

    let productos = [];
    let carrito = {};
    let ultimaActualizacion = Date.now();
    let falloConexion = false;
    const tarjetas = new Map(); // id -> elemento

    /** Minúsculas y sin tildes, para que buscar "cafe" encuentre "Café". */
    function normalizar(s) {
      return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    }

    /** Crea el esqueleto vacío de la tarjeta de un producto (lo rellena pintarTarjeta). */
    function crearTarjeta(p) {
      const el = document.createElement('article');
      el.className = 'producto';
      el.dataset.id = p.id;
      el.innerHTML =
        '<div class="producto-img"></div>' +
        '<div class="producto-info">' +
        '  <span class="badge p-cat"></span>' +
        '  <h3 class="producto-nombre"></h3>' +
        '  <p class="nota p-desc"></p>' +
        '  <div class="producto-precio"></div>' +
        '  <div class="p-disp"></div>' +
        '  <div class="nota p-en-carrito"></div>' +
        '  <form class="fila p-agregar">' +
        '    <input type="number" class="p-cant" min="1" value="1" aria-label="Cantidad">' +
        '    <button type="submit" class="btn btn-primario btn-sm">Agregar al carrito</button>' +
        '  </form>' +
        '</div>';
      el.querySelector('.p-agregar').addEventListener('submit', e => {
        e.preventDefault();
        agregar(el);
      });
      return el;
    }

    /**
     * Rellena/actualiza una tarjeta con los datos del producto: imagen, nombre,
     * precio, disponibilidad ("Disponible: N", "¡Últimas N unidades!" o
     * "Agotado"), lo que hay en el carrito y el máximo del selector de cantidad.
     * Si el precio o la disponibilidad cambian, los resalta un instante (destellar).
     */
    function pintarTarjeta(el, p) {
      // Imagen (solo se toca si cambió)
      const img = el.querySelector('.producto-img');
      if (img.dataset.src !== String(p.imagen_url || '')) {
        img.dataset.src = String(p.imagen_url || '');
        img.innerHTML = p.imagen_url
          ? '<img src="' + esc(p.imagen_url) + '" alt="' + esc(p.nombre) + '" loading="lazy">'
          : '<span aria-hidden="true">📦</span>';
      }
      el.querySelector('.p-cat').textContent = p.categoria;
      el.querySelector('.producto-nombre').textContent = p.nombre;
      const desc = el.querySelector('.p-desc');
      desc.textContent = p.descripcion || '';
      desc.hidden = !p.descripcion;

      const precio = el.querySelector('.producto-precio');
      const precioTxt = moneda(p.precio);
      if (precio.textContent && precio.textContent !== precioTxt) destellar(precio);
      precio.textContent = precioTxt;

      const disp = el.querySelector('.p-disp');
      const agotado = p.disponible <= 0;
      let html;
      if (agotado) html = '<span class="badge badge-peligro">Agotado</span>';
      else if (p.disponible <= p.stock_minimo)
        html = '<span class="badge badge-alerta">¡Últimas ' + p.disponible + ' unidades!</span>';
      else html = '<span class="badge badge-ok">Disponible: ' + p.disponible + '</span>';
      if (disp.innerHTML !== html) {
        if (disp.innerHTML) destellar(disp);
        disp.innerHTML = html;
      }
      el.classList.toggle('agotado', agotado);

      const enCarrito = carrito[String(p.id)] || 0;
      const nota = el.querySelector('.p-en-carrito');
      nota.textContent = enCarrito ? 'En tu carrito: ' + enCarrito : '';
      nota.hidden = !enCarrito;

      const cant = el.querySelector('.p-cant');
      const boton = el.querySelector('.p-agregar button');
      cant.max = Math.max(p.disponible, 1);
      cant.disabled = agotado;
      boton.disabled = agotado;
      boton.textContent = agotado ? 'Agotado' : 'Agregar al carrito';
      if (!agotado && Number(cant.value) > p.disponible) cant.value = p.disponible;
    }

    /** Reproduce la animación CSS .destello sobre un elemento que acaba de cambiar. */
    function destellar(nodo) {
      nodo.classList.remove('destello');
      void nodo.offsetWidth; // reinicia la animación
      nodo.classList.add('destello');
    }

    /**
     * Hace que las tarjetas en pantalla coincidan con la lista recibida:
     * quita las de productos desactivados, crea las de productos nuevos,
     * actualiza el resto y las deja en el mismo orden que la lista.
     */
    function sincronizar(lista) {
      productos = lista;
      const vigentes = new Set(lista.map(p => p.id));
      // Quitar productos desactivados
      for (const [id, el] of tarjetas) {
        if (!vigentes.has(id)) { el.remove(); tarjetas.delete(id); }
      }
      // Crear/actualizar y mantener el orden recibido
      let anterior = null;
      for (const p of lista) {
        let el = tarjetas.get(p.id);
        if (!el) {
          el = crearTarjeta(p);
          tarjetas.set(p.id, el);
        }
        pintarTarjeta(el, p);
        const siguiente = anterior ? anterior.nextElementSibling : cont.firstElementChild;
        if (siguiente !== el) cont.insertBefore(el, siguiente);
        anterior = el;
      }
      actualizarCategorias();
      filtrar();
    }

    /** Reconstruye el selector de categorías si aparecieron o desaparecieron
     *  categorías, conservando la que el cliente tenía elegida. */
    function actualizarCategorias() {
      const actual = selCat.value;
      const cats = [...new Set(productos.map(p => p.categoria))].sort((a, b) => a.localeCompare(b, 'es'));
      const existentes = [...selCat.options].slice(1).map(o => o.value);
      if (cats.join('\u0001') === existentes.join('\u0001')) return;
      selCat.length = 1;
      for (const c of cats) selCat.add(new Option(c, c));
      if (actual && !cats.includes(actual)) selCat.add(new Option(actual, actual));
      selCat.value = actual;
    }

    /** Muestra/oculta tarjetas según búsqueda, categoría y "solo disponibles".
     *  Se hace en el navegador, sin consultar al servidor. */
    function filtrar() {
      const q = normalizar(inBuscar.value.trim());
      const cat = selCat.value;
      const soloDisp = chkDisp && chkDisp.checked;
      let visibles = 0;
      for (const p of productos) {
        const el = tarjetas.get(p.id);
        const coincide =
          (!q || normalizar(p.nombre + ' ' + p.descripcion + ' ' + p.categoria).includes(q)) &&
          (!cat || p.categoria === cat) &&
          (!soloDisp || p.disponible > 0);
        el.hidden = !coincide;
        if (coincide) visibles++;
      }
      vacio.hidden = visibles > 0;
      vacio.textContent = productos.length
        ? 'No hay productos que coincidan con tu búsqueda.'
        : 'Por ahora no hay productos en el catálogo.';
    }

    /** Botón "Agregar al carrito" de una tarjeta: envía la cantidad elegida,
     *  muestra el resultado (o el aviso si se ajustó la cantidad) y refresca
     *  la disponibilidad. */
    async function agregar(el) {
      const id = Number(el.dataset.id);
      const input = el.querySelector('.p-cant');
      const boton = el.querySelector('.p-agregar button');
      const cantidad = parseInt(input.value, 10);
      if (!cantidad || cantidad < 1) { avisar('Indica una cantidad válida.', 'error'); return; }
      boton.disabled = true;
      try {
        const d = await enviarCarrito(id, cantidad, 'agregar');
        if (d.lineas) {
          carrito = {};
          d.lineas.forEach(l => { carrito[String(l.id)] = l.cantidad; });
          actualizarContador(d.articulos || 0);
        }
        if (d.ok) {
          avisar((d.avisos && d.avisos.length) ? d.avisos[0] : d.mensaje, d.avisos && d.avisos.length ? 'info' : 'ok');
          input.value = 1;
        } else {
          avisar(d.error || 'No se pudo agregar al carrito.', 'error');
        }
      } catch (err) {
        avisar(err.message, 'error');
      } finally {
        boton.disabled = false;
        await actualizar(); // refleja la disponibilidad más reciente
      }
    }

    /** Consulta la API del catálogo y sincroniza las tarjetas y el contador. */
    async function actualizar() {
      try {
        const d = await pedirJSON(urlApi);
        carrito = d.carrito || {};
        sincronizar(d.productos || []);
        actualizarContador(Object.values(carrito).reduce((a, b) => a + Number(b), 0));
        ultimaActualizacion = Date.now();
        falloConexion = false;
      } catch (_e) {
        falloConexion = true;
      }
      pintarIndicador();
    }

    /** Texto "Actualizado hace X s" (y "Sin conexión" si la última consulta falló). */
    function pintarIndicador() {
      const s = Math.max(0, Math.round((Date.now() - ultimaActualizacion) / 1000));
      indicador.textContent = (falloConexion ? 'Sin conexión · ' : '') + 'Actualizado hace ' + s + ' s';
      indicador.classList.toggle('indicador-error', falloConexion);
    }

    inBuscar.addEventListener('input', filtrar);
    selCat.addEventListener('change', filtrar);
    if (chkDisp) chkDisp.addEventListener('change', filtrar);

    // Datos iniciales incrustados en la página
    try {
      const ini = JSON.parse(document.getElementById('datos-catalogo').textContent);
      carrito = ini.carrito || {};
      sincronizar(ini.productos || []);
    } catch (_e) { /* se cargará con la primera consulta */ }

    // Consulta periódica (solo si la pestaña está visible, para no gastar recursos).
    setInterval(() => { if (!document.hidden) actualizar(); }, INTERVALO_MS);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && Date.now() - ultimaActualizacion > INTERVALO_MS) actualizar();
    });
    setInterval(pintarIndicador, 1000);
  }

  // ------------------------------------------------------------ carrito
  /**
   * Página del carrito: cambiar cantidades y quitar productos sin recargar,
   * y revalidar precios/disponibilidad cada 15 s.
   * @param caja contenedor #carrito (su data-api es la URL del resumen)
   */
  function iniciarCarrito(caja) {
    const tbody = document.getElementById('carrito-lineas');
    const tabla = document.getElementById('carrito-tabla');
    const vacio = document.getElementById('carrito-vacio');
    const total = document.getElementById('carrito-total');
    const form = document.getElementById('form-pedido');
    const avisos = document.getElementById('avisos-carrito');

    /** Redibuja la tabla del carrito, el total y los avisos con el resumen del servidor.
     *  Si el carrito queda vacío, oculta la tabla y el formulario de envío. */
    function pintar(d) {
      tbody.innerHTML = d.lineas.map(l =>
        '<tr data-id="' + l.id + '">' +
        '<td><strong>' + esc(l.nombre) + '</strong><br><span class="nota">' + esc(l.categoria) +
        ' · Disponible: ' + l.disponible + '</span></td>' +
        '<td class="num">' + moneda(l.precio) + '</td>' +
        '<td><input class="cant-carrito" type="number" min="1" max="' + l.disponible + '" value="' +
        l.cantidad + '" aria-label="Cantidad de ' + esc(l.nombre) + '"></td>' +
        '<td class="num">' + moneda(l.subtotal) + '</td>' +
        '<td><button type="button" class="btn btn-sec btn-sm quitar">Quitar</button></td>' +
        '</tr>'
      ).join('');
      total.textContent = moneda(d.total);
      const hay = d.lineas.length > 0;
      tabla.hidden = !hay;
      vacio.hidden = hay;
      form.hidden = !hay;
      avisos.innerHTML = (d.avisos || []).map(a => '<div class="alerta alerta-info">' + esc(a) + '</div>').join('') +
        (d.error ? '<div class="alerta alerta-error">' + esc(d.error) + '</div>' : '');
      actualizarContador(d.articulos || 0);
    }

    /** Espera una petición al carrito mostrando el estado "cargando" y luego repinta. */
    async function ejecutar(promesa) {
      caja.classList.add('cargando');
      try { pintar(await promesa); }
      catch (err) { avisos.innerHTML = '<div class="alerta alerta-error">' + esc(err.message) + '</div>'; }
      finally { caja.classList.remove('cargando'); }
    }

    // Cambio de cantidad en una línea: la fija en el servidor (0 = quitar, con confirmación).
    tbody.addEventListener('change', e => {
      if (!e.target.classList.contains('cant-carrito')) return;
      const id = Number(e.target.closest('tr').dataset.id);
      const cant = parseInt(e.target.value, 10);
      if (!cant || cant < 1) {
        if (!confirm('¿Quitar este producto del carrito?')) { e.target.value = 1; return; }
      }
      ejecutar(enviarCarrito(id, cant || 0, 'fijar'));
    });

    // Botón "Quitar" de una línea.
    tbody.addEventListener('click', e => {
      const b = e.target.closest('.quitar');
      if (!b) return;
      const id = Number(b.closest('tr').dataset.id);
      ejecutar(pedirJSON(API_CARRITO + '/' + id + '/quitar', { method: 'POST', headers: cabecerasJSON() }));
    });

    // Revalida precios y disponibilidad periódicamente mientras el usuario no esté escribiendo
    setInterval(() => {
      if (document.hidden) return;
      const activo = document.activeElement;
      if (activo && tbody.contains(activo)) return;
      pedirJSON(caja.dataset.api).then(pintar).catch(() => {});
    }, INTERVALO_MS);

    // Al enviar el pedido, deshabilita el botón para evitar enviarlo dos veces.
    // (setTimeout 0: se deshabilita justo después de que el navegador envíe el formulario.)
    form.addEventListener('submit', () => {
      const b = form.querySelector('button[type="submit"]');
      setTimeout(() => { b.disabled = true; b.textContent = 'Enviando…'; }, 0);
    });
  }

  // ------------------------------------------------------------ arranque
  // Cuando la página termina de cargar, se inicia lo que corresponda a esa página.
  document.addEventListener('DOMContentLoaded', () => {
    const cat = document.getElementById('catalogo');
    const car = document.getElementById('carrito');
    if (cat) iniciarCatalogo(cat);
    if (car) iniciarCarrito(car);
    refrescarContador();
  });
})();

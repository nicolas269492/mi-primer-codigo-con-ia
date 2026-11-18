// Formulario de pedidos (admin): líneas dinámicas producto + cantidad.
// Lo carga templates/admin/pedido_form.html. Permite añadir/quitar líneas,
// muestra el disponible, el precio y el subtotal de cada línea, y el total.
// Todo está dentro de una función que se ejecuta sola "(function () { ... })()"
// para que sus variables no choquen con las de otros archivos JS.
(function () {
  const cuerpo = document.getElementById('lineas');
  const form = document.getElementById('form-pedido');
  if (!cuerpo || !form) return;  // no estamos en la página del formulario

  // Fila modelo: copia de la primera línea, vacía. Se clona cada vez que se
  // pulsa "Agregar línea".
  const modelo = cuerpo.querySelector('tr.linea').cloneNode(true);
  modelo.querySelector('.sel-producto').value = '';
  modelo.querySelector('.inp-cantidad').value = '1';

  /** Devuelve la <option> del producto elegido en una fila, o null si no hay producto.
   *  Cada <option> lleva data-disponible y data-precio puestos por la plantilla. */
  function opcion(fila) {
    const sel = fila.querySelector('.sel-producto');
    return sel.value ? sel.options[sel.selectedIndex] : null;
  }

  // Unidades pedidas por producto en todo el formulario (por si se repite en varias líneas)
  function pedidasPorProducto() {
    const total = {};
    cuerpo.querySelectorAll('tr.linea').forEach(f => {
      const pid = f.querySelector('.sel-producto').value;
      const cant = parseInt(f.querySelector('.inp-cantidad').value, 10) || 0;
      if (pid) total[pid] = (total[pid] || 0) + cant;
    });
    return total;
  }

  /** Actualiza todas las filas (disponible, precio, subtotal) y el total del pedido.
   *  Marca en rojo (.excedido) el disponible si, sumando todas las líneas de un
   *  mismo producto, se pide más de lo que hay. Se llama en cada cambio. */
  function recalcular() {
    const pedidas = pedidasPorProducto();
    let total = 0;
    cuerpo.querySelectorAll('tr.linea').forEach(f => {
      const op = opcion(f);
      const cDisp = f.querySelector('.col-disponible');
      const cPrecio = f.querySelector('.col-precio');
      const cSub = f.querySelector('.col-subtotal');
      const inp = f.querySelector('.inp-cantidad');
      if (!op) {
        cDisp.textContent = cPrecio.textContent = cSub.textContent = '—';
        cDisp.classList.remove('excedido');
        inp.removeAttribute('max');
        return;
      }
      const disp = parseInt(op.dataset.disponible, 10) || 0;
      const precio = Number(op.dataset.precio) || 0;
      const cant = parseInt(inp.value, 10) || 0;
      cDisp.textContent = disp;
      cDisp.classList.toggle('excedido', pedidas[f.querySelector('.sel-producto').value] > disp);
      cDisp.title = cDisp.classList.contains('excedido') ? 'La cantidad pedida supera lo disponible' : '';
      inp.max = disp;
      cPrecio.textContent = moneda(precio);
      cSub.textContent = moneda(precio * cant);
      total += precio * cant;
    });
    document.getElementById('total-pedido').textContent = moneda(total);
  }

  // Botón "Agregar línea": añade una fila vacía al final.
  document.getElementById('agregar-linea').addEventListener('click', () => {
    const fila = modelo.cloneNode(true);
    cuerpo.appendChild(fila);
    fila.querySelector('.sel-producto').focus();
    recalcular();
  });

  // Botón "Quitar" de una fila. Se escucha en la tabla completa (delegación de
  // eventos) para que funcione también en las filas añadidas después.
  // Si es la última fila, no se borra: solo se vacía.
  cuerpo.addEventListener('click', e => {
    const btn = e.target.closest('.quitar-linea');
    if (!btn) return;
    const filas = cuerpo.querySelectorAll('tr.linea');
    const fila = btn.closest('tr.linea');
    if (filas.length > 1) {
      fila.remove();
    } else {
      fila.querySelector('.sel-producto').value = '';
      fila.querySelector('.inp-cantidad').value = '1';
    }
    recalcular();
  });

  // Recalcular al cambiar un producto o escribir una cantidad.
  cuerpo.addEventListener('change', recalcular);
  cuerpo.addEventListener('input', recalcular);

  // Antes de enviar: evita mandar un pedido sin ningún producto.
  // (El servidor vuelve a validar todo; esto solo ahorra un viaje.)
  form.addEventListener('submit', e => {
    const conProducto = [...cuerpo.querySelectorAll('.sel-producto')].some(s => s.value);
    if (!conProducto) {
      e.preventDefault();
      alert('Agrega al menos un producto al pedido.');
    }
  });

  recalcular();  // cálculo inicial (al editar, las líneas ya vienen rellenas)
})();

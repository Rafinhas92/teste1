const config = window.restaurantConfig;
const money = value => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);
let savedCart = [];
try { savedCart = JSON.parse(localStorage.getItem('benedetto-cart') || '[]'); } catch {}
const cart = new Map(Array.isArray(savedCart) ? savedCart.filter(line => Array.isArray(line) && config.menu.some(item => item.id === line[0]) && Number.isInteger(line[1]) && line[1] > 0 && line[1] <= 30) : []);
let paymentConfig = { enabled: false, test_mode: true };
let checkoutBusy = false;
let deliveryQuote = null;
let deliveryBusy = false;
let quoteExpiryTimer;
let deliveryPoint = null;
let deliveryMap;
let deliveryMarker;
const categories = Object.fromEntries(config.categories.map(category => [category.id, [category.label.toUpperCase(), 'main-art']]));
const menu = document.querySelector('#menu-items');
const contactReady = /^\d{10,15}$/.test(config.whatsapp);
const orderingReady = contactReady && !config.demo;
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}
config.menu.forEach(item => {
  const card = element('article', '', 'menu-card');
  card.dataset.category = item.category;
  const art = element('div', '', `card-art ${categories[item.category][1]}`);
  if (item.image) {
    const image = element('img'); image.src = item.image; image.alt = item.name; image.loading = 'lazy'; art.append(image);
  } else { art.textContent = 'B'; art.setAttribute('aria-hidden', 'true'); }
  const copy = element('div', '', 'card-copy');
  copy.append(element('span', categories[item.category][0]), element('h3', item.name), element('p', item.description));
  const bottom = element('div', '', 'card-bottom');
  const add = element('button', 'Adicionar +', 'add-button');
  add.type = 'button';
  add.setAttribute('aria-label', `Adicionar ${item.name} ao pedido`);
  add.addEventListener('click', () => { cart.set(item.id, Math.min(30, (cart.get(item.id) || 0) + 1)); renderCart(); });
  bottom.append(element('strong', money(item.price)), add);
  copy.append(bottom); card.append(art, copy); menu.append(card);
});
const filterContainer = document.querySelector('#menu-filters');
config.categories.forEach((category, index) => {
  const button = element('button', category.label, index === 0 ? 'active' : '');
  button.type = 'button'; button.dataset.filter = category.id;
  button.setAttribute('aria-pressed', String(index === 0)); filterContainer.append(button);
});
const filters = document.querySelectorAll('[data-filter]');
document.querySelectorAll('[data-category]').forEach(card => { card.hidden = card.dataset.category !== config.categories[0].id; });
filters.forEach(button => button.addEventListener('click', () => {
  filters.forEach(filter => {
    filter.classList.toggle('active', filter === button);
    filter.setAttribute('aria-pressed', String(filter === button));
  });
  document.querySelectorAll('[data-category]').forEach(card => {
    card.hidden = button.dataset.filter !== 'all' && card.dataset.category !== button.dataset.filter;
  });
}));
function renderCart() {
  const container = document.querySelector('#cart-items');
  container.replaceChildren();
  let total = 0, count = 0;
  cart.forEach((quantity, id) => {
    const item = config.menu.find(item => item.id === id);
    total += quantity * item.price; count += quantity;
    const row = element('div', '', 'cart-row');
    const controls = element('div', '', 'quantity-controls');
    for (const [label, delta] of [['−', -1], ['+', 1]]) {
      const button = element('button', label);
      button.type = 'button';
      button.setAttribute('aria-label', `${delta > 0 ? 'Aumentar' : 'Diminuir'} quantidade de ${item.name}`);
      button.addEventListener('click', () => {
        const next = Math.min(30, quantity + delta);
        if (next === 0) cart.delete(id); else cart.set(id, next);
        renderCart();
      });
      controls.append(button);
      if (delta === -1) controls.append(element('span', String(quantity)));
    }
    row.append(element('span', item.name), controls, element('strong', money(item.price * quantity)));
    container.append(row);
  });
  const includeDelivery = deliveryQuote && document.querySelector('select[name=fulfillment]').value === 'Entrega' && deliveryQuote.expires_at * 1000 > Date.now();
  document.querySelector('#cart-total').textContent = money(total + (includeDelivery ? deliveryQuote.fee : 0));
  document.querySelector('#cart-status').textContent = count ? `${count} ${count === 1 ? 'item no pedido' : 'itens no pedido'}.` : 'Seu carrinho está vazio.';
  try { localStorage.setItem('benedetto-cart', JSON.stringify([...cart])); } catch {}
  updatePaymentButton(count);
}
const orderForm = document.querySelector('#order-form');
orderForm.elements.fulfillment.addEventListener('change', () => {
  const delivery = orderForm.elements.fulfillment.value === 'Entrega';
  document.querySelector('#address-label').hidden = !delivery;
  orderForm.elements.address.required = delivery;
  document.querySelector('#delivery-section').hidden = !delivery;
  if (!delivery) deliveryQuote = null;
  renderCart();
});
function openWhatsApp(message) {
  window.open(`https://wa.me/${config.whatsapp}?text=${encodeURIComponent(message)}`, '_blank', 'noopener,noreferrer');
}
orderForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (!orderingReady || !cart.size || !orderForm.reportValidity()) return;
  const data = new FormData(orderForm);
  if (data.get('payment') === 'online') {
    await payOnline(data);
    return;
  }
  let total = 0;
  const lines = [...cart].map(([id, quantity]) => {
    const item = config.menu.find(item => item.id === id);
    total += item.price * quantity;
    return `${quantity} × ${item.name}: ${money(item.price * quantity)}`;
  });
  openWhatsApp(`Olá, Ristorante Benedetto! Quero solicitar um pedido.\nNome: ${data.get('name')}\nTelefone: ${data.get('phone')}\n${lines.join('\n')}\nSubtotal: ${money(total)}\nRecebimento: ${data.get('fulfillment')}${data.get('fulfillment') === 'Entrega' ? `\nEndereço: ${data.get('address')}` : ''}${deliveryQuote && data.get('fulfillment') === 'Entrega' ? `\nFrete calculado: ${money(deliveryQuote.fee)}\nTotal com frete: ${money(total + deliveryQuote.fee)}` : ''}\nObservações: ${data.get('notes') || 'Nenhuma'}\nAguardo confirmação de disponibilidade, frete e pagamento.`);
  document.querySelector('#order-feedback').textContent = 'Continue no WhatsApp e envie a mensagem. O pedido depende da confirmação do restaurante.';
});
const reservationForm = document.querySelector('#reservation-form');
const todayParts = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Campo_Grande', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
const dateParts = Object.fromEntries(todayParts.map(part => [part.type, part.value]));
const localDate = `${dateParts.year}-${dateParts.month}-${dateParts.day}`;
reservationForm.elements.date.min = localDate;
document.querySelector('#send-reservation').disabled = !contactReady;
reservationForm.addEventListener('submit', event => {
  event.preventDefault();
  if (!contactReady || !reservationForm.reportValidity()) return;
  const data = new FormData(reservationForm);
  const date = data.get('date').split('-').reverse().join('/');
  openWhatsApp(`Olá, Ristorante Benedetto! Quero solicitar uma reserva.\nNome: ${data.get('name')}\nData: ${date}\nHorário: ${data.get('time')}\nPessoas: ${data.get('guests')}\nObservações: ${data.get('notes') || 'Nenhuma'}\nAguardo confirmação da reserva.`);
  document.querySelector('#reservation-feedback').textContent = 'Continue no WhatsApp e envie a mensagem. A reserva depende da confirmação do restaurante.';
});
renderCart();

function updatePaymentButton(count = [...cart.values()].reduce((sum, quantity) => sum + quantity, 0)) {
  const online = orderForm.elements.payment.value === 'online';
  const button = document.querySelector('#send-order');
  const delivery = orderForm.elements.fulfillment.value === 'Entrega';
  const validQuote = deliveryQuote && deliveryQuote.expires_at * 1000 > Date.now();
  const allowed = !online || (paymentConfig.enabled && (!delivery || (paymentConfig.delivery_enabled && validQuote)));
  document.querySelector('#calculate-delivery').disabled = deliveryBusy || !paymentConfig.delivery_enabled;
  button.disabled = checkoutBusy || !count || !orderingReady || !allowed;
  button.textContent = checkoutBusy ? 'Preparando pagamento…' : online ? 'Continuar para o Mercado Pago ↗' : 'Enviar pedido pelo WhatsApp ↗';
  const notice = document.querySelector('#payment-notice');
  if (!paymentConfig.enabled) notice.textContent = 'Pagamento online ainda não ativado. Você pode combinar o pagamento pelo WhatsApp.';
  else if (delivery) notice.textContent = paymentConfig.delivery_enabled ? 'Calcule o frete para incluir a entrega no total antes de pagar.' : 'O cálculo de frete ainda não está ativado. Combine a entrega pelo WhatsApp.';
  else notice.textContent = paymentConfig.test_mode ? 'Ambiente de teste: não realiza cobranças reais. Retirada no restaurante.' : 'Pagamento pelo Mercado Pago para retirada no restaurante. Pix e cartão conforme disponibilidade no checkout.';
}
orderForm.elements.payment.addEventListener('change', () => {
  document.querySelector('#order-feedback').textContent = '';
  updatePaymentButton();
});
async function loadPaymentConfig() {
  try {
    const response = await fetch('/api/payment-config');
    if (!response.ok) throw new Error();
    paymentConfig = await response.json();
  } catch { paymentConfig.enabled = false; }
  const option = orderForm.elements.payment.querySelector('[value=online]');
  option.disabled = !paymentConfig.enabled;
  option.textContent = paymentConfig.enabled ? (paymentConfig.test_mode ? 'Mercado Pago — teste' : 'Mercado Pago — Pix e cartão') : 'Pagamento online — ainda não ativado';
  updatePaymentButton();
}
async function payOnline(data) {
  if (checkoutBusy || !paymentConfig.enabled || (data.get('fulfillment') === 'Entrega' && (!deliveryQuote || deliveryQuote.expires_at * 1000 <= Date.now()))) return;
  checkoutBusy = true;
  updatePaymentButton();
  const feedback = document.querySelector('#order-feedback');
  const payload = {
    customer: { name: data.get('name'), phone: data.get('phone'), notes: data.get('notes') },
    fulfillment: data.get('fulfillment'),
    address: data.get('fulfillment') === 'Entrega' ? data.get('address') : '',
    quote_id: data.get('fulfillment') === 'Entrega' ? deliveryQuote?.quote_id : null,
    destination: data.get('fulfillment') === 'Entrega' ? deliveryPoint : null,
    pin_confirmed: document.querySelector('#confirm-delivery-pin').checked,
    items: [...cart].map(([id, quantity]) => ({ id, quantity }))
  };
  const fingerprint = JSON.stringify(payload);
  let attempt;
  try { attempt = JSON.parse(sessionStorage.getItem('benedetto-checkout')); } catch {}
  if (!attempt || attempt.fingerprint !== fingerprint) attempt = { fingerprint, key: crypto.randomUUID() };
  try { sessionStorage.setItem('benedetto-checkout', JSON.stringify(attempt)); } catch {}
  try {
    const response = await fetch('/api/checkout', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': attempt.key }, body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Não foi possível iniciar o pagamento.');
    const destination = new URL(result.checkout_url);
    if (destination.protocol !== 'https:' || !['www.mercadopago.com.br', 'sandbox.mercadopago.com.br'].includes(destination.hostname)) throw new Error('Endereço de pagamento inválido.');
    try { sessionStorage.setItem('benedetto-order', result.order_id); } catch {}
    window.location.assign(destination.href);
  } catch (error) {
    feedback.textContent = error.message || 'Não foi possível iniciar o pagamento. Tente novamente.';
    checkoutBusy = false;
    updatePaymentButton();
  }
}
async function checkReturnedPayment() {
  const orderId = new URLSearchParams(window.location.search).get('order');
  if (!orderId) return;
  const display = document.querySelector('#payment-status');
  document.querySelector('#pedido').scrollIntoView({ behavior: 'smooth' });
  const messages = {
    paid: 'Pagamento aprovado! Aguarde a confirmação do restaurante sobre o preparo e o prazo de entrega ou retirada.',
    pending: 'Pagamento em processamento. Aguarde a confirmação; não refaça o pagamento.',
    awaiting_payment: 'Ainda não encontramos uma transação no Mercado Pago para este pedido. Se não houver pagamento na sua Atividade, você pode retomar o checkout existente.',
    verification_pending: 'Encontramos uma transação, mas ainda não conseguimos confirmar os dados do pagamento. Não pague novamente; fale com o restaurante.',
    failed: 'Pagamento recusado. Confira o Mercado Pago ou fale com o restaurante.',
    cancelled: 'Pagamento cancelado. Fale com o restaurante se precisar de ajuda.',
    refunded: 'Pagamento estornado ou contestado. Entre em contato com o restaurante.'
  };
  let attempts = 0;
  async function refresh() {
    try {
      const response = await fetch('/api/order-status?order=' + encodeURIComponent(orderId));
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      display.textContent = (result.test_mode ? 'TESTE — ' : '') + (messages[result.status] || 'Não foi possível determinar o status do pagamento. Fale com o restaurante antes de tentar novamente.');
      if (result.status === 'awaiting_payment' && result.checkout_url) {
        const destination = new URL(result.checkout_url);
        if (destination.protocol === 'https:' && destination.hostname === 'www.mercadopago.com.br') {
          const resume = element('a', 'Retomar este checkout ↗', 'button');
          resume.href = destination.href;
          display.append(document.createElement('br'), resume);
        }
      }
      if (result.status === 'paid') {
        try {
          if (sessionStorage.getItem('benedetto-order') === orderId) {
            cart.clear(); renderCart(); sessionStorage.removeItem('benedetto-order'); sessionStorage.removeItem('benedetto-checkout');
          }
        } catch {}
      }
      if (['pending', 'awaiting_payment', 'verification_pending'].includes(result.status) && ++attempts < 30) setTimeout(refresh, 10000);
    } catch (error) { display.textContent = error.message || 'Não foi possível verificar o pagamento. Atualize a página para tentar novamente.'; }
  }
  await refresh();
}
loadPaymentConfig();
checkReturnedPayment();

orderForm.elements.address.addEventListener('input', () => {
  deliveryQuote = null;
  document.querySelector('#confirm-delivery-pin').checked = false;
  clearTimeout(quoteExpiryTimer);
  document.querySelector('#delivery-feedback').textContent = 'Calcule o frete para este endereço.';
  renderCart();
});
document.querySelector('#calculate-delivery').addEventListener('click', async () => {
  const address = orderForm.elements.address.value.trim();
  const feedback = document.querySelector('#delivery-feedback');
  if (address.length < 15) { feedback.textContent = 'Informe rua, número, bairro, cidade e CEP.'; return; }
  if (deliveryPoint && !document.querySelector('#confirm-delivery-pin').checked) { feedback.textContent = 'Confirme se o ponto marcado corresponde ao endereço.'; return; }
  if (deliveryBusy || !paymentConfig.delivery_enabled) return;
  const selectedPoint = JSON.stringify(deliveryPoint);
  deliveryBusy = true;
  deliveryQuote = null;
  feedback.textContent = 'Calculando a rota e o frete…';
  renderCart();
  try {
    const response = await fetch('/api/delivery-quote', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ address, destination: deliveryPoint, pin_confirmed: document.querySelector('#confirm-delivery-pin').checked }) });
    const result = await response.json();
    if (address !== orderForm.elements.address.value.trim() || selectedPoint !== JSON.stringify(deliveryPoint) || (deliveryPoint && !document.querySelector('#confirm-delivery-pin').checked) || orderForm.elements.fulfillment.value !== 'Entrega') return;
    if (!response.ok) throw new Error(result.error || 'Não foi possível calcular o frete.');
    deliveryQuote = result;
    feedback.textContent = `${(result.distance_meters / 1000).toLocaleString('pt-BR', { maximumFractionDigits: 2 })} km de percurso · Frete: ${money(result.fee)} · Válido por 15 minutos.`;
    clearTimeout(quoteExpiryTimer);
    quoteExpiryTimer = setTimeout(() => { deliveryQuote = null; feedback.textContent = 'O frete expirou. Calcule novamente.'; renderCart(); }, Math.max(0, result.expires_at * 1000 - Date.now()));
  } catch (error) { feedback.textContent = error.message || 'Não foi possível calcular o frete. Combine a entrega pelo WhatsApp.'; }
  finally { deliveryBusy = false; renderCart(); }
});

function invalidateDeliveryQuote() {
  deliveryQuote = null;
  clearTimeout(quoteExpiryTimer);
  document.querySelector('#delivery-feedback').textContent = 'Calcule novamente o frete para este ponto de entrega.';
  renderCart();
}
function showDeliveryMap() {
  const container = document.querySelector('#delivery-map');
  container.hidden = false;
  if (!deliveryMap) {
    if (!window.L) throw new Error('O mapa não carregou. Tente atualizar a página.');
    const location = config.restaurant_location;
    deliveryMap = L.map(container).setView([location.latitude, location.longitude], 15);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
    }).addTo(deliveryMap);
    L.circleMarker([location.latitude, location.longitude], { radius: 7, color: '#263e31', fillOpacity: 1 }).addTo(deliveryMap).bindTooltip('Ristorante Benedetto');
    deliveryMap.on('click', event => selectDeliveryPoint(event.latlng.lat, event.latlng.lng));
  }
  setTimeout(() => deliveryMap.invalidateSize(), 0);
  return deliveryMap;
}
function selectDeliveryPoint(latitude, longitude) {
  deliveryPoint = { latitude, longitude };
  document.querySelector('#confirm-delivery-pin').checked = false;
  document.querySelector('#pin-confirmation').hidden = false;
  if (deliveryMarker) deliveryMarker.remove();
  deliveryMarker = L.circleMarker([latitude, longitude], { radius: 10, color: '#c45632', fillOpacity: 0.8 }).addTo(deliveryMap).bindTooltip('Local de entrega');
  document.querySelector('#pin-feedback').textContent = 'Ponto selecionado. Confira a posição no mapa e confirme abaixo.';
  invalidateDeliveryQuote();
}
document.querySelector('#choose-delivery-point').addEventListener('click', () => {
  try { showDeliveryMap(); document.querySelector('#pin-feedback').textContent = 'Toque ou clique no mapa exatamente onde deseja receber a entrega.'; }
  catch (error) { document.querySelector('#pin-feedback').textContent = error.message; }
});
document.querySelector('#confirm-delivery-pin').addEventListener('change', invalidateDeliveryQuote);
document.querySelector('#use-my-location').addEventListener('click', () => {
  const feedback = document.querySelector('#pin-feedback');
  if (!navigator.geolocation) { feedback.textContent = 'Seu navegador não oferece localização. Marque o ponto no mapa.'; return; }
  feedback.textContent = 'Aguardando sua permissão de localização…';
  navigator.geolocation.getCurrentPosition(position => {
    if (position.coords.accuracy > 100) { feedback.textContent = 'A localização está imprecisa. Marque o ponto exato no mapa.'; return; }
    try {
      const map = showDeliveryMap();
      selectDeliveryPoint(position.coords.latitude, position.coords.longitude);
      map.setView([position.coords.latitude, position.coords.longitude], 17);
    } catch (error) { feedback.textContent = error.message; }
  }, () => { feedback.textContent = 'Não conseguimos obter sua localização. Marque o ponto no mapa.'; }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
});

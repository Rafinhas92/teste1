const stages = { received: 'Recebido', preparing: 'Em preparo', ready: 'Pronto para retirada', out_for_delivery: 'Saiu para entrega', completed: 'Concluído', cancelled: 'Cancelado' };
const payments = { paid: 'Pagamento aprovado', pending: 'Pagamento pendente', awaiting_payment: 'Aguardando pagamento', verification_pending: 'Pagamento em verificação', failed: 'Pagamento recusado', cancelled: 'Pagamento cancelado', refunded: 'Pagamento estornado' };
const currency = number => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(number);
const date = seconds => new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Campo_Grande', dateStyle: 'short', timeStyle: 'short' }).format(new Date(seconds * 1000));
let csrf = null, authenticated = false, page = 1, pages = 1, requestSequence = 0, busy = false;
function node(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; }
function showLogin(message = '') {
  authenticated = false; csrf = null; requestSequence++;
  document.querySelector('#dashboard').hidden = true;
  document.querySelector('#logout').hidden = true;
  document.querySelector('#login-section').hidden = false;
  document.querySelector('#orders').replaceChildren();
  document.querySelector('#login-message').textContent = message;
}
function showDashboard() {
  authenticated = true;
  document.querySelector('#login-section').hidden = true;
  document.querySelector('#dashboard').hidden = false;
  document.querySelector('#logout').hidden = false;
}
async function api(path, body) {
  const headers = {};
  if (body !== undefined) { headers['Content-Type'] = 'application/json'; if (csrf) headers['X-CSRF-Token'] = csrf; }
  const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers, credentials: 'same-origin', cache: 'no-store', body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/admin/login') showLogin('Sua sessão expirou. Entre novamente.');
    throw new Error(result.error || 'Não foi possível completar a operação.');
  }
  return result;
}
async function loadOrders() {
  if (!authenticated) return;
  const sequence = ++requestSequence;
  document.querySelector('#orders').setAttribute('aria-busy', 'true');
  try {
    const filter = document.querySelector('#stage-filter').value;
    const result = await api(`/api/admin/orders?stage=${encodeURIComponent(filter)}&page=${page}`);
    if (!authenticated || sequence !== requestSequence) return;
    pages = result.pages;
    if (page > pages) { page = pages; return loadOrders(); }
    document.querySelector('#test-banner').hidden = !result.test_mode;
    const stats = document.querySelector('#stats'); stats.replaceChildren();
    for (const [label, count] of [['Recebidos', result.counts.received || 0], ['Em preparo', result.counts.preparing || 0], ['Prontos / em entrega', (result.counts.ready || 0) + (result.counts.out_for_delivery || 0)], ['Concluídos', result.counts.completed || 0]]) {
      const card = node('div', undefined, 'stat'); card.append(node('strong', count), node('span', label)); stats.append(card);
    }
    const container = document.querySelector('#orders');
    const opened = new Set([...container.querySelectorAll('details[open]')].map(el => el.dataset.id));
    container.replaceChildren();
    if (!result.orders.length) container.append(node('p', 'Nenhum pedido neste filtro.', 'empty'));
    result.orders.forEach(order => container.append(orderCard(order, opened.has(order.id), result.test_mode)));
    document.querySelector('#previous').disabled = page <= 1;
    document.querySelector('#next').disabled = page >= pages;
    document.querySelector('#page-label').textContent = `Página ${page} de ${pages} · ${result.total} pedidos`;
    document.querySelector('#last-updated').textContent = 'Atualizado às ' + new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Campo_Grande', timeStyle: 'short' }).format(new Date());
  } catch (error) { if (authenticated) document.querySelector('#dashboard-message').textContent = error.message; }
  finally { if (sequence === requestSequence) document.querySelector('#orders').setAttribute('aria-busy', 'false'); }
}
async function act(order, action, body) {
  if (busy) return;
  busy = true;
  document.querySelector('#dashboard-message').textContent = '';
  try { await api(`/api/admin/orders/${encodeURIComponent(order.id)}/${action}`, body); await loadOrders(); }
  catch (error) { if (authenticated) document.querySelector('#dashboard-message').textContent = error.message; }
  finally { busy = false; }
}
function orderCard(order, open, testMode) {
  const card = node('details', undefined, 'order'); card.dataset.id = order.id; card.open = open;
  const summary = node('summary');
  const title = node('div', undefined, 'order-title'); title.append(node('strong', order.customer.name), node('small', `Pedido ${order.id.slice(0, 8)} · ${date(order.created)} · ${order.fulfillment}`));
  const badges = node('div', undefined, 'badges');
  const paymentClass = order.payment_status === 'paid' ? 'badge-paid' : ['failed', 'cancelled', 'refunded'].includes(order.payment_status) ? 'badge-failed' : 'badge-pending';
  badges.append(node('span', stages[order.stage] || order.stage, 'badge'), node('span', payments[order.payment_status] || 'Pagamento não confirmado', 'badge ' + paymentClass));
  if (order.test_mode === true) badges.append(node('span', 'TESTE', 'badge badge-pending'));
  else if (order.test_mode === null) badges.append(node('span', 'Ambiente a conferir', 'badge badge-pending'));
  summary.append(title, badges, node('span', currency(order.total), 'order-total'));
  const body = node('div', undefined, 'order-body');
  const info = node('div', undefined, 'order-info');
  const customer = node('div'); customer.append(node('h2', 'Cliente'));
  const phone = node('a', order.customer.phone); const digits = order.customer.phone.replace(/\D/g, ''); phone.href = 'tel:+' + ([10, 11].includes(digits.length) ? '55' + digits : digits); customer.append(phone);
  if (order.customer.notes) customer.append(node('p', 'Observações: ' + order.customer.notes, 'notes'));
  const destination = node('div'); destination.append(node('h2', order.fulfillment === 'Entrega' ? 'Entrega' : 'Retirada'));
  destination.append(node('p', order.address || 'Retirada no restaurante'));
  if (order.destination) {
    const point = node('a', 'Ver ponto de entrega ↗');
    point.href = 'https://www.openstreetmap.org/?mlat=' + encodeURIComponent(order.destination.latitude) + '&mlon=' + encodeURIComponent(order.destination.longitude) + '#map=18/' + encodeURIComponent(order.destination.latitude) + '/' + encodeURIComponent(order.destination.longitude);
    point.target = '_blank'; point.rel = 'noopener noreferrer'; destination.append(point);
  }
  info.append(customer, destination); body.append(info);
  const items = node('ul', undefined, 'items');
  order.items.forEach(item => { const line = node('li'); line.append(node('span', `${item.quantity} × ${item.title}`), node('strong', currency(item.quantity * item.unit_price))); items.append(line); });
  body.append(items);
  const actions = node('div', undefined, 'order-actions');
  const next = { received: ['preparing', 'Iniciar preparo'], preparing: order.fulfillment === 'Retirada' ? ['ready', 'Pronto para retirada'] : ['out_for_delivery', 'Saiu para entrega'], ready: ['completed', 'Concluir retirada'], out_for_delivery: ['completed', 'Concluir entrega'] }[order.stage];
  if (next) {
    const advance = node('button', next[1]); advance.type = 'button'; advance.disabled = order.payment_status !== 'paid' || order.test_mode !== testMode;
    advance.addEventListener('click', () => act(order, 'stage', { stage: next[0], expected: order.stage })); actions.append(advance);
  }
  const refresh = node('button', 'Consultar pagamento', 'secondary'); refresh.type = 'button'; refresh.addEventListener('click', () => act(order, 'refresh', {})); actions.append(refresh);
  if (!['cancelled', 'completed'].includes(order.stage)) {
    const cancel = node('button', 'Cancelar atendimento', 'danger'); cancel.type = 'button';
    cancel.addEventListener('click', () => {
      if (window.confirm('Cancelar o atendimento deste pedido? Isso NÃO estorna o pagamento. Se já estiver pago, o estorno deve ser feito no Mercado Pago.')) act(order, 'stage', { stage: 'cancelled', expected: order.stage });
    }); actions.append(cancel);
  }
  body.append(actions);
  if (next && order.test_mode !== testMode) body.append(node('p', 'Consulte o pagamento para conferir o ambiente. Pedidos de teste não avançam em produção.', 'hint'));
  if (next && order.payment_status !== 'paid') body.append(node('p', 'O preparo só pode começar após pagamento aprovado.', 'hint'));
  if (order.stage === 'cancelled' && order.payment_status === 'paid') body.append(node('p', 'Atendimento cancelado. Confira o estorno no Mercado Pago; ele não é feito automaticamente pelo painel.', 'hint'));
  card.append(summary, body); return card;
}
document.querySelector('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.querySelector('#password'), button = document.querySelector('#login-button');
  const password = input.value; input.value = ''; button.disabled = true;
  document.querySelector('#login-message').textContent = '';
  try { const result = await api('/api/admin/login', { password }); csrf = result.csrf; page = 1; showDashboard(); await loadOrders(); }
  catch (error) { document.querySelector('#login-message').textContent = error.message; }
  finally { button.disabled = false; }
});
document.querySelector('#logout').addEventListener('click', async () => {
  try { await api('/api/admin/logout', {}); showLogin(); }
  catch (error) { document.querySelector('#dashboard-message').textContent = error.message; }
});
document.querySelector('#refresh').addEventListener('click', () => { document.querySelector('#dashboard-message').textContent = ''; loadOrders(); });
document.querySelector('#stage-filter').addEventListener('change', () => { page = 1; loadOrders(); });
document.querySelector('#previous').addEventListener('click', () => { if (page > 1) { page--; loadOrders(); } });
document.querySelector('#next').addEventListener('click', () => { if (page < pages) { page++; loadOrders(); } });
setInterval(() => { if (authenticated && !busy && document.visibilityState === 'visible') loadOrders(); }, 30000);
(async () => { try { const state = await api('/api/admin/session'); if (state.authenticated) { csrf = state.csrf; showDashboard(); await loadOrders(); } else if (!state.configured) showLogin('O acesso ainda precisa ser configurado pela administração.'); } catch { showLogin('Não foi possível conectar. Atualize a página para tentar novamente.'); } })();

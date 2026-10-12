document.querySelectorAll('[data-prompt]').forEach((button) => {
  button.addEventListener('click', () => {
    const input = document.getElementById('message');
    input.value = button.dataset.prompt;
    input.focus();
  });
});
document.body.addEventListener('htmx:afterRequest', (event) => {
  const error = document.getElementById('chat-error');
  error.hidden = event.detail.successful;
  if (!event.detail.successful && event.detail.xhr.status !== 503 && event.detail.xhr.getResponseHeader('X-Chat-Error') !== '1') {
    error.textContent = 'Não foi possível enviar. Verifica a mensagem ou inicia uma nova conversa.';
  }
  if (event.detail.successful) {
    document.getElementById('message').value = '';
    document.getElementById('solicitation').value = '';
    document.getElementById('document').value = '';
    document.getElementById('message').focus();
  }
});
document.body.addEventListener('htmx:afterSwap', () => {
  document.getElementById('messages').lastElementChild?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});
document.body.addEventListener('htmx:beforeSwap', (event) => {
  if (event.detail.xhr.status === 503 || event.detail.xhr.getResponseHeader('X-Chat-Error') === '1') {
    event.detail.shouldSwap = true;
    event.detail.target = document.getElementById('chat-error');
    event.detail.swapOverride = 'innerHTML';
    event.detail.target.hidden = false;
  }
});
document.getElementById('message').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || !event.ctrlKey || event.isComposing || event.repeat) return;
  event.preventDefault();
  const form = event.currentTarget.form;
  const sendButton = form.querySelector('button[type="submit"]');
  if (!sendButton.disabled) form.requestSubmit(sendButton);
});


function watchChatSearches() {
  document.querySelectorAll('[data-chat-search]:not([data-watching])').forEach(card => {
    card.dataset.watching='true';
    async function poll() {
      if (!card.isConnected) return;
      try {
        const response=await fetch(card.dataset.chatSearch,{cache:'no-store'});
        if (!response.ok) throw new Error();
        const status=await response.json();
        if (status.html) { card.outerHTML=status.html; return; }
        if (status.state==='idle') {
          card.querySelector('[role="status"]').textContent='O progresso já não está disponível após reinício. Os resultados guardados estão nos detalhes do pedido.';
          return;
        }
        card.querySelector('[role="status"]').textContent=`${status.done || 0}/${status.total || 0} artigos processados. A comparação aparece aqui no fim.`;
      } catch { card.querySelector('[role="status"]').textContent='A aguardar ligação ao progresso…'; }
      setTimeout(poll,2500);
    }
    poll();
  });
}
document.body.addEventListener('htmx:afterSwap',watchChatSearches);
watchChatSearches();

const online = document.getElementById('use-online');
online?.addEventListener('change', () => { document.getElementById('store-picker').hidden = !online.checked; });
document.getElementById('store-filter')?.addEventListener('input', event => {
  document.querySelectorAll('.store-options label').forEach(label => { label.hidden = !label.textContent.toLowerCase().includes(event.target.value.toLowerCase()); });
});
document.getElementById('document').addEventListener('change', event => {
  event.target.setCustomValidity(event.target.files.length > 5 ? 'Anexa no máximo 5 proformas.' : '');
  document.getElementById('selected-files').textContent = [...event.target.files].map(file => file.name).join(' · ');
});
document.querySelectorAll('.store-options input').forEach(input => input.addEventListener('change', () => {
  if (document.querySelectorAll('.store-options input:checked').length > 5) input.checked = false;
}));

let inspectionVersion = 0;
document.getElementById('document').addEventListener('change', async event => {
  const version = ++inspectionVersion;
  const files = [...event.target.files];
  if (!files.length || files.length > 5) { event.target.form.querySelector('button[type="submit"]').disabled = false; return; }
  const status = document.getElementById('selected-files');
  const button = event.target.form.querySelector('button[type="submit"]');
  button.disabled = true;
  status.textContent = 'A identificar fornecedores e artigos… A extracção será reutilizada na comparação.';
  try {
    const data = new FormData();
    files.forEach(file => data.append('proformas', file));
    const response = await fetch('/matrices/inspect', {method:'POST', body:data});
    const html = await response.text();
    if (version === inspectionVersion) status.innerHTML = html;
  } catch {
    if (version === inspectionVersion) status.textContent = 'Não foi possível identificar os fornecedores agora. Podes tentar comparar.';
  } finally { if (version === inspectionVersion) button.disabled = false; }
});

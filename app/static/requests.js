document.querySelectorAll('#proforma-batch, .document-batch-form').forEach((proformaForm) => {
  proformaForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (proformaForm.dataset.submitting) return;
    const status = proformaForm.querySelector('[role="status"]');
    status.textContent = 'A guardar as linhas associadas…';
    proformaForm.dataset.submitting = 'true';
    const buttons = proformaForm.querySelectorAll('button');
    buttons.forEach(button => button.disabled = true);
    try {
      const response = await fetch(proformaForm.action, {method: 'POST', body: new FormData(proformaForm)});
      if (response.ok) {
        window.location.assign(response.url);
        return;
      }
      const error = await response.json();
      status.textContent = typeof error.detail === 'string' ? error.detail : 'Verifica os campos. Os valores continuam aqui para corrigires.';
    } catch {
      status.textContent = 'Não foi possível confirmar a gravação. Verifica o histórico noutro separador antes de reenviar.';
    }
    delete proformaForm.dataset.submitting;
    buttons.forEach(button => button.disabled = false);
  });
});

document.querySelectorAll('[data-select-offers]').forEach((toggle) => {
  toggle.addEventListener('change', () => {
    toggle.form.querySelectorAll('[name="offer_ids"]').forEach(box => box.checked = toggle.checked);
  });
});


const searchPanel = document.querySelector('#background-search');
if (searchPanel) {
  const status = searchPanel.querySelector('[role="status"]');
  const progress = searchPanel.querySelector('progress');
  const stop = searchPanel.querySelector('[data-stop-search]');
  const refresh = searchPanel.querySelector('[data-refresh-results]');
  const results = searchPanel.querySelector('[data-search-results]');
  const forms = document.querySelectorAll('#batch-price-search, .price-search-form');
  const storageKey = 'price-search:' + searchPanel.dataset.url;
  let timer;
  let submitting = false;
  const disable = (busy) => forms.forEach(form => form.querySelectorAll('button').forEach(button => button.disabled = busy));
  const remember = (running) => { try { if (running) sessionStorage.setItem(storageKey, 'running'); else sessionStorage.removeItem(storageKey); } catch {} };
  function render(job) {
    const busy = ['running', 'stopping'].includes(job.state);
    stop.hidden = !busy;
    stop.disabled = job.state === 'stopping';
    disable(busy);
    refresh.disabled = busy || job.state === 'idle';
    progress.hidden = job.state === 'idle';
    progress.max = job.total || 1;
    progress.value = job.done || 0;
    results.replaceChildren();
    (job.results || []).forEach(result => {
      const li = document.createElement('li');
      li.textContent = '#' + result.item_id + ' · ' + result.message;
      results.append(li);
    });
    if (job.state === 'idle') {
      try { if (sessionStorage.getItem(storageKey)) status.textContent = 'A tarefa já não está disponível, possivelmente após reinício. Actualiza a página para ver os resultados guardados antes de pesquisar novamente.'; } catch {}
      remember(false);
    } else {
      status.textContent = job.state === 'stopping' ? 'A parar: a consulta do artigo actual termina antes de libertar a pesquisa.' : job.state === 'cancelled' ? `Pesquisa interrompida. ${job.done}/${job.total} artigos processados; os resultados foram guardados.` : busy ? `${job.done}/${job.total} artigos concluídos · A consultar artigo #${job.current || '…'}` : `${job.done}/${job.total} artigos processados. ${job.state === 'completed' ? 'Pesquisa concluída.' : 'Pesquisa terminada com falhas ou fontes incompletas.'} Podes actualizar os resultados.`;
      remember(busy);
    }
    if (busy) timer = setTimeout(poll, 2000);
  }
  async function poll() {
    clearTimeout(timer);
    try {
      const response = await fetch(searchPanel.dataset.url, {cache:'no-store'});
      if (!response.ok) throw new Error();
      render(await response.json());
    } catch {
      status.textContent = 'Sem ligação ao progresso. A tentar novamente…';
      timer = setTimeout(poll, 5000);
    }
  }
  forms.forEach(form => form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submitting) return;
    submitting = true;
    clearTimeout(timer);
    const data = new FormData(form);
    data.set('refresh', event.submitter?.value || 'false');
    disable(true);
    refresh.disabled = true;
    status.textContent = 'A iniciar pesquisa…';
    try {
      const response = await fetch(searchPanel.dataset.url, {method:'POST', body:data});
      const job = await response.json();
      if (!response.ok) {
        status.textContent = typeof job.detail === 'string' ? job.detail : 'Selecciona os artigos e fornecedores.';
        disable(false);
        timer = setTimeout(poll, 3000);
      } else render(job);
    } catch {
      status.textContent = 'Não foi possível confirmar o início. A verificar o estado…';
      timer = setTimeout(poll, 2000);
    } finally { submitting = false; }
  }));
  searchPanel.querySelector('[data-select-search]').addEventListener('change', event => {
    searchPanel.querySelectorAll('[name="item_ids"]').forEach(box => box.checked = event.target.checked);
  });
  stop.addEventListener('click', async () => {
    stop.disabled = true;
    clearTimeout(timer);
    try {
      const response = await fetch(searchPanel.dataset.url + '/cancel', {method:'POST'});
      if (!response.ok) throw new Error();
      render(await response.json());
    } catch {
      status.textContent = 'Não foi possível confirmar a paragem. A verificar o estado…';
      timer = setTimeout(poll, 2000);
    }
  });
  refresh.addEventListener('click', () => window.location.reload());
  poll();
}

document.body.addEventListener('htmx:beforeSwap', event => {
  if (event.detail.xhr.status === 422) {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
});
document.querySelectorAll('[data-online-status]').forEach(form => {
  async function poll() {
    try {
      const response = await fetch(form.dataset.onlineStatus, {cache:'no-store'});
      if (!response.ok) throw new Error();
      const status = await response.json();
      form.querySelector('[role="status"]').textContent = `${status.done || 0}/${status.total || 0} artigos pesquisados · ${status.state}`;
      if (!['running','stopping'].includes(status.state)) {
        form.querySelector('button').disabled = false;
        return;
      }
    } catch { form.querySelector('[role="status"]').textContent = 'A aguardar ligação…'; }
    setTimeout(poll, 2500);
  }
  poll();
});

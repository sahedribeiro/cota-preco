const csrfToken = () => document.cookie.split('; ').find(value => value.startsWith('kota_csrf='))?.split('=')[1] || '';
document.body.addEventListener('htmx:configRequest', event => { event.detail.headers['X-CSRF-Token'] = csrfToken(); });
const originalFetch = window.fetch.bind(window);
window.fetch = (input, options = {}) => {
  const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
  if (url.origin === window.location.origin && !['GET','HEAD','OPTIONS'].includes((options.method || 'GET').toUpperCase())) {
    const headers = new Headers(options.headers || {});
    headers.set('X-CSRF-Token', csrfToken());
    options = {...options, headers};
  }
  return originalFetch(input, options);
};

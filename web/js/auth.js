// Injects the server-provided API token into every same-origin fetch so the
// web UI does not need to pass it around manually.
(() => {
  const token = window.API_TOKEN || "";
  const originalFetch = window.fetch.bind(window);

  window.fetch = (input, init = {}) => {
    if (token) {
      const headers = new Headers(
        init.headers || (input instanceof Request ? input.headers : undefined),
      );
      headers.set("Authorization", `Bearer ${token}`);
      init = { ...init, headers };
    }
    return originalFetch(input, init);
  };
})();

/* Local-only documentation bootstrap. No persistence, cookies, or external requests. */
(() => {
  "use strict";
  const form = document.getElementById("token-form");
  const input = document.getElementById("token");
  const status = document.getElementById("status");
  const target = document.getElementById("reference");
  let token = "";

  document.getElementById("clear-token").addEventListener("click", () => location.reload());

  // The schema originates in host code, but tool schemas may contain untrusted refs.
  // Never let a renderer resolve remote references or select a remote API server.
  function constrainSchema(value) {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) {
      value.forEach(constrainSchema);
      return;
    }
    if (typeof value.$ref === "string" && !value.$ref.startsWith("#/")) {
      throw new Error("Only local schema references can be displayed.");
    }
    if (Object.hasOwn(value, "servers")) value.servers = [{url: location.origin}];
    delete value["x-logo"];
    Object.values(value).forEach(constrainSchema);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    token = input.value;
    input.value = "";
    target.replaceChildren();
    status.textContent = "Loading protected API schema…";
    try {
      const response = await fetch("/openapi.json", {
        headers: {Authorization: "Bearer " + token},
        credentials: "omit", redirect: "error", cache: "no-store"
      });
      if (!response.ok) throw new Error("Unable to load schema. Check your local bearer token.");
      const schema = await response.json();
      constrainSchema(schema);
      schema.servers = [{url: location.origin}];
      if (document.body.dataset.view === "swagger") {
        SwaggerUIBundle({
          spec: schema, dom_id: "#reference", validatorUrl: null,
          persistAuthorization: false, supportedSubmitMethods: ["get", "post", "put", "patch", "delete"],
          requestInterceptor: (request) => {
            const url = new URL(request.url, location.origin);
            if (url.origin !== location.origin || url.username || url.password) {
              throw new Error("Only same-origin API requests are allowed.");
            }
            request.headers.Authorization = "Bearer " + token;
            request.credentials = "omit";
            request.redirect = "error";
            return request;
          }
        });
      } else {
        Redoc.init(schema, {
          disableGoogleFont: true, hideDownloadButton: true,
          theme: {typography: {fontFamily: "system-ui, sans-serif", headings: {fontFamily: "system-ui, sans-serif"}}}
        }, target);
      }
      status.textContent = "Schema loaded. API requests use the token entered in this tab.";
    } catch (error) {
      token = "";
      status.textContent = "Unable to display API documentation. Check your token and local server.";
    }
  });
})();

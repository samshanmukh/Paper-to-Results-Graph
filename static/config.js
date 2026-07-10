/** API routing shim — local dev uses /api/*, Butterbase deploy uses edge function. */
(function () {
  const FN = window.VERIGRAPH_FN_URL || "";

  const native = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    if (typeof url !== "string" || !url.startsWith("/api/")) {
      return native(url, opts);
    }
    const path = url.slice(5);
    const method = (opts && opts.method) || "GET";
    let route = path;
    if (method === "POST" && path.startsWith("run/")) route = path;
    const target = FN ? `${FN}?route=${encodeURIComponent(route)}` : url;
    const requestOpts = { ...(opts || {}) };
    const headers = new Headers(requestOpts.headers || {});
    const visitorId = localStorage.getItem("vg_visitor_id");
    if (visitorId && path !== "register") {
      headers.set("X-Verigraph-Visitor", visitorId);
    }
    requestOpts.headers = headers;
    return native(target, requestOpts);
  };
})();

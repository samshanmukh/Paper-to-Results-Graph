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
    const visitorId = localStorage.getItem("vg_visitor_id") || "";
    const visitorParam = visitorId && path !== "register"
      ? `&visitor=${encodeURIComponent(visitorId)}`
      : "";
    const target = FN
      ? `${FN}?route=${encodeURIComponent(route)}${visitorParam}`
      : visitorId
        ? `${url}${url.includes("?") ? "&" : "?"}visitor=${encodeURIComponent(visitorId)}`
        : url;
    const requestOpts = { ...(opts || {}) };
    return native(target, requestOpts);
  };
})();

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
    const view = sessionStorage.getItem("vg_bb_view") || "full";
    let removed = "";
    try {
      removed = JSON.parse(sessionStorage.getItem("vg_bb_removed_papers") || "[]")
        .filter(id => /^[a-z0-9-]{1,96}$/.test(String(id)))
        .slice(0, 50)
        .join(",");
    } catch (_) {
      removed = "";
    }
    const viewParams = FN
      ? `&view=${encodeURIComponent(view)}${removed ? `&removed=${encodeURIComponent(removed)}` : ""}`
      : "";
    const target = FN
      ? `${FN}?route=${encodeURIComponent(route)}${visitorParam}${viewParams}`
      : visitorId
        ? `${url}${url.includes("?") ? "&" : "?"}visitor=${encodeURIComponent(visitorId)}`
        : url;
    const requestOpts = { ...(opts || {}) };
    return native(target, requestOpts);
  };
})();

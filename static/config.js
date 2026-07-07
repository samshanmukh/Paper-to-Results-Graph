/** API routing shim — local dev uses /api/*, Butterbase deploy uses edge function. */
(function () {
  const FN = window.VERIGRAPH_FN_URL || "";
  if (!FN) return;

  const native = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    if (typeof url !== "string" || !url.startsWith("/api/")) {
      return native(url, opts);
    }
    const path = url.slice(5);
    const method = (opts && opts.method) || "GET";
    let route = path;
    if (method === "POST" && path.startsWith("run/")) route = path;
    const target = `${FN}?route=${encodeURIComponent(route)}`;
    return native(target, opts);
  };
})();

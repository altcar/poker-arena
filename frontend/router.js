import { useCallback, useEffect, useState } from "react";

export function parseHash(hash = window.location.hash) {
  const raw = String(hash || "").replace(/^#/, "") || "/";
  const at = raw.indexOf("?");
  const pathPart = at === -1 ? raw : raw.slice(0, at);
  const query = Object.fromEntries(new URLSearchParams(at === -1 ? "" : raw.slice(at + 1)));
  const path = pathPart.split("/").filter(Boolean);
  return { path, query, key: path[0] || "home" };
}

const listeners = new Set();

function emit() {
  for (const listener of listeners) listener();
}

export function go(path) {
  const next = path.startsWith("#") ? path : `#${path}`;
  if (window.location.hash === next) emit();
  else window.location.hash = next;
  window.scrollTo({ top: 0, behavior: "instant" });
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash());
  const update = useCallback(() => setRoute(parseHash()), []);

  useEffect(() => {
    listeners.add(update);
    window.addEventListener("hashchange", update);
    window.addEventListener("route:refresh", update);
    return () => {
      listeners.delete(update);
      window.removeEventListener("hashchange", update);
      window.removeEventListener("route:refresh", update);
    };
  }, [update]);

  return route;
}

export function isRoute(route, key) {
  return route.key === key;
}

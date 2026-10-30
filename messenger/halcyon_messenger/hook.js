// Runs before any of Discord's code: catches each module loader (webpack /
// rspack runtime) as it's created, so bridge.js can reach Discord's stores.
(() => {
    if (window.__hmReqs) return;
    const reqs = (window.__hmReqs = []);
    Object.defineProperty(Function.prototype, "m", {
        configurable: true,
        set(v) {
            Object.defineProperty(this, "m", { value: v, configurable: true, enumerable: true, writable: true });
            // A loader has both a module table (m) and, once set up, a cache (c).
            if (typeof this === "function" && v && typeof v === "object" && !reqs.includes(this)) reqs.push(this);
        },
    });
})();

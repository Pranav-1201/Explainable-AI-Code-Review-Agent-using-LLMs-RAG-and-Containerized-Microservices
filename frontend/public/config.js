/*
 * Runtime configuration, loaded by index.html before the bundle (D36).
 *
 * THIS copy is the static default served by `vite dev`, `vite preview` and the
 * Playwright suite. It sets no key, so api.ts falls back to VITE_API_KEY and
 * local development behaves exactly as it did before.
 *
 * The deployed web image replaces this file with config.js.template, which
 * Caddy renders on each request from the container's own API_KEY. That is how
 * a published image -- public, so it can never carry a key -- still sends one.
 *
 * A same-origin file rather than an inline <script> for the same reason as
 * theme-init.js: the CSP is script-src 'self' with no 'unsafe-inline'.
 */
window.__ACRA_CONFIG__ = {};

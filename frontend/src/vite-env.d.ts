/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend origin, e.g. https://api.example.com. Defaults to localhost:8000. */
  readonly VITE_API_BASE?: string;
  /** Must match the backend's API_KEY when the backend has one configured. */
  readonly VITE_API_KEY?: string;
  /**
   * Absolute public origin, e.g. https://example.com. Empty until a domain
   * exists; canonical links, og:url and sitemap.xml all omit themselves rather
   * than emit a placeholder origin.
   */
  readonly VITE_SITE_URL?: string;
  /** Google Search Console verification token, when one has been issued. */
  readonly VITE_SEARCH_CONSOLE_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/**
 * Set by /config.js before the bundle loads (D36). A deployed web container
 * renders it from its own API_KEY; the static default in public/config.js
 * sets no key.
 */
interface AcraRuntimeConfig {
  /** The server's API_KEY. Empty or absent in local development. */
  readonly apiKey?: string;
}

interface Window {
  __ACRA_CONFIG__?: AcraRuntimeConfig;
}

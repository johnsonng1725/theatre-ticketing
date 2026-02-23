// ─────────────────────────────────────────────────────────────────────────────
// Shared API configuration for all frontend pages.
//
// LOCAL DEV (Mac browser)  → http://localhost:8000
// PHONE on same WiFi       → http://<your-mac-ip>:8000  (auto-detected)
// PRODUCTION (Netlify)     → replace PRODUCTION_API below with your Render URL
// ─────────────────────────────────────────────────────────────────────────────

const PRODUCTION_API = 'https://theatre-ticketing-api.onrender.com';

(function () {
  const h = window.location.hostname;

  // Detect local / private network hostnames
  const isLocalhost = h === 'localhost' || h === '127.0.0.1' || h === '';
  const isPrivateIP = (
    /^192\.168\./.test(h) ||        // 192.168.x.x
    /^10\./.test(h) ||              // 10.x.x.x
    /^172\.(1[6-9]|2\d|3[01])\./.test(h)  // 172.16-31.x.x
  );

  if (isLocalhost) {
    // Mac browser — use localhost
    window.API_BASE = 'http://localhost:8000';
  } else if (isPrivateIP) {
    // Phone / tablet on the same WiFi — point to the Mac's IP on port 8000
    window.API_BASE = `http://${h}:8000`;
  } else {
    // Deployed to Netlify (or any public host) — use the Render backend
    window.API_BASE = PRODUCTION_API;
  }
})();

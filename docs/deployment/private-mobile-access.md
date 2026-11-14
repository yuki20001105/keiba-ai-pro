# Private smartphone access

This setup lets a phone reach the Windows PC's existing Keiba AI Pro instance through Tailscale's private HTTPS Serve. It does not publish the app to the public internet.

## One-time setup

1. Install and sign in to Tailscale on both the Windows PC running Keiba AI Pro and the phone, using the same tailnet account.
2. On the PC, start the app with `start-keiba-ai-pro.bat`. If it is already running, do not start a second copy.
3. From the repository directory, run PowerShell:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\enable-private-mobile-access.ps1
   ```

   The helper verifies the local frontend health and Tailscale connection, then configures only HTTPS Serve on port 443 to proxy to `http://127.0.0.1:3000`. It refuses to overwrite an existing Serve configuration.
4. If the helper reports that Serve is not enabled, have the tailnet owner open the approval URL printed by Tailscale, enable Serve, and rerun the helper. This approval cannot be granted by the app.
5. Open the private HTTPS URL printed by the helper in Safari or Chrome on the phone. Sign in with the app's normal account credentials; the local test auto-login is not available from the phone.

## Use and safety

- Tailscale must be connected on the phone. For an outside-home test, turn off phone Wi-Fi and use cellular data while keeping Tailscale connected.
- Keep the PC awake, the app running, and Tailscale connected while using the app.
- The phone must use the printed `https://…ts.net/` URL, not `localhost` or the PC's LAN IP.
- Do not enable Funnel, open router ports, expose FastAPI port 8000, or weaken app authentication. The frontend remains bound to loopback and proxies to the loopback-only backend.
- Check the current route with `tailscale serve status`. The setup helper does not reset or replace any pre-existing Serve configuration.

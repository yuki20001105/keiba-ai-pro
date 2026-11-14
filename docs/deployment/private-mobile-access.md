# Private mobile access with Tailscale

This configures Tailscale Serve to proxy the local Next.js frontend over HTTPS to devices on your tailnet. It does not expose the FastAPI port or make the app public.

## Enable

1. Install Tailscale on the Windows PC and mobile device, and sign both in to the same tailnet.
2. Start Keiba AI Pro on the PC and confirm it is available at `http://127.0.0.1:3000`.
3. In PowerShell from the repository root, run:

   ```powershell
   .\scripts\enable-private-mobile-access.ps1
   ```

   The helper runs:

   ```powershell
   & 'C:\Program Files\Tailscale\tailscale.exe' serve --bg --yes --https=443 http://127.0.0.1:3000
   ```

4. Run `tailscale serve status` and open the HTTPS URL it reports from the mobile device.

Tailscale Serve is tailnet-only. Do not enable Tailscale Funnel if the app should remain private.

## Disable

`tailscale serve reset` removes the Serve configuration from this PC. It also clears any other Tailscale Serve configuration on the same PC.

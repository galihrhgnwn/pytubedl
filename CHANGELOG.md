# Changelog

All notable changes to this project will be documented in this file.

## [3.1.0] - 2026-06-24

### Added
- **VPN Gate Integration**: Integrated VPN Gate for automated region restriction bypass.
- **Auto-Switching VPN**: Implemented logic to automatically switch VPN servers if the connection speed degrades or the server becomes unresponsive.
- **UI Toggle**: Added a "Bypass Region (VPN)" toggle in the API Playground and updated documentation for all relevant endpoints.
- **Bypass Logic**: Integrated VPN switching into the backend `make_yt` helper, allowing transparent region bypass when requested via the `bypass_region` query parameter.
- **OpenVPN Dependency**: Added OpenVPN to `setup.sh` as a required system dependency for the VPN feature.

### Improved
- **Error Handling**: Enhanced backend logic to handle `VideoRegionBlocked` errors by attempting a VPN reconnection when the bypass feature is enabled.
- **Documentation**: Updated `index.html` to reflect new query parameters and system requirements.

### Technical Details
- Added `vpn_gate.py` for managing VPN connections and server list fetching.
- Modified `app.py` to support `bypass_region` parameter across all video-related endpoints.
- Updated `index.html` with new playground fields and status indicators.
- Updated `setup.sh` to automate the installation of `openvpn`.

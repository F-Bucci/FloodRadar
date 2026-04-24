# 🌊 FloodWatch Austria

Real-time flood detection for Austria using **Sentinel-1 SAR** imagery via the **Copernicus Data Space Ecosystem** (Sentinel Hub Process API).

---

## How it works

1. The Flask backend requests Sentinel-1 GRD (Ground Range Detected) SAR imagery from the Sentinel Hub Process API for 6 Austrian regions.
2. A custom **evalscript** compares two recent SAR passes (before/after). Areas where backscatter drops significantly indicate open water — i.e., flooding.
3. The mobile-friendly frontend renders the SAR tile with a flood/at-risk/normal colour overlay and shows an estimated flood percentage.

**Why SAR?** Sentinel-1 uses radar, so it sees through clouds and works day and night — perfect for flood emergencies when optical satellites are often obscured.

---

## Setup

### 1. Copernicus credentials

Register at [dataspace.copernicus.eu](https://dataspace.copernicus.eu), then go to **Dashboard → User Settings → OAuth Clients** and create a new client. You'll get a `client_id` and `client_secret`.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set credentials & run

```bash
export SH_CLIENT_ID="your-client-id"
export SH_CLIENT_SECRET="your-client-secret"
python app.py
```

Open [http://localhost:5000](http://localhost:5000) on your phone or browser.

---

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Mobile web UI |
| `GET /api/regions` | List of monitored Austrian regions |
| `GET /api/flood/<region_key>` | Fetch live SAR tile + flood stats |

Region keys: `vienna`, `salzburg`, `linz`, `graz`, `innsbruck`, `klagenfurt`

---

## Evalscript logic

The evalscript uses **Sentinel-1 VV polarisation** with `mosaicking: "ORBIT"` to compare two passes:

- **Flood** (blue): backscatter ratio < 0.5 and absolute VV < 0.05 — characteristic of calm open water surface
- **At risk** (teal): ratio < 0.7 — possibly waterlogged or near-flood
- **Normal** (greyscale): everything else

---

## Extending

- Add **Sentinel-2** optical imagery for cloud-free days (NDWI water index)
- Integrate **Copernicus Emergency Management Service** (CEMS) alerts
- Add push notifications via a service worker when flood_pct exceeds a threshold
- Store historical flood_pct per region in SQLite for trend charts

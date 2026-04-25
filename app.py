"""
FloodWatch Austria — Backend
Uses Copernicus Data Space Ecosystem (Sentinel Hub) to fetch Sentinel-1 SAR
imagery and detect flood conditions over Austria.
"""

import os
import json
import base64
import requests
from datetime import datetime, timedelta
import numpy as np
from skimage import measure
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union
import openrouteservice

try:
    from flask import Flask, render_template, jsonify, request
except ImportError as exc:
    raise ImportError(
        "Flask is required to run this application. Install it with `pip install flask`."
    ) from exc

app = Flask(__name__)

# ── Copernicus / Sentinel Hub credentials ────────────────────────────────────
# Set these as environment variables before running:
#   export SH_CLIENT_ID="your-client-id"
#   export SH_CLIENT_SECRET="your-client-secret"
SH_CLIENT_ID     = "sh-9cd10f25-2f96-4942-a9e8-f2ec71766649"
SH_CLIENT_SECRET = "S44S5oIcKh89LHaFCsS6N0WPokBfbTud"
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
ors_client = openrouteservice.Client(key=ORS_API_KEY)
TOKEN_URL  = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

# ── Austrian regions with bounding boxes [min_lon, min_lat, max_lon, max_lat] ─
REGIONS = {
    "vienna":      {"name": "Vienna",         "bbox": [16.18, 48.12, 16.58, 48.32], "risk": "moderate"},
    "salzburg":    {"name": "Salzburg",       "bbox": [12.95, 47.70, 13.15, 47.90], "risk": "high"},
    "linz":        {"name": "Linz / Danube",  "bbox": [14.18, 48.22, 14.38, 48.37], "risk": "high"},
    "graz":        {"name": "Graz / Mur",     "bbox": [15.35, 47.00, 15.55, 47.10], "risk": "moderate"},
    "innsbruck":   {"name": "Innsbruck",      "bbox": [11.30, 47.22, 11.50, 47.32], "risk": "low"},
    "klagenfurt":  {"name": "Klagenfurt",     "bbox": [14.25, 46.58, 14.45, 46.68], "risk": "low"},
}

# ── Sentinel-1 SAR flood-detection evalscript (VV polarisation) ───────────────
# Compares two orbits: "before" (baseline) vs "now" (current).
# Pixels where backscatter drops significantly → open water / flood.
FLOOD_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["VV", "dataMask"] }],
    output: [
      { id: "flood",   bands: 3, sampleType: "UINT8" },
      { id: "default", bands: 3, sampleType: "UINT8" }
    ],
    mosaicking: "ORBIT"
  };
}
def mask_to_geojson(mask, bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    H, W = mask.shape

    def pixel_to_geo(row, col):
        lon = min_lon + (col / W) * (max_lon - min_lon)
        lat = max_lat - (row / H) * (max_lat - min_lat)
        return [lon, lat]

    contours = measure.find_contours(mask.astype(np.uint8), 0.5)

    polygons = []
    for contour in contours:
        coords = [pixel_to_geo(r, c) for r, c in contour]

        if len(coords) > 3:
            poly = Polygon(coords)
            if poly.is_valid and poly.area > 1e-8:
                polygons.append(poly)

    if not polygons:
        return None

    merged = unary_union(polygons).simplify(0.0005)
    geojson = mapping(merged)

    if geojson["type"] == "Polygon":
        geojson = {
            "type": "MultiPolygon",
            "coordinates": [geojson["coordinates"]]
        }

    return geojson
    
function preProcessScenes(collections) {
  // Keep only the two most recent orbits for before/after comparison
  collections.scenes.orbits = collections.scenes.orbits.slice(-2);
  return collections;
}

function evaluatePixel(samples) {
  if (samples.length < 2) {
    return {
      flood:   [128, 128, 128],
      default: [128, 128, 128]
    };
  }

  var before = samples[0].VV;
  var after  = samples[samples.length - 1].VV;

  // Significant backscatter decrease → water surface
  var ratio = after / (before + 1e-10);

  var r, g, b;
  if (ratio < 0.5 && after < 0.05) {
    // Flooded: bright blue
    r = 0; g = 100; b = 255;
  } else if (ratio < 0.7 && after < 0.1) {
    // Possibly wet / at-risk: teal
    r = 0; g = 200; b = 200;
  } else {
    // Normal terrain: grayscale SAR
    var v = Math.min(255, Math.round(after * 1200));
    r = v; g = v; b = v;
  }

  return {
    flood:   [r, g, b],
    default: [r, g, b]
  };
}
"""


def get_token():
    """Fetch an OAuth2 bearer token from Copernicus identity service."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "client_credentials",
            "client_id":     SH_CLIENT_ID,
            "client_secret": SH_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_flood_tile(region_key: str) -> dict:
    """
    Call the Sentinel Hub Process API for a given Austrian region and return:
      - base64-encoded PNG image
      - estimated flood percentage
      - timestamp used
    """
    region = REGIONS[region_key]
    bbox   = region["bbox"]

    # Use a 30-day window so we capture at least 2 Sentinel-1 passes
    end_dt   = datetime.utcnow()
    start_dt = end_dt - timedelta(days=30)

    token = get_token()

    payload = {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
            },
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    "timeRange": {
                        "from": start_dt.strftime("%Y-%m-%dT00:00:00Z"),
                        "to":   end_dt.strftime("%Y-%m-%dT23:59:59Z"),
                    },
                    "acquisitionMode": "IW",
                    "polarization":    "DV",
                    "resolution":      "HIGH",
                },
                "processing": {
                    "backCoeff":   "GAMMA0_TERRAIN",
                    "orthorectify": True,
                    "demInstance": "COPERNICUS",
                }
            }]
        },
        "output": {
            "width":  512,
            "height": 512,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}]
        },
        "evalscript": FLOOD_EVALSCRIPT
    }

    resp = requests.post(
        PROCESS_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        timeout=60,
    )
    resp.raise_for_status()

    img_b64 = base64.b64encode(resp.content).decode("utf-8")

    # Rough flood-pixel estimation from the PNG bytes (blue channel dominance heuristic)
    blue_pixels = resp.content.count(b'\x00\x64\xff')  # exact flood colour match
    estimated_pct = min(100, round(blue_pixels / 50, 1))  # coarse estimate

    return {
        "image_b64":    img_b64,
        "flood_pct":    estimated_pct,
        "region":       region["name"],
        "risk":         region["risk"],
        "fetched_at":   end_dt.strftime("%Y-%m-%d %H:%M UTC"),
        "time_from":    start_dt.strftime("%Y-%m-%d"),
        "time_to":      end_dt.strftime("%Y-%m-%d"),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", regions=REGIONS)


@app.route("/api/regions")
def api_regions():
    return jsonify(REGIONS)


@app.route("/api/flood/<region_key>")
def api_flood(region_key):
    if region_key not in REGIONS:
        return jsonify({"error": "Unknown region"}), 404

    if not SH_CLIENT_ID or not SH_CLIENT_SECRET:
        return jsonify({
            "error": "credentials_missing",
            "message": "Set SH_CLIENT_ID and SH_CLIENT_SECRET environment variables."
        }), 503

    try:
        data = fetch_flood_tile(region_key)
        return jsonify(data)
    except requests.HTTPError as e:
        return jsonify({"error": "sentinel_hub_error", "message": str(e)}), 502
    except Exception as e:
        return jsonify({"error": "internal_error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)

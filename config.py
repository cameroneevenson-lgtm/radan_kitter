from __future__ import annotations

import os
from typing import List, Tuple

# Core kit taxonomy
CANON_KITS = [
    "Bottoms",
    "Sides",
    "Tops",
    "Backs",
    "Tall Sides",
    "Brackets",
    "Wheel Wells",
    "Walls",
    "Flat Parts",
]
KIT_ABBR = {
    "Bottoms": "BOT",
    "Sides": "SID",
    "Tops": "TOP",
    "Backs": "BAC",
    "Tall Sides": "TAL",
    "Brackets": "BRK",
    "Wheel Wells": "WHL",
    "Walls": "WAL",
    "Flat Parts": "FLT",
    "Balance": "BAL",
}
KIT_TO_PRIORITY = {k: str(i + 1) for i, k in enumerate(CANON_KITS)}  # 1..9
BALANCE_KIT = "Balance"

# Directory naming
BAK_DIRNAME = "_bak"
OUT_DIRNAME = "_out"
KITS_DIRNAME = "_kits"
ML_RUNS_DIRNAME = "_ml_runs"
ML_MODELS_DIRNAME = "_ml_models"
RUNTIME_DIRNAME = "_runtime"

# Paths
TOOLS_DIR = r"C:\Tools"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANY_LOGO_PATH = os.path.join(APP_DIR, "bs-logo.png")

# ML artifacts are project-local.
GLOBAL_DATASET_PATH = os.path.join(APP_DIR, "ml_dataset.csv")
GLOBAL_RUNS_DIR = os.path.join(APP_DIR, ML_RUNS_DIRNAME)
GLOBAL_MODELS_DIR = os.path.join(APP_DIR, ML_MODELS_DIRNAME)
GLOBAL_RUNTIME_DIR = os.path.join(APP_DIR, RUNTIME_DIRNAME)
GLOBAL_RUNTIME_LOG_PATH = os.path.join(GLOBAL_RUNTIME_DIR, "runtime_trace.jsonl")
HOT_RELOAD_REQUEST_PATH = os.path.join(GLOBAL_RUNTIME_DIR, "hot_reload_request.json")
HOT_RELOAD_RESPONSE_PATH = os.path.join(GLOBAL_RUNTIME_DIR, "hot_reload_response.json")

# Temporary packet debug controls.
# Enabled now to keep packet output local/visible during layer-0 debugging.
PACKET_TEMP_LOCAL_OUTPUT_ENABLED = True
PACKET_TEMP_LOCAL_OUTPUT_DIR = os.path.join(APP_DIR, "_packet_debug_out")
PACKET_TEMP_FIRST_PAGE_ONLY = False
# Temporary packet page cap for debug iteration. Set <=0 to disable cap.
PACKET_TEMP_MAX_PAGES = 0

# Packet layer-0 preserve window (bottom-right), as page fractions.
# Keeps only this zone from layer 0 (e.g., company logo in title block).
PACKET_LAYER0_KEEP_BR_ENABLED = True
PACKET_LAYER0_KEEP_BR_WIDTH_FRAC = 0.23
PACKET_LAYER0_KEEP_BR_HEIGHT_FRAC = 0.18

DONOR_TEMPLATE_PATH = os.path.join(APP_DIR, "KitDonor-100Instances.sym")
if not os.path.exists(DONOR_TEMPLATE_PATH):
    legacy_tools = os.path.join(TOOLS_DIR, "KitDonor-100Instances.sym")
    DONOR_TEMPLATE_PATH = legacy_tools

# Asset mapping
W_RELEASE_ROOT = r"W:\LASER\For Battleshield Fabrication"
ENG_RELEASE_MAP: List[Tuple[str, str]] = [
    (r"L:\BATTLESHIELD\F-LARGE FLEET", W_RELEASE_ROOT),
    (r"L:\BATTLESHIELD", W_RELEASE_ROOT),
]

# ML feature schema
ML_SIGNAL_COLS = [
    "dxf_perimeter_area_ratio",
    "dxf_concavity_ratio",
    "dxf_internal_void_area_ratio",
    "dxf_entity_count",
    "dxf_arc_length_ratio",
    "dxf_exterior_notch_count",
    "dxf_has_interior_polylines",
    "dxf_color_count",
    "dxf_has_nondefault_color",
    "pdf_dim_density",
    "pdf_text_to_geom_ratio",
    "pdf_bendline_score",
    "pdf_ink_gradient_mean",
    "pdf_ink_gradient_std",
]

# Backward-compat aliases
HUD_SIGNALS_6 = ML_SIGNAL_COLS[:]
HUD_SIGNALS_8 = ML_SIGNAL_COLS[:]
RF_FEATURES = ML_SIGNAL_COLS[:]

RF_MODEL_PATH = os.path.join(GLOBAL_MODELS_DIR, "rf_kit_predictor.joblib")
RF_META_PATH = os.path.join(GLOBAL_MODELS_DIR, "rf_kit_predictor.meta.json")

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

# Paths
TOOLS_DIR = r"C:\Tools"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANY_LOGO_PATH = os.path.join(APP_DIR, "bs-logo.png")

GLOBAL_DATASET_PATH = os.path.join(TOOLS_DIR, "ml_dataset.csv")
GLOBAL_RUNS_DIR = os.path.join(TOOLS_DIR, ML_RUNS_DIRNAME)
GLOBAL_MODELS_DIR = os.path.join(TOOLS_DIR, ML_MODELS_DIRNAME)

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
    "dxf_convexity_ratio",
    "dxf_internal_void_area_ratio",
    "dxf_entity_count",
    "dxf_exterior_notch_count",
    "dxf_has_interior_polylines",
    "dxf_color_count",
    "dxf_has_nondefault_color",
    "pdf_dim_density",
    "pdf_text_to_geom_ratio",
    "pdf_bendline_score",
    "pdf_ink_gradient_mean",
    "pdf_ink_gradient_std",
    "pdf_ink_gradient_max",
]

# Backward-compat aliases
HUD_SIGNALS_6 = ML_SIGNAL_COLS[:]
HUD_SIGNALS_8 = ML_SIGNAL_COLS[:]
RF_FEATURES = ML_SIGNAL_COLS[:]

RF_MODEL_PATH = os.path.join(GLOBAL_MODELS_DIR, "rf_kit_predictor.joblib")
RF_META_PATH = os.path.join(GLOBAL_MODELS_DIR, "rf_kit_predictor.meta.json")

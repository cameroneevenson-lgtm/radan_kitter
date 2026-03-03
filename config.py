# config.py
import os

TOOLS_DIR = r"C:\Tools"
APP_DIR = os.path.dirname(os.path.abspath(__file__))

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
KIT_TO_PRIORITY = {k: str(i + 1) for i, k in enumerate(CANON_KITS)}

W_RELEASE_ROOT = r"W:\LASER\For Battleshield Fabrication"

GLOBAL_DATASET_PATH = os.path.join(TOOLS_DIR, "ml_dataset.csv")
GLOBAL_RUNS_DIR = os.path.join(TOOLS_DIR, "_ml_runs")
GLOBAL_MODELS_DIR = os.path.join(TOOLS_DIR, "_ml_models")

DONOR_TEMPLATE_PATH = os.path.join(APP_DIR, "KitDonor-100Instances.sym")
if not os.path.exists(DONOR_TEMPLATE_PATH):
    legacy_tools = os.path.join(TOOLS_DIR, "KitDonor-100Instances.sym")
    DONOR_TEMPLATE_PATH = legacy_tools

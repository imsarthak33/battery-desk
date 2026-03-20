"""
config.py  —  All business constants and battery chemistry profiles.
Loaded once at startup; values can be overridden via .env.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ───────────────────────────────────────────────────────────────────
NVIDIA_API_KEY      = os.environ["NVIDIA_API_KEY"]            # required
SERPER_API_KEY      = os.environ["SERPER_API_KEY"]            # required
METAL_PRICE_API_KEY = os.getenv("METAL_PRICE_API_KEY", "")   # preferred source
METALS_API_KEY      = os.getenv("METALS_API_KEY", "")        # fallback source
EXCHANGE_RATE_KEY   = os.getenv("EXCHANGE_RATE_API_KEY", "")  # live forex

# ── Business Constants ─────────────────────────────────────────────────────────
RECYCLER_PAYABLE_RATE = float(os.getenv("RECYCLER_PAYABLE_RATE", "0.75"))
PROFIT_MARGIN         = float(os.getenv("PROFIT_MARGIN",         "0.10"))
ERROR_BUFFER          = float(os.getenv("ERROR_BUFFER",          "0.01"))
HUB_COST_INR          = float(os.getenv("HUB_COST_INR",          "12.70"))
AGGREGATOR_ASK_INR    = float(os.getenv("AGGREGATOR_ASK_INR",    "300.0"))

# Fallback forex rate if live API fails (updated manually monthly)
FALLBACK_USD_INR      = float(os.getenv("FALLBACK_USD_INR",      "84.0"))

# ── Battery Chemistry Profiles ─────────────────────────────────────────────────
# Each value = fraction of 1 kg of scrap material.
# Sources: industry average black mass compositions.
BATTERY_CHEMISTRIES = {
    "NMC": {
        "full_name": "Lithium Nickel Manganese Cobalt Oxide (NMC)",
        "metals": {
            "nickel":   0.150,   # 15%
            "cobalt":   0.050,   # 5%
            "lithium":  0.015,   # 1.5%
            "manganese": 0.040,  # 4%
        },
        "notes": "Most common EV battery. LME Ni & Co dominate value.",
    },
    "NCA": {
        "full_name": "Lithium Nickel Cobalt Aluminium Oxide (NCA)",
        "metals": {
            "nickel":  0.110,
            "cobalt":  0.030,
            "lithium": 0.008,
        },
        "notes": "Tesla chemistry. Lower Co, higher Ni ratio.",
    },
    "LCO": {
        "full_name": "Lithium Cobalt Oxide (LCO)",
        "metals": {
            "cobalt":  0.200,   # 20% — this is where the value is
            "lithium": 0.070,   # 7%
        },
        "notes": "Consumer electronics (phones/laptops). High Co makes it very valuable.",
    },
    "LFP": {
        "full_name": "Lithium Iron Phosphate (LFP)",
        "metals": {
            "lithium": 0.040,   # 4%
            # Iron and phosphate have negligible scrap value
        },
        "notes": "BYD/CATL chemistry. Low scrap value; mostly recovered for lithium.",
    },
    "LEAD_ACID": {
        "full_name": "Lead-Acid Battery",
        "metals": {
            "lead": 0.600,      # 60% lead by weight
        },
        "notes": "Oldest chemistry. Highest volume in Indian informal market. LME Lead price drives value.",
    },
}

# Metals whose LME prices we need to fetch (symbol: LME ticker)
# metalpriceapi.com uses these symbols
LME_SYMBOLS = {
    "nickel":    "XNI",   # USD per metric tonne
    "cobalt":    "XCO",   # USD per metric tonne
    "lithium":   "LITHIUM",  # handled separately — not on LME proper
    "manganese": "XMN",
    "lead":      "XPB",
}

# Conversion: all LME prices are per metric tonne; we need per kg
TONNE_TO_KG = 1000.0

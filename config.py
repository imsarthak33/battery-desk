"""
config.py - Business constants and battery chemistry profiles.
Supports both NVIDIA NIM and DeepSeek as LLM backends.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
# LLM: supports NVIDIA NIM (preferred) or DeepSeek (fallback)
NVIDIA_API_KEY   = os.getenv('NVIDIA_API_KEY', '')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')

if not NVIDIA_API_KEY and not DEEPSEEK_API_KEY:
    raise EnvironmentError('No LLM API key found. Set NVIDIA_API_KEY or DEEPSEEK_API_KEY in your .env')

SERPER_API_KEY      = os.getenv('SERPER_API_KEY', '')
METAL_PRICE_API_KEY = os.getenv('METAL_PRICE_API_KEY', '')
METALS_API_KEY      = os.getenv('METALS_API_KEY', '')
EXCHANGE_RATE_KEY   = os.getenv('EXCHANGE_RATE_API_KEY', '')

# Optional news API keys
GNEWS_API_KEY       = os.getenv('GNEWS_API_KEY', '')
MARKETAUX_API_KEY   = os.getenv('MARKETAUX_API_KEY', '')
NEWSDATA_API_KEY    = os.getenv('NEWSDATA_API_KEY', '')
CURRENTS_API_KEY    = os.getenv('CURRENTS_API_KEY', '')
THENEWS_API_KEY     = os.getenv('THENEWS_API_KEY', '')

# Business Constants
RECYCLER_PAYABLE_RATE = float(os.getenv('RECYCLER_PAYABLE_RATE', '0.75'))
PROFIT_MARGIN         = float(os.getenv('PROFIT_MARGIN', '0.10'))
ERROR_BUFFER          = float(os.getenv('ERROR_BUFFER', '0.01'))
HUB_COST_INR          = float(os.getenv('HUB_COST_INR', '12.70'))
AGGREGATOR_ASK_INR    = float(os.getenv('AGGREGATOR_ASK_INR', '300.0'))
FALLBACK_USD_INR      = float(os.getenv('FALLBACK_USD_INR', '84.0'))
TONNE_TO_KG = 1000.0

BATTERY_CHEMISTRIES = {
    'NMC': {
        'full_name': 'Lithium Nickel Manganese Cobalt Oxide (NMC)',
        'metals': {'nickel': 0.150, 'cobalt': 0.050, 'lithium': 0.015, 'manganese': 0.040},
        'notes': 'Most common EV battery. LME Ni & Co dominate value.',
    },
    'NCA': {
        'full_name': 'Lithium Nickel Cobalt Aluminium Oxide (NCA)',
        'metals': {'nickel': 0.110, 'cobalt': 0.030, 'lithium': 0.008},
        'notes': 'Tesla chemistry. Lower Co, higher Ni ratio.',
    },
    'LCO': {
        'full_name': 'Lithium Cobalt Oxide (LCO)',
        'metals': {'cobalt': 0.200, 'lithium': 0.070},
        'notes': 'Consumer electronics (phones/laptops). High Co makes it very valuable.',
    },
    'LFP': {
        'full_name': 'Lithium Iron Phosphate (LFP)',
        'metals': {'lithium': 0.040},
        'notes': 'BYD/CATL chemistry. Low scrap value; mostly recovered for lithium.',
    },
    'LEAD_ACID': {
        'full_name': 'Lead-Acid Battery',
        'metals': {'lead': 0.600},
        'notes': 'Oldest chemistry. Highest volume in Indian informal market.',
    },
}

LME_SYMBOLS = {'nickel': 'XNI', 'cobalt': 'XCO', 'lithium': 'LITHIUM', 'manganese': 'XMN', 'lead': 'XPB'}

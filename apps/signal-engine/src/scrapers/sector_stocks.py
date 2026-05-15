"""Static mapping of sector names → NSE symbols (yfinance .NS format)."""

SECTOR_STOCKS: dict[str, list[str]] = {
    "IT": [
        "TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS",
        "MPHASIS.NS", "COFORGE.NS", "LTIM.NS", "PERSISTENT.NS", "OFSS.NS",
    ],
    "Banking": [
        "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS",
        "INDUSINDBK.NS", "BANDHANBNK.NS", "PNB.NS", "CANBK.NS", "BANKBARODA.NS",
    ],
    "Pharma": [
        "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "BIOCON.NS",
        "TORNTPHARM.NS", "AUROPHARMA.NS", "ALKEM.NS", "LUPIN.NS", "IPCALAB.NS",
    ],
    "Auto": [
        "MARUTI.NS", "TATAMOTORS.NS", "M&M.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS",
        "HEROMOTOCO.NS", "ASHOKLEY.NS", "TVSMOTOR.NS", "BOSCHLTD.NS", "BALKRISIND.NS",
    ],
    "FMCG": [
        "HINDUNILVR.NS", "ITC.NS", "BRITANNIA.NS", "NESTLEIND.NS", "DABUR.NS",
        "MARICO.NS", "COLPAL.NS", "EMAMILTD.NS", "GODREJCP.NS", "TATACONSUM.NS",
    ],
    "Energy": [
        "RELIANCE.NS", "ONGC.NS", "BPCL.NS", "IOC.NS", "GAIL.NS",
        "PETRONET.NS", "HINDPETRO.NS", "MGL.NS", "IGL.NS", "ADANIGREEN.NS",
    ],
    "Metals": [
        "TATASTEEL.NS", "HINDALCO.NS", "JSWSTEEL.NS", "SAIL.NS", "VEDL.NS",
        "NMDC.NS", "COALINDIA.NS", "HINDZINC.NS", "NATIONALUM.NS", "MOIL.NS",
    ],
    "Realty": [
        "DLF.NS", "GODREJPROP.NS", "PRESTIGE.NS", "OBEROIRLTY.NS", "PHOENIXLTD.NS",
        "SOBHA.NS", "MAHLIFE.NS", "SUNTECK.NS",
    ],
    "Infrastructure": [
        "LT.NS", "ADANIPORTS.NS", "ADANIENT.NS", "NTPC.NS", "POWERGRID.NS",
        "BHEL.NS", "SIEMENS.NS", "ABB.NS", "CUMMINSIND.NS", "THERMAX.NS",
    ],
    "Finance": [
        "BAJFINANCE.NS", "HDFCAMC.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS", "MUTHOOTFIN.NS",
        "MANAPPURAM.NS", "LTFH.NS", "CANFINHOME.NS", "ICICIGI.NS", "ICICIPRULI.NS",
    ],
    "Chemicals": [
        "SRF.NS", "PIDILITIND.NS", "UPL.NS", "DEEPAKNITR.NS", "COROMANDEL.NS",
        "ATUL.NS", "VINATIORGA.NS", "NAVINFLUOR.NS", "FINEORG.NS", "TATACHEM.NS",
    ],
    "Consumer": [
        "TITAN.NS", "PAGEIND.NS", "ASIANPAINT.NS", "BERGERPAINTS.NS", "HAVELLS.NS",
        "VOLTAS.NS", "CROMPTON.NS", "DIXON.NS", "VBL.NS", "UNITDSPR.NS",
    ],
    "Telecom": [
        "BHARTIARTL.NS", "IDEA.NS", "INDUSTOWER.NS", "TATACOMM.NS", "HFCL.NS",
    ],
    "Healthcare": [
        "APOLLOHOSP.NS", "FORTIS.NS", "MAXHEALTH.NS", "NARAYANHRU.NS",
        "LALPATHLAB.NS", "METROPOLIS.NS", "THYROCARE.NS",
    ],
}

# Normalise free-text sector names from LLM to canonical keys above
SECTOR_ALIASES: dict[str, str] = {
    "information technology": "IT",
    "technology": "IT",
    "software": "IT",
    "it services": "IT",
    "bank": "Banking",
    "banks": "Banking",
    "banking & finance": "Banking",
    "banking": "Banking",
    "pharmaceutical": "Pharma",
    "pharmaceuticals": "Pharma",
    "healthcare": "Healthcare",
    "health care": "Healthcare",
    "hospital": "Healthcare",
    "hospitals": "Healthcare",
    "automobile": "Auto",
    "automobiles": "Auto",
    "automotive": "Auto",
    "consumer goods": "FMCG",
    "fmcg": "FMCG",
    "oil & gas": "Energy",
    "oil and gas": "Energy",
    "power": "Energy",
    "energy": "Energy",
    "metal": "Metals",
    "metals": "Metals",
    "mining": "Metals",
    "real estate": "Realty",
    "construction": "Infrastructure",
    "capital goods": "Infrastructure",
    "infrastructure": "Infrastructure",
    "financial services": "Finance",
    "nbfc": "Finance",
    "insurance": "Finance",
    "chemical": "Chemicals",
    "specialty chemicals": "Chemicals",
    "retail": "Consumer",
    "consumer durables": "Consumer",
    "telecom": "Telecom",
    "telecommunications": "Telecom",
}


def normalise_sector(name: str) -> str | None:
    """Map free-text sector name to a canonical SECTOR_STOCKS key, or None."""
    key = name.strip().lower()
    if key in SECTOR_STOCKS:
        return key
    canonical = SECTOR_ALIASES.get(key)
    if canonical:
        return canonical
    # prefix match
    for alias, canon in SECTOR_ALIASES.items():
        if key.startswith(alias):
            return canon
    return None

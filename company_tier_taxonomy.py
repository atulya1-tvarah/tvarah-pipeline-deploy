from __future__ import annotations

import os
from difflib import SequenceMatcher
from typing import Any

# ---------------------------------------------------------------------------
# Tier map: Tier 1 = FAANG/top product, 2 = unicorns/top-tier funded,
# 3 = mid-size funded / strong regional, 4 = service/consulting/SME,
# 5 = unknown/very small
# ---------------------------------------------------------------------------
TIER_MAP: dict[str, int] = {
    # Tier 1 — FAANG / global hyper-scale product / top AI labs
    "google": 1, "alphabet": 1, "google deepmind": 1,
    "amazon": 1, "aws": 1, "amazon web services": 1,
    "meta": 1, "facebook": 1, "instagram": 1, "whatsapp": 1,
    "apple": 1, "microsoft": 1, "msft": 1,
    "netflix": 1, "openai": 1, "deepmind": 1, "google brain": 1,
    "nvidia": 1, "salesforce": 1, "adobe": 1, "oracle": 1, "sap": 1,
    "twitter": 1, "x corp": 1, "x.com": 1, "linkedin": 1,
    "uber": 1, "airbnb": 1, "stripe": 1, "palantir": 1,
    "databricks": 1, "snowflake": 1, "atlassian": 1,
    "workday": 1, "servicenow": 1, "zoom": 1,
    "anthropic": 1, "xai": 1, "cohere": 1,
    "waymo": 1, "boston dynamics": 1,
    "tesla": 1, "spacex": 1,
    "intel": 1, "amd": 1, "qualcomm": 1, "broadcom": 1, "arm": 1,
    "ibm research": 1, "microsoft research": 1,
    "hugging face": 1,
    "cloudflare": 1, "hashicorp": 1,
    "github": 1, "gitlab": 1,
    "elastic": 1, "mongodb": 1, "confluent": 1,
    "twilio": 1, "sendgrid": 1,
    "shopify": 1, "square": 1, "block inc": 1, "block": 1,
    "lyft": 1, "doordash": 1, "instacart": 1,
    "coinbase": 1, "robinhood": 1,
    "snowflake inc": 1,
    "figma": 1, "notion": 1, "airtable": 1,
    "vercel": 1, "netlify": 1,
    "samsara": 1, "datadog": 1, "crowdstrike": 1,
    "okta": 1, "auth0": 1, "duo security": 1,
    "pagerduty": 1, "new relic": 1, "dynatrace": 1,
    "zendesk": 1, "hubspot": 1,
    "twitch": 1, "discord": 1, "slack": 1,
    "dropbox": 1, "box": 1, "docusign": 1,

    # Tier 2 — Unicorns / top-tier regional product companies
    "flipkart": 2, "phonepe": 2, "paytm": 2, "razorpay": 2, "cred": 2,
    "byju's": 2, "byjus": 2, "swiggy": 2, "zomato": 2, "ola": 2, "oyo": 2,
    "meesho": 2, "nykaa": 2, "lenskart": 2, "freshworks": 2, "chargebee": 2,
    "hasura": 2, "postman": 2, "browserstack": 2, "druva": 2, "icertis": 2,
    "rapido": 2, "groww": 2, "zerodha": 2, "upstox": 2, "sharechat": 2,
    "dailyhunt": 2, "inmobi": 2, "mu sigma": 2, "innovaccer": 2,
    "spinny": 2, "slice": 2, "open": 2, "jupiter": 2, "fi money": 2,
    "unacademy": 2, "vedantu": 2, "eruditus": 2, "testbook": 2,
    "dunzo": 2, "porter": 2, "shiprocket": 2, "delhivery": 2,
    "udaan": 2, "moglix": 2,
    "grab": 2, "gojek": 2, "sea group": 2, "tokopedia": 2, "shopee": 2,
    "nubank": 2, "rappi": 2, "mercado libre": 2,
    # More Indian unicorns / D2C / B2B SaaS
    "mswipe": 2, "khatabook": 2, "okcredit": 2, "ofbusiness": 2, "zetwerk": 2,
    "darwinbox": 2, "springworks": 2, "leadsquared": 2, "zoho": 2, "freshdesk": 2,
    "capillary technologies": 2, "capillary": 2, "mindtickle": 2,
    "whatfix": 2, "exotel": 2, "ozonetel": 2, "kaleyra": 2,
    "niyo": 2, "epifi": 2, "smallcase": 2, "kuvera": 2,
    "classplus": 2, "physicswallah": 2, "pw": 2, "scaler": 2, "masai school": 2,
    "1mg": 2, "pharmeasy": 2, "netmeds": 2, "practo": 2, "healthifyme": 2,
    "cult.fit": 2, "mfine": 2, "ninjacart": 2, "dehaat": 2, "agristar": 2,
    "blue dart": 2, "xpressbees": 2, "shadowfax": 2, "ecom express": 2,
    "ola electric": 2, "ather energy": 2, "pure ev": 2, "bounce": 2,
    "digit insurance": 2, "acko": 2, "coverfox": 2,
    "cars24": 2, "cardekho": 2, "droom": 2, "moto.cash": 2,
    "housing.com": 2, "99acres": 2, "magicbricks": 2, "nobroker": 2,
    "urban company": 2, "urbanclap": 2,
    "milkbasket": 2, "bigbasket": 2, "grofers": 2, "blinkit": 2, "zepto": 2,
    "myntra": 2, "ajio": 2, "tata cliq": 2,
    "dream11": 2, "mpl": 2, "games24x7": 2,
    "razorpay": 2, "instamojo": 2, "cashfree": 2, "payu": 2,
    "airtel": 2, "jio": 2, "reliance jio": 2, "vi": 2, "vodafone idea": 2,
    "infosys bpm": 2, "wipro ventures": 2,
    "clevertap": 2, "moengage": 2,
    # Global unicorns
    "canva": 2, "atlassian": 2, "wix": 2, "squarespace": 2,
    "intercom": 2, "zendesk": 2, "freshservice": 2,
    "asana": 2, "monday.com": 2, "clickup": 2, "trello": 2, "jira": 2,
    "zoom info": 2, "zoominfo": 2,
    "sendbird": 2, "agora": 2, "twilio segment": 2,
    "amplitude": 2, "mixpanel": 2, "segment": 2, "heap": 2,
    "braze": 2, "iterable": 2, "customer.io": 2,
    "miro": 2, "figjam": 2,
    "lattice": 2, "rippling": 2, "deel": 2, "remote.com": 2, "gusto": 2,
    "plaid": 2, "marqeta": 2, "brex": 2, "ramp": 2, "divvy": 2,
    "blend": 2, "mix": 2, "chime": 2, "sofi": 2,
    "gitlab": 2, "jetbrains": 2,
    "hashicorp": 2, "chef": 2, "puppet": 2,
    "lucidworks": 2, "coveo": 2, "algolia": 2,
    "sentry.io": 2, "launchdarkly": 2, "split.io": 2,
    "pendo": 2, "productboard": 2, "appcues": 2,
    "loom": 2, "otter.ai": 2,
    "scale ai": 2, "labelbox": 2, "snorkel ai": 2, "weights & biases": 2,
    "hugging face": 2, "lightning ai": 2, "modal labs": 2,
    "together ai": 2, "replicate": 2, "groq": 2, "perplexity": 2,
    "mistral ai": 2, "stability ai": 2, "runway ml": 2, "midjourney": 2,
    "character.ai": 2, "inflection ai": 2,
    "adept ai": 2, "imbue": 2, "aleph alpha": 2,
    "anyscale": 2, "ray": 2,

    # Tier 3 — Mid-size funded / established regional / strong consulting boutiques / MNC India subsidiaries
    "thoughtworks": 3, "hexaware": 3, "mindtree": 3,
    "l&t technology": 3, "ltts": 3, "persistent systems": 3,
    "zensar": 3, "cyient": 3, "sonata software": 3, "kellton tech": 3,
    "niit technologies": 3, "coforge": 3, "ratechain": 3,
    "sprinklr": 3, "clevertap": 3, "mixpanel": 3, "amplitude": 3,
    "segment": 3, "moengage": 3, "webengage": 3, "netcore": 3,
    "pubmatic": 3, "infoedge": 3, "just dial": 3, "tradeindia": 3,
    "india mart": 3, "indiamart": 3, "mapmyindia": 3, "policybazaar": 3,
    "paytm payments bank": 3, "axis bank": 3, "hdfc bank": 3,
    "icici bank": 3, "kotak mahindra": 3, "yes bank": 3,
    "bajaj finserv": 3, "lending kart": 3, "capital float": 3,
    # MNC India arms (global brand, India-based entity)
    "lg soft india": 3, "lg soft": 3, "lg electronics": 3,
    "ericsson india": 3, "ericsson india global services": 3, "ericsson": 3,
    "samsung r&d india": 3, "samsung india": 3, "samsung research": 3,
    "qualcomm india": 3, "intel india": 3, "amd india": 3,
    "siemens india": 3, "bosch india": 3, "bosch global software": 3,
    "continental india": 3, "harman india": 3, "harman connected services": 3,
    "honeywell india": 3, "ge india": 3, "ge digital": 3,
    "philips india": 3, "sony india": 3, "panasonic india": 3,
    "cisco india": 3, "dell india": 3, "hp india": 3, "hpe india": 3,
    "vmware india": 3, "broadcom india": 3, "texas instruments india": 3,
    "shell india": 3, "bp india": 3, "hsbc india": 3, "jp morgan india": 3,
    "goldman sachs india": 3, "deutsche bank india": 3, "barclays india": 3,
    "mastercard india": 3, "visa india": 3, "american express india": 3,
    "paypal india": 3, "ebay india": 3, "walmart labs india": 3, "walmart global tech": 3,
    "caterpillar india": 3, "cummins india": 3, "3m india": 3,
    "oracle india": 3, "sap labs india": 3, "sap labs": 3,
    "akamai india": 3, "f5 india": 3, "juniper india": 3,

    # Tier 4 — Service / consulting / body-shopping / large SMEs
    "tcs": 4, "tata consultancy": 4, "tata consultancy services": 4, "infosys": 4, "wipro": 4,
    "hcl technologies": 4, "hcltech": 4, "tech mahindra": 4,
    "cognizant": 4, "capgemini": 4, "accenture": 4, "ibm": 4,
    "deloitte": 4, "ey": 4, "ernst & young": 4, "kpmg": 4, "pwc": 4,
    "bain": 4, "mckinsey": 4, "bcg": 4, "boston consulting": 4,
    "dxc technology": 4, "unisys": 4, "ntt data": 4, "fujitsu": 4,
    "atos": 4, "cgi": 4, "virtusa": 4, "mastech": 4,
    "mphasis": 4, "niit": 4, "aptech": 4,
    "kritikal solutions": 4, "kritikal": 4,
    "tata elxsi": 4, "tata technologies": 4, "tata advanced systems": 4,
    "birlasoft": 4, "zensar technologies": 4, "happiest minds": 4,
    "sasken": 4, "quess corp": 4, "teamlease": 4, "manpower india": 4,
    "igate": 4, "patni computer": 4, "hexaware technologies": 4,
    "atos india": 4, "syntel": 4, "kpit technologies": 4,
    "rmsi": 4, "rapsys technologies": 4, "mindlance": 4,
    # More Indian IT services
    "l&t infotech": 4, "lti": 4, "ltimindtree": 4, "mindtree": 3,
    "mphasis": 4, "wns": 4, "wns global": 4, "exl service": 4, "exl": 4,
    "genpact": 4, "concentrix": 4, "firstsource": 4, "teleperformance": 4,
    "eclerx": 4, "datamatics": 4, "subex": 4, "sonata software": 4,
    "nucleus software": 4, "newgen software": 4, "ramco systems": 4,
    "nucleus software exports": 4, "oracle financial services": 4, "ofss": 4,
    "fis global": 4, "fis": 4, "fiserv": 4, "temenos": 4,
    "niit technologies": 4, "coforge": 4, "infoedge": 3,
    "serco": 4, "steria": 4, "cts": 4,
    "accenture solutions": 4, "accenture technology": 4,
    "sapient": 4, "sapient consulting": 4, "sapient nitro": 4,
    "publicis sapient": 4,
    "infosys bpo": 4, "infosys cts": 4,
    "wipro bpo": 4, "wipro technologies": 4,
    "global logic": 4, "globallogic": 4, "epam systems": 4, "epam": 4,
    "globant": 4, "endava": 4, "nagarro": 4, "luxoft": 4,
    "stefanini": 4, "ci&t": 4,
    "xoriant": 4, "persistent systems": 3, "cyient": 3,
    "softchoice": 4, "slalom": 4, "objectivity": 4,
    # Indian banking & financial services
    "sbi": 4, "state bank of india": 4, "pnb": 4, "punjab national bank": 4,
    "bank of baroda": 4, "canara bank": 4, "union bank": 4, "central bank of india": 4,
    "uco bank": 4, "indian bank": 4, "bank of india": 4,
    "sebi": 4, "nabard": 4, "sidbi": 4, "nsdl": 4, "cdsl": 4,
    "bse": 4, "nse": 4,
    "sundaram finance": 4, "mahindra finance": 4, "chola": 4,
    "cholamandalam": 4, "shriram finance": 4, "muthoot finance": 4,
    "lic": 4, "lic of india": 4, "max life": 4, "hdfc life": 4,
    "sbi life": 4, "icici prudential": 4, "bajaj allianz": 4,
    # Global banks & fintech
    "citibank": 3, "citi": 3, "wells fargo": 3,
    "bank of america": 3, "bofa": 3, "morgan stanley": 3,
    "ubs": 3, "credit suisse": 3, "standard chartered": 3,
    "bnp paribas": 3, "bnp": 3, "societe generale": 3, "sg": 3,
    "nomura": 3, "dbs bank": 3, "dbs": 3,
    "fidelity": 3, "vanguard": 3, "blackrock": 3,
    "macquarie": 3, "northern trust": 3, "state street": 3,
    "american express": 3, "amex": 3,
    # Automotive / EV tech
    "waymo": 1, "cruise": 2, "mobileye": 2, "aurora": 2,
    "rivian": 2, "lucid motors": 2, "nio": 2, "xpeng": 2, "li auto": 2,
    "volkswagen group": 3, "volkswagen": 3, "bmw": 3, "daimler": 3, "mercedes": 3,
    "ford": 3, "general motors": 3, "gm": 3, "stellantis": 3, "fiat": 3,
    "toyota": 3, "honda": 3, "hyundai": 3, "kia": 3,
    "tata motors": 3, "mahindra": 3, "maruti suzuki": 3, "maruti": 3,
    "ashok leyland": 4, "hero motocorp": 4, "bajaj auto": 4,
    "continental": 3, "bosch": 3, "denso": 3, "valeo": 3, "magna": 3,
    "aptiv": 3, "lear": 3, "autoliv": 3, "visteon": 3,
    "here technologies": 2, "tomtom": 3,
    # Healthcare tech
    "epic systems": 2, "cerner": 2, "meditech": 2, "mckesson": 3,
    "philips healthcare": 3, "ge healthcare": 3, "siemens healthineers": 3,
    "intuitive surgical": 2, "stryker": 3, "medtronic": 3, "abbott": 3,
    "roche": 3, "novartis digital": 3, "pfizer digital": 3,
    "health catalyst": 2, "truven health": 3, "optum": 3, "change healthcare": 3,
    "muhealth": 4, "athenahealth": 2, "veeva systems": 2, "veeva": 2,
    "iqvia": 3, "iqvia india": 3,
    # Defence / aerospace
    "drdo": 3, "isro": 3, "hal": 4, "beml": 4, "bel": 4,
    "lockheed martin": 2, "boeing": 2, "raytheon": 2, "northrop grumman": 2,
    "bae systems": 2, "thales": 3, "leonardo": 3, "saab": 3,
    "airbus": 3, "rolls royce": 3, "pratt & whitney": 3,
    # Energy / utilities
    "reliance industries": 3, "reliance": 3, "ongc": 4, "ioc": 4,
    "bharat petroleum": 4, "bpcl": 4, "hindustan petroleum": 4, "hpcl": 4,
    "ntpc": 4, "power grid": 4, "tata power": 4, "adani power": 3,
    "greenko": 3, "renew power": 3,
    "schlumberger": 3, "slb": 3, "halliburton": 3, "baker hughes": 3,
    "chevron": 3, "exxon": 3, "total": 3, "bp": 3,
    # Telecom
    "nokia": 3, "ericsson": 3, "huawei": 3, "zte": 3,
    "rakuten mobile": 2, "jio platforms": 2, "airtel enterprise": 3,
    # Media / entertainment
    "hotstar": 2, "disney+": 2, "disney hotstar": 2,
    "sony liv": 3, "zee5": 3, "voot": 3, "mx player": 3,
    "times internet": 3, "times of india": 4,
    "ndtv": 4, "india today": 4, "star india": 3,
    "prime video": 1, "amazon prime video": 1,
    # E-commerce / retail global
    "ebay": 2, "etsy": 2, "alibaba": 2, "jd.com": 2, "pinduoduo": 2,
    "rakuten": 2, "lazada": 2, "bukalapak": 2,
    "target": 3, "walmart": 3, "costco": 3,
    # B2B SaaS global
    "workiva": 2, "zendesk": 2, "freshworks": 2,
    "drift": 2, "gong": 2, "outreach": 2, "salesloft": 2,
    "chorus.ai": 2, "clari": 2, "highspot": 2,
    "seismic": 2, "showpad": 2,
    "zuora": 2, "chargebee": 2, "recurly": 2,
    "coupa": 2, "procurify": 3, "tradogram": 3,
    "netsuite": 3, "sage": 3, "epicor": 3, "infor": 3,
    "unit4": 3, "ifs": 3,
    "benchling": 2, "veeva systems": 2,
    "procore": 2, "autodesk": 2,
    "ptc": 3, "dassault systemes": 3, "ansys": 3,
    # Security
    "palo alto networks": 2, "fortinet": 2, "check point": 2, "checkpoint": 2,
    "splunk": 2, "rapid7": 2, "tenable": 2, "qualys": 2,
    "sentinelone": 2, "cylance": 3, "carbonblack": 3, "carbon black": 3,
    "darktrace": 2, "illumio": 2, "zscaler": 2, "netskope": 2,
    "sailpoint": 2, "cyberark": 2, "beyondtrust": 2,
    "recorded future": 2, "anomali": 3,

    # Tier 5 entries omitted — anything unmatched defaults to 5
}

# Operating model hints used by company_similarity logic
COMPANY_DOMAIN_TAGS: dict[str, list[str]] = {
    "google": ["SEARCH", "CLOUD", "AI", "ADVERTISING"],
    "amazon": ["ECOMMERCE", "CLOUD", "LOGISTICS"],
    "meta": ["SOCIAL", "ADVERTISING", "AI"],
    "microsoft": ["CLOUD", "PRODUCTIVITY", "AI", "ENTERPRISE"],
    "flipkart": ["ECOMMERCE", "LOGISTICS"],
    "paytm": ["FINTECH", "PAYMENTS"],
    "zomato": ["FOODTECH", "LOGISTICS"],
    "swiggy": ["FOODTECH", "LOGISTICS"],
    "ola": ["MOBILITY", "LOGISTICS"],
    "byju's": ["EDTECH"], "byjus": ["EDTECH"],
    "razorpay": ["FINTECH", "PAYMENTS"],
    "phonepe": ["FINTECH", "PAYMENTS"],
    "freshworks": ["SAAS", "CRM"],
    "zerodha": ["FINTECH", "BROKERAGE"],
    "tcs": ["IT_SERVICES", "CONSULTING"],
    "infosys": ["IT_SERVICES", "CONSULTING"],
    "wipro": ["IT_SERVICES", "CONSULTING"],
    "accenture": ["CONSULTING", "IT_SERVICES"],
    "thoughtworks": ["CONSULTING", "PRODUCT_ENGINEERING"],
}


def _normalize(name: str) -> str:
    return name.lower().strip()


def _fuzzy_match(query: str, threshold: float = 0.75) -> str | None:
    """Return best-matching key in TIER_MAP above threshold."""
    q = _normalize(query)
    best_key: str | None = None
    best_ratio = 0.0
    for key in TIER_MAP:
        ratio = SequenceMatcher(None, q, key).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_key = key
    if best_ratio >= threshold:
        return best_key
    # Partial / substring match
    for key in TIER_MAP:
        if key in q or q in key:
            return key
    return None


def classify_company_tier(company_name: str, llm_fallback: bool = True) -> int:
    """Return tier 1-5 for a company name.
    Uses exact → fuzzy match first; LLM fallback when <threshold.
    """
    if not company_name:
        return 5
    q = _normalize(company_name)
    # Exact match
    if q in TIER_MAP:
        return TIER_MAP[q]
    # Fuzzy
    matched_key = _fuzzy_match(q)
    if matched_key:
        return TIER_MAP[matched_key]
    # LLM fallback (only when enabled AND llm_client available)
    if llm_fallback and os.getenv("ENABLE_COMPANY_TIER_LLM", "false").lower() == "true":
        try:
            from llm_client import call_llm_json  # type: ignore
            prompt = (
                f"Classify this company into a tier (1=FAANG/top product, 2=unicorn/well-funded startup, "
                f"3=mid-size funded/established regional, 4=IT services/consulting, 5=unknown/small).\n"
                f"Company: {company_name}\n"
                f'Return JSON: {{"tier": <int 1-5>, "reason": "<one line>"}}'
            )
            result = call_llm_json(
                system_prompt="You classify companies into tiers for a hiring intelligence system.",
                user_message=prompt,
                schema={"type": "object", "properties": {"tier": {"type": "integer"}, "reason": {"type": "string"}}, "required": ["tier", "reason"]},
                label="company_tier_llm",
            )
            if result and isinstance(result.get("tier"), int):
                return max(1, min(5, result["tier"]))
        except Exception:
            pass
    return 5


def tier_to_points(tier: int, max_points: int = 5) -> int:
    """Convert tier 1-5 to points (tier1=max, tier5=1)."""
    mapping = {1: max_points, 2: max(1, max_points - 1), 3: max(1, max_points - 2),
               4: max(1, max_points - 3), 5: 1}
    return mapping.get(tier, 1)


def get_company_domain_tags(company_name: str) -> list[str]:
    """Return known domain tags for a company, if any."""
    q = _normalize(company_name)
    if q in COMPANY_DOMAIN_TAGS:
        return COMPANY_DOMAIN_TAGS[q]
    matched_key = _fuzzy_match(q)
    if matched_key and matched_key in COMPANY_DOMAIN_TAGS:
        return COMPANY_DOMAIN_TAGS[matched_key]
    return []

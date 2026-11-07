"""Fixed reference data: fuel types, interconnectors, regions and emission factors."""


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

# Elexon FUELINST fuel type -> internal key
FUELS = {
    'CCGT': 'ccgt', 'OCGT': 'ocgt', 'COAL': 'coal', 'NUCLEAR': 'nuclear',
    'OIL': 'oil', 'WIND': 'wind', 'NPSHYD': 'hydro', 'PS': 'pumped',
    'BIOMASS': 'biomass', 'BESS': 'battery', 'OTHER': 'other',
}

# Interconnectors: FUELINST code -> (name, country, capacity GW, landing note)
INTERCONNECTORS = {
    'INTFR': ('IFA', 'France', 2.0, 'Sellindge to Calais'),
    'INTIFA2': ('IFA2', 'France', 1.0, 'Hampshire to Normandy'),
    'INTELEC': ('ElecLink', 'France', 1.0, 'Through the Channel Tunnel'),
    'INTNED': ('BritNed', 'Netherlands', 1.0, 'Isle of Grain to Maasvlakte'),
    'INTNEM': ('Nemo Link', 'Belgium', 1.0, 'Kent to Zeebrugge'),
    'INTNSL': ('North Sea Link', 'Norway', 1.4, 'Blyth to Kvilldal'),
    'INTVKL': ('Viking Link', 'Denmark', 1.4, 'Lincolnshire to Revsing'),
    'INTIRL': ('Moyle', 'Ireland', 0.5, 'Scotland to Northern Ireland'),
    'INTEW': ('East West', 'Ireland', 0.5, 'Wales to Dublin'),
    'INTGRNL': ('Greenlink', 'Ireland', 0.5, 'Pembrokeshire to Wexford'),
}
COUNTRY_ORDER = ['France', 'Norway', 'Netherlands', 'Belgium', 'Denmark', 'Ireland']

# The 14 distribution regions. Sheffield Solar's PES IDs (also NESO's licence
# area IDs) -> NESO Carbon Intensity API region IDs. 15 to 17 are England,
# Scotland and Wales, and 18 is Great Britain.
PES_REGIONS = {17: 1, 18: 2, 16: 3, 15: 4, 23: 5, 13: 6, 21: 7, 14: 8, 11: 9, 10: 10, 22: 11, 20: 12, 12: 13, 19: 14}
REGION_NAMES = {
    1: 'North Scotland', 2: 'South Scotland', 3: 'North West England', 4: 'North East England', 5: 'Yorkshire',
    6: 'North Wales & Merseyside', 7: 'South Wales', 8: 'West Midlands', 9: 'East Midlands', 10: 'East England',
    11: 'South West England', 12: 'South England', 13: 'London', 14: 'South East England', 15: 'England',
    16: 'Scotland', 17: 'Wales', 18: 'Great Britain',
}
MIX_FUELS = ['biomass', 'coal', 'imports', 'gas', 'nuclear', 'other', 'hydro', 'solar', 'wind']
INDEX_LEVELS = ['very low', 'low', 'moderate', 'high', 'very high']

# Emission factors (gCO2/kWh) from NESO's Carbon Intensity methodology
# (May 2024 issue). Norway and Denmark are not in NESO's published table;
# the values used here are approximations. Only used as a fallback when the
# official Carbon Intensity API figure is unavailable.
CARBON_FACTORS = {
    'ccgt': 394, 'ocgt': 651, 'coal': 937, 'oil': 935, 'biomass': 120,
    'other': 300, 'nuclear': 0, 'wind': 0, 'solar': 0, 'hydro': 0,
    'pumped': 0, 'battery': 0,
}
IMPORT_FACTORS = {
    'France': 53, 'Netherlands': 474, 'Belgium': 179, 'Ireland': 458,
    'Norway': 25, 'Denmark': 150,
}

# Series exposed by the history API, in stacking order (bottom to top)
SUPPLY_SERIES = ['nuclear', 'wind', 'solar', 'hydro', 'biomass', 'gas',
                 'coal', 'oil', 'other', 'imports', 'storage_out']
NEGATIVE_SERIES = ['exports', 'storage_in']

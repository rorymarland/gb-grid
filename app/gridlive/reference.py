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
# the values used here are approximations. Used for the estimate when the
# official Carbon Intensity API figure is unavailable, and as each
# neighbour's typical figure where there is no live one (neighbours.py).
CARBON_FACTORS = {
    'ccgt': 394, 'ocgt': 651, 'coal': 937, 'oil': 935, 'biomass': 120,
    'other': 300, 'nuclear': 0, 'wind': 0, 'solar': 0, 'hydro': 0,
    'pumped': 0, 'battery': 0,
}
# ENTSO-E production types (psrType) -> g/kWh, for a neighbour's generation mix (entsoe.py).
# Counted the way NESO counts Britain's own generation: emissions at the power station only, at
# NESO's factors, so the figures compare like with like. ENTSO-E's gas isn't split into combined
# and open cycle, so it counts as combined cycle, as most of it is. Lignite, peat and gas from coal
# (none of them in the zones linked to Britain) count as coal, oil shale as oil, and waste as NESO's
# 'other'.
ENTSOE_FACTORS = {
    'B01': 120,     # biomass
    'B02': 937,     # lignite, as coal
    'B03': 937,     # gas derived from coal, as coal
    'B04': 394,     # fossil gas, as combined cycle
    'B05': 937,     # hard coal
    'B06': 935,     # oil
    'B07': 935,     # oil shale, as oil
    'B08': 937,     # peat, as coal
    'B09': 0,       # geothermal
    'B10': 0,       # pumped storage, generating
    'B11': 0,       # run-of-river hydro
    'B12': 0,       # reservoir hydro
    'B13': 0,       # marine
    'B14': 0,       # nuclear
    'B15': 0,       # other renewable
    'B16': 0,       # solar
    'B17': 300,     # waste, as NESO's 'other'
    'B18': 0,       # offshore wind
    'B19': 0,       # onshore wind
    'B20': 300,     # other, as NESO's 'other'
    'B25': 0,       # energy storage, discharging
}
IMPORT_FACTORS = {
    'France': 53, 'Netherlands': 474, 'Belgium': 179, 'Ireland': 458,
    'Norway': 25, 'Denmark': 150,
}

# Series exposed by the history API, in stacking order (bottom to top)
SUPPLY_SERIES = ['nuclear', 'wind', 'solar', 'hydro', 'biomass', 'gas',
                 'coal', 'oil', 'other', 'imports', 'storage_out']
NEGATIVE_SERIES = ['exports', 'storage_in']

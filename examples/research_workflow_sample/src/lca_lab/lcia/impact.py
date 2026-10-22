"""Separate LCIA climate-change accounting functionality."""


def calculate_gwp(inventory, characterization_factors):
    return sum(value * characterization_factors.get(name, 0)
               for name, value in inventory.items())


def export_climate_result(result):
    return {"kg_CO2eq": result}

"""Urban Density — shared pipeline code.

Modules:
  config    paths, constants, population-source catalogue
  registry  cities_all.json (the city list the dashboard reads)
  boundary  OSM landuse mask (Overpass) and reference polygons
  sources   population rasters: WorldPop / GHS-POP / HRSL acquisition
  pipeline  clip → density → percentile cores → map assets
"""

"""One module per data source. Each exposes NAME, KIND and fetch(ctx) -> list[dict]."""

from . import gdacs, gdelt, reliefweb, unhcr, usgs

SOURCES = {m.NAME: m for m in (usgs, gdacs, gdelt, reliefweb, unhcr)}

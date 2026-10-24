"""``acsf-usd view``: a read-only 3D view of exported stages, drawn with Strata.

Optional: it needs the Strata library (``acsf-usd fetch-strata``), which nothing else in
acsf-usd-env loads. The exporter commands never import this package.
"""

# Texts the bottom panel can show (kept here so argument parsing loads nothing else).
PANELS = ("thought", "reflection", "perception", "reply")

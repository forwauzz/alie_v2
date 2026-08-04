"""Pipeline stages (PRD §4.2). Each is a pure function over ids: ids in, stores read,
stores written. That is what makes cloud migration a deployment detail (§3.8, §13.4).

Deterministic: 1, 2 (except the classifier fallback), 3, 4a, 5, 6, and all validation.
Model: 4b, the classifier fallback, the adjudicator, and the health narrative composer.
"""

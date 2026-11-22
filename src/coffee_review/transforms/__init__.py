"""The clean stage's transforms: parsed text columns in, typed columns out.

Pure Polars, no I/O (apart from reading the hand-kept lookup CSVs that ship
with the code). Every transform reads ``raw_<field>`` columns and adds its typed
columns plus a ``_rule_<field>`` column saying which rules it applied; see
``rules.py``.
"""

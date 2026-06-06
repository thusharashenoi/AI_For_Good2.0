"""RaktSetu intelligence layer: data processing, eligibility, matching, blood graph, buffer.

This package owns the ML / data-science portion of the RaktSetu Blood Warriors project:
cleaning the donor/patient dataset, an ABO/Rh compatibility + eligibility engine, a donor
"willingness" model, a population-scale Blood Graph (replacing rigid 8-10 donor bridges),
and a per-patient one-cycle buffer system so no patient is ever left searching for blood.
"""

__version__ = "0.1.0"

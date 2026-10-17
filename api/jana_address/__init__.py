"""Jana Address — free-form Kazakhstan address parser (hackathon skeleton).

Pipeline:  preprocess → tokenize → rules (+ ML tagger later) → arbiter/assembly
           → canonicalize → gazetteer enrichment → confidence → jury schema.

Every output value is cut from the ORIGINAL string by character offsets
(`raw[start:end]`), which is what makes the jury's exact-match metric safe.
"""

__version__ = "0.1.0"

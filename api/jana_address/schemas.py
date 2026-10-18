"""Pydantic schemas. `RecordOut` is EXACTLY the jury schema — do not add keys to it.
Extra diagnostics live in `ExplainOut` (returned only with ?explain=1)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RecordIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    raw_address: str = ""


class BatchIn(BaseModel):
    records: list[RecordIn]


class RecordOut(BaseModel):
    id: str
    raw_address: str
    province: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    living_area: Optional[str] = None
    street_type: Optional[str] = None
    street_name: Optional[str] = None
    house: Optional[str] = None
    apartment: Optional[str] = None
    entrance: Optional[str] = None
    floor: Optional[str] = None
    landmark: Optional[str] = None
    confidence: float = Field(0.05, ge=0.0, le=1.0)


class SpanOut(BaseModel):
    field: str
    start: int
    end: int
    text: str
    rule: str
    score: float


class TokenOut(BaseModel):
    text: str
    start: int
    end: int
    kind: str
    label: Optional[str] = None
    role: Optional[str] = None
    model_label: Optional[str] = None      # BIO label from the ML tagger (explain mode, when a model is loaded)
    model_prob: Optional[float] = None


class ExplainOut(RecordOut):
    spans: list[SpanOut] = []
    tokens: list[TokenOut] = []
    flags: list[str] = []
    provenance: dict[str, Optional[str]] = {}
    field_confidence: dict[str, float] = {}
    model_probs: dict[str, float] = {}
    geo: Optional[dict] = None        # {lat, lon, precision: house|house_near|street|microdistrict|district|locality, source}


class Correction(BaseModel):
    """A manual fix from the UI. Stored for RETRAINING ONLY — never consulted at inference
    (the brief bans returning stored answers for exact input matches)."""
    id: str
    raw_address: str
    fields: dict[str, Optional[str]]
    reviewer: Optional[str] = None
    note: Optional[str] = None

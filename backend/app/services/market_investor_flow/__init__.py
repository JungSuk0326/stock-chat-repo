"""Market-wide investor-type trading flows (KRX detailed breakdown).

Phase 1 KR only — pykrx behind KRX login (KRX_ID/KRX_PW env vars).
Higher-level service code imports from `kr` directly; the base class
exists so Phase 2 can drop in a non-KRX source (e.g. NYSE/NASDAQ TAQ
or 13F-style filings, though semantics differ wildly).
"""

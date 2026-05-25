from pydantic import BaseModel, ConfigDict


class InstrumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exchange: str
    symbol: str
    market: str | None
    name: str | None
    country: str
    currency: str

    @property
    def canonical_id(self) -> str:
        return f"{self.exchange}:{self.symbol}"

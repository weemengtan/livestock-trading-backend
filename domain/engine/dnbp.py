"""The single source of truth (PRD §5.3, §17.1).

    bing_dnbp = (avg_price_aud - cif_buffer) * dnbp_factor[species]

Three inputs. Nothing else in A-V or X-AF can change the published number.
This function must stay exactly this small: no avg_weight_kg, pack_cost_ph,
offal_return_ph, skin_return_ph, expected_livestock_cost_per_kg, incoterm,
standard_weight, or dnbp_benchmark in its signature — not merely unused,
physically absent, so a future change elsewhere cannot smuggle a new
dependency in. Changes here require two reviewers (§17.1).
"""

from decimal import Decimal

from domain.engine.config import DEFAULT_MODEL_TYPE, EverhealthConfig

MODEL_FACTOR_AFTER_BUFFER = DEFAULT_MODEL_TYPE


class UnknownModelTypeError(Exception):
    """A reference-data version names a model this code does not implement.
    Never guessed or defaulted: a price must not be produced by a formula
    nobody selected."""

    def __init__(self, model_type: str) -> None:
        self.model_type = model_type
        super().__init__(f"No DNBP model implemented for '{model_type}'")


class NoDnbpFactorError(Exception):
    """Raised when `species` has no configured dnbp_factor. Caught by the
    calling layer (workings.py) and turned into a NO_DNBP_FACTOR BLOCK issue
    — never guessed, never defaulted (§5.7, §6.9)."""

    def __init__(self, species: str) -> None:
        self.species = species
        super().__init__(f"No DNBP factor configured for {species}")


def _factor_after_buffer(avg_price_aud: Decimal, species: str, config: EverhealthConfig) -> Decimal:
    factor = config.dnbp_factor_by_species.get(species)
    if factor is None:
        raise NoDnbpFactorError(species)
    return (avg_price_aud - config.cif_buffer_per_kg) * factor


# Whitelist of implemented formulas. A version chooses one by name; adding a
# formula is a code change with review (§17.1), never a runtime expression.
_MODELS = {MODEL_FACTOR_AFTER_BUFFER: _factor_after_buffer}


def available_model_types() -> tuple[str, ...]:
    return tuple(_MODELS)


def compute_bing_dnbp(avg_price_aud: Decimal, species: str, config: EverhealthConfig) -> Decimal:
    model = _MODELS.get(config.model_type)
    if model is None:
        raise UnknownModelTypeError(config.model_type)
    return model(avg_price_aud, species, config)
